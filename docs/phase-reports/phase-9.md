# Phase 9 — Result-sheet reader: completion report

**Date:** 20 Nov 2026 **Result:** PASS. **PRs:** #43.

An observer or analyst photographs a polling-unit result sheet (Form EC8A). The server re-encodes the photo as JPEG
(dropping location and camera data) and stores it privately. A vision model (Groq `qwen/qwen3.8-27b`) reads every
figure and says how sure it is of each. A person compares the figures with the photo, corrects any mistakes and
confirms. Only then does the platform check the sheet's own arithmetic (party scores add up to valid votes; valid plus
rejected equals votes cast; votes cast do not exceed accredited voters; accredited voters do not exceed registered
voters) and compare every figure with that polling unit's row in the uploaded results. Every mismatch becomes a
`cross_check` finding in the review queue and in reports. A sheet is confirmed once; afterwards it cannot change,
and both the upload and the confirmation are in the hash-chained audit log.

| Check | Result |
|---|---|
| Unit tests (photo intake, parsing untrusted model output, checks, rate-limit handling) | 21 pass |
| Database tests (flow, permissions, immutability, findings, reports) | 7 pass |
| Web tests (upload, confirm with corrections, viewer read-only) | 3 pass |
| Live check, real vision model + Storage | 12/12; 11/11 figures exact on a clean synthetic sheet and on a tilted, blurred, unevenly lit "phone photo" |
| End-to-end on the deployed site | PASS (upload, confirm, findings link, accessibility scans) |

Limits: tested on synthetic typed sheets, not real handwritten ones. The AI's reading is always a draft; nothing is
checked until a person confirms. With no AI key, or when the free tier is busy, the person types the figures in.
