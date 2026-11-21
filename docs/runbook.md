# AI4Elections runbook

What runs where, the free-tier limits that matter, what to do when something goes wrong, and the demo-day checklist.
Keep this short enough to use under pressure.

## What runs where

| Part | Where | Notes |
|---|---|---|
| Website | Vercel project `ai4elections-4d9a` (https://ai4elections-4d9a.vercel.app) | Static React app. Built only when `main` changes `apps/web` or the API contract. |
| API | Vercel project `ai4elections` (https://ai4elections.vercel.app) | FastAPI, Python 3.13. Built only when `main` changes `services/api` or the contract. |
| Background jobs (ingest, validate, analyse) | GitHub Actions workflow **Worker** | Started by the API for each job; a safety sweep runs every 15 minutes and re-queues any job whose worker stopped. |
| Database, sign-in, file storage | Supabase (free plan) | Row-level security on every table; two-step sign-in required in the database itself. |
| AI (data assistant, sheet reader) | Groq (free plan): `openai/gpt-oss-120b` for questions, `qwen/qwen3.8-27b` for sheet photos. Backup: Google Gemini (free plan), a Flash model for both | Keys `LLM_API_KEY` (Groq) and `GEMINI_API_KEY` in Vercel (API project) and GitHub Actions secrets. When Groq is busy or out of quota, Gemini answers automatically. `AI_PROVIDER_ORDER=gemini,groq` swaps the order. Everything else works without either key. |

## Free-plan limits and what to do

| Limit | What you see | What to do |
|---|---|---|
| Vercel Hobby: about 100 deployments a day | "Deployment rate limited", or a merge that does not change the site | Wait for the limit to reset, then in the Vercel dashboard open the project → latest `main` deployment → **Redeploy**. An empty commit will not help: builds are skipped unless the app changed. |
| Groq: about 8,000 tokens a minute and 200,000 a day per model | Ask the data: "The assistant is busy (free-tier limit reached)". Sheet reader: "The sheet reader is busy …", with the limit named | Wait a minute. In a demo, leave about 30 seconds between questions and between sheet photos (one photo is 3,000–4,500 tokens). If the daily limit is reached, type the sheet figures in by hand: every check still works. |
| Supabase free: project pauses after 7 days without activity | Sign-in and every page fail | The Worker sweep every 15 minutes keeps it active. If it was paused, open the Supabase dashboard → **Restore project** (a few minutes), then run the **Database** workflow to confirm. |
| Supabase free: sign-in emails are limited (a few an hour) | Password-reset or sign-up emails do not arrive | Create demo accounts ahead of time. Email confirmation is off for this project. Use Google sign-in where possible. |
| Supabase free: 500 MB database | Uploads or analysis fail with a database error | Live checks create test organisations named "ZZ CI …". If space runs low, archive old dataset versions; ask before deleting anything. |

## Lessons from 20 November (database filled up)

The live checks used to leave a test organisation (12,000 rows) behind on every run. After many runs in one day
the free database passed 500 MB and Supabase made it read-only; deleting the test data then filled the 1 GB disk
and the database stopped, so the project was rebuilt in a new Supabase project (`enusesjccqsvuqkxkjsz`).
Since then:

- every **Database** run ends by removing its own "ZZ CI …" organisations, and every **End-to-end** run removes
  its own "E2E <run id>" organisation, even after a failure;
- a size guard stops the live checks at 350 MB;
- `scripts/purge_test_orgs.py` removes test organisations by hand (`--dry-run` first), and
  `scripts/compact_keep_orgs.py` is the low-disk recovery used on 20 Nov (keeps every real organisation, empties
  the rest in one checked transaction).

Do not start **End-to-end** while **Database** is running (or the reverse): the background worker started by one
can pick up the other's jobs and make a check report a false failure. Run them one after the other.

## When something goes wrong

**A job is stuck at "queued" or "running".** The sweep re-queues a job whose worker stopped after about a minute, and gives up after 3 attempts. To start the sweep now: GitHub → Actions → **Worker** → Run workflow (mode `drain`). If a job failed three times, the page shows its error. Fix the cause (for example a mapping problem), then run the step again.

**A page shows "This page hit a problem".** The page crashed, but your data is safe. Press **Try again** or go to the dashboard. Note the "Details" line and the page address, and report it.

**Ask the data or the sheet reader says the AI service is unavailable.** Both providers failed. Check `LLM_API_KEY` and `GEMINI_API_KEY` are still set in Vercel (API project → Settings → Environment Variables) and in GitHub secrets. Model lists change over time; the platform picks the first available model from its preference list for each provider. The **Database** workflow's live checks (including "Live Gemini backup check") say which provider or model is missing. Gemini's daily quota resets at midnight Pacific time (9 am in Nigeria).

**Uploads fail.** Check the file is CSV (UTF-8), under 25 MB, and that you are an admin or analyst. For result-sheet photos: JPEG, PNG or WebP under 4.5 MB.

**Someone was given the wrong role.** An admin changes it on the organisation's Members page. Every change is in the audit log.

## Keys and secrets

| Secret | Where | Rotate by |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_URL` | Vercel (API project), GitHub secrets | Supabase → Settings → API → roll the key, then update both places and redeploy the API. |
| `DATABASE_URL` | Vercel (API project), GitHub secrets | Supabase → Settings → Database → reset the password, update both places. |
| `LLM_API_KEY` | Vercel (API project), GitHub secrets | Groq console → API keys → create a new key, update both places, delete the old key. |
| `GEMINI_API_KEY` | Vercel (API project), GitHub secrets | Google AI Studio → Get API key → create a new key, update both places, delete the old key. |
| `GITHUB_DISPATCH_TOKEN` | Vercel (API project) | GitHub → Settings → Developer settings → fine-grained token with Actions write on this repository. |
| `BACKUP_PASSPHRASE` (optional) | GitHub secrets | Choose a long passphrase and keep it in a password manager. Without it, backups cannot be decrypted. |

Never paste a key into chat, an issue, a commit or a log. The secret scan in CI fails the build if one is committed.

## Backups

The **Backup and restore check** workflow runs every Sunday (and on demand). It dumps the database, restores it into an empty database built from the migrations, and checks that every table, foreign key, row count and audit chain matches. When `BACKUP_PASSPHRASE` is set it keeps an encrypted copy for 30 days. The repository is public, so an unencrypted copy is never uploaded.

To restore after a disaster:

1. Download the artifact from the workflow run.
2. `gpg -d ai4e-backup.tgz.gpg > ai4e-backup.tgz` (enter the passphrase), then `tar -xzf ai4e-backup.tgz`.
3. In a new Supabase project, apply the migrations with `python services/api/scripts/migrate.py` (`DATABASE_URL` set to the new project).
4. `pg_restore --data-only --disable-triggers --no-owner -d "$DATABASE_URL" ai4e-*.pgc`.
5. Users must be re-created in Supabase Auth with the same ids; `users.csv` lists them.

Uploaded CSV files and sheet photos live in Supabase Storage, which is not in the database dump. Every row of every CSV is also kept in the database (`raw_rows`), and each sheet photo's SHA-256 is recorded with it.

## Demo-day checklist

**The day before**
- [ ] GitHub → Actions → **End-to-end (deployed)** → Run workflow. It must pass.
- [ ] GitHub → Actions → **Database** → Run workflow. All live checks must pass, including the assistant and the sheet reader.
- [ ] Do not merge anything after this point.
- [ ] Sign in with the demo account and check the two-step code works on the phone you will use.
- [ ] Prepare the demo project: upload the data, map, validate, analyse; upload one result sheet and one collation file so there is something to show if the AI is busy.
- [ ] Record a 3-minute backup video of the full flow.

**One hour before**
- [ ] Open the site and sign in, which wakes the API.
- [ ] Ask one question in Ask the data, then wait a minute before the demo (the free AI tier resets every minute).
- [ ] Have the sample files ready: `data/synthetic/SYNTHETIC_sheet_mismatch_row2.png` and `data/synthetic/SYNTHETIC_declared_totals.csv` (also downloadable from the Collation check page).

**During the demo**
- [ ] Leave about 30 seconds between AI requests.
- [ ] If the AI is busy: show that the person can type the sheet figures, and that every check still runs.
- [ ] If the site is down: play the backup video.
