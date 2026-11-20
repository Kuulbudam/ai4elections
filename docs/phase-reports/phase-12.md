# Phase 12 — Hardening: completion report

**Date:** 20 Nov 2026 **Result:** PASS. **PRs:** #46, #48, #49.

| Area | What was done | Evidence |
|---|---|---|
| Security re-test | Live isolation check extended to result sheets and collation checks | 8 users × 9 tables: own organisation visible, other never; 18/18 |
| Security headers | Website: strict Content-Security-Policy, HSTS, Permissions-Policy, COOP. API: no-sniff, deny framing, deny-all CSP, `no-store` | Unit test; deployed end-to-end test passes with the policy |
| Dependencies | `pip-audit` added to CI (npm audit already there). Found 5 Starlette advisories; upgraded to FastAPI 0.165 / Starlette 1.10 | 537 API tests pass; audit clean |
| Performance | 12,023 rows validated in under 30 s and analysed in under 60 s, first run included, now enforced | 6.9 s; 37 s cold / 4–5 s warm |
| Recovery | Stuck jobs re-queued by the 15-minute sweep (existing ledger tests: killed worker, lost ownership, retries) | 10 ledger tests |
| Backups | Weekly backup and restore into a database built from the migrations; tables, row counts, all 63 foreign keys and every audit chain verified | 7/7 on production |
| Runbook | `docs/runbook.md` | — |

**Incident, 20 Nov.** The live checks left a 12,000-row test organisation behind on every run; the free database
passed 500 MB and became read-only; the cleanup then filled the disk and the project stopped. With the founder's
approval the platform moved to a new Supabase project. Every check now removes its own test data (waiting for its
jobs, then sweeping orphan rows), and a size guard stops the checks at 350 MB. The first backup-and-restore run then
caught one orphan row left by a cleanup race, which led to the job-wait and sweep.
