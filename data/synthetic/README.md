# SYNTHETIC data — fictional, not real election results

Every file in this folder is **synthetic and fictional**. The states (Synthia, Norvale, Eastmarch, Quillon), LGAs, wards, polling units, party codes (PA–PE) and all numbers are invented by `tools/synthetic/generate.py`. They do not describe any real place, election or person. No coordinates are given for normal rows; the few coordinate values present are deliberately invalid test values.

| File | What it is |
|---|---|
| `SYNTHETIC_results_seed2026.csv` | Demo upload file: 12,023 polling-unit rows, ~1 MB |
| `SYNTHETIC_ground_truth_seed2026.csv` | For each row: planted scenario, expected validation rule IDs, expected statistical flags, expected "not scored" reason |
| `SYNTHETIC_admin_units_seed2026.csv` | Reference list of valid state / LGA / ward labels (used by rule V011) |
| `SYNTHETIC_sheet_matches_row1.png` | A SYNTHETIC result-sheet photo (Form EC8A layout) whose figures match row 1 of the results file |
| `SYNTHETIC_sheet_mismatch_row2.png` | A SYNTHETIC result sheet for row 2 with 15 extra votes for PA (party scores no longer add up; differs from the data) |

Regenerate (deterministic): `python tools/synthetic/generate.py --seed 2026 --out data/synthetic`. The sheet images come from `python -m app.sheets.synthetic` (in `services/api`); every one is stamped SYNTHETIC. CI fails if these files drift from the generator.

## Planted scenarios

| Scenario | Rows | Expected |
|---|---|---|
| duplicate PU identifiers | 10 (5 pairs) | V010; excluded from statistics |
| invalid numeric fields | 10 | V004 (V003 for a blank cell) |
| missing PU identifier | 5 | V003 |
| accredited > registered | 8 | V006 |
| vote reconciliation error | 8 | V007 |
| party votes ≠ valid votes | 6 | V008 |
| negative value | 5 | V005 |
| party votes > valid votes | 5 | V009 + V008 |
| abnormal turnout | 15 | turnout statistical flag |
| unusual leading-party share | 15 | leading-share statistical flag |
| missing geography | 6 | V003 |
| unmatched admin label | 6 | V011 |
| invalid coordinates | 5 | V012 |
| small ward (6 PUs) | 6 | scored against LGA (fallback) |
| tiny LGA (8 PUs) | 8 | not scored: insufficient peers |
| identical ward (12 PUs) | 12 | not scored: zero MAD |
| normal | 11,893 | no rule, no flag (false-positive baseline) |

Validation convention used by the ground truth: when a field fails a type or range rule (V003, V004, V005), arithmetic rules that need that field (V006–V009) are reported as "not evaluated" for that row rather than as extra failures.
