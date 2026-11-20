-- Phase 10: collation check (founder plan, 20 Nov).
-- A file of declared ward / LGA / state totals is compared with the sum of the polling units of each area in a
-- dataset version. The declared rows, the comparison and the findings it raised are kept exactly as made.
CREATE TABLE public.collation_checks (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  project_id          uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  filename            text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
  sha256              text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  engine_version      text NOT NULL,
  summary             jsonb NOT NULL,
  skipped             jsonb NOT NULL DEFAULT '[]'::jsonb,
  areas               jsonb NOT NULL,
  finding_ids         uuid[] NOT NULL DEFAULT '{}',
  created_by          uuid NOT NULL REFERENCES auth.users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id)
);
CREATE INDEX collation_checks_project_idx ON public.collation_checks (project_id, created_at DESC);

CREATE TRIGGER append_only_collation_checks BEFORE UPDATE OR DELETE ON public.collation_checks
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_collation_checks BEFORE TRUNCATE ON public.collation_checks
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER audit_collation_checks AFTER INSERT ON public.collation_checks
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

ALTER TABLE public.collation_checks ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.collation_checks TO authenticated;
GRANT ALL ON public.collation_checks TO service_role;
CREATE POLICY collation_checks_member_read ON public.collation_checks FOR SELECT TO authenticated
  USING (app.is_member(org_id));
CREATE POLICY collation_checks_require_mfa ON public.collation_checks AS RESTRICTIVE FOR ALL TO authenticated
  USING (app.is_aal2()) WITH CHECK (app.is_aal2());
