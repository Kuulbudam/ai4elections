"""Apply supabase/migrations/*.sql in filename order, exactly once each.

    DATABASE_URL=... python scripts/migrate.py            # apply pending
    DATABASE_URL=... python scripts/migrate.py --dry-run  # list pending

Each file runs in its own transaction together with its bookkeeping row, so a failure leaves
nothing half-applied. An already-applied file whose content changed is an error (write a new
migration instead of editing an old one).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

BOOKKEEPING = """
CREATE SCHEMA IF NOT EXISTS app;
CREATE TABLE IF NOT EXISTS app.schema_migrations (
  filename   text PRIMARY KEY,
  sha256     text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    files = sorted(MIGRATIONS.glob("*.sql"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(BOOKKEEPING)
        applied = dict(conn.execute("SELECT filename, sha256 FROM app.schema_migrations").fetchall())
        pending = []
        for f in files:
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            if f.name in applied:
                if applied[f.name] != digest:
                    print(f"ERROR: {f.name} was changed after it was applied. Add a new migration instead.")
                    return 1
                continue
            pending.append((f, digest))
        print(f"{len(files)} migration file(s), {len(pending)} pending")
        for f, digest in pending:
            print(("would apply " if args.dry_run else "applying ") + f.name)
            if args.dry_run:
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute(
                    "INSERT INTO app.schema_migrations (filename, sha256) VALUES (%s, %s)", (f.name, digest)
                )
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
