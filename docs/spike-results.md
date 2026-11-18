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

**Pending.** Needs `DATABASE_URL` secret (see `docs/founder-setup.md`). Run: Actions → Worker → mode `spike`. Paste the job summary table here.

## Not yet measured

- Dispatch latency (API `POST /jobs` → `repository_dispatch` → runner claims job). Requires the API on Vercel; measure in Phase 1/2.
