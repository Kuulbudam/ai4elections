# Analysis methodology (method version 1.0.0)

Code: `services/api/app/analysis/engine.py`. Tests: `services/api/tests/test_analysis_engine.py`.

## What is measured

| Indicator | Formula | Not calculated when |
|---|---|---|
| Turnout | accredited voters ÷ registered voters | registered voters is 0 or missing |
| Leading-party share | highest party votes ÷ valid votes | valid votes is 0, or a party count is missing |

When a file lists only some parties (ruleset 1.1.0, `party_columns_complete = false`), the leading party is the highest of the listed parties.

A unit is left out of an indicator when validation excluded it from that indicator (for example V006 for turnout, V007/V008/V009 for vote share, V010 for both). It still counts for the other indicator.

## Peer groups

1. The polling units in the same ward (state + LGA + ward), counting only units that have a value for the indicator.
2. If the ward has fewer than **10** such units, the whole LGA is used, and the finding says so.
3. If the LGA also has fewer than 10, the unit is **not scored — insufficient peers**.

The unit being scored is **included** in its own peer group. With at least 10 units this lowers sensitivity a little (an extreme value pulls the median toward itself slightly) but keeps the group definition simple and the same for every unit.

## Score

```
median = median(x)
MAD    = median(|xᵢ − median|)
z      = 0.6745 × (x − median) / MAD
```

* Listed when |z| ≥ 3.5 (Iglewicz and Hoaglin). Both directions are listed.
* If MAD = 0 (most peers have the same value), no score is given: **not scored — zero MAD**. No score is invented.
* Values are rounded to 6 decimals and z to 4 decimals before any comparison, so results are reproducible.

## Severity and confidence (kept separate)

| How unusual (severity) | |z| |
|---|---|
| Medium | 3.5 to under 5 |
| High | 5 to under 7 |
| Very high | 7 or more |

| Confidence | When |
|---|---|
| Low | the row has validation warnings |
| High | ward group with 20 or more units |
| Medium | ward group of 10–19, or LGA fallback |

## Data quality findings

Every validation issue becomes a `data_quality_issue` finding, one per polling unit and rule, with the original cells as evidence. Errors are medium severity, warnings low; confidence is high because the check is exact.

## Reproducibility

Each run stores the engine and method versions, the parameters, the ruleset and schema versions it read, the list of units not scored and why, and a SHA-256 content hash of all findings in a fixed order. The same validated data always gives the same hash (tested, and checked live). A new run never changes an earlier one; findings cannot be edited or deleted.

## What a finding means

A finding is a reason to look at the result sheet. It is not evidence of wrongdoing, and the platform never says "fraud". Ordinary causes include reading or transcription errors, very small units, local turnout patterns and home-area voting.
