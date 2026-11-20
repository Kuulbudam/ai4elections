# Phase 11 — Answers in Hausa, Yoruba, Igbo and Nigerian Pidgin: completion report

**Date:** 20 Nov 2026 **Result:** PASS. **PRs:** #45, #47.

Ask the data now answers in English, Hausa, Yoruba, Igbo or Nigerian Pidgin (chosen on the page, remembered per
browser). Figures stay as digits and are still checked against what the lookups returned; codes, places and party
names stay as in the data. The wording guard also catches the local words for rigging, fraud and vote theft (after
removing accents and Hausa hooked letters). The question log records the language. Also: the assistant may never
add figures together into new totals (any language).

Live check (real model): Hausa answer "Jiha Norvale ce ke da mafi yawan alamomin … 15" — right state, right count,
grounded, in Hausa; Pidgin answer right and grounded. Hausa is the strongest of the four; Pidgin, Yoruba and Igbo
answers can mix in English. Every non-English answer is labelled "check the wording before sharing".

Found and fixed on the way: row-level security evaluated a function per row, which timed out on large tables. It now
looks up the user's organisations once per query (same rule; 9× faster on 200,000 visible rows).
