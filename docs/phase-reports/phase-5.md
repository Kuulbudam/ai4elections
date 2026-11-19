# Phase 5 — Findings and review: completion report

**Date:** 19 Nov 2026 **Result:** PASS — awaiting founder approval to start Phase 6.
**PR:** #26 `phase-5-findings` → `main` (merged). Decisions: ADR-0008.

## Implemented

- **API**
  - `GET /findings`: one project; filters for category, indicator, severity, confidence, review status, state / LGA / ward; most unusual first; cursor pagination. Defaults to the latest run of each version that is not archived.
  - `GET /findings/{id}` (added to the contract): the finding with its source row's **original cells** and the columns a check flagged.
  - `POST /findings/{id}/reviews`: admin / analyst; a reason is required.
  - `POST /claims`: admin / analyst / researcher. A claim always starts as an unverified allegation; its evidence is saved in the same transaction.
  - `POST /claims/{id}/verify`: admin / analyst who did not create the claim, with a reason, and only when evidence is attached.
  - All of these: non-members get 404, the wrong role gets 403. They support Idempotency-Key, and every write is audited with the person who made it.
- **Database** (migration `20261119001000_findings_review.sql`)
  - `finding_overview` view (security_invoker): the current review status (latest decision) and note and claim counts, under the caller's RLS and two-step rule.
  - A trigger refuses to verify a claim that has no evidence. Another refuses a claim whose finding is in a different project.
  - Indexes for the new queries.
  - Already in place from Phase 1: reviews, notes, claim evidence and audit events are append-only, and a claim cannot be created as verified or verified by its author.
- **Web**
  - **Findings dashboard** `/projects/:id/findings`: all filters, with geography choices taken from the reference list.
  - **Finding detail** `/findings/:id`:
    - explanation, severity and confidence shown as separate badges, peer comparison, and the units left out of the comparison with reasons;
    - the source row exactly as uploaded, with flagged cells marked;
    - limitations and provenance (dataset SHA-256, engine, method and rule versions);
    - review form and full history (latest marked current) and investigation notes;
    - claims, each clearly labelled **Unverified allegation** or **Verified fact**, with evidence (link, document reference, or a file's SHA-256 fingerprint computed in the browser) and a verify form shown only to an admin or analyst who did not record the claim;
    - the audit trail for the finding.
  - **Claims register** `/projects/:id/claims`.
  - Links added from the project page and the analysis page.

## Acceptance

| Criterion | Result |
|---|---|
| Every finding links to its source rows | PASS. The DB test checks that every finding's evidence row number equals its raw row. The API returns the original cells. Live: a finding's evidence shows the same PU code as its row (real project and production). |
| Review history cannot be altered (tested at database level) | PASS. UPDATE, DELETE and TRUNCATE on `finding_reviews` are refused for the **table owner**, `service_role` and API users (tests). Live: a PATCH or DELETE through the Data API returns 403 and the row is unchanged. |
| Permissions enforced per the 5.1 table | PASS. Reviews: admin and analyst 201, researcher and viewer 403. Notes: researcher yes, viewer no. Claims: admin, analyst and researcher yes, viewer no. Verify: researcher and viewer 403, the claim's author 409, no evidence 409, already verified 409. Other organisation: 404 everywhere. Checked in the API and again in the database. |

## Verification

- Local: API 421 tests (25 new for Phase 5); web 33 tests, typecheck, lint, build.
- CI on the PR: all green.
- Live, real Supabase project: security 18/18; ingestion, validation, analysis and findings 21/21; Plateau 11/11.
- Production (Deployed check run 44495163482): **22/22**, including findings list and detail through the deployed API.

## Limitations / open

- **File evidence** stores the file's SHA-256 and description only. The file stays with whoever holds it. Storing files is deferred (ADR-0008).
- The claims register lists the latest 100 claims, with no pagination yet.
- I have not yet seen the new pages in a signed-in browser; please click through once.

## Next

Phase 6, geography: state / LGA / ward summary tables and charts, filters and comparisons, with no map (the Phase 0 geography decision).
