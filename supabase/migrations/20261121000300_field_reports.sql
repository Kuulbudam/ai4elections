-- Phase 16: field reports from polling units (observer method, not INEC's BVAS).
-- An agent at an assigned polling unit sends short structured reports: polls opened, the accreditation count the
-- BVAS shows, incidents, the result sheet, and closing. Reports are written on the phone first and sent when there is
-- signal; client_id makes a resend harmless. Reports are evidence: never edited or deleted, and audited.

CREATE TABLE public.field_reports (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id       uuid NOT NULL,
  project_id   uuid NOT NULL,
  client_id    text NOT NULL CHECK (client_id ~ '^[A-Za-z0-9_-]{8,64}$'),
  agent_id     uuid NOT NULL REFERENCES auth.users (id),
  kind         text NOT NULL CHECK (kind IN ('arrival', 'accreditation', 'incident', 'result', 'closing')),
  state        text NOT NULL,
  lga          text NOT NULL,
  ward         text NOT NULL,
  pu_code      text NOT NULL,
  payload      jsonb NOT NULL,
  severity     text CHECK (severity IS NULL OR severity IN ('low', 'medium', 'critical')),
  sheet_id     uuid,
  captured_at  timestamptz NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now(),
  lat          double precision CHECK (lat IS NULL OR lat BETWEEN -90 AND 90),
  lng          double precision CHECK (lng IS NULL OR lng BETWEEN -180 AND 180),
  accuracy_m   double precision CHECK (accuracy_m IS NULL OR accuracy_m >= 0),
  UNIQUE (org_id, id),
  UNIQUE (org_id, client_id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, sheet_id) REFERENCES public.result_sheets (org_id, id),
  CHECK ((kind = 'incident') = (severity IS NOT NULL)),
  CHECK ((kind = 'result') = (sheet_id IS NOT NULL)),
  -- the phone's clock may be off a little, not by days
  CHECK (captured_at <= received_at + interval '10 minutes' AND captured_at >= received_at - interval '7 days')
);
CREATE INDEX field_reports_project_idx ON public.field_reports (project_id, received_at DESC);
CREATE INDEX field_reports_pu_idx ON public.field_reports (project_id, pu_code);
CREATE INDEX field_reports_agent_idx ON public.field_reports (agent_id);
CREATE INDEX field_reports_org_idx ON public.field_reports (org_id);

CREATE TRIGGER append_only_field_reports BEFORE UPDATE OR DELETE ON public.field_reports
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_field_reports BEFORE TRUNCATE ON public.field_reports
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER audit_field_reports AFTER INSERT ON public.field_reports
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

-- Field agents see only their own reports; every other member sees the organisation's.
CREATE OR REPLACE FUNCTION app.sees_field_report(p_org uuid, p_agent uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT EXISTS (SELECT 1 FROM public.memberships m
                 WHERE m.org_id = p_org AND m.user_id = auth.uid()
                   AND (m.role <> 'field_agent' OR p_agent = auth.uid()))
$$;
REVOKE ALL ON FUNCTION app.sees_field_report(uuid, uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION app.sees_field_report(uuid, uuid) TO authenticated, service_role;

ALTER TABLE public.field_reports ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.field_reports TO authenticated;
GRANT ALL ON public.field_reports TO service_role;
CREATE POLICY field_reports_read ON public.field_reports FOR SELECT TO authenticated
  USING (org_id IN (SELECT app.my_org_ids()) AND app.sees_field_report(org_id, agent_id));
CREATE POLICY field_reports_require_mfa ON public.field_reports AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));

-- Field agents never see other agents' sheet photos either: result_sheets they did not upload are hidden from them.
DROP POLICY IF EXISTS result_sheets_member_read ON public.result_sheets;
CREATE POLICY result_sheets_member_read ON public.result_sheets FOR SELECT TO authenticated
  USING (org_id IN (SELECT app.my_org_ids()) AND app.sees_field_report(org_id, created_by));

-- On election day there may be no results file yet: a sheet from the field is checked on its own sums only.
ALTER TABLE public.result_sheets ALTER COLUMN dataset_version_id DROP NOT NULL;

CREATE OR REPLACE FUNCTION app.result_sheet_confirm_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP <> 'UPDATE' THEN
    RAISE EXCEPTION 'append_only: % on result_sheets is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status <> 'read' OR NEW.status <> 'confirmed'
     OR NEW.id <> OLD.id OR NEW.org_id <> OLD.org_id OR NEW.project_id <> OLD.project_id
     OR NEW.dataset_version_id IS DISTINCT FROM OLD.dataset_version_id OR NEW.image_path <> OLD.image_path
     OR NEW.image_sha256 <> OLD.image_sha256 OR NEW.original_sha256 <> OLD.original_sha256
     OR NEW.image_bytes <> OLD.image_bytes OR NEW.model IS DISTINCT FROM OLD.model
     OR NEW.extracted <> OLD.extracted OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'append_only: a result sheet can only be confirmed once' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;

-- Escalations can now come from a field report or from a result sheet that has no results file to be compared with.
ALTER TABLE public.escalations DROP CONSTRAINT IF EXISTS escalations_source_kind_check;
ALTER TABLE public.escalations ADD CONSTRAINT escalations_source_kind_check
  CHECK (source_kind IN ('finding', 'field_report', 'result_sheet'));
