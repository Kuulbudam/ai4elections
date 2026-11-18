# Phase 2 — Upload and ingestion: completion report

**Date:** 18 Nov 2026 **Result:** PASS — the upload → ingest flow is proven on the real Supabase project. The public Vercel deployment is waiting on the founder's account setup (does not block the acceptance criteria).
**PR:** #13 (`phase-2-ingestion` → `main`)

## Objective

Private CSV upload, immutable versions, SHA-256, raw-row preservation, column mapping, safe parsing, ingestion jobs. Synthetic generator.

## Implemented

- **Synthetic generator** `tools/synthetic/generate.py` (seeded and deterministic): 12,023 fictional polling units in 4 invented states, 15 planted scenarios plus normal rows, a ground-truth file and an admin-units reference list. Committed demo files are in `data/synthetic/`; CI fails if they drift from the generator.
- **Endpoints** (contract-first, generated models):
  - `POST /uploads` — admin or analyst. The server builds the storage path (`{org}/{project}/{dataset}/{version}.csv`); the filename is kept only as cleaned metadata. Every upload creates a new dataset version (v1, v2…) and returns a 2-hour signed upload URL. Supports `Idempotency-Key`.
  - `POST /dataset-versions/{id}/finalize` — reads the stored bytes, computes SHA-256 and the size on the server, rejects binary or non-UTF-8 files, flags identical re-uploads (`duplicate_of_version_id`), and queues ingestion. One-time: repeating it returns the same result.
  - `PUT /dataset-versions/{id}/mapping` — checks the mapping against the file's real header (V001 missing or unknown columns, latitude/longitude pairing; V002 the same column used twice, or a party code used twice). Can save the mapping as a template.
  - `GET /jobs/{id}` — any member. Non-members get 404, so the job's existence isn't revealed.
- **Rate limits:** per user (60/min) and per IP (120/min), stored in Postgres so every serverless instance shares them. Over the limit returns 429 `RATE_LIMITED`.
- **Ingestion job** (`app/jobs/ingest.py`):
  - Re-checks that the job belongs to the right organisation and project, and re-verifies the SHA-256 before parsing (`INTEGRITY_MISMATCH`).
  - Safe parser: UTF-8 / UTF-8-BOM only, no binary, delimiter detection, cell/column/row limits, header checks, formulas kept as plain text. Blank lines are skipped but counted; rows with the wrong number of cells are kept and counted.
  - Raw rows are written with `COPY`, in the **same transaction** as the version status and the job's success, so a retry can never duplicate rows.
  - Stable failure codes (for example `HEADER_NOT_FOUND`, `MALFORMED_CSV`). A version whose ingestion finally fails is marked `ingest_failed`.
- **Web pages:** projects (list; admins create), project overview with every dataset version (status, rows, size, short SHA-256, duplicate and archived badges), upload (provenance fields, upload progress, live status, plain-language errors), column mapping (auto-suggested fields and party columns, templates, issues list, read-only for researchers and viewers).
- **Deployment config:** `services/api/vercel.json` (120 s), `.python-version`, `pyproject.toml` dependencies (kept in sync with `requirements.txt` by a test), web SPA rewrites and security headers.

## Database migrations

`20261119000500_api_support.sql` (rate limits, idempotency keys; private `app` schema). Applied to the real project (Database run 44372619444).

## Tests and results

| Suite | Result |
|---|---|
| `pytest -q` (local Postgres 16 + Supabase shim) | **280 passed**, 0 failed (+46 new) |
| `npm test` (web) | **17 passed**, 0 failed (+5 new) |
| CI on PR #13 | all jobs success |
| Live security check (real Supabase) | **15/15** |
| **Live ingestion check** (real Supabase; run 44373548885) | **9/9** |

Live ingestion check, in full:
- `POST /uploads` with a **real Supabase token** (JWT verified via the project's JWKS) → 201.
- Signed-URL upload of the 1.06 MB demo file to the private bucket → 200 in 1.3 s; a second write to the same file is refused → 400.
- Finalize: the server-side SHA-256 matches → 200 in 5.1 s.
- **12,023 rows ingested and preserved in 13.1 s**.
- `GET /jobs` shows succeeded; another organisation gets 404; a forged token gets 401.
- The mapping is saved with no issues.

New local tests cover:
- the full demo file end to end;
- re-upload creates v2 and flags the duplicate; the original can't be overwritten;
- finalize is one-time; Idempotency-Key replays the same request and returns 409 for a different body;
- an ingest retry doesn't duplicate rows;
- role checks (researcher and viewer → 403; other org → 404; no token → 401);
- request validation (size, extension, content type, empty provenance); path-traversal-like filenames;
- finalize before the file is uploaded → 409; binary file → 400;
- 5 malformed-CSV codes; a tampered stored file is detected;
- mapping: V001/V002 cases, templates, researcher denied;
- rate limit → 429; upload actions audited with the right actor;
- 12 parser unit tests; the synthetic data's ground truth checked against its own rules.

## Security review

- Storage paths are never built from user input; the stored original can't be overwritten; the SHA-256 is computed by the server, never taken from the browser.
- The service-role key is used only in FastAPI and the worker; every query is filtered by the organisation checked via `authorize()` (non-member → 404, wrong role → 403).
- Errors return contract codes only; parser messages are plain language with no internals.
- Rate limits are shared across instances. Signed upload URLs last 2 h (a Supabase limit); compensated by server-side paths, one-time finalize and the hash re-check before ingestion.

## Deployment status

- Real Supabase: migrated; full flow verified.
- Vercel (web + API): **waiting on founder setup** (steps sent 18 Nov). The API will deploy from `main` after this PR merges.
- Worker: GitHub Actions; the dispatch token is waiting on the founder. The scheduled sweep had not fired yet; moved to off-peak minutes (`7,22,37,52`). To verify after merge.

## Known limitations

- Each live-check run leaves CI organisations/users in the project (append-only design; invisible to others).
- The first live run's script stopped after 5 jobs, which failed 5 leftover fake CI jobs from Phase 1 with `UNKNOWN_JOB_TYPE` (CI orgs only). Fixed: CI jobs are now seeded as finished.
- Dev-only `picomatch` advisory (no fix yet).

## Cost and billing

$0. No payment method anywhere.

## Days to demo / critical path

8 Dec 2026 — 20 days left. Phase 2 finished 18 Nov (plan said 22–24 Nov). Ahead of schedule.

## Acceptance criteria and evidence

| Criterion | Evidence | Status |
|---|---|---|
| Upload and ingestion end-to-end with synthetic data | Live ingestion check 9/9 on real Supabase; local full-file test | PASS |
| Invalid inputs handled safely | 5 malformed-CSV codes, binary/encoding/size/extension rejections, tamper detection, all tested | PASS |
| Provenance preserved | Server SHA-256, immutable version fields (DB trigger), source type/description, uploader, timestamps, audit events with actor | PASS |

## Decisions needed from founder

1. Finish the Vercel + token setup and send both URLs.
2. Approve Phase 3 (validation rules V001–V012 and the validation page).

## Next phase

Phase 3 — Validation.
