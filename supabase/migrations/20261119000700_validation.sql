-- Phase 3: link each dataset version to its current validation run and summary.
ALTER TABLE public.dataset_versions
  ADD COLUMN validation_job_id uuid,
  ADD COLUMN validated_at timestamptz,
  ADD COLUMN validation_summary jsonb,
  ADD CONSTRAINT dataset_versions_validation_job_fk
    FOREIGN KEY (org_id, validation_job_id) REFERENCES public.processing_jobs (org_id, id);

-- Fast per-row lookups from the validation page.
CREATE INDEX IF NOT EXISTS validation_issues_row_idx ON public.validation_issues (dataset_version_id, source_row_number);
CREATE INDEX IF NOT EXISTS result_records_status_idx ON public.result_records (dataset_version_id, row_status);
