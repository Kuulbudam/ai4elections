# STATUS — handoff file

Read this first in every session. Update it last.

**Last updated:** 18 Nov 2026
**Current phase:** 2 — Upload and ingestion: **PASSED** (report `docs/phase-reports/phase-2.md`). Stopped at gate; waiting for founder "approved, continue" to start Phase 3.
**Demo:** Rise Networks #AI4Elections Hackathon, **8 December 2026** (20 days from 18 Nov).

## Decisions made (founder, 18 Nov 2026)

- Hosting: **Option B**, no card. API on Vercel Hobby, worker on GitHub Actions (ADR-0002).
- Repo: `Kuulbudam/ai4elections`, **public**.
- Auth email: confirmation **off** on the demo Supabase project.
- Supabase Free project created; Actions secrets: `DATABASE_URL` (Session pooler), `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`.

## Done (Phase 0)

- Repo, docs, ADRs 0001–0006, provider checks, founder setup steps.
- OpenAPI 3.1 contract + JSON schemas; generated Pydantic + TS types with CI sync checks.
- FastAPI skeleton, job ledger + worker, web shell, CI, worker workflow, Dependabot.
- CI green on GitHub (run 44341531979). Hosted 90 s spike PASS (run 44363949162). Report: `docs/phase-reports/phase-0.md`.

## Done (Phase 1)

- 4 migrations applied to the real project; 234 local tests; live security check 15/15 (run 44368401747).
- FastAPI JWT verification + DB role checks; web sign-in, organisations, members, profile.

## Done (Phase 2)

- Synthetic generator + committed demo data (`data/synthetic/`).
- Upload/finalize/mapping/job endpoints, ingestion worker, rate limits, idempotency; web upload + mapping pages.
- Live ingestion check 9/9 on real Supabase (12,023 rows in 13.1 s).

## Working schedule to 8 Dec (target dates, adjust as we go)

| Dates | Work |
|---|---|
| 19–21 Nov | Phase 1 — migrations, auth, orgs/roles, RLS, audit chain, tenant-isolation tests |
| 22–24 Nov | Phase 2 — upload, SHA-256, raw rows, mapping, ingestion jobs, synthetic generator; deploy API to Vercel |
| 25–27 Nov | Phase 3 — validation rules V001–V012 + validation page |
| 27–29 Nov | Phase 4 — MAD analysis engine + ground-truth scoring |
| 30 Nov–2 Dec | Phase 5 — findings, review, claims; Phase 6 tables (light) |
| 2–4 Dec | Phase 7 — reports + exports; remaining Phase 8 pages |
| 5–6 Dec | Phase 9 essentials (RLS re-test, perf, backup/restore) |
| 7 Dec | Phase 10 — clean demo account, seed data, script, backup recording, 3 clean runs |
| 8 Dec | Demo |

Frontend pages are built inside each phase (only what that phase needs), not saved for the end.

## Next step

Phase 3 on branch `phase-3-validation`: rules V001–V012 as versioned definitions, result_records build from mapping, row statuses and scoped quarantine, validation issues, POST /jobs (validate), validation page, ground-truth scoring. Founder: Vercel setup + dispatch token (steps sent 18 Nov; record URLs here).

## Open questions / to verify

- Scheduled worker sweep never fired with `*/15`; changed to `7,22,37,52 * * * *` in Phase 2. Verify it fires after merge (it also keeps Supabase awake).
- Dispatch latency (API → `repository_dispatch` → runner claims job): measure once the API is on Vercel (Phase 2).

## Known issues

- Dev-only `picomatch` advisory GHSA-fc5q-39xc-vjpf (no fix version); production deps 0 vulnerabilities.
- Actions `checkout@v4`/`setup-python@v5` warn about Node 20 deprecation; take the Dependabot bump PR.
