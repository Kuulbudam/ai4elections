# ADR-0004: Geography-free MVP

- Status: Accepted (18 Nov 2026)

## Context

Development data is synthetic, with fictional states, LGAs and wards. Real boundary files cannot be joined to fictional names, and the specification forbids inventing boundaries or presenting synthetic coordinates as real.

## Decision

- MVP shows state / LGA / ward **aggregates as tables and charts**, with filters and comparisons by administrative label.
- **No maps** in the MVP. PostGIS is not enabled yet.
- Any synthetic coordinates are labelled "SYNTHETIC — not a real location" wherever shown.

## Later

Before any real-data work: pick a boundary source, verify its licence, load into PostGIS, and record source + licence in a new ADR.
