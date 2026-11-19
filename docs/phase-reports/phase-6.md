# Phase 6 — Geography: completion report

**Date:** 19 Nov 2026 **Result:** PASS. Per the founder's working rule (19 Nov), the next step starts without waiting for approval.
**PR:** #28 `phase-6-geography` → `main` (merged). Decisions: ADR-0004 (no maps), ADR-0009 (boundary sources, post-MVP).

## Implemented

- **`GET /areas`** (contract-first): totals for one validated dataset version at state, LGA or ward level, optionally inside a state / LGA.
  - Turnout and vote-share totals use the analysis's own counting rules: a unit counts only where the analysis could score it.
  - Party totals, leading party and share; finding counts from the latest run (unusual turnout, unusual share, very high, data quality, not reviewed).
  - `in_reference_list` per area. Sums are done in Postgres on integers; rates are divided once.
  - All roles may read; other organisations get 404; an unvalidated version gets 409.
  - `area_list()` is shared, so the data assistant can reuse it.
- **Areas page** `/versions/:id/areas`:
  - drill-down state → LGA → ward, and sort;
  - two single-series bar charts (turnout; unusual units) using the validated reference blue, thin bars from one baseline, hover values, no legend (the title names the series), first 30 shown with the table listing all;
  - side-by-side comparison of up to four areas (counts, turnout, each party's share).
  - Labels are shown exactly as in the data. Unmatched labels are marked "not in reference list", never corrected. No map. Linked from the project, analysis and findings pages.
- **ADR-0009**: boundary-source assessment for later maps (HDX COD-AB, CC BY 4.0, national; GRID3 operational wards v3.0, CC BY-SA, 24 states with Plateau not yet covered). Nothing loaded.

## Acceptance

| Criterion | Result |
|---|---|
| Aggregates match direct calculations on synthetic data | PASS. `test_areas.py` recomputes every state, LGA and ward **straight from the raw CSV cells and the ground-truth rule labels** (no engine code) and compares units, counted units, registered, accredited, rate, valid votes and every party total: all equal at all three levels. Drill-down sums equal their parent. Per-state finding counts add up to the run (49 turnout + 91 share). |
| No invented geography anywhere | PASS. No map or coordinates are shown. Areas come only from the data's own labels. The planted unmatched labels appear as themselves, flagged "not in reference list". |

## Verification

- Local: API 425 tests; web 34 tests, typecheck, lint, build.
- Page rendered and screenshotted with sample data at desktop and phone width. Fixed: row headers styled like column headers; the charts now stack on narrow screens.
- CI: green. Live, real project: 18/18, 22/22, **Plateau 12/12** (LGA totals: 4,989 units; turnout and vote-share counted units equal the analysis's eligible counts; all 17 LGAs in the reference list).
- **Production** (run 44502927493): **23/23**, including `GET /areas`.

## Open

- Wide tables still scroll sideways on a phone. This is responsive polish for Phase 8.

## Next

The data assistant (founder requirement, 19 Nov; see STATUS), then Phase 7 reports.
