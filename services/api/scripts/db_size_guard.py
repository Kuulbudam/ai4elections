"""Stop the live checks before the free database gets near its 500 MB limit (Supabase locks it read-only there)."""

import os
import sys

import psycopg

LIMIT_MB = int(os.environ.get("DB_SIZE_GUARD_MB", "350"))

with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20) as c:
    mb = c.execute("SELECT pg_database_size(current_database()) / 1048576").fetchone()[0]
print(f"Database size: {mb} MB (guard {LIMIT_MB} MB, Supabase Free limit 500 MB)")
if mb > LIMIT_MB:
    print("Too close to the limit: live checks skipped. Run scripts/purge_test_orgs.py --yes (see docs/runbook.md).")
    sys.exit(1)
