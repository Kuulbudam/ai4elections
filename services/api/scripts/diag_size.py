"""Read-only diagnostic: database size, read-only state, and which organisations hold the rows."""

import os

import psycopg
from psycopg.rows import dict_row

with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as c:
    print("default_transaction_read_only:", c.execute("SHOW default_transaction_read_only").fetchone())
    print("db size:", c.execute("SELECT pg_size_pretty(pg_database_size(current_database())) s").fetchone()["s"])
    for r in c.execute(
        """SELECT n.nspname || '.' || c.relname AS t, pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
                  pg_total_relation_size(c.oid) AS b
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'r' AND n.nspname IN ('public', 'app', 'auth', 'storage')
           ORDER BY b DESC LIMIT 15"""
    ).fetchall():
        print(f"{r['t']:45} {r['size']}")
    rows = c.execute(
        """SELECT o.name LIKE 'ZZ CI%%' AS ci, count(DISTINCT o.id) AS orgs, sum(x.n) AS raw_rows
           FROM public.organisations o
           LEFT JOIN (SELECT org_id, count(*) n FROM public.raw_rows GROUP BY org_id) x ON x.org_id = o.id
           GROUP BY 1"""
    ).fetchall()
    print("orgs by CI naming:", rows)
    for r in c.execute(
        """SELECT o.name, o.created_at::date AS d, coalesce(x.n, 0) AS raw_rows
           FROM public.organisations o LEFT JOIN (SELECT org_id, count(*) n FROM public.raw_rows GROUP BY org_id) x
             ON x.org_id = o.id
           WHERE o.name NOT LIKE 'ZZ CI%%' ORDER BY o.created_at"""
    ).fetchall():
        print("  non-CI org:", r["name"][:40], r["d"], r["raw_rows"])
