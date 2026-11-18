# ADR-0007: Real 2023 data in the demo (CCIJ/VEZA, Plateau) and ruleset 1.1.0

- Status: Accepted (18 Nov 2026)
- Decided by: founder (real data in the demo; Plateau State; use with attribution while CCIJ is asked to confirm terms)

## Context

The master prompt asked for synthetic data only. The founder changed this: the demo should show real
results. The most complete public polling-unit dataset for the 2023 presidential election is the
Centre for Collaborative Investigative Journalism (CCIJ) and VEZA analysis,
<https://github.com/anndawn/Nigeria-2023-election>. Its figures were read by OCR from INEC IReV
result sheets, with registered voters from the INEC register and accreditation from BVAS data
synced to IReV.

The source repository has no licence file. The founder has written to CCIJ to confirm terms.

## Decision

1. **No CCIJ data is committed.** `tools/real_data/prepare_ccij.py` downloads the two source files
   at run time, pinned to commit `e38e3bc9…`, and writes an upload-ready CSV plus a reference list
   into `data/real/` (git-ignored). Every output carries the attribution text and the SHA-256 of
   the source files.
2. **Nothing is corrected or filled in.** Blank cells stay blank (an unreadable sheet becomes a V003
   "missing value", never zero). The only change is `266.0` → `266`.
3. **Column meanings** (documented in the output's `_SOURCE.txt`): `total_use` is used as Valid
   Votes (the source's best reading of the sheet total); only APC, PDP, LP and NNPP were extracted;
   there is no rejected-votes or total-cast column.
4. **Ruleset 1.1.0 / canonical schema 1.1.0** so the platform accepts files like this one:
   - `rejected_votes` and `total_cast` become optional. Rules that need them run only when mapped.
     When total cast is not mapped, V007 compares valid votes (plus rejected, if mapped) with
     accredited voters.
   - New mapping flag `party_columns_complete` (default `true`). When `false`, V008 checks that the
     listed parties' votes do not exceed valid votes, instead of an exact match.
   - Mappings with `schema_version: "1.0.0"` are still accepted and behave exactly as before.
   - Stored issues keep the ruleset version they were produced with; the validation page shows the
     rule texts of that version.

## Language

Flags on this data mean **"this figure needs checking against the source sheet"**. They do not
mean fraud, rigging or misconduct. OCR misreads, blurred sheets and wrong uploads explain many of
them, and the source's own `Source Status` and `Source Document` columns let a reviewer open the
sheet. Reports and the demo script must say this.

## Consequences

- If CCIJ does not agree, the demo falls back to synthetic data with no code change.
- A live CI check (`live_real_data_check.py`) runs the Plateau file through the deployed storage,
  database and worker and confirms the stored summary equals the engine's local result.
