# ADR-0002: No-card hosting (Option B) — API on Vercel, worker on GitHub Actions

- Status: Accepted by founder (18 Nov 2026). Spike result: pending (see `docs/spike-results.md`).
- Rejected: Option A, Cloud Run — requires a payment method on a Google Cloud billing account.

## Decision

| Part | Host | Limit that matters |
|---|---|---|
| Web app | Vercel Hobby (static) | Non-commercial use |
| FastAPI (short requests only) | Vercel Python function, Fluid compute | 300 s max per request |
| Worker (ingestion, validation, analysis) | GitHub Actions workflow `worker.yml` on a **public** repo | Free; 6 h max per job |
| Database, auth, storage | Supabase Free | See `docs/providers.md` |

## Job flow

1. `POST /jobs` (FastAPI) inserts a `queued` row in `processing_jobs` (idempotent on job type + target + input fingerprint).
2. FastAPI sends a `repository_dispatch` event (`ai4e-job`) to GitHub using a fine-grained token stored only in Vercel env (`GITHUB_DISPATCH_TOKEN`, permission: this repo, Contents read/write).
3. `worker.yml` starts, runs `python -m app.jobs.worker --drain`, which atomically claims queued jobs (`FOR UPDATE SKIP LOCKED`), heartbeats, and finishes them.
4. A **scheduled sweep** (every 15 min) runs the same worker: it reconciles stale jobs and picks up anything whose dispatch was lost. Side effect: regular database requests keep the free Supabase project from pausing.

If the dispatch call fails, the job stays `queued` and the sweep picks it up — the ledger, not the dispatch, is the source of truth.

## Known costs of this choice

- Job start latency: GitHub runner start-up (expected ~20–60 s). Measured in the spike.
- Signed upload URLs from Supabase last 2 hours (not configurable). Compensation: server-generated storage path, version row created first, finalize is one-time and recomputes SHA-256 from stored bytes.
- Public repo means code is public. Data is synthetic and secrets live only in Actions/Vercel secrets.

## Fallback if the spike fails

Run short jobs (< 250 s) inside a Vercel function triggered by `POST /jobs`, keeping the same ledger and sweep. Founder approval needed before switching.
