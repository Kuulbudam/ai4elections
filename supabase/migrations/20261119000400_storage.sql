-- Private storage bucket for uploaded CSV files (Phase 1).
-- Paths are generated server-side: {org_id}/{project_id}/{dataset_id}/{version_id}.csv
-- Uploads happen only through short-lived signed upload URLs created by the server, so the
-- browser has no INSERT/UPDATE/DELETE policy at all. Members may read their own org's files.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('datasets', 'datasets', false, 26214400, ARRAY['text/csv', 'application/vnd.ms-excel', 'text/plain'])
ON CONFLICT (id) DO UPDATE
  SET public = false,
      file_size_limit = EXCLUDED.file_size_limit,
      allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS datasets_member_read ON storage.objects;
CREATE POLICY datasets_member_read ON storage.objects FOR SELECT TO authenticated
  USING (bucket_id = 'datasets' AND app.is_member(app.try_uuid((storage.foldername(name))[1])));
