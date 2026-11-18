# ADR-0006: Contract-first API with generated types

- Status: Accepted (18 Nov 2026)

## Decision

- `packages/contracts/openapi.yaml` (OpenAPI 3.1) is written by hand and reviewed. It is the single source.
- TypeScript types: generated with `openapi-typescript` into `apps/web/src/api/schema.d.ts`.
- Python models: generated with `datamodel-code-generator` into `services/api/app/contracts/models.py`. FastAPI endpoints use these models.
- CI regenerates both and fails if the committed files differ (`--check`).
- CI also validates the spec itself (`openapi-spec-validator`).

## Consequences

Changing an API shape means editing `openapi.yaml` first, then regenerating. Hand-written duplicate schemas are not allowed.
