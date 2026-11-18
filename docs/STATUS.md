# STATUS — handoff file

Read this first in every session. Update it last.

**Last updated:** 18 Nov 2026
**Current phase:** 0 — Foundation (pushed; CI green on GitHub; waiting on Supabase setup for the hosted spike)
**Demo:** Rise Networks #AI4Elections Hackathon, December 2026 (exact date: ask founder; record here)

## Decisions made (founder, 18 Nov 2026)

- Hosting: **Option B**, no card. API on Vercel Hobby, worker on GitHub Actions (ADR-0002).
- Repo: `Kuulbudam/ai4elections`, **public**.
- Auth email: confirmation **off** on the demo Supabase project.

## Done

- Repo structure, README, `.env.example`, `.gitignore`.
- ADRs 0001–0006, `docs/providers.md` (checked 18 Nov 2026), `docs/founder-setup.md`.
- OpenAPI 3.1 contract (`packages/contracts/openapi.yaml`) + JSON schemas; generated Pydantic models and TypeScript types; sync checks.
- FastAPI skeleton: `/health`, contract-shaped errors, no internal details leaked, CORS allow-list.
- PostgreSQL job ledger (`app/jobs/ledger.py`) + worker (`app/jobs/worker.py`) + spike schema and runner.
- React/Vite web shell showing real API health only.
- CI (`ci.yml`), worker workflow (`worker.yml`), Dependabot.
- Local test results: see `docs/phase-reports/phase-0.md`.

## Waiting on founder (exact steps in `docs/founder-setup.md`)

1. ~~Install the Claude GitHub App~~ — done 18 Nov 2026.
2. Create Supabase Free project, turn off email confirmation, copy Session pooler URI.
3. Add `DATABASE_URL` Actions secret.
4. Run Actions → Worker → mode `spike`.
5. Tell the agent the demo date.

## Next step

Foundation pushed to `main`; CI run 44341531979 green (API, Web, Migrations, Secret scan).
Once 2–4 are done: record hosted spike results in `docs/spike-results.md`, finish Phase 0 report, stop at gate.
Then Phase 1 (database and security).

## Open questions

- Exact demo date.
- Dispatch latency (API → `repository_dispatch` → runner claims job) is not measured yet; needs the API deployed (Phase 1/2). Measure then and record here.

## Known issues

- `npm audit` (dev dependencies only): picomatch ReDoS advisory GHSA-fc5q-39xc-vjpf via vite/vitest, no fixed version. Build-time only, inputs are our own glob patterns. Production dependencies: 0 vulnerabilities. Re-check weekly via Dependabot.
