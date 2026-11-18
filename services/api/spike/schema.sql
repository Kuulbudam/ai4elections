-- Worker spike schema (Phase 0). Lives in its own schema so it never collides with the
-- Phase 1 application tables in `public`. Safe to drop after Phase 1: DROP SCHEMA spike CASCADE;
CREATE SCHEMA IF NOT EXISTS spike;

CREATE TABLE IF NOT EXISTS spike.processing_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type          text        NOT NULL,
    target_id         uuid        NOT NULL,
    input_fingerprint text        NOT NULL,
    payload           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status            text        NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    progress          real        NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    attempt_count     integer     NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts      integer     NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    heartbeat_at      timestamptz,
    worker_id         text,
    error_code        text,
    retryable         boolean
);

-- Duplicate-request protection: only one live job per (type, target, input).
CREATE UNIQUE INDEX IF NOT EXISTS processing_jobs_live_unique
    ON spike.processing_jobs (job_type, target_id, input_fingerprint)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS processing_jobs_queued_idx
    ON spike.processing_jobs (created_at) WHERE status = 'queued';

-- Output of the spike job. Keyed by job_id so a retry overwrites, never duplicates.
CREATE TABLE IF NOT EXISTS spike.spike_outputs (
    job_id     uuid PRIMARY KEY REFERENCES spike.processing_jobs (id),
    attempt    integer     NOT NULL,
    result     jsonb       NOT NULL,
    written_at timestamptz NOT NULL DEFAULT now()
);

-- Every time a worker starts executing a job. Used to prove "duplicate dispatch → one execution".
CREATE TABLE IF NOT EXISTS spike.spike_executions (
    id         bigserial PRIMARY KEY,
    job_id     uuid        NOT NULL,
    worker_id  text        NOT NULL,
    attempt    integer     NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now()
);
