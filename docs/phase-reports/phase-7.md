# Phase 7 — Reporting: completion report

**Date:** 19 Nov 2026 **Result:** PASS. Moving on to Phase 8 under the founder's working rule.
**PR:** `phase-7-reports` → `main` (merged). Decisions: ADR-0005, ADR-0011.

## Implemented

- **`POST /reports`** (admin, analyst): an immutable snapshot of one complete analysis run. It contains:
  - project; dataset version, SHA-256, size, rows and source;
  - analysis run with engine, method and rule versions, parameters and run hash;
  - plain-language methodology and the validation summary;
  - **every finding** with evidence, severity, confidence, current review status and full review history;
  - **claims labelled VERIFIED FACT / UNVERIFIED ALLEGATION** with their evidence;
  - limitations, including "Statistical anomalies are not evidence of fraud".
- The audit checkpoint (the latest event ID and hash in the organisation's chain, taken under the audit lock), the generation time and the user are stored and shown next to the body.
- **Content hash** = SHA-256 of the canonical JSON body (sorted keys, no spaces, fixed decimals, defined order). It leaves out the id, time, user and checkpoint, so the same inputs give the same hash. Why the checkpoint is left out: ADR-0011.
- **`GET /reports/{id}/export?format=csv|md`** (admin, analyst):
  - CSV has one row per finding, with metadata as `#` lines.
  - Markdown is the full narrative.
  - Text starting with `=`, `+`, `-`, `@`, tab or CR gets a leading `'`; numbers stay numbers; Markdown escapes `|`, `<` and `>`. Both send `X-Report-Content-Hash`.
- Reports are append-only (database trigger). No AI code is used in report building or export (tested by import scan).
- **Web:**
  - Reports page: list, and generate from any analysed version.
  - Report view, readable by all roles: hashes, checkpoint, provenance, method, validation, outliers table linked to findings, data-quality counts, reviews, labelled claims, limitations.
  - **Print / save as PDF** with a print stylesheet; CSV and Markdown downloads for admins and analysts.

## Acceptance

| Criterion | Result |
|---|---|
| Regenerating from the same inputs gives the same content hash | PASS. Test: two reports by different people, with audit events in between, give the same hash; a new review changes it; the stored hash equals SHA-256 of the stored body. Live: two reports give the same hash. |
| Exports pass formula-injection tests | PASS. Unit cases for `=`, `+`, `-`, `@`, tab, CR; a hostile report title is quoted in the CSV; negative z-scores stay numeric; Markdown escapes HTML and table pipes. |
| No AI used | PASS. The report modules import nothing from the assistant (tested). |

## Verification

- Local: API 470 tests; web 38 tests, typecheck, lint, build. The report page and the **printed PDF** were rendered and checked: navigation and buttons are hidden, and tables stay legible.
- Live, real project: 18/18, **25/25** (same hash twice, export header, edit refused with 403), Plateau 12/12, assistant 9/9.
- **Production** (run 44517467464): **25/25**, including report generation and export.

## Next

Phase 8, frontend integration: every page complete, responsive and accessible, with no dead buttons, plus a Playwright end-to-end test of the whole workflow from a clean account on the deployed site.
