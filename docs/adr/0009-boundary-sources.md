# ADR-0009: Boundary sources for maps after the MVP (assessment)

- Status: Proposed — for post-MVP use. The MVP stays map-free (ADR-0004).
- Date: 19 Nov 2026

## Why

Phase 6 shows state / LGA / ward totals as tables and charts only. A map would need real boundary polygons
whose names join to the data's labels, and a licence that allows reuse. Nothing here is loaded yet.

## Candidates

| Source | Levels | Coverage | Licence (as published) | Notes |
|---|---|---|---|---|
| OCHA / HDX COD-AB Nigeria (`cod-ab-nga`), with OSGOF | admin 0–3 (state, LGA, ward-level admin 3) | national | HDX lists the Nigeria administrative boundaries under **CC BY 4.0** | Common operational dataset; P-codes for joins; last reviewed Oct 2024. |
| GRID3 NGA Operational Wards v3.0 (CIESIN, Columbia) | ward | **24 states** (July 2026), Plateau **not** in the list | v2.0 release notes cite **CC BY-SA 4.0** (share-alike) | "Operational", not validated by government; names harmonised. Share-alike affects any derived boundary files we publish. |
| Older GRID3 per-state operational wards (open.africa, 2021) | ward | some states incl. Plateau | licence field empty on the mirror | Do not use until the licence is confirmed with GRID3. |
| Polling-unit coordinates in the data (e.g. CCIJ/VEZA lat/lng) | point | where present | inherits the data source's terms | Points, not boundaries; can be shown as dots only for real data, never for synthetic data. |

## Recommendation (post-MVP)

1. LGA and state maps from **HDX COD-AB** (CC BY 4.0, national, P-coded).
2. Ward maps from **GRID3 v3+** where the state is covered, keeping the CC BY-SA terms and the "operational" caveat on screen; otherwise fall back to LGA.
3. Join on a curated name-to-P-code table per project, never on fuzzy matching at display time; units that do not join are listed, not guessed.
4. Load into PostGIS in a separate migration; record the exact version, download date, SHA-256 and licence in a follow-up ADR before any map ships.
5. Synthetic projects never get maps or coordinates presented as real (master prompt 5.8).

## Must verify before use

Licence text on the actual download page for the version we load; attribution wording; whether admin-3 in
COD-AB matches INEC registration areas (wards) for the states we use.
