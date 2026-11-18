# Worker spike results (master prompt Section 8)

## Run 1 — local container (18 Nov 2026)

Command: `DATABASE_URL=<local pg16> python spike/run_spike.py --duration 90 --stale 20`

| Scenario | Result | Detail |
|---|---|---|
| normal 90 s run | PASS | status=succeeded, wall=90.2 s, 1 output row |
| kill → reconcile → retry | PASS | worker SIGKILLed at ~30 s; reconcile requeued 1; rescue worker succeeded on attempt 2; 1 output row |
| duplicate dispatch → one execution | PASS | 3 workers started at once; executions=1 |
| cost | $0 | local |

Note: a first attempt was invalidated because a parallel CI simulation dropped the `spike` schema mid-run (operator error, not a code fault). Re-run in an isolated database passed.

## Run 2 — GitHub Actions runner + Supabase Free (chosen hosting)

**PASS — 18 Nov 2026.** Workflow run 44363949162 (`workflow_dispatch`, mode `spike`, duration 90 s, commit 891f896), GitHub-hosted `ubuntu-latest` runner → Supabase Free via Session pooler.

| Scenario | Result | Evidence |
|---|---|---|
| normal 90 s run | PASS | `run_spike.py` exits 0 only if all three scenarios pass; step "Run 90-second worker spike" = success |
| kill → reconcile → retry | PASS | as above |
| duplicate dispatch → one execution | PASS | as above |
| spike step wall time | 4 min 12 s | 17:55:23 → 17:59:35 UTC (90 + ~30 + 22 + 90 + ~10 s expected) |
| cost | $0 | public repo, standard runner; Supabase Free |

Earlier runs 44347487253, 44350155927, 44362839186 failed at "Ensure spike schema exists" (database connection, before the founder's final `DATABASE_URL` value). No code change was needed between them and the passing run.

Note: the per-scenario table is in the run's job summary on GitHub (needs sign-in to view logs).

## Not yet measured

- Dispatch latency (API `POST /jobs` → `repository_dispatch` → runner claims job). Requires the API on Vercel; measure in Phase 1/2.
