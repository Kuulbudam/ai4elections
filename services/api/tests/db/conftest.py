"""Fixtures for database security tests.

Builds a fresh database from supabase/tests/supabase_shim.sql + every migration, creates two
organisations (A and B) with one user per role plus an outsider, and seeds one row in every
tenant table for each organisation. Requires TEST_DATABASE_URL (a superuser connection).
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app import auth, deps
from app.main import app
from app.storage import MemoryStorage

DSN = os.environ.get("TEST_DATABASE_URL")
REPO = Path(__file__).resolve().parents[4]
ROLES = ("admin", "analyst", "researcher", "viewer")

TENANT_TABLES = [
    "memberships", "projects", "data_sources", "datasets", "dataset_versions", "raw_rows", "result_records",
    "mapping_templates", "admin_units", "processing_jobs", "analytical_runs", "validation_issues", "findings",
    "finding_reviews", "investigation_notes", "claims", "claim_evidence", "reports", "audit_events",
    "result_sheets",
]  # fmt: skip


def _dsn_for(db: str) -> str:
    return psycopg.conninfo.make_conninfo(DSN, dbname=db)


@dataclass
class Org:
    name: str
    id: str = ""
    users: dict[str, str] = field(default_factory=dict)  # role -> user id
    rows: dict[str, str] = field(default_factory=dict)  # table -> seeded row id


@dataclass
class World:
    dsn: str
    a: Org
    b: Org
    outsider: str

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)


@contextmanager
def as_user(
    conn: psycopg.Connection, user_id: str | None, role: str = "authenticated", commit: bool = False, aal: str = "aal2"
):
    """Run statements as a Supabase API caller: SET ROLE + JWT claims, inside one transaction.

    Defaults to a fully verified session (aal2: password + authenticator code)."""
    tx = conn.transaction(force_rollback=not commit)
    with tx:
        conn.execute(f"SET LOCAL ROLE {role}")
        claims = json.dumps({"sub": user_id, "role": role, "aal": aal}) if user_id else ""
        conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (claims,))
        yield conn


@contextmanager
def as_service(conn: psycopg.Connection, actor: str | None = None):
    with conn.transaction():
        conn.execute("SET LOCAL ROLE service_role")
        conn.execute("SELECT set_config('app.actor_id', %s, true)", (actor or "",))
        yield conn


def _create_user(conn, email: str) -> str:
    return str(conn.execute("INSERT INTO auth.users (email) VALUES (%s) RETURNING id", (email,)).fetchone()["id"])


def _seed_org(conn, org: Org) -> None:
    admin = org.users["admin"]
    emails = {
        role: conn.execute("SELECT email FROM auth.users WHERE id = %s", (uid,)).fetchone()["email"]
        for role, uid in org.users.items()
    }
    with as_user(conn, admin, commit=True) as c:
        org.id = str(c.execute("SELECT public.create_organisation(%s) AS id", (org.name,)).fetchone()["id"])
        for role in ("analyst", "researcher", "viewer"):
            c.execute("SELECT public.add_member(%s, %s, %s)", (org.id, emails[role], role))
        org.rows["projects"] = str(
            c.execute(
                """INSERT INTO public.projects (org_id, election_name, election_year, country, election_level,
                   created_by) VALUES (%s, 'Synthetic General Election', 2027, 'Synthetica', 'presidential', %s)
                   RETURNING id""",
                (org.id, admin),
            ).fetchone()["id"]
        )
    pid = org.rows["projects"]
    with as_service(conn, admin) as c:

        def ins(sql: str, params: tuple) -> str:
            return str(c.execute(sql, params).fetchone()["id"])

        r = org.rows
        r["data_sources"] = ins(
            "INSERT INTO public.data_sources (org_id, project_id, source_type, description, created_by) "
            "VALUES (%s,%s,'synthetic','SYNTHETIC test data',%s) RETURNING id",
            (org.id, pid, admin),
        )
        r["datasets"] = ins(
            "INSERT INTO public.datasets (org_id, project_id, name, created_by) VALUES (%s,%s,'Results',%s) "
            "RETURNING id",
            (org.id, pid, admin),
        )
        vid = str(uuid.uuid4())
        r["dataset_versions"] = ins(
            """INSERT INTO public.dataset_versions (id, org_id, project_id, dataset_id, data_source_id,
               version_number, storage_path, original_filename, content_type, declared_size_bytes, uploaded_by)
               VALUES (%s,%s,%s,%s,%s,1,%s,'SYNTHETIC_results.csv','text/csv',1000,%s) RETURNING id""",
            (
                vid,
                org.id,
                pid,
                r["datasets"],
                r["data_sources"],
                f"{org.id}/{pid}/{r['datasets']}/{vid}.csv",
                admin,
            ),
        )
        c.execute(
            "UPDATE public.dataset_versions SET status='ingested', size_bytes=1000, sha256=%s, finalized_at=now() "
            "WHERE id=%s",
            ("a" * 64, vid),
        )
        r["raw_rows"] = ins(
            "INSERT INTO public.raw_rows (org_id, dataset_version_id, source_row_number, cells) "
            'VALUES (%s,%s,2,\'{"pu_code":"SY-1"}\') RETURNING id',
            (org.id, vid),
        )
        r["result_records"] = ins(
            "INSERT INTO public.result_records (org_id, dataset_version_id, raw_row_id, schema_version, pu_code) "
            "VALUES (%s,%s,%s,'1.0.0','SY-1') RETURNING id",
            (org.id, vid, r["raw_rows"]),
        )
        r["mapping_templates"] = ins(
            "INSERT INTO public.mapping_templates (org_id, name, mapping, created_by) "
            "VALUES (%s,'Default','{}',%s) RETURNING id",
            (org.id, admin),
        )
        r["admin_units"] = ins(
            "INSERT INTO public.admin_units (org_id, project_id, state, lga, ward, created_by) "
            "VALUES (%s,%s,'S','L','W',%s) RETURNING id",
            (org.id, pid, admin),
        )
        r["processing_jobs"] = ins(
            "INSERT INTO public.processing_jobs (org_id, project_id, job_type, target_id, input_fingerprint, "
            "created_by) VALUES (%s,%s,'analyse',%s,'fp',%s) RETURNING id",
            (org.id, pid, vid, admin),
        )
        r["analytical_runs"] = ins(
            "INSERT INTO public.analytical_runs (org_id, project_id, dataset_version_id, job_id, engine_version, "
            "method_version, status, content_hash, completed_at) "
            "VALUES (%s,%s,%s,%s,'0.1.0','1.0.0','complete',%s,now()) RETURNING id",
            (org.id, pid, vid, r["processing_jobs"], "b" * 64),
        )
        r["validation_issues"] = ins(
            "INSERT INTO public.validation_issues (org_id, dataset_version_id, job_id, raw_row_id, rule_id, "
            "rule_version, severity, message) VALUES (%s,%s,%s,%s,'V005','1.0.0','error','Negative value') "
            "RETURNING id",
            (org.id, vid, r["processing_jobs"], r["raw_rows"]),
        )
        r["findings"] = ins(
            "INSERT INTO public.findings (org_id, project_id, dataset_version_id, analytical_run_id, category, "
            "indicator, severity, confidence, title, explanation, limitations, provenance) VALUES "
            "(%s,%s,%s,%s,'statistical_anomaly','turnout_rate','high','medium','Unusual turnout',"
            "'Turnout is far from the ward median.','Signal only.','{}') RETURNING id",
            (org.id, pid, vid, r["analytical_runs"]),
        )
        r["reports"] = ins(
            "INSERT INTO public.reports (org_id, project_id, analytical_run_id, dataset_version_id, body, "
            "content_hash, generated_by) VALUES (%s,%s,%s,%s,'{}',%s,%s) RETURNING id",
            (org.id, pid, r["analytical_runs"], vid, "c" * 64, admin),
        )
        sheet_id = str(uuid.uuid4())
        r["result_sheets"] = ins(
            "INSERT INTO public.result_sheets (id, org_id, project_id, dataset_version_id, image_path, image_sha256, "
            "original_sha256, image_bytes, extracted, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,10,"
            '\'{"status": "no_model", "figures": {"party_votes": []}, "confidence": {}, "legible": false}\',%s) '
            "RETURNING id",
            (sheet_id, org.id, pid, vid, f"{org.id}/{pid}/{sheet_id}.jpg", "d" * 64, "e" * 64, admin),
        )
        c.execute(
            "INSERT INTO storage.objects (bucket_id, name) VALUES ('datasets', %s)",
            (f"{org.id}/{pid}/{r['datasets']}/{vid}.csv",),
        )
    with as_user(conn, org.users["analyst"], commit=True) as c:
        r["finding_reviews"] = str(
            c.execute(
                "INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id) "
                "VALUES (%s,%s,'needs_more_evidence','Need the original result sheet.',%s) RETURNING id",
                (org.id, r["findings"], org.users["analyst"]),
            ).fetchone()["id"]
        )
    with as_user(conn, org.users["researcher"], commit=True) as c:
        r["investigation_notes"] = str(
            c.execute(
                "INSERT INTO public.investigation_notes (org_id, finding_id, body, author_id) "
                "VALUES (%s,%s,'Checked peer group.',%s) RETURNING id",
                (org.id, r["findings"], org.users["researcher"]),
            ).fetchone()["id"]
        )
        r["claims"] = str(
            c.execute(
                "INSERT INTO public.claims (org_id, project_id, finding_id, statement, created_by) "
                "VALUES (%s,%s,%s,'An observer reported a late count here.',%s) RETURNING id",
                (org.id, pid, r["findings"], org.users["researcher"]),
            ).fetchone()["id"]
        )
        r["claim_evidence"] = str(
            c.execute(
                "INSERT INTO public.claim_evidence (org_id, claim_id, kind, description, url, added_by) "
                "VALUES (%s,%s,'link','Observer note','https://example.org/note',%s) RETURNING id",
                (org.id, r["claims"], org.users["researcher"]),
            ).fetchone()["id"]
        )
    r["memberships"] = str(
        conn.execute("SELECT id FROM public.memberships WHERE org_id=%s AND role='admin'", (org.id,)).fetchone()["id"]
    )
    r["audit_events"] = str(
        conn.execute("SELECT id FROM public.audit_events WHERE org_id=%s LIMIT 1", (org.id,)).fetchone()["id"]
    )


def build_database(name: str) -> str:
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    dsn = _dsn_for(name)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((REPO / "supabase" / "tests" / "supabase_shim.sql").read_text())
        for f in sorted((REPO / "supabase" / "migrations").glob("*.sql")):
            c.execute(f.read_text())
    return dsn


@pytest.fixture(scope="session")
def world() -> World:
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    dsn = build_database("ai4e_rls_test")
    a, b = Org("Org A (synthetic)"), Org("Org B (synthetic)")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        for org, tag in ((a, "a"), (b, "b")):
            for role in ROLES:
                org.users[role] = _create_user(conn, f"{role}.{tag}@example.test")
        outsider = _create_user(conn, "outsider@example.test")
        _seed_org(conn, a)
        _seed_org(conn, b)
    return World(dsn=dsn, a=a, b=b, outsider=outsider)


@pytest.fixture()
def conn(world: World):
    c = world.connect()
    yield c
    c.close()


# ------------------------------------------------------------------ API client fixture (shared)


class Dispatches(list):
    def __call__(self, job_id: str) -> str:
        self.append(job_id)
        return "ok"


@pytest.fixture()
def api(world, monkeypatch):
    monkeypatch.setenv("LEDGER_SCHEMA", "public")
    monkeypatch.setattr(deps, "USER_LIMIT_PER_MIN", 100_000)  # tests run faster than any human
    monkeypatch.setattr(deps, "IP_LIMIT_PER_MIN", 100_000)
    storage = MemoryStorage()
    dispatched = Dispatches()

    def db():
        c = psycopg.connect(world.dsn, autocommit=True, row_factory=dict_row)
        try:
            yield c
        finally:
            c.close()

    def user(request: Request) -> auth.CurrentUser:
        uid = request.headers.get("X-Test-User")
        if not uid:
            raise HTTPException(status_code=401, detail="Missing or invalid access token")
        return auth.CurrentUser(id=uid, email=None, claims={})

    app.dependency_overrides[deps.get_db] = db
    app.dependency_overrides[deps.get_storage] = lambda: storage
    app.dependency_overrides[deps.get_dispatcher] = lambda: dispatched
    app.dependency_overrides[auth.current_user] = user
    monkeypatch.setattr(deps, "_storage", storage)  # the worker uses deps.get_storage()
    client = TestClient(app, raise_server_exceptions=False)
    client.storage, client.dispatched = storage, dispatched
    yield client
    app.dependency_overrides.clear()
