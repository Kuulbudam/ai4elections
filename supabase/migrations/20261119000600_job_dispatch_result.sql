-- Record the outcome of waking the worker for each job (Phase 2 fix).
-- Values are short codes only (never tokens or response bodies): ok, not_configured,
-- invalid_config, http_<status>, network_error.
ALTER TABLE public.processing_jobs
  ADD COLUMN dispatch_result text CHECK (dispatch_result IS NULL OR dispatch_result ~ '^[a-z_0-9]{1,40}$'),
  ADD COLUMN dispatched_at timestamptz;
