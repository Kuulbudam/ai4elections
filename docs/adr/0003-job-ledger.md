# ADR-0003: PostgreSQL as the durable job ledger

- Status: Accepted (18 Nov 2026)

## Decision

`processing_jobs` in PostgreSQL is the only queue. States: `queued → running → succeeded | failed` (`cancelled` optional).

- **Claim:** one statement selects the oldest `queued` job `FOR UPDATE SKIP LOCKED`, sets `running`, `started_at`, `heartbeat_at`, `worker_id`, increments `attempt_count`. Two workers can never claim the same job.
- **Heartbeat:** the worker updates `heartbeat_at` and `progress` at least every 10 s, only while it still owns the job (`worker_id` match).
- **Finish:** `succeeded` or `failed` is written only by the owning worker. Outputs are written in one transaction keyed by the run ID; a retry deletes its own earlier partial output first, so nothing is duplicated.
- **Reconcile:** a `running` job with `heartbeat_at` older than 60 s goes back to `queued` if `attempt_count < max_attempts`, else `failed` with `error_code = STALE_HEARTBEAT_EXHAUSTED`.
- **Duplicate requests:** unique index on `(job_type, target_id, input_fingerprint)` for non-terminal jobs; repeated `POST /jobs` returns the existing job.
- **Errors:** only safe codes are stored for clients (`error_code`, `retryable`); details go to server logs.

## Consequences

The spike (`services/api/spike/schema.sql`) proves claim/heartbeat/reconcile before the full Phase 1 schema adds `org_id` and RLS to the same table design.
