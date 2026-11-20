"""Backup the production database and prove the backup restores (Phase 12).

1. pg_dump (data only) of the platform's schemas from DATABASE_URL, plus the user ids and emails that rows refer to.
2. A fresh database built from supabase/migrations (with the local Supabase stand-in) on RESTORE_DATABASE_URL.
3. Restore into it, then check: every table restored, every foreign key holds, every organisation's audit chain is
   intact, and the restored row counts agree with production (append-only tables can only have grown since).
4. Writes backup/ai4e-<date>.pgc and users.csv for the workflow to encrypt and keep.

Needs DATABASE_URL (production, read) and RESTORE_DATABASE_URL (a scratch Postgres server, superuser). pg_dump and
pg_restore must be at least the production server's major version.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

REPO = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("BACKUP_DIR", "backup"))
EXCLUDE = ["app.schema_migrations", "app.rate_limits", "app.idempotency_keys"]  # bookkeeping, rebuilt or short-lived
VOLATILE = {"processing_jobs", "audit_events", "assistant_queries", "dataset_versions"}  # may change during the dump
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    return ok


def public_tables(conn: psycopg.Connection) -> list[str]:
    return [r["tablename"] for r in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename").fetchall()]  # fmt: skip


def counts(conn: psycopg.Connection, tables: list[str]) -> dict[str, int]:
    return {t: conn.execute(sql.SQL("SELECT count(*) AS n FROM public.{}").format(sql.Identifier(t))).fetchone()["n"]
            for t in tables}  # fmt: skip


def main() -> int:
    src_url, dst_url = os.environ["DATABASE_URL"], os.environ["RESTORE_DATABASE_URL"]
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
    dump = OUT / f"ai4e-{stamp}.pgc"

    with psycopg.connect(src_url, row_factory=dict_row) as src:
        ver = src.execute("SHOW server_version").fetchone()["server_version"]
        before = counts(src, public_tables(src))
    print(f"Production Postgres {ver}; {sum(before.values()):,} rows in {len(before)} tables", flush=True)

    t0 = datetime.now(UTC)
    pg_dump, pg_restore = shutil.which("pg_dump") or "pg_dump", shutil.which("pg_restore") or "pg_restore"
    cmd = [pg_dump, "--data-only", "--format=custom", "--no-owner", "--no-privileges", "--schema=public",
           "--schema=app", *(f"--exclude-table-data={t}" for t in EXCLUDE), f"--file={dump}", src_url]  # fmt: skip
    p = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603 - fixed command, no shell
    if not check("pg_dump of public and app schemas", p.returncode == 0 and dump.stat().st_size > 0,
                 f"{dump.stat().st_size / 1e6:.1f} MB in {(datetime.now(UTC) - t0).seconds}s" if p.returncode == 0
                 else p.stderr[-400:]):  # fmt: skip
        return report()

    with psycopg.connect(src_url) as src, (OUT / "users.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "email"])
        with src.cursor() as cur:
            cur.execute("SELECT id, email FROM auth.users ORDER BY created_at")
            for row in cur:
                w.writerow(row)

    # Fresh target from the migrations, exactly as the tests build theirs.
    with psycopg.connect(dst_url, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS ai4e_restore WITH (FORCE)")
        admin.execute("CREATE DATABASE ai4e_restore")
    target = psycopg.conninfo.make_conninfo(dst_url, dbname="ai4e_restore")
    with psycopg.connect(target, autocommit=True) as t:
        t.execute((REPO / "supabase" / "tests" / "supabase_shim.sql").read_text())
        for m in sorted((REPO / "supabase" / "migrations").glob("*.sql")):
            t.execute(m.read_text())
        t.execute("SET session_replication_role = replica")  # rows are restored as they were; no triggers fire
        with t.cursor() as cur, cur.copy("COPY auth.users (id, email) FROM STDIN WITH (FORMAT csv, HEADER)") as cp:
            cp.write((OUT / "users.csv").read_bytes())
    check("empty database built from the migrations", True)

    p = subprocess.run(  # noqa: S603
        [pg_restore, "--data-only", "--disable-triggers", "--no-owner", "--no-privileges", "--exit-on-error",
         f"--dbname={target}", str(dump)], capture_output=True, text=True)  # fmt: skip
    check("pg_restore into it", p.returncode == 0, p.stderr[-600:])

    with psycopg.connect(target, row_factory=dict_row) as t, psycopg.connect(src_url, row_factory=dict_row) as src:
        restored = counts(t, public_tables(t))
        after = counts(src, public_tables(src))
        missing = sorted(set(before) - set(restored))
        check("every production table exists after restore", not missing, ", ".join(missing))
        off = []
        for tbl, n in restored.items():
            lo, hi = min(before.get(tbl, 0), after.get(tbl, 0)), max(before.get(tbl, 0), after.get(tbl, 0))
            if not (lo <= n <= hi) and not (tbl in VOLATILE and abs(n - lo) <= 50):
                off.append(f"{tbl}: restored {n}, production {lo}-{hi}")
        check("row counts match production", not off, "; ".join(off[:6]) or f"{sum(restored.values()):,} rows")

        orphans = []
        fks = t.execute("""
            SELECT c.conname, cl.relname AS child, pl.relname AS parent,
                   array_agg(a.attname ORDER BY k.ord) AS ccols, array_agg(pa.attname ORDER BY k.ord) AS pcols
            FROM pg_constraint c
            JOIN pg_class cl ON cl.oid = c.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
            JOIN pg_class pl ON pl.oid = c.confrelid JOIN pg_namespace pn ON pn.oid = pl.relnamespace
            CROSS JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord)
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.ck
            JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
            WHERE c.contype = 'f' AND n.nspname = 'public' AND pn.nspname IN ('public', 'auth')
            GROUP BY c.conname, cl.relname, pl.relname, pn.nspname""").fetchall()
        for fk in fks:
            parent_schema = "auth" if fk["parent"] == "users" else "public"
            q = sql.SQL("SELECT count(*) AS n FROM public.{c} x WHERE {notnull} AND NOT EXISTS "
                        "(SELECT 1 FROM {ps}.{p} y WHERE {join})").format(
                c=sql.Identifier(fk["child"]), ps=sql.Identifier(parent_schema), p=sql.Identifier(fk["parent"]),
                notnull=sql.SQL(" AND ").join(sql.SQL("x.{} IS NOT NULL").format(sql.Identifier(c)) for c in fk["ccols"]),
                join=sql.SQL(" AND ").join(sql.SQL("y.{} = x.{}").format(sql.Identifier(p), sql.Identifier(c))
                                           for c, p in zip(fk["ccols"], fk["pcols"], strict=True)))  # fmt: skip
            n = t.execute(q).fetchone()["n"]
            if n:
                orphans.append(f"{fk['child']}→{fk['parent']}: {n}")
        check(f"all {len(fks)} foreign keys hold in the restored data", not orphans, "; ".join(orphans[:6]))

        broken = [str(r["id"]) for r in t.execute(
            "SELECT o.id FROM public.organisations o WHERE app.audit_chain_first_break(o.id) IS NOT NULL").fetchall()]  # fmt: skip
        n_orgs = t.execute("SELECT count(*) AS n FROM public.organisations").fetchone()["n"]
        check(f"audit chain intact for all {n_orgs} organisations after restore", not broken, ", ".join(broken[:5]))
    return report()


def report() -> int:
    ok = all(r[1] for r in results)
    print(f"{sum(r[1] for r in results)}/{len(results)} checks passed")
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(s, "a") as f:
            f.write("## Backup and restore check\n\n| Check | Result |\n|---|---|\n")
            for name, good, detail in results:
                f.write(f"| {name} | {'PASS' if good else 'FAIL'} {detail.replace('|', '/')[:200]} |\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
