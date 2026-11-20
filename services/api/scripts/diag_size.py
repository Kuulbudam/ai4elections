"""Read-only: which project is this, and what is in it (counts and test-account prefixes only)."""

import os

import psycopg

with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20) as c:
    print("supabase url host:", os.environ.get("SUPABASE_URL", "").split("//")[-1][:20])
    print("orgs:", c.execute("SELECT count(*) FROM public.organisations").fetchone()[0])
    print("users:", c.execute("SELECT count(*) FROM auth.users").fetchone()[0])
    for r in c.execute("SELECT split_part(email, '-', 1), created_at FROM auth.users ORDER BY created_at DESC LIMIT 5"):
        print("  recent user prefix:", r[0], r[1])
