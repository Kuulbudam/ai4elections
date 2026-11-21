# Phase 14 — Escalation: completion report

**Date:** 21 Nov 2026 **Result:** PASS. **Branch:** `phase-14-escalation`.

| Area | What was done | Evidence |
|---|---|---|
| Review wording | The review decision reads **Escalate** (stored value unchanged) | Web tests |
| Escalation | Escalating a finding raises an escalation sent to the first level above the reviewer that has coordinators for the area, else to the admins | 6 DB tests; live check |
| Resolve / send up | People at the current level resolve with a note, or send it up one level; admins can always resolve; it resolves once | DB unique index; tests |
| Inbox | Bell with unread count; notifications for raised, sent up and resolved; a user reads and marks only their own | RLS tests; live check |
| Records | `escalations`, `escalation_events`, `notifications` append-only (read time is the only change allowed) and audited | DB triggers; tests |

Live checks on the real Supabase project: 24/24.
