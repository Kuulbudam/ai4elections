-- Phase 9: result-sheet reader (founder plan, 20 Nov).
-- A photo of a polling-unit result sheet (Form EC8A) is read by a vision model, a person confirms or corrects
-- every figure, and the platform checks the sheet's arithmetic and compares it with the uploaded results.
-- Mismatches become findings of the new category 'cross_check' (also used by the Phase 10 collation check).

-- 1. New finding category for cross-checks between sources (sheet vs data, units vs declared totals).
ALTER TABLE public.findings DROP CONSTRAINT IF EXISTS findings_category_check;
ALTER TABLE public.findings ADD CONSTRAINT findings_category_check
  CHECK (category IN ('observation', 'data_quality_issue', 'statistical_anomaly', 'cross_check'));

-- 2. Result sheets. One row per uploaded photo. The AI reading is kept as it was produced; the person's
-- confirmation is written once (read -> confirmed) and nothing can change afterwards.
CREATE TABLE public.result_sheets (
  id                  uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id              uuid NOT NULL,
  project_id          uuid NOT NULL,
  dataset_version_id  uuid NOT NULL,
  image_path          text NOT NULL UNIQUE,
  image_sha256        text NOT NULL CHECK (image_sha256 ~ '^[a-f0-9]{64}$'),
  original_sha256     text NOT NULL CHECK (original_sha256 ~ '^[a-f0-9]{64}$'),
  image_bytes         integer NOT NULL CHECK (image_bytes > 0),
  status              text NOT NULL DEFAULT 'read' CHECK (status IN ('read', 'confirmed')),
  model               text,
  extracted           jsonb NOT NULL,
  confirmed           jsonb,
  checks              jsonb,
  comparison          jsonb,
  pu_code             text,
  raw_row_id          uuid,
  finding_ids         uuid[] NOT NULL DEFAULT '{}',
  created_by          uuid NOT NULL REFERENCES auth.users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  confirmed_by        uuid REFERENCES auth.users (id),
  confirmed_at        timestamptz,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  FOREIGN KEY (org_id, dataset_version_id) REFERENCES public.dataset_versions (org_id, id),
  FOREIGN KEY (org_id, raw_row_id) REFERENCES public.raw_rows (org_id, id),
  CHECK (image_path = org_id::text || '/' || project_id::text || '/' || id::text || '.jpg'),
  CHECK (
    (status = 'read' AND confirmed IS NULL AND confirmed_by IS NULL AND confirmed_at IS NULL AND checks IS NULL)
    OR (status = 'confirmed' AND confirmed IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL
        AND checks IS NOT NULL)
  )
);
CREATE INDEX result_sheets_project_idx ON public.result_sheets (project_id, created_at DESC);

-- The only change ever allowed: read -> confirmed, filling the confirmation columns. Everything else is fixed.
CREATE OR REPLACE FUNCTION app.result_sheet_confirm_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP <> 'UPDATE' THEN
    RAISE EXCEPTION 'append_only: % on result_sheets is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status <> 'read' OR NEW.status <> 'confirmed'
     OR NEW.id <> OLD.id OR NEW.org_id <> OLD.org_id OR NEW.project_id <> OLD.project_id
     OR NEW.dataset_version_id <> OLD.dataset_version_id OR NEW.image_path <> OLD.image_path
     OR NEW.image_sha256 <> OLD.image_sha256 OR NEW.original_sha256 <> OLD.original_sha256
     OR NEW.image_bytes <> OLD.image_bytes OR NEW.model IS DISTINCT FROM OLD.model
     OR NEW.extracted <> OLD.extracted OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'append_only: a result sheet can only be confirmed once' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER result_sheets_confirm_only BEFORE UPDATE OR DELETE ON public.result_sheets
  FOR EACH ROW EXECUTE FUNCTION app.result_sheet_confirm_only();
CREATE TRIGGER append_only_truncate_result_sheets BEFORE TRUNCATE ON public.result_sheets
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();

-- Uploads and confirmations go into the hash-chained audit log like every other change.
CREATE TRIGGER audit_result_sheets AFTER INSERT OR UPDATE ON public.result_sheets
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

ALTER TABLE public.result_sheets ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.result_sheets TO authenticated;
GRANT ALL ON public.result_sheets TO service_role;
-- Every member of the organisation may see its sheets; all writes go through the API (service role).
CREATE POLICY result_sheets_member_read ON public.result_sheets FOR SELECT TO authenticated
  USING (app.is_member(org_id));
CREATE POLICY result_sheets_require_mfa ON public.result_sheets AS RESTRICTIVE FOR ALL TO authenticated
  USING (app.is_aal2()) WITH CHECK (app.is_aal2());

-- 3. Private bucket for sheet photos. The API re-encodes every photo as JPEG (which drops location and
-- camera metadata) and writes it with the service key; members read through short-lived signed URLs.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('sheets', 'sheets', false, 5242880, ARRAY['image/jpeg'])
ON CONFLICT (id) DO UPDATE
  SET public = false,
      file_size_limit = EXCLUDED.file_size_limit,
      allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS sheets_member_read ON storage.objects;
CREATE POLICY sheets_member_read ON storage.objects FOR SELECT TO authenticated
  USING (bucket_id = 'sheets' AND app.is_member(app.try_uuid((storage.foldername(name))[1])));
