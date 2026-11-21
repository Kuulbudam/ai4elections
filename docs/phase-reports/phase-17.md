# Phase 17 — Live view and signed submission pack: completion report

**Date:** 21 Nov 2026 **Result:** PASS. **Branch:** `phase-17-live`.

| Area | What was done | Evidence |
|---|---|---|
| Live view | Polling units reporting, polls opened, results in, critical incidents, open escalations, votes from confirmed sheets, a table by LGA. Updates every 15 seconds; pauses when the tab is hidden | 2 DB tests; 2 web tests; live check |
| Counts only | Every number is a count of stored reports or a sum of figures a person confirmed. Labelled "Not official results" | Code review; tests |
| Submission pack | Zip: results by polling unit, field reports, incidents, escalations, summary, `manifest.json` (SHA-256 of every file), `manifest.sig` (Ed25519), `public_key.txt`, `VERIFY.txt` (how to check it offline with Python) | DB test; live check |
| Privacy | No sheet photos (names and signatures); each row carries the photo's SHA-256. Observers appear as a short reference, not by name | DB test |
| Checking a pack | Upload a pack: signature, signing key, every file's hash, no added files, and whether it was made here. Public key at `GET /signing-key` (no sign-in) | DB test (changed file, edited manifest, other key, added file, not a zip); live check |
| Records | `submission_packs` append-only and audited; field agents cannot see packs; zips in a private bucket | DB test |

Packs are handed over by the organisation itself. Nothing is sent into any INEC system.

Live checks on the real Supabase project: 35/35. The Gemini backup check failed that run because the newest Gemini
models had used up their daily free quota; fixed in Phase 18 (the backup now reaches 2.5 Flash and Flash-Lite).
