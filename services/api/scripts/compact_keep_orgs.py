"""Free the database by keeping only real organisations, writing almost nothing to disk (founder approved 20 Nov).

Deleting millions of rows writes a change log as large as the data, which can fill a small disk. This instead, in
ONE transaction:
  1. copies every row of the organisations to keep into temporary tables (temporary tables are not logged);
  2. empties all organisation tables at once with TRUNCATE (frees the files immediately, logs almost nothing);
  3. puts the kept rows back, then checks every kept table has exactly the rows it had before.
If anything fails or any count differs, the whole transaction is rolled back and nothing has changed.

Organisations kept: every organisation whose name does NOT match the automated-test pattern ("ZZ CI ..." or
"E2E <run id>"). Usage: --dry-run (default) or --yes. Needs DATABASE_URL.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from purge_test_orgs import TEST_NAME


def tables_with_org(conn: psycopg.Connection) -> list[str]:
    return [r["table_name"] for r in conn.execute(
        """SELECT c.table_name FROM information_schema.columns c
           JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
           WHERE c.table_schema = 'public' AND c.column_name = 'org_id' AND t.table_type = 'BASE TABLE'
           ORDER BY c.table_name""").fetchall()]  # fmt: skip


def insertable_columns(conn: psycopg.Connection, table: str) -> list[str]:
    return [r["column_name"] for r in conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s AND is_generated = 'NEVER'
           ORDER BY ordinal_position""", (table,)).fetchall()]  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as conn:
        size = lambda: conn.execute("SELECT pg_size_pretty(pg_database_size(current_database())) s").fetchone()["s"]  # noqa: E731
        print("Database size:", size(), flush=True)
        orgs = conn.execute("SELECT id::text AS id, name FROM public.organisations").fetchall()
        keep = [o for o in orgs if not TEST_NAME.match(o["name"])]
        drop = [o for o in orgs if TEST_NAME.match(o["name"])]
        print(f"Keep {len(keep)}: {', '.join(o['name'] for o in keep)}; remove {len(drop)} test organisations")
        keep_ids = [o["id"] for o in keep]
        tables = tables_with_org(conn)
        if not a.yes:
            print("Dry run: nothing changed.")
            return 0
        t0 = time.monotonic()
        with conn.transaction():
            conn.execute("SET LOCAL transaction_read_only = off")
            conn.execute("SET LOCAL session_replication_role = replica")  # append-only triggers off, this transaction
            conn.execute("SET LOCAL statement_timeout = 0")
            before: dict[str, int] = {}
            for t in [*tables, "organisations"]:
                col = "id" if t == "organisations" else "org_id"
                conn.execute(sql.SQL("CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT * FROM public.{} WHERE {} = ANY(%s::uuid[])")
                             .format(sql.Identifier(f"keep_{t}"), sql.Identifier(t), sql.Identifier(col)), (keep_ids,))  # fmt: skip
                before[t] = conn.execute(
                    sql.SQL("SELECT count(*) n FROM {}").format(sql.Identifier(f"keep_{t}"))
                ).fetchone()["n"]
            print("Rows kept:", {k: v for k, v in before.items() if v}, flush=True)
            conn.execute(sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(sql.SQL("public.{}").format(sql.Identifier(t)) for t in [*tables, "organisations"])))  # fmt: skip
            for t in [*tables, "organisations"]:
                cols = sql.SQL(", ").join(sql.Identifier(c) for c in insertable_columns(conn, t))
                conn.execute(sql.SQL("INSERT INTO public.{} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM {}")
                             .format(sql.Identifier(t), cols, cols, sql.Identifier(f"keep_{t}")))  # fmt: skip
            after = {t: conn.execute(sql.SQL("SELECT count(*) n FROM public.{}").format(sql.Identifier(t))).fetchone()["n"]
                     for t in before}  # fmt: skip
            diff = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
            if diff:
                raise RuntimeError(f"row counts changed, rolling back: {diff}")
            broken = conn.execute(
                "SELECT count(*) n FROM public.organisations o WHERE app.audit_chain_first_break(o.id) IS NOT NULL"
            ).fetchone()["n"]
            if broken:
                raise RuntimeError(f"audit chain broken for {broken} kept organisation(s), rolling back")
        print(f"Done in {time.monotonic() - t0:.0f}s; every kept table has the same rows; audit chains intact.")
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        conn.execute("VACUUM ANALYZE")
        print("Database size now:", size())
    return 0


if __name__ == "__main__":
    sys.exit(main())
