# Design: field network, hierarchy, escalation, voice, AI summaries

Status: approved scope (21 Nov 2026). Phases 14 to 19, each tested live before the next.

## Goals

1. Election day in real time: an observer at each polling unit (PU) reports what they see, from a phone, even with
   poor signal, and the result sheet as soon as it is posted.
2. A chain of responsibility: PU agent → ward → LGA → state coordinator → admin. Each level sees its own area and
   resolves what reaches it; anything it cannot resolve goes up one level.
3. Evidence the organisation can hand over: a signed, tamper-evident submission pack for INEC or a tribunal.
4. Easier use: ask the data by voice, and a written summary at the end of the findings.

## Non-goals (and why)

- **No voter biometrics or voter accreditation.** That is INEC's legal role (BVAS); an observer tool must not
  collect voters' fingerprints or faces. Agents record the accreditation count the BVAS shows.
- **No pushing data into INEC systems.** There is no public interface, and doing so without permission would be
  unauthorised. "Send to INEC" means a signed pack the organisation submits through official channels.
- **No INEC branding.** EaaS is an independent observer platform.

## Model (method)

Follows the Process and Results Verification for Transparency (PRVT) approach used by citizen observer groups:
trained observers at assigned polling units send structured reports (arrival, accreditation, critical incidents,
results, closing) in near real time, verified and analysed centrally.

## Data

| Table | What | Rules |
|---|---|---|
| `memberships.role` | adds `coordinator`, `field_agent` | |
| `area_assignments` | a member's area: level state / lga / ward / polling_unit + names | admin manages; audited |
| `field_reports` | one report from a PU: kind arrival / accreditation / incident / result / closing; payload; device time; received time; GPS; photo hash; `client_id` | append-only; unique `client_id` (offline retries are safe); agents only for their assigned PUs |
| `escalations` | an item raised for resolution: source (finding / field report), area, current level, status | status changes only through the API; every change is an `escalation_events` row; audited |
| `escalation_events` | raised / forwarded / resolved / comment, actor, note, level | append-only |
| `notifications` | per-user inbox: title, body, link, read time | a user reads and marks only their own |

## Escalation routing

Raised (review decision **Escalate**, or a critical incident) → the lowest level above the raiser whose area covers
the item and that has at least one coordinator; if none, the organisation's admins. Everyone at that level gets a
notification. A coordinator can **Resolve** (reason required; the raiser is notified) or **Send up** to the next
level. Admins are the top; only they can close an item no one below could resolve.

## Field app

A phone-first page for agents: pick the assigned PU, tap a report type, fill a short form, snap the EC8A. Reports are
written to an outbox on the phone first and sent when there is signal (retries are safe because of `client_id`).
The result sheet goes through the existing reader (AI reads, the agent confirms). Critical incidents escalate at
once.

## Live view

Coverage (PUs reported / assigned), incidents by severity, results rolled up by ward / LGA / state from confirmed
sheets, turnout, and the escalation queue. Refreshes every few seconds.

## Submission pack

A zip: results, incidents, escalations, findings summary, sheet photos, and `manifest.json` listing every file with
its SHA-256, signed with the organisation's key (Ed25519). A verify page checks a pack against its signature.

## Voice

Speak a question (Whisper speech-to-text through the AI provider; language hint from the chosen answer language) and
hear the answer read aloud (the device's own voices). Audio is not stored.

## AI summary of findings

At the end of the findings page: a short written summary built only from counts the API computes (by type, area,
how unusual, review status). Numbers are checked against those counts; labelled "Written by AI".

## Security

Same model as today: Postgres RLS by organisation, two-step sign-in, append-only evidence, hash-chained audit.
Agents see only their own reports and assigned PUs; coordinators see their organisation's data and act on their area.
