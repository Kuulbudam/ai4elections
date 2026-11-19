# ADR-0008: Findings, review and claims — which path reads and writes take

- Status: Accepted (19 Nov 2026)

## Decision

- **API (FastAPI)** for the operations in the master prompt's endpoint list and for anything that needs several checks:
  `GET /findings` (filters, cursor pagination, original cells as evidence), `GET /findings/{id}`,
  `POST /findings/{id}/reviews`, `POST /claims` (claim and its evidence in one transaction),
  `POST /claims/{id}/verify`. Each checks membership (404) and role (403) itself.
- **Data API (Supabase, RLS)** for simple reads and single-row inserts that RLS fully covers: review history,
  investigation notes (insert and read), claim evidence added later, audit events, profiles.
- The database enforces the rules either way: reviews, notes, claim evidence, findings and audit events are
  append-only (triggers refuse UPDATE/DELETE/TRUNCATE even for the table owner); a claim can only be created as an
  unverified allegation; it can only become a verified fact once, by a different person, with a reason, and with at
  least one evidence item attached (trigger added in migration 1000); two-step verification is required on every table.
- `public.finding_overview` (security_invoker) gives each finding its current review status (latest decision) and
  note / claim counts.

## File evidence

For a file, the browser computes its SHA-256 and only the fingerprint and description are stored. The file stays
with the person who holds it; anyone can later prove a copy is the same file. Storing the files themselves is left
for later (it needs a second private bucket and virus-scanning considerations).

## Contract changes

`GET /findings/{id}` added. The `Finding` schema gained `note_count`, `claim_count`, `statistics.direction`,
`statistics.leading_party`, `evidence.flagged_columns`, and `exclusions.pu_code` / `rules`, to match what the
analysis engine stores.
