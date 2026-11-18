# ADR-0001: Modular monolith on Supabase + FastAPI

- Status: Accepted (18 Nov 2026)
- Source: master prompt v2, Section 4

## Decision

- **Frontend:** React + TypeScript + Vite, hosted on Vercel (Hobby).
- **System of record:** Supabase PostgreSQL with deny-by-default RLS on every tenant-owned table; Supabase Auth; private Supabase Storage.
- **API + worker:** one Python package (`services/api`). The same code runs as the HTTP API (FastAPI) and as the job worker (CLI entry point).
- **Contracts:** OpenAPI 3.1 in `packages/contracts` is the single source (ADR-0006).
- No Kubernetes, Kafka, Redis, microservices or blockchain.

## Who talks to whom

| Caller | Target | Credential | Protection |
|---|---|---|---|
| Browser | Supabase REST/Storage | user JWT (anon key) | RLS + storage policies |
| Browser | FastAPI | user JWT in `Authorization` | FastAPI verifies JWT, then checks membership/role in DB |
| FastAPI | Supabase DB/Storage | service-role (server only) | every query filters by verified `org_id`/`project_id`; tested |
| Worker (GitHub Actions) | Supabase DB/Storage | service-role via Actions secret | worker re-verifies job ownership before processing |

## Consequences

- Service-role bypasses RLS, so server code carries an explicit tenant filter and tests prove it.
- The analysis engine is plain Python with no AI dependency.
