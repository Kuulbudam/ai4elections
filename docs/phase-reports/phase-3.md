# Phase 3 — Validation: completion report

**Date:** 18 Nov 2026 **Result:** PASS — awaiting founder approval to start Phase 4.
**PR:** `phase-3-validation` → `main`

## Objective

Validation rules V001–V012 with versioned definitions, row eligibility, scoped quarantine, issue storage, and the validation page.

## Implemented

- **Rule catalogue** (`app/validation/rules.py`, rule set 1.0.0), one source of truth. Every rule has an ID, version, severity, classification, description, affected fields, remediation and quarantine scope. The definitions are stored in `validation_rules` when a validation runs and are checked against `packages/contracts/schemas/validation-rule.schema.json`.

  | Rule | Severity | Excludes from |
  |---|---|---|
  | V001 required/known columns mapped, lat+lon together, ≥2 parties | error (mapping) | — (blocks the job) |
  | V002 column or party code used twice | error (mapping) | — (blocks the job) |
  | V003 missing value (blank or missing cell) | error | depends on the field |
  | V004 not a whole number | error | depends on the field |
  | V005 negative count | error | depends on the field |
  | V006 accredited > registered | error | turnout |
  | V007 total ≠ valid + rejected, or total > accredited | error | vote share |
  | V008 party votes ≠ valid votes | error | vote share |
  | V009 count above the count it belongs to | error | vote share |
  | V010 duplicate PU code (all copies flagged) | error | all |
  | V011 label not in the project reference list | warning | — |
  | V012 invalid coordinates | warning | — |

  "Depends on the field" means: PU code or geography → all analysis; registered/accredited → turnout; valid/party votes → vote share; rejected/total cast → none.
- **Engine** (`app/validation/engine.py`): pure and deterministic.
  - Builds one canonical record per raw row and never changes the original cells.
  - When a cell fails a type rule, the arithmetic rules that need it are not evaluated, so one bad cell is reported once.
  - Labels are compared after trimming spaces and ignoring case.
  - Row statuses: `eligible` / `eligible_with_warnings` / `quarantined_from_specific_analysis`. Each row records exactly which indicators it is excluded from (`excluded_from`) and why (`exclusion_reasons`).
- **Validate job** (`app/jobs/validate.py`):
  - Re-checks that the job belongs to the right organisation and project, and uses the mapping saved when the job was queued.
  - Replaces any earlier validation of the version (records, issues, summary) in **one transaction** together with the job's success, so retries never duplicate results.
  - Writes rows with `COPY`.
- **`POST /jobs`** (validate): allowed for admin, analyst and researcher. Viewers get 403; other organisations get 404. The version must be ingested and its mapping complete (otherwise 409). The job's fingerprint covers the mapping, the rule set and the reference list. A duplicate request returns the live job (200). Supports Idempotency-Key and records the dispatch result. `analyse` returns 400 until Phase 4.
- **Migration** `20261119000700_validation.sql`: each version points to its current validation job, time and summary; adds indexes.
- **Web:**
  - Validation page: run or re-run with live progress; summary cards; rows excluded per indicator; a plain warning when V011 was not checked because no reference list is loaded; rules table with counts, rows affected and how to fix; issues table filterable by rule and paginated, showing source row, column and **original cell exactly as uploaded**.
  - Reference-list loader on the project page (CSV with state, lga, ward; only new units are inserted).
  - Links from the project page and the mapping page.

## Tests and results

| Suite | Result |
|---|---|
| `pytest -q` | **322 passed**, 0 failed (+42) |
| `npm test` | **21 passed**, 0 failed (+4) |
| Live security check (real Supabase) | 15/15 |
| **Live ingest + validation check** (Database run 44400377399) | **12/12** |

**Ground-truth result:** on the 12,023-row synthetic file, the issues found per (row, rule) **match the answer key exactly**: 79 expected, 79 found, **0 false positives, 0 missed** (V003 12, V004 9, V005 5, V006 8, V007 8, V008 11, V009 5, V010 10, V011 6, V012 5). All **11,893 normal rows** are clean, and every planted row has its documented quarantine scope.

**Performance on the real platform:** ingestion of 12,023 rows took 6.6 s; **validation took 6.4 s** (target < 30 s). That run had no reference list and no coordinates mapped, so it found 68 issues: exactly the 79 expected minus the 6 V011 and 5 V012 issues that weren't checked.

Other new tests:
- every issue links to its raw source row and original cell;
- re-validation replaces results with no duplicates;
- roles: viewer 403, researcher allowed, other org 404, `analyse`/`ingest` 400;
- mapping required and complete (409);
- the rule catalogue matches the published JSON schema;
- 13 single-row rule cases, including whitespace, thousands separators, scientific notation, ragged rows and cascade suppression;
- results are deterministic;
- web: validation page shows the summary, the honest V011 status and traced issues; viewers can't run it; a researcher can run it; filtering by rule; CSV parser.

## Security review

- Validation reads only rows of the job's organisation and project (re-checked by the worker). Results are written by the service role and read by members through RLS.
- Reference-list inserts go through the existing RLS policy (admin and analyst only, as themselves).
- No raw cell content is ever executed or interpreted beyond integer and decimal parsing.

## Known limitations

- **Re-validation replaces the version's previous records and issues.** The raw rows never change. Phase 4 analysis runs will store a snapshot of everything they use, so their findings stay reproducible.
- **V011 checks the full state/LGA/ward combination against the reference list.** A ward appearing under two LGAs is caught only if that combination is missing from the list.
- **The reference list can only be added to, not edited**, in the MVP.
- **Automatic worker start from the deployed site waits on the founder's token fix.** Diagnosis: GitHub returns 403 "Resource not accessible by personal access token".

## Cost and billing

$0.

## Days to demo / critical path

8 Dec 2026 — 20 days left. Phases 0–3 finished on 18 Nov; the plan had Phase 3 on 25–27 Nov.

## Acceptance criteria and evidence

| Criterion | Evidence | Status |
|---|---|---|
| All validation tests pass | 322 + 21 tests; live 12/12 | PASS |
| Planted scenarios detected, normal rows not flagged | Exact per-row match with the ground truth (79/79, 0 FP, 0 FN; live 68/68 for the rules evaluated) | PASS |
| Source rows remain traceable | Every issue → raw row id, source row number, column, original cell (tested, local and live) | PASS |
| Quarantine explained and limited to affected calculations | `excluded_from` + `exclusion_reasons` per row; scope tests per scenario | PASS |

## Next phase

Phase 4 — Analysis engine (peer groups, MAD, severity and confidence, evidence, ground-truth scoring).
