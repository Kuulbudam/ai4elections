# Phase 10 — Collation check: completion report

**Date:** 20 Nov 2026 **Result:** PASS. **PRs:** #44.

An analyst uploads the totals declared at ward, LGA or state collation (Forms EC8B, EC8C, EC8D typed into CSV). For
every area the platform adds up the same figures over that area's polling units in the uploaded results and compares
them. A figure is compared only when every polling unit has it (otherwise it is "not checked", with the number of
units missing it), and a repeated polling-unit code counts once, so an incomplete file never looks like a mismatch.
Every area that differs becomes a `cross_check` finding (severity high when a vote figure is off by 100 votes or 1%).
No AI. Checks are append-only and audited.

| Check | Result |
|---|---|
| Engine tests (aliases, levels, incomplete, duplicates, unmatched, severity) | 10 pass |
| Database tests (planted differences found exactly; permissions; immutability; findings in run queue and reports) | 3 pass |
| Web tests | 2 pass |
| End-to-end on the deployed site | PASS: the three planted differences (Ward 2 PA +120, Ward 3 valid −40, LGA total votes cast +35) found, linked to their findings |
