# STATUS — handoff file

Read this first in every session. Update it last.

**Last updated:** 18 Nov 2026
**Current phase:** 0 — Foundation: **PASSED**. Stopped at gate; waiting for founder "approved, continue" to start Phase 1.
**Demo:** Rise Networks #AI4Elections Hackathon, **8 December 2026** (20 days from 18 Nov).

## Decisions made (founder, 18 Nov 2026)

- Hosting: **Option B**, no card. API on Vercel Hobby, worker on GitHub Actions (ADR-0002).
- Repo: `Kuulbudam/ai4elections`, **public**.
- Auth email: confirmation **off** on the demo Supabase project.
- Supabase Free project created; `DATABASE_URL` (Session pooler) stored as Actions secret.

## Done (Phase 0)

- Repo, docs, ADRs 0001–0006, provider checks, founder setup steps.
- OpenAPI 3.1 contract + JSON schemas; generated Pydantic + TS types with CI sync checks.
- FastAPI skeleton, job ledger + worker, web shell, CI, worker workflow, Dependabot.
- CI green on GitHub (run 44341531979). Hosted 90 s spike PASS (run 44363949162). Report: `docs/phase-reports/phase-0.md`.

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

Phase 1 on a feature branch (`phase-1-db-security`), merged by PR after CI. Needs from founder in Phase 1: Supabase project URL, anon key, and service-role key as Actions secrets (exact names will be given), and a Vercel login for Phase 2.

## Open questions / to verify

- Scheduled 15-min worker sweep has not fired yet; verify it starts (it also keeps Supabase awake).
- Dispatch latency (API → `repository_dispatch` → runner claims job): measure once the API is on Vercel (Phase 2).

## Known issues

- Dev-only `picomatch` advisory GHSA-fc5q-39xc-vjpf (no fix version); production deps 0 vulnerabilities.
- Actions `checkout@v4`/`setup-python@v5` warn about Node 20 deprecation; take the Dependabot bump PR.
