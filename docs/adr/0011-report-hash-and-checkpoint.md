# ADR-0011: What the report content hash covers

- Status: Accepted (19 Nov 2026). Refines ADR-0005.

## Context

The master prompt asks for a report content hash that excludes the generation timestamp and report ID, "so that
regenerating from the same inputs gives the same hash", and for an audit checkpoint (ID and hash of the latest audit
event) in every report. The audit chain is **per organisation**: any activity in the organisation, including creating
the previous report, adds an event. If the checkpoint were inside the hashed body, two reports from identical inputs
would almost never have the same hash.

## Decision

- The hashed body (`reports.body`) contains everything a reader needs to check the work: project, dataset version
  and SHA-256, source, analysis run (versions, parameters, run content hash), methodology text, validation summary,
  every finding of the run with evidence (source row number and values or flagged cells), each finding's review
  status and full review history, claims with their labels and evidence, and limitations. Findings, reviews and claims
  are in a fixed order.
- Stored next to the body and shown on every report, but **not hashed**: report id, generated_at, generated_by, and the
  audit checkpoint (`audit_event_id`, `audit_event_hash`), taken under the organisation's audit lock just before the
  report is inserted.
- A new review decision or claim changes the body, so it changes the hash (tested).
- Reports are append-only (no UPDATE/DELETE/TRUNCATE, even for the table owner). No AI is used to build or export them.

## Exports

CSV (one row per finding, report metadata as `#` lines) and Markdown (full narrative; data-quality findings counted
by rule, listed in full in the CSV). Text cells starting with `=`, `+`, `-`, `@`, tab or CR get a leading `'`; numbers
are written as numbers. Markdown also escapes `|`, `<` and `>`. Both carry `X-Report-Content-Hash`.
