# Phase 16 — Field app: completion report

**Date:** 21 Nov 2026 **Result:** PASS. **Branch:** `phase-16-field`.

Observers at polling units report from a phone. It follows the PRVT method used by citizen observer groups; it does
not touch voters' biometrics (that is INEC's BVAS).

| Area | What was done | Evidence |
|---|---|---|
| Reports | Polls opened, accreditation count (from the BVAS screen), incident, result sheet, closing. Strict forms; the phone's time and location are kept | 5 DB tests |
| Own unit only | Field agents report only for their assigned polling units and see only their own reports (database and API) | DB tests; live check |
| Poor signal | Reports are saved on the phone first and sent when there is signal. Each has an ID made on the phone, so a resend is stored once | Web tests; live check (repeat returns the same report) |
| Incidents | A critical incident goes straight to the area coordinator | DB test; live check |
| Result sheets | Photo → AI reads → agent confirms → arithmetic checks, with no results file needed. A sheet that fails a check is escalated | DB test |
| Dashboard | Field agents see "Report from your polling unit" instead of the analyst to-do list | Web test |

Live checks on the real Supabase project: 32/32. Local: 571 API and database tests, 60 web tests.
