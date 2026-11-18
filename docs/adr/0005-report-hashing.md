# ADR-0005: Reproducible report and audit hashing

- Status: Accepted (18 Nov 2026)

## Report content hash

`content_hash = SHA-256( canonical_json(report_body) )` where:

- `report_body` excludes `report_id`, `generated_at` and `generated_by` (stored alongside, not hashed).
- `canonical_json`: UTF-8, keys sorted, no insignificant whitespace (`separators=(",", ":")`), lists in a defined order (findings by `finding_id`), decimals rendered as strings with fixed places (rates: 6 dp), integers as integers, no NaN/Infinity (use `null` + reason).

Same inputs (dataset version, mapping, rule versions, method version, reviews up to the checkpoint) ⇒ same hash.

## Audit chain

Each `audit_events` row stores `prev_hash` (hash of the previous event in the same organisation) and `event_hash = SHA-256(prev_hash || canonical_json(event_without_hashes))`. A report's **audit checkpoint** is the `id` and `event_hash` of the latest audit event included.

## Exports

CSV/Markdown: text cells beginning with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'`; numbers are written as numbers.
