-- Audit chain, append-only enforcement, integrity triggers and tenancy RPCs (Phase 1).

-- ---------------------------------------------------------------- helpers

CREATE OR REPLACE FUNCTION app.actor() RETURNS uuid
LANGUAGE sql STABLE SET search_path = '' AS $$
  -- Browser calls: the JWT user. Server/worker (service role): the user it acts for, set per
  -- transaction with SELECT set_config('app.actor_id', '<uuid>', true).
  SELECT coalesce(auth.uid(), nullif(current_setting('app.actor_id', true), '')::uuid)
$$;

CREATE OR REPLACE FUNCTION app.try_uuid(p text) RETURNS uuid
LANGUAGE plpgsql IMMUTABLE SET search_path = '' AS $$
BEGIN
  RETURN p::uuid;
EXCEPTION WHEN invalid_text_representation THEN
  RETURN NULL;
END $$;

-- ---------------------------------------------------------------- audit chain (ADR-0005)

CREATE TABLE public.audit_events (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id       uuid NOT NULL REFERENCES public.organisations (id),
  seq          bigint NOT NULL CHECK (seq >= 1),
  actor_id     uuid,
  action       text NOT NULL,
  entity_type  text NOT NULL,
  entity_id    uuid,
  details      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at   timestamptz NOT NULL,
  prev_hash    text CHECK (prev_hash IS NULL OR prev_hash ~ '^[a-f0-9]{64}$'),
  event_hash   text NOT NULL CHECK (event_hash ~ '^[a-f0-9]{64}$'),
  UNIQUE (org_id, seq),
  UNIQUE (org_id, id)
);

CREATE OR REPLACE FUNCTION app.audit_hash(
  p_prev text, p_org uuid, p_seq bigint, p_actor uuid, p_action text, p_entity_type text,
  p_entity_id uuid, p_created_at timestamptz, p_details jsonb
) RETURNS text LANGUAGE sql IMMUTABLE SET search_path = '' AS $$
  SELECT encode(sha256(convert_to(concat_ws('|',
    coalesce(p_prev, ''), p_org::text, p_seq::text, coalesce(p_actor::text, ''), p_action, p_entity_type,
    coalesce(p_entity_id::text, ''),
    to_char(p_created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    p_details::text), 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION app.log_event(
  p_org uuid, p_action text, p_entity_type text, p_entity_id uuid, p_details jsonb DEFAULT '{}'::jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_prev  text;
  v_seq   bigint;
  v_now   timestamptz := clock_timestamp();
  v_actor uuid := app.actor();
  v_id    uuid := gen_random_uuid();
BEGIN
  -- Serialise appends per organisation so the chain never forks.
  PERFORM pg_advisory_xact_lock(hashtextextended('ai4e-audit:' || p_org::text, 0));
  SELECT e.event_hash, e.seq INTO v_prev, v_seq
    FROM public.audit_events e WHERE e.org_id = p_org ORDER BY e.seq DESC LIMIT 1;
  v_seq := coalesce(v_seq, 0) + 1;
  INSERT INTO public.audit_events (id, org_id, seq, actor_id, action, entity_type, entity_id, details,
                                   created_at, prev_hash, event_hash)
  VALUES (v_id, p_org, v_seq, v_actor, p_action, p_entity_type, p_entity_id, coalesce(p_details, '{}'::jsonb),
          v_now, v_prev,
          app.audit_hash(v_prev, p_org, v_seq, v_actor, p_action, p_entity_type, p_entity_id, v_now,
                         coalesce(p_details, '{}'::jsonb)));
  RETURN v_id;
END $$;

-- Returns the first broken sequence number, or NULL when the whole chain is intact.
CREATE OR REPLACE FUNCTION app.audit_chain_first_break(p_org uuid) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  r       record;
  v_prev  text := NULL;
  v_seq   bigint := 0;
BEGIN
  FOR r IN SELECT * FROM public.audit_events WHERE org_id = p_org ORDER BY seq LOOP
    v_seq := v_seq + 1;
    IF r.seq <> v_seq OR r.prev_hash IS DISTINCT FROM v_prev
       OR r.event_hash <> app.audit_hash(r.prev_hash, r.org_id, r.seq, r.actor_id, r.action, r.entity_type,
                                         r.entity_id, r.created_at, r.details) THEN
      RETURN r.seq;
    END IF;
    v_prev := r.event_hash;
  END LOOP;
  RETURN NULL;
END $$;

-- Generic row trigger: records material actions. Large payload columns are left out of details.
CREATE OR REPLACE FUNCTION app.audit_row() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_row     jsonb := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  v_old     jsonb := CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END;
  v_org     uuid  := CASE WHEN TG_TABLE_NAME = 'organisations' THEN (v_row ->> 'id')::uuid
                          ELSE (v_row ->> 'org_id')::uuid END;
  v_details jsonb;
  v_key     text;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    -- job heartbeats/progress are not material; only status changes are
    IF TG_TABLE_NAME = 'processing_jobs' AND (v_row ->> 'status') = (v_old ->> 'status') THEN
      RETURN NULL;
    END IF;
    v_details := '{}'::jsonb;
    FOR v_key IN SELECT jsonb_object_keys(v_row) LOOP
      IF v_row -> v_key IS DISTINCT FROM v_old -> v_key
         AND v_key NOT IN ('heartbeat_at', 'progress', 'body', 'cells', 'mapping', 'header', 'payload') THEN
        v_details := v_details || jsonb_build_object(v_key, v_row -> v_key);
      END IF;
    END LOOP;
    v_details := jsonb_build_object('changed', v_details);
  ELSE
    v_details := v_row - ARRAY['body', 'cells', 'mapping', 'header', 'payload', 'evidence', 'statistics',
                               'peer_group', 'exclusions', 'provenance'];
  END IF;
  PERFORM app.log_event(v_org, TG_TABLE_NAME || '.' || lower(TG_OP), TG_TABLE_NAME,
                        (v_row ->> 'id')::uuid, v_details);
  RETURN NULL;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['organisations', 'memberships', 'projects', 'data_sources', 'datasets',
                           'dataset_versions', 'processing_jobs', 'analytical_runs', 'finding_reviews',
                           'investigation_notes', 'claims', 'claim_evidence', 'reports', 'mapping_templates']
  LOOP
    EXECUTE format('CREATE TRIGGER audit_%1$s AFTER INSERT OR UPDATE OR DELETE ON public.%1$I
                    FOR EACH ROW EXECUTE FUNCTION app.audit_row()', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- append-only and immutability

CREATE OR REPLACE FUNCTION app.forbid_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  RAISE EXCEPTION 'append_only: % rows cannot be %d', TG_TABLE_NAME, lower(TG_OP)
    USING ERRCODE = 'insufficient_privilege';
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['audit_events', 'finding_reviews', 'investigation_notes', 'claim_evidence',
                           'reports', 'raw_rows']
  LOOP
    EXECUTE format('CREATE TRIGGER append_only_%1$s BEFORE UPDATE OR DELETE ON public.%1$I
                    FOR EACH ROW EXECUTE FUNCTION app.forbid_change()', t);
    EXECUTE format('CREATE TRIGGER append_only_truncate_%1$s BEFORE TRUNCATE ON public.%1$I
                    FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change()', t);
  END LOOP;
END $$;

-- Dataset versions: never deleted; identity and provenance fields never change once set.
CREATE OR REPLACE FUNCTION app.dataset_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'append_only: dataset versions cannot be deleted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF (NEW.id, NEW.org_id, NEW.project_id, NEW.dataset_id, NEW.data_source_id, NEW.version_number,
      NEW.storage_path, NEW.original_filename, NEW.content_type, NEW.declared_size_bytes, NEW.uploaded_by,
      NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.org_id, OLD.project_id, OLD.dataset_id, OLD.data_source_id, OLD.version_number,
      OLD.storage_path, OLD.original_filename, OLD.content_type, OLD.declared_size_bytes, OLD.uploaded_by,
      OLD.created_at) THEN
    RAISE EXCEPTION 'immutable: dataset version identity fields cannot change' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF (OLD.sha256 IS NOT NULL AND NEW.sha256 IS DISTINCT FROM OLD.sha256)
     OR (OLD.size_bytes IS NOT NULL AND NEW.size_bytes IS DISTINCT FROM OLD.size_bytes)
     OR (OLD.finalized_at IS NOT NULL AND NEW.finalized_at IS DISTINCT FROM OLD.finalized_at) THEN
    RAISE EXCEPTION 'immutable: provenance of a finalized version cannot change' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER dataset_version_guard BEFORE UPDATE OR DELETE ON public.dataset_versions
  FOR EACH ROW EXECUTE FUNCTION app.dataset_version_guard();

-- Claims: never deleted; statement fixed; status can only move unverified -> verified (once).
CREATE OR REPLACE FUNCTION app.claim_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'append_only: claims cannot be deleted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF (NEW.id, NEW.org_id, NEW.project_id, NEW.finding_id, NEW.statement, NEW.created_by, NEW.created_at)
     IS DISTINCT FROM (OLD.id, OLD.org_id, OLD.project_id, OLD.finding_id, OLD.statement, OLD.created_by, OLD.created_at)
     OR OLD.status = 'verified_fact' THEN
    RAISE EXCEPTION 'immutable: claim content cannot change' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER claim_guard BEFORE UPDATE OR DELETE ON public.claims
  FOR EACH ROW EXECUTE FUNCTION app.claim_guard();

-- Organisations are not deleted through the app.
CREATE TRIGGER no_delete_organisations BEFORE DELETE ON public.organisations
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();

-- Every organisation keeps at least one admin.
CREATE OR REPLACE FUNCTION app.keep_one_admin() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF OLD.role = 'admin' AND (TG_OP = 'DELETE' OR NEW.role <> 'admin') THEN
    IF NOT EXISTS (SELECT 1 FROM public.memberships m
                   WHERE m.org_id = OLD.org_id AND m.role = 'admin' AND m.user_id <> OLD.user_id) THEN
      RAISE EXCEPTION 'last_admin: an organisation must keep at least one admin' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER keep_one_admin BEFORE UPDATE OR DELETE ON public.memberships
  FOR EACH ROW EXECUTE FUNCTION app.keep_one_admin();

-- ---------------------------------------------------------------- profiles

CREATE OR REPLACE FUNCTION app.handle_new_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  INSERT INTO public.profiles (id, email, display_name)
  VALUES (NEW.id, NEW.email, nullif(left(NEW.raw_user_meta_data ->> 'display_name', 120), ''))
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END $$;
CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION app.handle_new_user();
INSERT INTO public.profiles (id, email) SELECT id, email FROM auth.users ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION app.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER profiles_updated_at BEFORE UPDATE ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

-- ---------------------------------------------------------------- tenancy RPCs (called by the browser)

CREATE OR REPLACE FUNCTION public.create_organisation(p_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_uid uuid := auth.uid();
  v_org uuid;
BEGIN
  IF v_uid IS NULL THEN
    RAISE EXCEPTION 'unauthenticated' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO public.organisations (name, created_by) VALUES (btrim(p_name), v_uid) RETURNING id INTO v_org;
  INSERT INTO public.memberships (org_id, user_id, role, created_by) VALUES (v_org, v_uid, 'admin', v_uid);
  RETURN v_org;
END $$;

CREATE OR REPLACE FUNCTION app.require_admin(p_org uuid) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.memberships m
                 WHERE m.org_id = p_org AND m.user_id = auth.uid() AND m.role = 'admin') THEN
    RAISE EXCEPTION 'forbidden: organisation admin required' USING ERRCODE = 'insufficient_privilege';
  END IF;
END $$;

-- "Invitation" for the MVP: an admin adds an already-registered user by email.
CREATE OR REPLACE FUNCTION public.add_member(p_org uuid, p_email text, p_role text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_user uuid;
  v_id   uuid;
BEGIN
  PERFORM app.require_admin(p_org);
  SELECT u.id INTO v_user FROM auth.users u WHERE lower(u.email) = lower(btrim(p_email));
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'not_found: no registered user with that email; ask them to sign up first'
      USING ERRCODE = 'no_data_found';
  END IF;
  INSERT INTO public.memberships (org_id, user_id, role, created_by)
  VALUES (p_org, v_user, p_role, auth.uid()) RETURNING id INTO v_id;
  RETURN v_id;
EXCEPTION WHEN unique_violation THEN
  RAISE EXCEPTION 'conflict: that user is already a member' USING ERRCODE = 'unique_violation';
END $$;

CREATE OR REPLACE FUNCTION public.change_member_role(p_org uuid, p_user uuid, p_role text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  PERFORM app.require_admin(p_org);
  UPDATE public.memberships SET role = p_role WHERE org_id = p_org AND user_id = p_user;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not_found: membership' USING ERRCODE = 'no_data_found';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.remove_member(p_org uuid, p_user uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  PERFORM app.require_admin(p_org);
  DELETE FROM public.memberships WHERE org_id = p_org AND user_id = p_user;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not_found: membership' USING ERRCODE = 'no_data_found';
  END IF;
END $$;

-- Archive or restore a dataset version (admin only, audited, reversible).
CREATE OR REPLACE FUNCTION public.set_dataset_version_archived(p_version uuid, p_archived boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_org uuid;
BEGIN
  SELECT org_id INTO v_org FROM public.dataset_versions WHERE id = p_version;
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'not_found: dataset version' USING ERRCODE = 'no_data_found';
  END IF;
  PERFORM app.require_admin(v_org);
  UPDATE public.dataset_versions
     SET archived_at = CASE WHEN p_archived THEN now() ELSE NULL END,
         archived_by = CASE WHEN p_archived THEN auth.uid() ELSE NULL END
   WHERE id = p_version;
END $$;

-- Members can check that their organisation's audit chain is intact.
CREATE OR REPLACE FUNCTION public.audit_chain_status(p_org uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_break bigint; v_last record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.memberships m WHERE m.org_id = p_org AND m.user_id = auth.uid()) THEN
    RAISE EXCEPTION 'forbidden' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_break := app.audit_chain_first_break(p_org);
  SELECT id, seq, event_hash INTO v_last FROM public.audit_events WHERE org_id = p_org ORDER BY seq DESC LIMIT 1;
  RETURN jsonb_build_object('intact', v_break IS NULL, 'first_break_seq', v_break,
                            'events', coalesce(v_last.seq, 0), 'head_event_id', v_last.id,
                            'head_event_hash', v_last.event_hash);
END $$;
