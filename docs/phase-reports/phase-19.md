# Phase 19 — AI summary at the end of Findings: completion report

**Date:** 21 Nov 2026 **Result:** see below. **Branch:** `phase-19-summary`.

| Area | What was done | Evidence |
|---|---|---|
| Counts first | The server counts the findings people see (by severity, kind and review), the areas with the most, the five most serious, open escalations, unverified claims and field incidents | DB test (count matches the Findings list) |
| AI words them | 4 to 6 plain sentences in English, Hausa, Yoruba, Igbo or Pidgin, labelled "Written by AI from the counts on this page" | Web tests |
| Every number checked | Each number in the text must appear in the counts. If not, the AI is asked once more; if it still fails, the summary is shown with a warning | DB test |
| Wording guard | No fraud or rigging wording (all five languages); otherwise a fixed, safe sentence | DB test |
| Kept and dated | Each summary is stored with the counts it was written from; it shows "The findings have changed since this was written" when they change | DB test |
| Who | Admins, analysts, researchers and coordinators write; viewers read; field agents do not see it | DB test |
