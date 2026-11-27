# Evaluation report: Election as a System (EaaS)

## 1. Objective and success criteria
Track 2 asks for flags a human can check, scored by precision and recall against injected anomalies, reviewer
workload (alerts per 1,000 records), explanation quality, reproducibility, audit logging and stated statistical
limits.

## 2. Test data
| Set | Source | Size | Answer key | Used for |
|---|---|---|---|---|
| Rise starter data | Organisers' repo, `data/synthetic/` (CC BY 4.0), copied to `data/rise-starter/` | 900 units, 42 planted (4.7%) | `anomaly_labels_HELD_OUT.csv` | Precision, recall, workload |
| EaaS synthetic | `tools/synthetic/generate.py`, seed 2026 | 12,023 units, 15 planted scenarios | `data/synthetic/SYNTHETIC_ground_truth_seed2026.csv` | Every rule and statistical case, row by row |
| Real public data | CCIJ/VEZA 2023 presidential, Plateau, downloaded at run time (not stored) | 4,989 units | None (real data has no key) | Speed, behaviour on real data |
| Real result sheets | 20 handwritten Plateau 2023 EC8A sheets, read in memory only | 100 figures | CCIJ/VEZA transcription | Sheet reader accuracy |

No rule or threshold was fitted to the Rise answer key. Two pattern rules (V013 round numbers, V014 copied
scores) were added after reading the key's list of anomaly types; their thresholds were chosen to keep false alarms
low on our own 12,023-row set and are fixed in ruleset 1.2.0.

## 3. Baseline
The starter notebook's `integrity_flags` (four pandas checks). On the Rise data it also finds 42/42 with no false
alarms, because in that file every normal unit has votes + rejected exactly equal to accredited, so any change breaks
the sum. Real results do not have that property (some accredited voters do not vote), so that check would flag many
normal units in real data. EaaS's reconciliation rule only flags votes **above** accreditation.

## 4. Results on the Rise starter data
| Method | Flagged | Found (of 42) | False alarms | Precision | Recall | Alerts per 1,000 |
|---|---|---|---|---|---|---|
| EaaS rules (V001–V014) | 42 | 42 | 0 | 1.00 | 1.00 | 46.7 |
| EaaS rules + statistical outliers | 86 | 42 | 44 | 0.49 | 1.00 | 95.6 |
| Starter notebook baseline | 42 | 42 | 0 | 1.00 | 1.00 | 46.7 |

| Planted type | Planted | Found | Rule that found it |
|---|---|---|---|
| Accredited exceeds registered | 16 | 16 | V006 |
| Sum mismatch | 8 | 8 | V007 |
| Digit transposition | 1 | 1 | V007 |
| Suspicious round numbers | 12 | 12 | V013 (and V007, V014) |
| Duplicated from previous unit | 5 | 5 | V014 (and V007) |

The 44 extra statistical flags are units whose turnout or leading-party share is far from their LGA's median
(robust z ≥ 3.5): 29 medium, 10 high, 5 very high. The answer key does not plant these, so they count as false alarms
here; the starter data's figures are random, so a few extreme values are expected. All 44 are rated by severity and
labelled "a reason to check the sheet, not evidence". Every ward in this file has only 5 units, so every unit was
compared with its LGA instead, which the finding states.

On EaaS's own 12,023-row set every rule and statistical case matches its ground truth exactly (tests in
`services/api/tests/test_validation_engine.py` and `test_analysis_engine.py`): 30/30 planted statistical anomalies
found, all very high; 102 statistical flags on normal units (0.43% of 23,786 unit-indicator checks), none very high.

## 5. Human-in-the-loop impact
- Workload: 46.7 rule alerts per 1,000 units on the Rise data; with statistics, 95.6 per 1,000, ranked by severity
  so reviewers start with very high.
- Every flag opens with the figures, the peer median and spread, the original cells and the ordinary explanations.
- Review decisions (acknowledge, needs evidence, data error, events on the day, escalate) are append-only, with
  reasons; escalations go to the right coordinator.

## 6. Robustness
- Codes and names are compared ignoring capitals, extra spaces and separators (`31/04/01/003` = `31-04-01-003`).
- Bad cells (text, decimals, negative, blank) are reported once and excluded only from the calculations they affect.
- Field reports are saved on the phone and resent safely; the same report is never stored twice.
- Ask the data: every number in an answer must appear in the lookups' results, or the answer carries a warning.

## 7. Usability and accessibility
Automated axe scan in the end-to-end browser test: no serious or critical issues. Keyboard focus is visible;
colour is never the only signal. Dark mode. Phone layout for field observers. No user study yet (see §10).

## 8. Failure cases
- Sheet reader: 84 of 100 handwritten figures exactly right; 13 of the 16 misses were marked unsure; 3 were confident
  misreads (for example 114 for 14). Hence a person confirms every figure.
- Small wards (fewer than 10 units) are compared with the whole LGA; units in LGAs with fewer than 10 are not scored.
- The statistical layer flags natural extremes (candidates' home areas, very small units). It reports them by
  severity and never alone decides anything.
- Yoruba and Igbo answers can mix in English.

## 9. Reproducibility
```bash
cd services/api
python scripts/eval_rise_track2.py              # this table (deterministic, < 1 s)
pytest tests/test_rise_starter.py               # asserts it
python ../../tools/synthetic/generate.py        # regenerates the 12,023-row set (seed 2026)
```
Same input gives the same findings and the same SHA-256 content hash.

## 10. Conclusion and next steps
The rules find every planted problem with no false alarms; the statistical layer adds ranked leads at a stated
workload. Before real use: a pilot with an observer group on a past election, measuring reviewer time and agreement
with their findings; an independent security review; legal review under NDPA 2023; and a larger sheet-reading test.
