-- Grants and Row Level Security (Phase 1). Deny by default: anon gets nothing; authenticated gets
-- only what is listed here; every row is further filtered by RLS on organisation membership.
-- The service role (server/worker only) bypasses RLS; server code filters by verified org_id.

-- ---------------------------------------------------------------- membership helpers

CREATE OR REPLACE FUNCTION app.is_member(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT EXISTS (SELECT 1 FROM public.memberships m WHERE m.org_id = p_org AND m.user_id = auth.uid())
$$;

CREATE OR REPLACE FUNCTION app.has_role(p_org uuid, p_roles text[]) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT EXISTS (SELECT 1 FROM public.memberships m
                 WHERE m.org_id = p_org AND m.user_id = auth.uid() AND m.role = ANY (p_roles))
$$;

CREATE OR REPLACE FUNCTION app.shares_org(p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT EXISTS (SELECT 1 FROM public.memberships a JOIN public.memberships b ON a.org_id = b.org_id
                 WHERE a.user_id = auth.uid() AND b.user_id = p_user)
$$;

-- ---------------------------------------------------------------- privileges

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM anon, authenticated, PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated, PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated, PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated, PUBLIC;

GRANT USAGE ON SCHEMA app TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION app.is_member(uuid), app.has_role(uuid, text[]), app.shares_org(uuid),
                          app.try_uuid(text) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION app.log_event(uuid, text, text, uuid, jsonb), app.audit_chain_first_break(uuid),
                          app.actor() TO service_role;

GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- Read access (rows still filtered by RLS)
GRANT SELECT ON public.organisations, public.profiles, public.memberships, public.projects,
  public.data_sources, public.datasets, public.dataset_versions, public.raw_rows, public.result_records,
  public.mapping_templates, public.admin_units, public.validation_rules, public.processing_jobs,
  public.analytical_runs, public.validation_issues, public.findings, public.finding_reviews,
  public.investigation_notes, public.claims, public.claim_evidence, public.reports, public.audit_events
  TO authenticated;

-- Direct writes the browser may make (each also constrained by an RLS policy below)
GRANT INSERT ON public.projects, public.mapping_templates, public.admin_units, public.finding_reviews,
  public.investigation_notes, public.claims, public.claim_evidence TO authenticated;
GRANT UPDATE (election_name, election_year, country, election_level, description, archived_at)
  ON public.projects TO authenticated;
GRANT UPDATE (display_name) ON public.profiles TO authenticated;

-- RPCs
GRANT EXECUTE ON FUNCTION public.create_organisation(text), public.add_member(uuid, text, text),
  public.change_member_role(uuid, uuid, text), public.remove_member(uuid, uuid),
  public.set_dataset_version_archived(uuid, boolean), public.audit_chain_status(uuid)
  TO authenticated;

-- ---------------------------------------------------------------- RLS: enable everywhere

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- Member-readable tenant tables
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['memberships', 'projects', 'data_sources', 'datasets', 'dataset_versions',
    'raw_rows', 'result_records', 'mapping_templates', 'admin_units', 'processing_jobs', 'analytical_runs',
    'validation_issues', 'findings', 'finding_reviews', 'investigation_notes', 'claims', 'claim_evidence',
    'reports', 'audit_events']
  LOOP
    EXECUTE format('CREATE POLICY %1$s_member_read ON public.%1$I FOR SELECT TO authenticated
                    USING (app.is_member(org_id))', t);
  END LOOP;
END $$;

CREATE POLICY organisations_member_read ON public.organisations FOR SELECT TO authenticated
  USING (app.is_member(id));

CREATE POLICY profiles_read ON public.profiles FOR SELECT TO authenticated
  USING (id = auth.uid() OR app.shares_org(id));
CREATE POLICY profiles_update_own ON public.profiles FOR UPDATE TO authenticated
  USING (id = auth.uid()) WITH CHECK (id = auth.uid());

CREATE POLICY validation_rules_read ON public.validation_rules FOR SELECT TO authenticated USING (true);

-- Projects: admins create and edit
CREATE POLICY projects_admin_insert ON public.projects FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin']) AND created_by = auth.uid() AND archived_at IS NULL);
CREATE POLICY projects_admin_update ON public.projects FOR UPDATE TO authenticated
  USING (app.has_role(org_id, ARRAY['admin'])) WITH CHECK (app.has_role(org_id, ARRAY['admin']));

-- Mapping templates and admin unit reference lists: admins and analysts
CREATE POLICY mapping_templates_insert ON public.mapping_templates FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin', 'analyst']) AND created_by = auth.uid());
CREATE POLICY admin_units_insert ON public.admin_units FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin', 'analyst']) AND created_by = auth.uid());

-- Human review decisions: admins and analysts only, as themselves
CREATE POLICY finding_reviews_insert ON public.finding_reviews FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin', 'analyst']) AND reviewer_id = auth.uid());

-- Notes, allegations and their evidence: admins, analysts, researchers
CREATE POLICY investigation_notes_insert ON public.investigation_notes FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin', 'analyst', 'researcher']) AND author_id = auth.uid());
CREATE POLICY claims_insert ON public.claims FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin', 'analyst', 'researcher']) AND created_by = auth.uid()
              AND status = 'unverified_allegation');
CREATE POLICY claim_evidence_insert ON public.claim_evidence FOR INSERT TO authenticated
  WITH CHECK (app.has_role(org_id, ARRAY['admin', 'analyst', 'researcher']) AND added_by = auth.uid());
