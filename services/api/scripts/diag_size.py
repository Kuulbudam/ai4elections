"""Light read-only health check: connection, read-only state, size (after restart, recheck)."""

import os
import time

import psycopg

for attempt in range(6):
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c:
            print("connected; read_only =", c.execute("SHOW default_transaction_read_only").fetchone()[0])
            print("db size:", c.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0])
            print("uptime:", c.execute("SELECT now() - pg_postmaster_start_time()").fetchone()[0])
            print("wal:", c.execute("SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir()").fetchone()[0])
        break
    except psycopg.Error as e:
        print(f"attempt {attempt + 1}: {str(e).splitlines()[0]}")
        time.sleep(20)
