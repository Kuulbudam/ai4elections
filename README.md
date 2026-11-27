# AI4Elections

Electoral data validation, statistical anomaly detection and evidence management platform.

**Workflow:** UPLOAD → PRESERVE → VALIDATE → ANALYSE → INVESTIGATE → REVIEW → REPORT

> AI4Elections is **not** a voting, counting, transmission or certification system, and it never issues fraud verdicts.
> A statistical anomaly is a signal for human investigation, not proof of wrongdoing.
> All data in this repository is **synthetic** and fictional.

## #AI4Elections Hackathon submission (Track 2, also Tracks 3 and 5)

| Item | Where |
|---|---|
| Working prototype | https://ai4elections-4d9a.vercel.app (API: https://ai4elections.vercel.app) |
| Problem statement | [`docs/submission/PROBLEM_STATEMENT.md`](docs/submission/PROBLEM_STATEMENT.md) |
| Methodology and evidence | [`docs/submission/EVALUATION_REPORT.md`](docs/submission/EVALUATION_REPORT.md), [`docs/methodology.md`](docs/methodology.md) |
| Model / system card | [`docs/submission/MODEL_CARD.md`](docs/submission/MODEL_CARD.md) |
| Safety and limitations | [`docs/submission/SAFETY_AND_LIMITATIONS.md`](docs/submission/SAFETY_AND_LIMITATIONS.md) |
| Datasheet | [`docs/submission/DATASHEET.md`](docs/submission/DATASHEET.md) |
| How every calculation works | `docs/submission/EaaS-how-it-works.pdf` |

On the organisers' starter data (900 units, 42 planted problems) the rules find **42 of 42 with no false alarms**
(46.7 alerts per 1,000 records): `cd services/api && python scripts/eval_rise_track2.py`.

This prototype is not approved for operational electoral deployment.

Licence: code MIT (`LICENSE`); documentation and our synthetic data CC BY 4.0; `data/rise-starter/` is © Rise Networks, CC BY 4.0.

## Status

Current phase and next steps: [`docs/STATUS.md`](docs/STATUS.md).
Full specification: [`docs/master-prompt-v2.txt`](docs/master-prompt-v2.txt).
Architecture decisions: [`docs/adr/`](docs/adr/).

## Repository layout

| Path | What it holds |
|---|---|
| `apps/web` | React + TypeScript + Vite frontend (deployed to Vercel) |
| `services/api` | Python FastAPI service: API endpoints + job worker |
| `packages/contracts` | OpenAPI 3.1 contract and JSON schemas (single source of truth) |
| `supabase/migrations` | PostgreSQL migrations (schema, RLS) |
| `tools/synthetic` | Synthetic election dataset generator (Phase 2) |
| `docs` | Status, ADRs, provider checks, phase reports |

## Run the checks (what CI runs)

Requirements: Python 3.12+, Node 22+, PostgreSQL 16 (only for ledger tests).

```bash
# API
cd services/api
python -m pip install -r requirements-dev.txt
ruff check . && ruff format --check .
python scripts/gen_models.py --check        # contract sync (Pydantic)
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest

# Web
cd apps/web
npm ci
npm run gen:types:check                     # contract sync (TypeScript)
npm run lint && npm run typecheck && npm test && npm run build
```

## Secrets

Never commit secrets. Copy `.env.example` to `.env` locally (git-ignored). In the cloud, secrets live in GitHub Actions secrets and Vercel environment settings. The Supabase **service-role key is server-only** and must never appear in a `VITE_` variable.
