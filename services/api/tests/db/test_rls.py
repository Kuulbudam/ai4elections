"""Tenant isolation, role permissions (master prompt 5.1), append-only rules and the audit chain."""

from __future__ import annotations

import threading

import psycopg
import pytest
from psycopg import errors

from .conftest import ROLES, TENANT_TABLES, as_service, as_user

ALL_PUBLIC_TABLES = [*TENANT_TABLES, "organisations", "profiles", "validation_rules"]


def _count(c, table: str, org_id: str) -> int:
    col = "id" if table == "organisations" else "org_id"
    return c.execute(f"SELECT count(*) AS n FROM public.{table} WHERE {col} = %s", (org_id,)).fetchone()["n"]


# ------------------------------------------------------------------ isolation


@pytest.mark.parametrize("table", [*TENANT_TABLES, "organisations"])
@pytest.mark.parametrize("role", ROLES)
def test_member_sees_own_org_and_never_the_other(world, conn, table, role):
    for me, other in ((world.a, world.b), (world.b, world.a)):
        with as_user(conn, me.users[role]) as c:
            assert _count(c, table, me.id) >= 1, f"{role} should read own {table}"
            assert _count(c, table, other.id) == 0, f"{role} leaked {table} across orgs"


@pytest.mark.parametrize("table", [*TENANT_TABLES, "organisations"])
def test_user_with_no_membership_sees_nothing(world, conn, table):
    with as_user(conn, world.outsider) as c:
        assert c.execute(f"SELECT count(*) AS n FROM public.{table}").fetchone()["n"] == 0


@pytest.mark.parametrize("table", ALL_PUBLIC_TABLES)
def test_anon_has_no_access(conn, table):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, None, role="anon") as c:
            c.execute(f"SELECT 1 FROM public.{table} LIMIT 1")


def test_direct_lookup_by_id_of_other_org_row_returns_nothing(world, conn):
    with as_user(conn, world.b.users["admin"]) as c:
        for table in TENANT_TABLES:
            row = c.execute(f"SELECT 1 FROM public.{table} WHERE id = %s", (world.a.rows[table],)).fetchone()
            assert row is None, table


def test_profiles_visible_only_within_shared_orgs(world, conn):
    with as_user(conn, world.a.users["viewer"]) as c:
        ids = {r["id"] for r in c.execute("SELECT id::text FROM public.profiles").fetchall()}
    assert set(world.a.users.values()) <= ids
    assert not (set(world.b.users.values()) & ids)
    assert world.outsider not in ids


def test_storage_objects_readable_only_by_own_org(world, conn):
    for me, other in ((world.a, world.b), (world.b, world.a)):
        with as_user(conn, me.users["viewer"]) as c:
            names = [r["name"] for r in c.execute("SELECT name FROM storage.objects").fetchall()]
        assert names and all(n.startswith(me.id + "/") for n in names)
        assert not any(n.startswith(other.id + "/") for n in names)


def test_browser_cannot_write_storage_objects(world, conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.a.users["admin"]) as c:
            c.execute(
                "INSERT INTO storage.objects (bucket_id, name) VALUES ('datasets', %s)",
                (f"{world.a.id}/x.csv",),
            )


# ------------------------------------------------------------------ cross-org writes


def test_cannot_insert_rows_into_another_org(world, conn):
    a, b = world.a, world.b
    attempts = [
        (
            "INSERT INTO public.projects (org_id, election_name, election_year, country, election_level, created_by) "
            "VALUES (%s,'X Election',2027,'Synthetica','other',%s)",
            (a.id, b.users["admin"]),
        ),
        (
            "INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id) "
            "VALUES (%s,%s,'acknowledged','Cross-org attempt here.',%s)",
            (a.id, a.rows["findings"], b.users["admin"]),
        ),
        (
            "INSERT INTO public.claims (org_id, project_id, statement, created_by) "
            "VALUES (%s,%s,'Cross-org claim attempt.',%s)",
            (a.id, a.rows["projects"], b.users["admin"]),
        ),
    ]
    for sql, params in attempts:
        with pytest.raises(errors.InsufficientPrivilege):  # RLS WITH CHECK violation
            with as_user(conn, b.users["admin"]) as c:
                c.execute(sql, params)


def test_composite_fk_blocks_cross_org_links_even_for_service_role(world, conn):
    """A B-owned review can never point at an A-owned finding, whoever writes it."""
    with pytest.raises(errors.ForeignKeyViolation):
        with as_service(conn) as c:
            c.execute(
                "INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id) "
                "VALUES (%s,%s,'acknowledged','Mixing organisations.',%s)",
                (world.b.id, world.a.rows["findings"], world.b.users["admin"]),
            )


def test_cannot_update_other_org_project(world, conn):
    with as_user(conn, world.b.users["admin"]) as c:
        cur = c.execute(
            "UPDATE public.projects SET description='hijack' WHERE id=%s", (world.a.rows["projects"],)
        )
        assert cur.rowcount == 0


# ------------------------------------------------------------------ role matrix (5.1)

ALLOWED = {
    "manage_members": {"admin"},
    "create_project": {"admin"},
    "save_mapping_template": {"admin", "analyst"},
    "add_note": {"admin", "analyst", "researcher"},
    "record_allegation": {"admin", "analyst", "researcher"},
    "review_decision": {"admin", "analyst"},
    "archive_version": {"admin"},
}


def _do(action: str, c, org, uid: str) -> None:
    if action == "manage_members":
        c.execute("SELECT public.change_member_role(%s,%s,'viewer')", (org.id, org.users["viewer"]))
    elif action == "create_project":
        c.execute(
            "INSERT INTO public.projects (org_id, election_name, election_year, country, election_level, created_by) "
            "VALUES (%s,'Role test',2027,'Synthetica','other',%s)",
            (org.id, uid),
        )
    elif action == "save_mapping_template":
        c.execute(
            "INSERT INTO public.mapping_templates (org_id, name, mapping, created_by) VALUES (%s,%s,'{}',%s)",
            (org.id, f"t-{uid[:8]}", uid),
        )
    elif action == "add_note":
        c.execute(
            "INSERT INTO public.investigation_notes (org_id, finding_id, body, author_id) VALUES (%s,%s,'note',%s)",
            (org.id, org.rows["findings"], uid),
        )
    elif action == "record_allegation":
        c.execute(
            "INSERT INTO public.claims (org_id, project_id, statement, created_by) VALUES (%s,%s,'A claim to test.',%s)",
            (org.id, org.rows["projects"], uid),
        )
    elif action == "review_decision":
        c.execute(
            "INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id) "
            "VALUES (%s,%s,'acknowledged','Reviewed in role test.',%s)",
            (org.id, org.rows["findings"], uid),
        )
    elif action == "archive_version":
        c.execute("SELECT public.set_dataset_version_archived(%s, true)", (org.rows["dataset_versions"],))


@pytest.mark.parametrize("action", sorted(ALLOWED))
@pytest.mark.parametrize("role", ROLES)
def test_role_permission_matrix(world, conn, action, role):
    org = world.a
    uid = org.users[role]
    if role in ALLOWED[action]:
        with as_user(conn, uid) as c:  # rolled back afterwards
            _do(action, c, org, uid)
    else:
        with pytest.raises(errors.InsufficientPrivilege):
            with as_user(conn, uid) as c:
                _do(action, c, org, uid)


@pytest.mark.parametrize("role", ROLES)
def test_viewer_and_everyone_can_read_reports_and_findings(world, conn, role):
    with as_user(conn, world.a.users[role]) as c:
        assert _count(c, "reports", world.a.id) >= 1
        assert _count(c, "findings", world.a.id) >= 1


def test_nobody_can_write_server_owned_tables_from_the_browser(world, conn):
    for table in ["findings", "dataset_versions", "processing_jobs", "reports", "audit_events", "raw_rows"]:
        with pytest.raises(errors.InsufficientPrivilege):
            with as_user(conn, world.a.users["admin"]) as c:
                c.execute(f"DELETE FROM public.{table} WHERE org_id=%s", (world.a.id,))


def test_allegation_cannot_be_created_as_verified(world, conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.a.users["analyst"]) as c:
            c.execute(
                "INSERT INTO public.claims (org_id, project_id, statement, created_by, status, verified_by, "
                "verified_at, verification_reason) VALUES (%s,%s,'Self-verified claim.',%s,'verified_fact',%s,now(),"
                "'I say so myself.')",
                (world.a.id, world.a.rows["projects"], world.a.users["analyst"], world.a.users["admin"]),
            )


def test_claim_creator_cannot_verify_own_claim(world, conn):
    claim = world.a.rows["claims"]
    creator = world.a.users["researcher"]
    with pytest.raises(errors.CheckViolation):
        with as_service(conn) as c:
            c.execute(
                "UPDATE public.claims SET status='verified_fact', verified_by=%s, verified_at=now(), "
                "verification_reason='Verified by creator.' WHERE id=%s",
                (creator, claim),
            )


def test_cannot_review_as_someone_else(world, conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.a.users["analyst"]) as c:
            c.execute(
                "INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id) "
                "VALUES (%s,%s,'acknowledged','Pretending to be admin.',%s)",
                (world.a.id, world.a.rows["findings"], world.a.users["admin"]),
            )


def test_review_requires_reason(world, conn):
    with pytest.raises(errors.CheckViolation):
        with as_user(conn, world.a.users["analyst"]) as c:
            c.execute(
                "INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id) "
                "VALUES (%s,%s,'acknowledged','  ',%s)",
                (world.a.id, world.a.rows["findings"], world.a.users["analyst"]),
            )


def test_last_admin_cannot_be_removed_or_demoted(world, conn):
    a = world.a
    for sql in ("SELECT public.remove_member(%s,%s)", "SELECT public.change_member_role(%s,%s,'viewer')"):
        with pytest.raises(errors.CheckViolation):
            with as_user(conn, a.users["admin"]) as c:
                c.execute(sql, (a.id, a.users["admin"]))


def test_admin_cannot_manage_members_of_other_org(world, conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.b.users["admin"]) as c:
            c.execute("SELECT public.add_member(%s,'outsider@example.test','viewer')", (world.a.id,))


def test_create_organisation_requires_login(conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, None, role="anon") as c:
            c.execute("SELECT public.create_organisation('Anonymous Org')")


# ------------------------------------------------------------------ append-only and immutability


@pytest.mark.parametrize(
    "table",
    ["finding_reviews", "audit_events", "raw_rows", "reports", "investigation_notes", "claim_evidence"],
)
def test_append_only_tables_reject_update_delete_even_for_service_role(world, conn, table):
    rid = world.a.rows[table]
    for sql in (
        f"UPDATE public.{table} SET org_id = org_id WHERE id=%s",
        f"DELETE FROM public.{table} WHERE id=%s",
    ):
        with pytest.raises(errors.InsufficientPrivilege, match="append_only"):
            with as_service(conn) as c:
                c.execute(sql, (rid,))
    with pytest.raises(errors.InsufficientPrivilege, match="append_only"):
        with as_service(conn) as c:
            c.execute(f"TRUNCATE public.{table} CASCADE")


def test_dataset_version_provenance_is_immutable(world, conn):
    vid = world.a.rows["dataset_versions"]
    for sql in (
        "UPDATE public.dataset_versions SET sha256=repeat('f',64) WHERE id=%s",
        "UPDATE public.dataset_versions SET storage_path='x' WHERE id=%s",
        "DELETE FROM public.dataset_versions WHERE id=%s",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            with as_service(conn) as c:
                c.execute(sql, (vid,))


# ------------------------------------------------------------------ audit chain


def test_material_actions_are_audited(world, conn):
    actions = {
        r["action"]
        for r in conn.execute(
            "SELECT action FROM public.audit_events WHERE org_id=%s", (world.a.id,)
        ).fetchall()
    }
    for expected in (
        "organisations.insert",
        "memberships.insert",
        "projects.insert",
        "dataset_versions.insert",
        "dataset_versions.update",
        "finding_reviews.insert",
        "claims.insert",
        "reports.insert",
    ):
        assert expected in actions, expected


def test_audit_records_actor(world, conn):
    row = conn.execute(
        "SELECT actor_id::text FROM public.audit_events WHERE org_id=%s AND action='finding_reviews.insert'",
        (world.a.id,),
    ).fetchone()
    assert row["actor_id"] == world.a.users["analyst"]


def test_audit_chain_is_intact_and_tamper_is_detected(world, conn):
    with as_user(conn, world.a.users["viewer"]) as c:
        status = c.execute("SELECT public.audit_chain_status(%s) AS s", (world.a.id,)).fetchone()["s"]
    assert status["intact"] is True and status["events"] >= 10

    # Simulate a database-level tamper by a superuser (triggers disabled) and check detection.
    with conn.transaction(force_rollback=True):
        conn.execute("ALTER TABLE public.audit_events DISABLE TRIGGER append_only_audit_events")
        conn.execute(
            "UPDATE public.audit_events SET details = '{\"tampered\": true}' WHERE org_id=%s AND seq=3",
            (world.a.id,),
        )
        broken = conn.execute("SELECT app.audit_chain_first_break(%s) AS b", (world.a.id,)).fetchone()["b"]
        assert broken == 3


def test_audit_status_not_available_to_other_org(world, conn):
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.b.users["admin"]) as c:
            c.execute("SELECT public.audit_chain_status(%s)", (world.a.id,))


def test_concurrent_audited_writes_keep_a_single_chain(world):
    org = world.a
    errs: list[Exception] = []

    def write(i: int) -> None:
        try:
            with (
                psycopg.connect(world.dsn, autocommit=True) as c,
                as_user(c, org.users["analyst"], commit=True),
            ):
                c.execute(
                    "INSERT INTO public.investigation_notes (org_id, finding_id, body, author_id) "
                    "VALUES (%s,%s,%s,%s)",
                    (org.id, org.rows["findings"], f"concurrent note {i}", org.users["analyst"]),
                )
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
    with psycopg.connect(world.dsn) as c:
        seqs = [
            r[0]
            for r in c.execute("SELECT seq FROM public.audit_events WHERE org_id=%s ORDER BY seq", (org.id,))
        ]
        assert seqs == list(range(1, len(seqs) + 1))
        assert c.execute("SELECT app.audit_chain_first_break(%s)", (org.id,)).fetchone()[0] is None


# ------------------------------------------------------------------ structural guarantees


def test_every_public_table_has_rls_and_every_tenant_table_has_org_id(conn):
    no_rls = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity"
    ).fetchall()
    assert no_rls == []
    for table in TENANT_TABLES:
        col = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name='org_id'",
            (table,),
        ).fetchone()
        assert col is not None and col["is_nullable"] == "NO", table


def test_anon_and_authenticated_cannot_execute_private_helpers(conn):
    for fn in (
        "app.log_event(gen_random_uuid(),'x','x',null,'{}')",
        "app.audit_chain_first_break(gen_random_uuid())",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            with as_user(conn, None, role="authenticated") as c:
                c.execute(f"SELECT {fn}")


def test_job_heartbeats_are_not_audited_but_status_changes_are(world, conn):
    jid = world.a.rows["processing_jobs"]

    def n() -> int:
        return conn.execute(
            "SELECT count(*) AS n FROM public.audit_events WHERE entity_id=%s", (jid,)
        ).fetchone()["n"]

    with conn.transaction(force_rollback=True):
        before = n()
        conn.execute("UPDATE public.processing_jobs SET heartbeat_at=now(), progress=0.5 WHERE id=%s", (jid,))
        assert n() == before
        conn.execute("UPDATE public.processing_jobs SET status='running' WHERE id=%s", (jid,))
        assert n() == before + 1
