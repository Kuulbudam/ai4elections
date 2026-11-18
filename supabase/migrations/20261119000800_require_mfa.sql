-- Require two-step verification (authenticator-app TOTP) for every signed-in user (founder decision, 18 Nov).
-- A session only reaches assurance level "aal2" after the user verifies a TOTP code. Enforced in the
-- database, not just the UI: restrictive RLS on every public table, on the private bucket, and in RPCs.

CREATE OR REPLACE FUNCTION app.is_aal2() RETURNS boolean
LANGUAGE sql STABLE SET search_path = '' AS $$
  SELECT coalesce(auth.jwt() ->> 'aal', '') = 'aal2'
$$;
REVOKE ALL ON FUNCTION app.is_aal2() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION app.is_aal2() TO authenticated, service_role;

CREATE OR REPLACE FUNCTION app.require_aal2() RETURNS void
LANGUAGE plpgsql STABLE SET search_path = '' AS $$
BEGIN
  IF NOT app.is_aal2() THEN
    RAISE EXCEPTION 'mfa_required: complete two-step verification first' USING ERRCODE = 'insufficient_privilege';
  END IF;
END $$;
REVOKE ALL ON FUNCTION app.require_aal2() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION app.require_aal2() TO authenticated, service_role;

-- Restrictive policy: AND-ed with every existing permissive policy, for every command.
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format(
      'CREATE POLICY %1$s_require_mfa ON public.%1$I AS RESTRICTIVE FOR ALL TO authenticated
         USING (app.is_aal2()) WITH CHECK (app.is_aal2())', t);
  END LOOP;
END $$;

DROP POLICY IF EXISTS datasets_require_mfa ON storage.objects;
CREATE POLICY datasets_require_mfa ON storage.objects AS RESTRICTIVE FOR ALL TO authenticated
  USING (bucket_id <> 'datasets' OR app.is_aal2())
  WITH CHECK (bucket_id <> 'datasets' OR app.is_aal2());

-- RPCs run as SECURITY DEFINER (RLS does not apply inside), so they check the level themselves.
CREATE OR REPLACE FUNCTION app.require_admin(p_org uuid) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  PERFORM app.require_aal2();
  IF NOT EXISTS (SELECT 1 FROM public.memberships m
                 WHERE m.org_id = p_org AND m.user_id = auth.uid() AND m.role = 'admin') THEN
    RAISE EXCEPTION 'forbidden: organisation admin required' USING ERRCODE = 'insufficient_privilege';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.create_organisation(p_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_uid uuid := auth.uid();
  v_org uuid;
BEGIN
  IF v_uid IS NULL THEN
    RAISE EXCEPTION 'unauthenticated' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM app.require_aal2();
  INSERT INTO public.organisations (name, created_by) VALUES (btrim(p_name), v_uid) RETURNING id INTO v_org;
  INSERT INTO public.memberships (org_id, user_id, role, created_by) VALUES (v_org, v_uid, 'admin', v_uid);
  RETURN v_org;
END $$;

CREATE OR REPLACE FUNCTION public.audit_chain_status(p_org uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_break bigint; v_last record;
BEGIN
  PERFORM app.require_aal2();
  IF NOT EXISTS (SELECT 1 FROM public.memberships m WHERE m.org_id = p_org AND m.user_id = auth.uid()) THEN
    RAISE EXCEPTION 'forbidden' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_break := app.audit_chain_first_break(p_org);
  SELECT id, seq, event_hash INTO v_last FROM public.audit_events WHERE org_id = p_org ORDER BY seq DESC LIMIT 1;
  RETURN jsonb_build_object('intact', v_break IS NULL, 'first_break_seq', v_break,
                            'events', coalesce(v_last.seq, 0), 'head_event_id', v_last.id,
                            'head_event_hash', v_last.event_hash);
END $$;
