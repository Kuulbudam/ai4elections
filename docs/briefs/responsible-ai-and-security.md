# AI4Elections: responsible AI and security brief

For the judges of the #AI4Elections Hackathon (responsible AI and safety; cybersecurity and resilience).
Two pages. Every claim points to a test or document in the repository.

## What the platform does

Election observers, analysts and newsrooms upload polling-unit results. The platform checks the file, finds figures
that are unusual compared with similar polling units, reads photos of result sheets, compares declared collation
totals with their polling units, and helps a team review every flag until it is backed by evidence or explained.
It never decides what happened: a finding is a reason to look again at a sheet, not a conclusion.

## Where AI is used, and the limits on it

| Use | The AI may | The AI may not | Checked by |
|---|---|---|---|
| **Statistical outliers** | Not AI: robust median/MAD comparison with peer polling units, fixed thresholds, published method (`docs/methodology.md`) | — | Engine tests on planted scenarios; identical results on every rerun (content hash) |
| **Result-sheet reader** (vision model) | Copy the figures on a photo and say how sure it is of each | Calculate, correct or guess; nothing is checked until a **person confirms** every figure | Unit tests on hostile model output; live check: 11/11 figures exact on clean and rough photos; **real benchmark: 84% of figures exact on 20 real handwritten Plateau 2023 EC8A sheets** (see below) |
| **Ask the data** (language model) | Call six fixed, tested lookups for one project and summarise them, in English, Hausa, Yoruba, Igbo or Pidgin | Write SQL, see other projects, invent or add up figures, or use words that mean fraud or rigging in any of the five languages | Every number in an answer is matched to the lookups, otherwise the answer carries a warning. Accusing answers are replaced. Live check 13/13 |
| **Collation check** | Not AI | — | Planted differences found exactly |

Every AI answer is labelled as AI-drafted and "not a finding". Every question and answer is kept in an append-only
log, with the language and the lookups used, and the name of the model that answered. The platform works fully without
AI: with no key, or when the free tier is busy, people type sheet figures and everything else still runs.

**Two AI providers, both free.** Groq answers first; when it is busy, out of its daily quota or down, Google Gemini
answers instead, with the same checks on its output. The order can be swapped with one setting. **Data note:**
on Gemini's free tier, Google may use what is sent to improve its products, and its reviewers may read it. The
platform sends only what the task needs: project figures for Ask the data, and the sheet photo for the reader. Result
sheets are public documents (INEC publishes them on IReV), but they carry agents' names and signatures. An
organisation that cannot accept this should remove `GEMINI_API_KEY` (Groq only) or move to a paid tier.

## Wording and fairness

- Titles, explanations and reports cannot contain "fraud" or "rigged". The database refuses them.
- Allegations are recorded as **unverified allegations** until a different person verifies them, with evidence attached. The database enforces this.
- Every finding states its limitations: excluded units, small peer groups, and possible ordinary causes.
- No party, candidate or official is named as responsible, by design.

## Security

| Layer | Control | Evidence |
|---|---|---|
| Sign-in | Two-step verification (authenticator app) required for everyone, enforced in the database itself, not only in the screens | MFA tests on every table; live check |
| Access | Row-level security on every table: an organisation's data is invisible to all others; four roles (admin, analyst, researcher, viewer) | 8 users × 9 tables live isolation check, 18/18 |
| Integrity | Uploaded files and sheet photos are fingerprinted (SHA-256). Reviews, notes, sheets, collation checks and reports cannot be edited or deleted. A hash-chained audit log records every change and shows if anything was altered | Immutability tests; audit chain verified after restore |
| Reports | Immutable snapshots with a content hash and an audit checkpoint; CSV export safe against spreadsheet formula injection | Report tests |
| Privacy | Location and camera data are removed from sheet photos before storage; photos are private and reached only through 10-minute links; synthetic data in tests; no personal data in reports | Photo intake tests; live check |
| Web and API | Strict content security policy, HSTS, no framing, `no-store` on API responses, rate limits per user and per address | Header tests; deployed end-to-end test |
| Supply chain | Secret scanning on every commit; npm and Python dependency audits in CI (five Starlette advisories found and fixed) | CI |
| Resilience | Jobs survive a crashed worker (re-queued, at most 3 attempts); weekly backup restored and verified (tables, rows, foreign keys, audit chains); runbook for every free-plan limit | Ledger tests; backup check 7/7; `docs/runbook.md` |

## Known limits (stated honestly)

- **Real handwritten sheets (21 Nov 2026).** We ran the reader on 20 clear, real 2023 Plateau EC8A sheets from IReV and
  compared it with the public CCIJ/VEZA transcription. It got 84 of 100 figures exactly right (up from 74 before we
  improved the prompt), and 8 of 20 sheets fully right. Of the 16 figures that differed, the AI had marked 13 as
  unsure or left them blank, so a person was sent to check them. Three were confident misreads (for example 114 for
  14), which is why a person confirms every figure and the sheet's own sums are checked. On blurred sheets it said it
  could not read them rather than guessing. Sheet images were read in memory only and never stored. The benchmark is
  `services/api/scripts/real_sheet_benchmark.py` (GitHub Actions: Real sheet benchmark).
- Yoruba and Igbo answers can mix in English. Hausa and Pidgin pass our live language checks; Pidgin needed a
  speech pattern in the prompt to stop it drifting into English.
- Free hosting plans set the ceilings: about two sheet photos and a few questions a minute, and a 500 MB database.
  The runbook says what to do at each limit.
- On 20 Nov our own test data filled the free database. We rebuilt on a new project, and every check now removes its
  own data. That is recorded in `docs/phase-reports/phase-12.md`.
- The platform must not be pointed at live INEC systems without authorisation. It works only on files and photos
  that users bring.
