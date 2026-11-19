-- Phase 4: analysis runs keep their summary and the list of units that were not scored (and why);
-- each dataset version points at its latest complete run. Findings become immutable: a new
-- analysis makes a new run with new findings, so reviews always refer to what the reviewer saw.

ALTER TABLE public.analytical_runs
  ADD COLUMN summary jsonb,
  ADD COLUMN not_scored jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.dataset_versions
  ADD COLUMN latest_analysis_run_id uuid,
  ADD COLUMN analysed_at timestamptz,
  ADD CONSTRAINT dataset_versions_latest_run_fk
    FOREIGN KEY (org_id, latest_analysis_run_id) REFERENCES public.analytical_runs (org_id, id);

CREATE INDEX IF NOT EXISTS findings_run_idx
  ON public.findings (analytical_run_id, category, indicator, severity);
CREATE INDEX IF NOT EXISTS analytical_runs_version_idx
  ON public.analytical_runs (dataset_version_id, started_at DESC);

CREATE TRIGGER append_only_findings BEFORE UPDATE OR DELETE ON public.findings
  FOR EACH ROW EXECUTE FUNCTION app.forbid_change();
CREATE TRIGGER append_only_truncate_findings BEFORE TRUNCATE ON public.findings
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_change();
