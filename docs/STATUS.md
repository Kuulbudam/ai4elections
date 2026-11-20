# STATUS — handoff file

Read this first in every session. Update it last.

**Last updated:** 19 Nov 2026
**Current phase:** 9 — Hardening (next). Phase 8 PASSED (report `docs/phase-reports/phase-8.md`).
**Demo:** Rise Networks #AI4Elections Hackathon, **8 December 2026** (20 days from 18 Nov).

## Working rule (founder, 19 Nov 2026) — read this first

- **Build the phases one after another without waiting for "approved, continue".** Move to the next phase ONLY when
  the current phase has been thoroughly tested and works as intended: all acceptance criteria met, CI green, live
  checks on the real Supabase project and the production deployed check passing. Do not assume; follow the master
  prompt's rules. If anything fails or is uncertain, stop and report instead of moving on.
- **Data assistant (LLM) is required**: staff ask questions about the data in plain language and get answers.
  Build it after Phase 6 (it reuses the area and findings lookups). Decisions: founder has **no LLM provider or key
  yet** (pick a free tier without a card, check data-use terms, ask founder to create the key); **admins and analysts
  only**. Design: fixed, tested lookup tools through the API with the user's own permissions (no free-form SQL);
  every number cited and linked; never says fraud; questions and answers audited; platform works if it is off.

## Decisions made (founder, 18 Nov 2026)

- Hosting: **Option B**, no card. API on Vercel Hobby, worker on GitHub Actions (ADR-0002).
- Repo: `Kuulbudam/ai4elections`, **public**.
- Auth email: confirmation **off** on the demo Supabase project.
- Deployed (18 Nov): web https://ai4elections-4d9a.vercel.app , API https://ai4elections.vercel.app (Vercel Hobby).
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

## Done (Phase 3)

- Rules V001–V012 (rule set 1.0.0), validation engine (exact ground-truth match), validate job, POST /jobs, validation page, reference-list loader.
- Live: 12,023 rows validated in 6.4 s on real Supabase.

## Fixed (20 Nov): Ask the data blank screen
- Newer Chrome returns a Promise from `scrollIntoView`; the assistant's effect returned it and React crashed. Effect now has a block body; regression test added; ErrorBoundary around shell and pages. e2e now asks the assistant a question (live run 44719282378 passed).
- Vercel Hobby daily deployment limit was hit by branch preview builds. Both `vercel.json` files now have an `ignoreCommand`: build only on `main`, only when the app folder or `packages/contracts` changed. After a blocked deploy, use Redeploy in the Vercel dashboard (an empty commit is skipped).

## Done (Phase 8)

- Dashboard, phone-friendly tables, focus ring. Playwright e2e of the deployed site from a clean account (e2e.yml, manual): full workflow + axe (no serious/critical) — PASS run 44521123100.

## Done (Phase 7)

- Immutable report snapshots, reproducible content hash (ADR-0011), CSV/Markdown export (formula-safe), print-to-PDF; no AI. Live 25/25; production 25/25.

## Done (data assistant)

- Ask the data (admins/analysts): fixed project-bound lookups, wording and figure checks, append-only log; Groq `openai/gpt-oss-120b` via `LLM_API_KEY` (set in Vercel and Actions, 19 Nov). Live 9/9 twice; production 24/24. Free tier ≈ 8k tokens/min.

## Done (Phase 6)

- GET /areas (state/LGA/ward totals, party totals, finding counts, reference flag); Areas page with charts and comparison; ADR-0009. Totals equal an independent CSV calculation at all levels. Live 18/18, 22/22, Plateau 12/12; production 23/23.

## Done (Phase 5)

- Findings API (list with filters + cursor, detail with original cells), reviews, claims, verification; finding_overview view; verification needs evidence (DB trigger). Web: findings dashboard, finding detail, claims register. ADR-0008.
- Review history unalterable even by the table owner; permission table tested in API and DB. Live 18/18, 21/21, 11/11; production 22/22.

## Done (Phase 4)

- MAD peer analysis (method 1.0.0), data quality findings, analyse job, analysis page, findings append-only. 30/30 planted anomalies (all very high); 102 false flags on normal rows (0.43%, none very high); identical hash on rerun.
- Live: real project 18/18, 15/15, Plateau 11/11; production 20/20 (upload → validate → analyse via API dispatch).

## Between Phase 3 and Phase 4 (founder requests, 18–19 Nov)

- **Two-step verification (TOTP) required for everyone** (PR #20): enforced in Postgres (restrictive RLS + storage + RPCs need `aal2`) and in the API. Live check 18/18.
- **Redesign** to a plain institutional research style; **Google sign-in first**, email/password as fallback (PR #21). Google provider enabled in Supabase.
- **Real data** (PR #22, ADR-0007): CCIJ/VEZA 2023 presidential polling-unit results for **Plateau**, downloaded at run time by `tools/real_data/prepare_ccij.py` (nothing committed; output in git-ignored `data/real/`). Ruleset/schema **1.1.0**: rejected votes and total cast optional; `party_columns_complete=false` for partial party lists.
  Live (real project): 4,989 PUs, 207 wards; validated in 7.9 s. V003 3,308 issues on 1,016 PUs (blank/unreadable sheets), V006 23, V007 324 (valid votes above BVAS accreditation), V008 91, V009 22. Flags mean "check against the sheet", not misconduct.
- Licence: source repo has no licence file; founder is asking CCIJ. Fallback is the synthetic data, no code change.

## Worker dispatch from production (18 Nov) — RESOLVED

- Cause: GitHub returned 403 "Resource not accessible by personal access token" (`dispatch_result=http_403_token_permission`).
- Fix: founder set the token's Contents permission to Read and write (22:25 WAT).
- Verified by Deployed check (14/14): API recorded `dispatch_result=ok`; a `repository_dispatch` Worker run started on its own; ingestion of 12,023 rows succeeded. Start latency ≈ 25 s, finalize → done 32 s.

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

Phase 9 on branch `phase-9-hardening`: RLS/security re-test, perf (12k validate < 30 s, analyse < 60 s incl. cold start), dependency + secret scanning, retry/recovery + job reconciliation tests, report integrity, scheduled pg_dump backup artifact + restore test, runbook incl. Supabase pause.

## Open questions / to verify

- File evidence stores only the SHA-256 fingerprint (ADR-0008); storing files deferred.

- CCIJ reply on terms of use (founder).
- Founder to confirm Google sign-in lands on two-step setup; inner pages of the redesign to be checked once signed in.

- GitHub schedule fired once (19 Nov 01:03 UTC) but is throttled; API dispatch is the main path. Consider a DB-side sweep in Phase 9.
- Dispatch latency (API → `repository_dispatch` → runner claims job): measure once the API is on Vercel (Phase 2).

## Known issues

- Dev-only `picomatch` advisory GHSA-fc5q-39xc-vjpf (no fix version); production deps 0 vulnerabilities.
- Actions `checkout@v4`/`setup-python@v5` warn about Node 20 deprecation; take the Dependabot bump PR.
