-- Phase 17: signed submission packs.
-- A pack is a zip of what an organisation's observers reported for one election (results by polling unit, field
-- reports, incidents, escalations), with a manifest listing every file's SHA-256 and an Ed25519 signature over the
-- manifest. Anyone with the public key can check that nothing was changed after it was made. Packs are never pushed
-- into any INEC system; the organisation hands them over itself. Each pack made is recorded here, append-only.

CREATE TABLE public.submission_packs (
  id               uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  org_id           uuid NOT NULL,
  project_id       uuid NOT NULL,
  created_by       uuid NOT NULL REFERENCES auth.users (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  manifest_sha256  text NOT NULL UNIQUE CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
  zip_sha256       text NOT NULL CHECK (zip_sha256 ~ '^[a-f0-9]{64}$'),
  zip_path         text NOT NULL UNIQUE,
  zip_bytes        integer NOT NULL CHECK (zip_bytes > 0),
  key_id           text NOT NULL CHECK (key_id ~ '^[a-f0-9]{16}$'),
  signature        text NOT NULL,
  counts           jsonb NOT NULL,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, project_id) REFERENCES public.projects (org_id, id),
  CHECK (zip_path = org_id::text || '/' || project_id::text || '/' || id::text || '.zip')
);
CREATE INDEX submission_packs_project_idx ON public.submission_packs (project_id, created_at DESC);
CREATE INDEX submission_packs_org_idx ON public.submission_packs (org_id);

CREATE TRIGGER append_only_submission_packs BEFORE UPDATE OR DELETE ON public.submission_packs
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_submission_packs BEFORE TRUNCATE ON public.submission_packs
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER audit_submission_packs AFTER INSERT ON public.submission_packs
  FOR EACH ROW EXECUTE FUNCTION app.audit_row();

ALTER TABLE public.submission_packs ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.submission_packs TO authenticated;
GRANT ALL ON public.submission_packs TO service_role;
-- Field agents see only their own reports, so they do not see packs (which hold everyone's).
CREATE POLICY submission_packs_read ON public.submission_packs FOR SELECT TO authenticated
  USING (app.has_role(org_id, ARRAY['admin', 'analyst', 'researcher', 'viewer', 'coordinator']));
CREATE POLICY submission_packs_require_mfa ON public.submission_packs AS RESTRICTIVE FOR ALL TO authenticated
  USING ((SELECT app.is_aal2())) WITH CHECK ((SELECT app.is_aal2()));

-- Private bucket for the zips. Only the API (service key) writes or reads them.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('packs', 'packs', false, 20971520, ARRAY['application/zip'])
ON CONFLICT (id) DO UPDATE
  SET public = false,
      file_size_limit = EXCLUDED.file_size_limit,
      allowed_mime_types = EXCLUDED.allowed_mime_types;
