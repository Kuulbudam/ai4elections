# Phase 8 — Frontend integration: completion report

**Date:** 19 Nov 2026 **Result:** PASS.
**PRs:** #35 (dashboard, responsive, e2e test), `phase-8b-e2e` (test selectors).

## Pages

| Required page | Where |
|---|---|
| Authentication | Sign in: Google first, email and password as fallback; two-step set-up and challenge |
| Organisation setup | Dashboard (create organisation), Members |
| **Main dashboard** (new) | `/`: your projects with unusual units still to review, unverified allegations and reports, plus your organisations |
| Projects / project overview / version history | Projects; project page with versions, statuses and links |
| Upload, column mapping | Upload, Mapping |
| Validation dashboard, analysis progress | Validation, Analysis (job progress shown while the worker runs) |
| Findings, finding details, investigation and review | Findings dashboard, finding page (evidence, review, notes, claims, audit), claims register |
| Geographic (tables) dashboard | Areas |
| Reports | Reports list and report view (print, CSV, Markdown) |
| User profile | Profile |
| (founder addition) | Ask the data |

Also in this phase:
- Tables scroll inside their own box on phones, so the page itself never scrolls sideways.
- A visible keyboard focus ring.
- The dashboard summary never blocks the page if one of its queries fails.
- Loading, error and empty states were checked on every page (already present).

## Acceptance

**An automated Playwright end-to-end test runs the full workflow from a clean account on the deployed site.** PASS (Actions run 44521123100, 2.4 min). `apps/web/e2e/workflow.spec.ts`, started manually with `e2e.yml`, does the following:

1. Creates a fresh account, then signs in with email and password through the real UI.
2. Sets up two-step verification by reading the key and typing a real authenticator code.
3. Creates an organisation and a project, then uploads the 12,023-row synthetic file. The real worker ingests it, started by the API's dispatch.
4. Saves the suggested mapping, runs validation, then runs analysis. It checks the counts (49 unusual turnout, 91 unusual share).
5. Opens the area charts, filters findings to very high and opens one, then records a review decision and sees it in the history.
6. Generates a report, checks the limitations text and downloads the CSV.
7. At phone width, checks the page does not scroll sideways.

**Accessibility:** axe (WCAG 2 A/AA) found **no serious or critical issues** on sign-in, dashboard, validation, analysis, areas, findings, finding detail and report. These scans are part of the test.

## Verification

Web unit tests 38; typecheck; lint; build; CI green. The end-to-end test was run three times: the first two runs failed on my test's own selectors (an ambiguous label), which I fixed; the third passed. No app defects were found.

## Next

Phase 9, hardening: security and RLS re-test, performance against targets, dependency and secret scanning, retry and recovery tests, job reconciliation, report integrity, backups (scheduled `pg_dump`, restore test) and the "project paused" runbook.
