-- Row-level security at scale (found by the live isolation check on 20 Nov: an unfiltered read of a large
-- table timed out). The rules do not change; only how they are evaluated.
--
-- Before: USING (app.is_member(org_id)) called a SECURITY DEFINER function once per row.
-- After:  USING (org_id IN (SELECT app.my_org_ids())) looks up the user's organisations once per query, so
--         Postgres can use an index on org_id. The two-step check is likewise evaluated once per query.

CREATE OR REPLACE FUNCTION app.my_org_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT m.org_id FROM public.memberships m WHERE m.user_id = auth.uid()
$$;
REVOKE ALL ON FUNCTION app.my_org_ids() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION app.my_org_ids() TO authenticated, service_role;

DO $$
DECLARE r record;
BEGIN
  -- Member-read policies on tenant tables (memberships keeps its own policy: it is what my_org_ids reads).
  FOR r IN
    SELECT tablename, policyname FROM pg_policies
    WHERE schemaname = 'public' AND qual = 'app.is_member(org_id)' AND tablename <> 'memberships'
  LOOP
    EXECUTE format('ALTER POLICY %I ON public.%I USING (org_id IN (SELECT app.my_org_ids()))',
                   r.policyname, r.tablename);
  END LOOP;
  -- Two-step (aal2) restrictive policies: one evaluation per query instead of per row.
  FOR r IN
    SELECT tablename, policyname, with_check FROM pg_policies
    WHERE schemaname = 'public' AND permissive = 'RESTRICTIVE' AND qual = 'app.is_aal2()'
  LOOP
    IF r.with_check IS NOT NULL THEN
      EXECUTE format('ALTER POLICY %I ON public.%I USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()))',
                     r.policyname, r.tablename);
    ELSE
      EXECUTE format('ALTER POLICY %I ON public.%I USING ((SELECT app.is_aal2()))', r.policyname, r.tablename);
    END IF;
  END LOOP;
END $$;

-- Indexes so "org_id IN (my organisations)" is a lookup on the large tables, not a scan.
CREATE INDEX IF NOT EXISTS findings_org_idx ON public.findings (org_id);
CREATE INDEX IF NOT EXISTS raw_rows_org_idx ON public.raw_rows (org_id);
CREATE INDEX IF NOT EXISTS result_records_org_idx ON public.result_records (org_id);
CREATE INDEX IF NOT EXISTS validation_issues_org_idx ON public.validation_issues (org_id);
CREATE INDEX IF NOT EXISTS processing_jobs_org_idx ON public.processing_jobs (org_id);
CREATE INDEX IF NOT EXISTS memberships_user_idx ON public.memberships (user_id, org_id);
