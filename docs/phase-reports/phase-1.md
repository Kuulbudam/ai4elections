# Phase 1 — Database and security: completion report

**Date:** 18 Nov 2026 **Result:** PASS — awaiting founder approval to start Phase 2.
**PR:** #12 (`phase-1-db-security` → `main`)

## Objective

Migrations, authentication, organisations and memberships, roles, RLS, audit infrastructure, and proof of tenant isolation.

## Implemented

- **Schema** (`supabase/migrations/20261119000100_schema.sql`): 22 tables — every Section 6 table plus `claims`, `claim_evidence`, `investigation_notes`, `mapping_templates`, `validation_rules`. Every tenant table has `org_id NOT NULL` and `UNIQUE (org_id, id)`; children use composite foreign keys, so a row can never link into another organisation (tested even for the service role).
- **Integrity** (`…000200_audit_and_integrity.sql`):
  - Hash-chained `audit_events` written by triggers on 14 tables (material actions only; job heartbeats excluded).
  - Append-only (no UPDATE/DELETE/TRUNCATE, even for the service role): `audit_events`, `finding_reviews`, `investigation_notes`, `claim_evidence`, `reports`, `raw_rows`.
  - Dataset versions never deleted; identity and SHA-256/size provenance immutable once set.
  - Claims: content fixed; `verified_fact` requires a verifier who is not the creator plus a reason.
  - Every organisation keeps at least one admin.
  - Profile row created automatically on sign-up.
  - RPCs: `create_organisation`, `add_member` (admin adds an existing user by email), `change_member_role`, `remove_member`, `set_dataset_version_archived` (admin, reversible), `audit_chain_status`.
- **Security** (`…000300_security.sql`): deny-by-default. `anon` has no table access. `authenticated` can read members-only rows and write only: projects (admin); mapping templates and admin units (admin, analyst); reviews (admin, analyst, as themselves); notes, allegations and evidence (admin, analyst, researcher, as themselves); own profile name. Everything else is server-only.
- **Storage** (`…000400_storage.sql`): private `datasets` bucket, 25 MiB limit, CSV types. Members read only their own organisation's folder; no browser uploads (server-issued signed URLs only).
- **Migration tool** `services/api/scripts/migrate.py`: applies each file once, in a transaction, with a checksum; refuses edited migrations.
- **FastAPI auth** `services/api/app/auth.py`: verifies Supabase JWTs (ES256/RS256 via the project's JWKS, cached; HS256 only if a secret is configured); checks expiry, audience, issuer and role; reads the caller's role from the database (`require_role`).
- **Web**: sign in / create account, organisations (list, create), members (admin: add by email, change role, remove; others read-only), profile (display name), sign out. Friendly error messages; no raw database errors shown.
- **Workflows**: `db.yml` (migrate the real project, then the live security check), CI migration job now uses the shim + real migration tool twice and asserts every table has RLS.

## Database migrations

`20261119000100_schema`, `20261119000200_audit_and_integrity`, `20261119000300_security`, `20261119000400_storage` — applied to the real Supabase project by Database workflow run 44367626272 (4 applied); re-run 44368401747 reported 0 pending.

## Tests executed and results

| Suite | Where | Result |
|---|---|---|
| `pytest -q` (API + ledger + auth + DB security) | local Postgres 16 + Supabase shim | **234 passed**, 0 failed |
| of which DB security/role tests (`tests/db`) | 2 orgs × 4 roles + outsider + anon | 189 passed |
| `npm test` (web) | jsdom | **12 passed**, 0 failed |
| lint, typecheck, contract sync, build | local + CI | pass |
| CI on PR #12 (run 44368310312) | GitHub | 4/4 jobs success |
| **Live security check** (run 44368401747) | **real Supabase Auth, Data API, Storage** | **15/15 PASS** |

Live check, in full: register + password sign-in for 8 users; profile trigger 8/8; orgs, members and projects created via Data API; 8 users × 7 tables own org visible and other org never; cross-org fetch by id returns nothing; anon key alone → HTTP 401; reviews: admin 201, analyst 201, researcher 403, viewer 403; cross-org review write 403; viewer note 403; review edit 403; own-org file download 200; other-org download 400; direct browser upload 400; audit chain intact (13 events).

DB test coverage: isolation for every tenant table and role, outsider sees nothing, anon denied on every table, direct-id lookups, profile visibility, storage read isolation, browser cannot write storage, cross-org inserts blocked by RLS, composite FKs block cross-org links for the service role, the full 5.1 role matrix (7 actions × 4 roles), allegation cannot be created as verified, creator cannot verify own claim, cannot review as someone else, review needs a reason, last admin protected, admins cannot manage other orgs, append-only enforced for 6 tables incl. TRUNCATE, dataset version provenance immutable, material actions audited with actor, tamper detected, chain single under 12 concurrent writers, every public table has RLS, private helpers not executable by users, heartbeats not audited.

## Security review

- No secrets in the repo; keys only in GitHub Actions secrets. Browser code uses the public key only.
- Service role bypasses RLS: composite FKs + append-only triggers still apply to it; server code must filter by verified org (enforced from Phase 2 endpoints, `require_role` ready and tested).
- `SECURITY DEFINER` functions all use `search_path = ''` and fully qualified names.
- Signed upload URLs last 2 h (Supabase fixed); compensated by server-generated paths and one-time finalize (Phase 2).
- API rate limits: not yet (no data endpoints yet); due in Phase 2 with the first endpoints.

## Deployment status

Real Supabase project migrated and verified. Web app and API not deployed yet (Vercel in Phase 2).

## Known limitations

- "Invitation" is add-by-email of an already-registered user (no email invite flow; email sending is limited on the free plan).
- Each live check run leaves two "ZZ CI isolation check …" organisations and 8 `ci-…@example.com` users in the project (append-only by design; invisible to other orgs via RLS). Recommendation: a second free Supabase project for CI later.
- Scheduled worker sweep still to be confirmed running.

## Cost and billing

$0. No payment method anywhere.

## Days to demo / critical path

8 Dec 2026 — 20 days left. Phase 1 finished on 18 Nov, ahead of the 19–21 Nov plan.

## Acceptance criteria and evidence

| Criterion | Evidence | Status |
|---|---|---|
| All migrations apply cleanly | CI from empty (twice, idempotent) + real Supabase run 44367626272 | PASS |
| Authentication works | Live check: 8 real sign-ups + password sign-ins; JWT verification tests | PASS |
| Role tests pass | 28 matrix tests locally + live review/note checks | PASS |
| Cross-tenant isolation proven | 189 DB tests + live 8 users × 7 tables + storage | PASS |

## Decisions needed from founder

Approve Phase 2 ("approved, continue"). Phase 2 will need a Vercel account (free Hobby, sign in with GitHub).

## Next phase

Phase 2 — Upload and ingestion (synthetic data generator, upload/finalize/mapping endpoints, ingestion jobs, API on Vercel).
