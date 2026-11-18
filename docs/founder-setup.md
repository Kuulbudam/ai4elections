# Founder setup steps (Phase 0)

Only the steps that need your accounts. Everything else is done by the build agent. No payment method is needed for any step.

## 1. Let Claude push to the repo (blocking)

1. Open https://github.com/apps/claude/installations/select_target
2. Choose **Kuulbudam** → **Only select repositories** → add **ai4elections** → **Install** (or **Save**).

## 2. Create the Supabase project (blocking for the hosted spike)

1. Sign in at https://supabase.com with GitHub. Stay on the **Free** plan; do not add a card.
2. **New project** → name `ai4elections-demo` → set a strong database password (save it in a password manager, never in chat or GitHub) → region: the closest one offered to Nigeria (for example London / eu-west-2) → Create.
3. **Authentication → Sign In / Providers → Email** → turn **off** "Confirm email" → Save. (Free email sender allows only 2 emails per hour.)
4. Click **Connect** (top bar) → **Session pooler** → copy the URI. It looks like
   `postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Replace `[YOUR-PASSWORD]` with your password. Use the **Session pooler**, not "Direct connection" (direct is IPv6-only and GitHub runners cannot reach it).

## 3. Add the secret to GitHub (blocking for the hosted spike)

1. GitHub → `ai4elections` → **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `DATABASE_URL`. Value: the Session pooler URI from step 2.4. Save.

## 4. Run the hosted spike

GitHub → `ai4elections` → **Actions → Worker → Run workflow** → mode `spike`, duration `90` → Run. It takes about 4–5 minutes. Tell the agent when it finishes (or the agent runs it once it has access).

## 5. Protect `main` (recommended, 1 minute)

**Settings → Branches → Add branch ruleset** (or classic rule) for `main`: require a pull request and require status checks `API`, `Web`, `Migrations`, `Secret scan` to pass.

## Later (Phase 1/2, not needed now)

- Vercel: sign in with GitHub on the free Hobby plan and import `ai4elections` (the agent will give exact settings).
- A fine-grained GitHub token for the API to trigger the worker (the agent will give exact scopes).
