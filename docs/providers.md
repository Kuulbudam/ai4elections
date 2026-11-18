# Provider checks

Checked **18 Nov 2026**. Re-check before each phase that creates a new resource. No payment method has been added anywhere.

| Provider | Plan | What matters for us | Payment method needed? | Source |
|---|---|---|---|---|
| Supabase | Free | ~500 MB database, 1 GB file storage, 2 active projects, **no backups**, project **pauses after 7 days with no database requests** | No | supabase.com/pricing (via 2026 summaries) |
| Supabase Auth | Free | Built-in email sender: **2 emails/hour per project**. Decision: email confirmation **off** on the demo project (founder, 18 Nov) | No | supabase.com/docs/guides/auth/rate-limits |
| Supabase Storage | Free | `createSignedUploadUrl` tokens are valid for **2 hours** and the expiry cannot be shortened. Compensation: server-generated path + one-time finalize (ADR-0002) | No | supabase.com/docs/reference/python/storage-from-createsigneduploadurl |
| Vercel | Hobby | Non-commercial use only. Python (FastAPI) functions with Fluid compute: **300 s default and maximum** duration | No | vercel.com/docs/functions/limitations |
| GitHub Actions | Free, **public repo** | Standard hosted runners **free and unmetered for public repos**; each job max **6 hours**; 20 concurrent jobs | No | docs.github.com/en/actions/reference/limits |
| Google Cloud Run | — | Not used (Option A rejected). Needs a billing account with a payment method | Yes | cloud.google.com/run/pricing |

## Free-tier risks and controls

| Risk | Control |
|---|---|
| Supabase project paused before demo | Runbook step: open dashboard and resume 1 day before demo; CI touches DB only if secrets exist |
| No Supabase backups | Phase 9: scheduled `pg_dump` in GitHub Actions, short-retention private artifact, tested restore |
| 500 MB database cap | Size budget in Phase 1; demo data ≈ 12k rows × few versions |
| Sign-up email limit | Email confirmation disabled on demo project |
| GitHub Actions job start latency (~20–60 s) | Measured in the worker spike; UI shows queued state honestly |
