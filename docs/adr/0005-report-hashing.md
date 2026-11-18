# ADR-0005: Reproducible report and audit hashing

- Status: Accepted (18 Nov 2026)

## Report content hash

`content_hash = SHA-256( canonical_json(report_body) )` where:

- `report_body` excludes `report_id`, `generated_at` and `generated_by` (stored alongside, not hashed).
- `canonical_json`: UTF-8, keys sorted, no insignificant whitespace (`separators=(",", ":")`), lists in a defined order (findings by `finding_id`), decimals rendered as strings with fixed places (rates: 6 dp), integers as integers, no NaN/Infinity (use `null` + reason).

Same inputs (dataset version, mapping, rule versions, method version, reviews up to the checkpoint) ⇒ same hash.

## Audit chain (as implemented in Phase 1)

Each `audit_events` row has a per-organisation `seq` (1, 2, 3, …), `prev_hash` (the previous event's hash in the same organisation) and

`event_hash = SHA-256( prev_hash | org_id | seq | actor_id | action | entity_type | entity_id | created_at (UTC, microseconds) | details::jsonb-text )`

computed inside PostgreSQL (`app.audit_hash`). Appends are serialised per organisation with an advisory lock, so the chain never forks (tested with 12 concurrent writers). `details` uses PostgreSQL's deterministic `jsonb` text form. `app.audit_chain_first_break(org)` recomputes the chain and returns the first broken `seq` (tested by simulating a superuser tamper). Members call `public.audit_chain_status(org)`.

A report's **audit checkpoint** is the `id` and `event_hash` of the latest audit event included.

## Exports

CSV/Markdown: text cells beginning with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'`; numbers are written as numbers.
