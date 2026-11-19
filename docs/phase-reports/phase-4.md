# Phase 4 — Analysis engine: completion report

**Date:** 19 Nov 2026 **Result:** PASS — awaiting founder approval to start Phase 5.
**PR:** #24 `phase-4-analysis` → `main` (merged)

## Objective

Peer groups, MAD statistics, severity, confidence, evidence and ground-truth scoring (master prompt 5.6, Phase 4).

## Implemented

- **Engine** (`app/analysis/engine.py`, engine 1.0.0, method 1.0.0), pure and deterministic. Full method in `docs/methodology.md`.
  - Indicators: turnout (accredited ÷ registered) and leading-party share (highest party ÷ valid).
  - Peers: same ward; LGA when the ward has fewer than 10 usable units (the finding says so); otherwise "not scored — insufficient peers". The unit is included in its own peer group (recorded in the methodology).
  - median, MAD, robust z = 0.6745 (x − median) / MAD; listed when |z| ≥ 3.5. MAD = 0 → "not scored — zero MAD"; no score is invented.
  - Severity by |z| (medium / high / very high) and confidence (low / medium / high) stored separately.
  - Units excluded by validation are left out only of the indicators their problem affects, and are listed in each finding's exclusions.
  - Every finding stores: peer group level, key and size, median, MAD, robust z, threshold, severity, confidence, exclusions with reasons, evidence (raw row id, source row number, values), limitations text, and provenance (dataset version, validation job, ruleset and schema versions, engine and method versions).
  - **Data quality findings**: one per polling unit and rule, made from the validation issues, with the original cells as evidence.
  - Content hash: SHA-256 over canonical JSON of versions, parameters, findings and the not-scored list, in a fixed order.
- **Analyse job** (`app/jobs/analyse.py`): writes the run, its findings (COPY) and the version's pointer to the run in one transaction together with the job's success. Refuses if the version was re-validated after the job was queued.
- **`POST /jobs` analyse**: admin, analyst, researcher; needs a completed validation (409 otherwise); fingerprint = validation job + engine + method versions; a live duplicate returns the same job.
- **Migration** `20261119000900_analysis.sql`: run summary and not-scored list; `dataset_versions.latest_analysis_run_id` and `analysed_at`; indexes; **findings are now append-only** (no update or delete), so a review always refers to what the reviewer saw. A new analysis makes a new run.
- **Analysis page** `/versions/:id/analysis`: run button, summary, per-indicator scoring table (scored, LGA fallback, by severity, not scored with reasons), list of unusual units (filters: indicator, how unusual; "very high" first), the not-scored list, method and content hash. Linked from the project and validation pages. No "fraud" wording anywhere (also enforced by a database check on findings).

## Acceptance

| Criterion | Result |
|---|---|
| Hand-checked median / MAD / robust z | PASS — worked example in `test_analysis_engine.py`: median 0.39, MAD 0.05, z(0.90) = 6.8799 → high; odd/even median; band edges 3.5 / 5 / 7 |
| Zero-MAD and small-group cases return "not scored" | PASS — unit tests, and on the demo data: all 12 zero-MAD rows → zero_mad, all 8 tiny-LGA rows → insufficient_peers, all 10 duplicate rows → excluded_by_validation; the 6 small-ward rows are scored against their LGA |
| Planted anomalies detected | PASS — **30 of 30** (15 turnout, 15 leading share), all **very high** |
| False positives on normal rows measured and recorded | **102** flags on the 11,893 normal rows × 2 indicators = 23,786 unit-indicators (**0.43%**): 93 medium, 9 high, **0 very high**. A test pins these numbers so any change is noticed. |
| Two runs on the same input give identical content hashes | PASS — unit test (also with input order reversed), DB test, and live on the real project (`5bf20bdd953d…` twice) |

Other flags on the demo data: 8 very-high leading-share flags on rows planted for a different scenario (accredited > registered), where the generator also produced an unusual vote split.

## Verification

- Local: API 396 tests pass (engine 28, job 3 new); web 29 tests, typecheck, lint, build.
- CI on the PR: all green.
- **Live, real Supabase project** (Database workflow): security 18/18; synthetic ingestion → validation → analysis 15/15 (analysis 6–7 s for 12,023 rows); **real Plateau data 11/11** — the stored analysis summary equals the engine run locally on the same file.
- **Production** (Deployed check, run 44467391577): **20/20** — the deployed API took an upload, mapping, validation and analysis, and each job was run by a GitHub worker woken by the API's own dispatch (ingest 31 s, validate 33 s, analyse 20 s end to end).

## Real data (Plateau, CCIJ/VEZA 2023)

4,989 polling units. Turnout: 4,905 scored, **101** unusual (71 medium, 23 high, 7 very high), 168 compared with their LGA, 55 not scored for zero MAD. Leading-party share: 3,545 scored, **61** unusual (42 / 13 / 6), 1,426 excluded by data checks. Plus 1,470 data quality findings. These are reasons to check sheets, not conclusions.

## Not done / open

- The GitHub schedule did fire once (01:03 UTC, 19 Nov) but is throttled on this repo; dispatch from the API is the main path and works. A database-side sweep is left for Phase 9 if needed.
- Findings dashboard with review, notes and claims is Phase 5.

## Next

Phase 5 on `phase-5-findings`: findings dashboard and details (evidence back to raw cells), investigation notes, append-only reviews, claims (unverified / verified with independent evidence and a second person's sign-off), audit history.
