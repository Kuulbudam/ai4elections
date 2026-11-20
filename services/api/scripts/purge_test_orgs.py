"""Remove organisations created by automated checks, to keep the free database under its 500 MB limit.

Only organisations whose name matches TEST_NAME ("ZZ CI ..." from the live checks, "E2E <run id>" from the
browser test) can ever be selected; anything else is refused. Real organisations are never touched.

Usage:
  python scripts/purge_test_orgs.py --dry-run            # count only (default)
  python scripts/purge_test_orgs.py --yes                # delete every test organisation, then compact
  python scripts/purge_test_orgs.py --yes --name "E2E 123" --no-vacuum   # one org (used after each test run)

The platform's tables are append-only (deletes are refused by triggers), so the deletion runs with triggers
switched off for this session only, as the database owner. Uploaded files of those organisations are removed
from Storage too. Needs DATABASE_URL; SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for the files.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

TEST_NAME = re.compile(r"^(ZZ CI .+|E2E \d+)$")
BIG = ["raw_rows", "result_records", "findings", "validation_issues", "audit_events", "processing_jobs"]


def test_orgs(conn: psycopg.Connection, name: str | None) -> list[dict]:
    rows = conn.execute("SELECT id::text AS id, name FROM public.organisations ORDER BY created_at").fetchall()
    chosen = [r for r in rows if TEST_NAME.match(r["name"]) and (name is None or r["name"] == name)]
    if name is not None and not chosen:
        print(f"No test organisation named {name!r}.")
    return chosen


def org_tables(conn: psycopg.Connection) -> list[str]:
    return [r["table_name"] for r in conn.execute(
        """SELECT c.table_name FROM information_schema.columns c
           JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
           WHERE c.table_schema = 'public' AND c.column_name = 'org_id' AND t.table_type = 'BASE TABLE'
           ORDER BY c.table_name""").fetchall()]  # fmt: skip


def count(conn: psycopg.Connection, tables: list[str], ids: list[str]) -> dict[str, int]:
    out = {}
    for t in tables:
        out[t] = conn.execute(
            sql.SQL("SELECT count(*) AS n FROM public.{} WHERE org_id = ANY(%s::uuid[])").format(sql.Identifier(t)),
            (ids,),
        ).fetchone()["n"]
    out["organisations"] = len(ids)
    return out


def delete_files(ids: list[str]) -> int:
    url, key = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("Storage keys not set; uploaded files were left in place.")
        return 0
    headers = {"apikey": key, **({"Authorization": f"Bearer {key}"} if key.startswith("eyJ") else {})}
    removed = 0
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as c:
        objs = c.execute(
            "SELECT bucket_id, name FROM storage.objects WHERE (storage.foldername(name))[1] = ANY(%s)", (ids,)
        ).fetchall()
    by_bucket: dict[str, list[str]] = {}
    for o in objs:
        by_bucket.setdefault(o["bucket_id"], []).append(o["name"])
    for bucket, names in by_bucket.items():
        for i in range(0, len(names), 500):
            r = httpx.request("DELETE", f"{url}/storage/v1/object/{bucket}", headers=headers,
                              json={"prefixes": names[i:i + 500]}, timeout=60)  # fmt: skip
            r.raise_for_status()
            removed += len(r.json())
    return removed


def purge(conn: psycopg.Connection, ids: list[str], tables: list[str]) -> None:
    with conn.transaction():
        conn.execute("SET LOCAL transaction_read_only = off")
        conn.execute("SET LOCAL session_replication_role = replica")  # triggers and FK checks off, this transaction
        conn.execute("SET LOCAL statement_timeout = 0")
        for t in [*tables, "organisations"]:
            col = "id" if t == "organisations" else "org_id"
            n = conn.execute(
                sql.SQL("DELETE FROM public.{} WHERE {} = ANY(%s::uuid[])").format(sql.Identifier(t), sql.Identifier(col)),
                (ids,),
            ).rowcount  # fmt: skip
            if n:
                print(f"  deleted {n:>9,} from {t}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="really delete (otherwise only count)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--name", help="only this test organisation (exact name)")
    ap.add_argument("--no-vacuum", action="store_true", help="skip compacting (for one small org)")
    ap.add_argument("--batch", type=int, default=25, help="organisations per transaction")
    a = ap.parse_args()
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        size = lambda: conn.execute("SELECT pg_size_pretty(pg_database_size(current_database())) s").fetchone()["s"]  # noqa: E731
        print("Database size:", size())
        orgs = test_orgs(conn, a.name)
        tables = org_tables(conn)
        ids = [o["id"] for o in orgs]
        keep = conn.execute("SELECT name FROM public.organisations WHERE NOT (id = ANY(%s::uuid[]))", (ids,)).fetchall()
        print(f"Test organisations selected: {len(orgs)}; kept: {', '.join(k['name'] for k in keep) or 'none'}")
        if not ids:
            return 0
        totals = count(conn, tables, ids)
        print("Rows that would be removed:", {k: v for k, v in totals.items() if v})
        if not a.yes or a.dry_run:
            with conn.transaction(force_rollback=True):
                try:
                    conn.execute("SET LOCAL session_replication_role = replica")
                    print("Triggers can be switched off for the deletion: yes")
                except psycopg.Error as e:
                    print("Triggers can be switched off for the deletion: NO -", str(e).splitlines()[0])
            print("Dry run: nothing deleted.")
            return 0
        t0 = time.monotonic()
        files = delete_files(ids)
        print(f"Removed {files} stored files")
        for i in range(0, len(ids), a.batch):
            print(f"Organisations {i + 1}-{min(i + a.batch, len(ids))} of {len(ids)}", flush=True)
            purge(conn, ids[i : i + a.batch], tables)
        print(f"Deleted in {time.monotonic() - t0:.0f}s")
        if not a.no_vacuum:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
            for t in BIG:
                t1 = time.monotonic()
                conn.execute(sql.SQL("VACUUM (FULL, ANALYZE) public.{}").format(sql.Identifier(t)))
                print(f"  compacted {t} in {time.monotonic() - t1:.0f}s", flush=True)
            conn.execute("VACUUM ANALYZE")
        print("Database size now:", size())
    return 0


if __name__ == "__main__":
    sys.exit(main())
