"""Two-step verification is enforced by the database, not only by the website."""

import pytest
from psycopg import errors

from .conftest import TENANT_TABLES, as_user


@pytest.mark.parametrize("table", [*TENANT_TABLES, "organisations", "profiles", "validation_rules"])
def test_password_only_session_reads_nothing(world, conn, table):
    with as_user(conn, world.a.users["admin"], aal="aal1") as c:
        assert c.execute(f"SELECT count(*) AS n FROM public.{table}").fetchone()["n"] == 0


def test_same_user_with_verified_code_reads_own_rows(world, conn):
    with as_user(conn, world.a.users["admin"], aal="aal2") as c:
        assert c.execute("SELECT count(*) AS n FROM public.projects").fetchone()["n"] >= 1


def test_password_only_session_cannot_write(world, conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.a.users["analyst"], aal="aal1") as c:
            c.execute(
                "INSERT INTO public.investigation_notes (org_id, finding_id, body, author_id) VALUES (%s,%s,'x',%s)",
                (world.a.id, world.a.rows["findings"], world.a.users["analyst"]),
            )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT public.create_organisation('No MFA Org')",
        "SELECT public.add_member('{org}', 'outsider@example.test', 'viewer')",
        "SELECT public.audit_chain_status('{org}')",
        "SELECT public.set_dataset_version_archived('{version}', true)",
    ],
)
def test_password_only_session_cannot_call_rpcs(world, conn, sql):
    with pytest.raises(errors.InsufficientPrivilege, match="mfa_required"):
        with as_user(conn, world.a.users["admin"], aal="aal1") as c:
            c.execute(sql.format(org=world.a.id, version=world.a.rows["dataset_versions"]))


def test_password_only_session_cannot_read_stored_files(world, conn):
    with as_user(conn, world.a.users["admin"], aal="aal1") as c:
        assert c.execute("SELECT count(*) AS n FROM storage.objects WHERE bucket_id='datasets'").fetchone()["n"] == 0
