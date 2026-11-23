"""Live security check against the real Supabase project (Auth + Data API + Storage).

Proves on the deployed platform, through the same HTTP APIs the browser uses, that:
  * users can register (admin API) and sign in with a password (authentication works; the
    profile trigger on auth.users does not break sign-up);
  * each user sees only their own organisation's rows;
  * the anon key alone reads nothing;
  * role rules hold (researcher/viewer cannot record a review; viewer cannot add a note);
  * members can read their own org's stored file, other orgs cannot, and nobody can upload
    directly from the browser.

Needs: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, DATABASE_URL.
Creates two clearly named CI organisations per run ("ZZ CI isolation check ..."). Rows are
append-only by design, so they remain; RLS keeps them invisible to every other organisation.
"""

from __future__ import annotations

import base64
import io
import os
import secrets
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from live_auth import upgrade_to_aal2

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RUN = os.environ.get("GITHUB_RUN_ID") or secrets.token_hex(4)
ROLES = ("admin", "analyst", "researcher", "viewer")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def key_headers(key: str) -> dict[str, str]:
    h = {"apikey": key}
    if key.startswith("eyJ"):  # legacy JWT-style keys also go in Authorization
        h["Authorization"] = f"Bearer {key}"
    return h


def user_headers(token: str) -> dict[str, str]:
    return {"apikey": ANON, "Authorization": f"Bearer {token}"}


def main() -> int:
    http = httpx.Client(timeout=30)
    password = secrets.token_urlsafe(18)
    users: dict[tuple[str, str], dict] = {}

    # --- 1. register users through the Auth admin API, then sign in with password
    for tag in ("a", "b"):
        for role in ROLES:
            email = f"ci-{RUN}-{role}-{tag}@example.com"
            r = http.post(
                f"{URL}/auth/v1/admin/users",
                headers=key_headers(SERVICE),
                json={"email": email, "password": password, "email_confirm": True},
            )
            if r.status_code >= 300:
                check(f"create user {role}-{tag}", False, f"HTTP {r.status_code}: {r.text[:200]}")
                return report()
            uid = r.json()["id"]
            t = http.post(
                f"{URL}/auth/v1/token?grant_type=password",
                headers={"apikey": ANON},
                json={"email": email, "password": password},
            )
            ok = t.status_code == 200 and "access_token" in t.json()
            users[(tag, role)] = {
                "id": uid,
                "email": email,
                "token": t.json().get("access_token") if ok else None,
            }
            if not ok:
                try:
                    why = {k: t.json().get(k) for k in ("error_code", "code", "msg", "error_description")}
                except ValueError:
                    why = {}
                check(f"sign in {role}-{tag}", False, f"HTTP {t.status_code} {why}")
                return report()
    check("register + password sign-in for 8 users", True)

    # --- 1b. two-step verification is required: a password-only session gets nothing
    aal1 = users[("a", "admin")]["token"]
    r = http.post(f"{URL}/rest/v1/rpc/create_organisation", headers=user_headers(aal1), json={"p_name": "no mfa"})
    check("password-only session cannot create an organisation", r.status_code in (401, 403), f"HTTP {r.status_code}")
    r = http.get(f"{URL}/rest/v1/profiles?select=id", headers=user_headers(aal1))
    check("password-only session reads no rows", r.status_code == 200 and r.json() == [], r.text[:80])
    for u in users.values():
        u["token"] = upgrade_to_aal2(http, URL, ANON, u["token"])
    check("authenticator-app code verified for 8 users (aal2)", True)

    # --- 2. profile trigger ran for every new user
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as db:
        n = db.execute(
            "SELECT count(*) FROM public.profiles WHERE id = ANY(%s::uuid[])",
            ([u["id"] for u in users.values()],),
        ).fetchone()[0]
        check("profile row created for each new user", n == 8, f"{n}/8")

        # --- 3. orgs, members and a project through the browser-facing API
        orgs: dict[str, str] = {}
        projects: dict[str, str] = {}
        for tag in ("a", "b"):
            admin = users[(tag, "admin")]
            r = http.post(
                f"{URL}/rest/v1/rpc/create_organisation",
                headers=user_headers(admin["token"]),
                json={"p_name": f"ZZ CI isolation check {RUN} {tag.upper()}"},
            )
            if r.status_code != 200:
                check(f"create org {tag}", False, f"HTTP {r.status_code}: {r.text[:200]}")
                return report()
            orgs[tag] = r.json()
            for role in ("analyst", "researcher", "viewer"):
                r = http.post(
                    f"{URL}/rest/v1/rpc/add_member",
                    headers=user_headers(admin["token"]),
                    json={"p_org": orgs[tag], "p_email": users[(tag, role)]["email"], "p_role": role},
                )
                if r.status_code != 200:
                    check(f"add {role} to org {tag}", False, f"HTTP {r.status_code}: {r.text[:200]}")
                    return report()
            r = http.post(
                f"{URL}/rest/v1/projects",
                headers={**user_headers(admin["token"]), "Prefer": "return=representation"},
                json={
                    "org_id": orgs[tag],
                    "election_name": "SYNTHETIC CI Election",
                    "election_year": 2027,
                    "country": "Synthetica",
                    "election_level": "other",
                    "created_by": admin["id"],
                },
            )
            if r.status_code != 201:
                check(f"admin creates project in org {tag}", False, f"HTTP {r.status_code}: {r.text[:200]}")
                return report()
            projects[tag] = r.json()[0]["id"]
        check("orgs, memberships and projects created via Data API", True)

        # --- 4. seed one finding per org server-side (service role path), plus a stored file
        findings: dict[str, str] = {}
        paths: dict[str, str] = {}
        for tag in ("a", "b"):
            org, pid, admin_id = orgs[tag], projects[tag], users[(tag, "admin")]["id"]
            with db.transaction():
                db.execute("SELECT set_config('app.actor_id', %s, true)", (admin_id,))
                ds = db.execute(
                    "INSERT INTO public.data_sources (org_id, project_id, source_type, description, created_by) "
                    "VALUES (%s,%s,'synthetic','SYNTHETIC CI',%s) RETURNING id",
                    (org, pid, admin_id),
                ).fetchone()[0]
                dset = db.execute(
                    "INSERT INTO public.datasets (org_id, project_id, name, created_by) VALUES (%s,%s,'CI',%s) "
                    "RETURNING id",
                    (org, pid, admin_id),
                ).fetchone()[0]
                vid = uuid.uuid4()
                paths[tag] = f"{org}/{pid}/{dset}/{vid}.csv"
                db.execute(
                    "INSERT INTO public.dataset_versions (id, org_id, project_id, dataset_id, data_source_id, "
                    "version_number, storage_path, original_filename, content_type, declared_size_bytes, uploaded_by) "
                    "VALUES (%s,%s,%s,%s,%s,1,%s,'SYNTHETIC_ci.csv','text/csv',20,%s)",
                    (vid, org, pid, dset, ds, paths[tag], admin_id),
                )
                job = db.execute(
                    "INSERT INTO public.processing_jobs (org_id, project_id, job_type, target_id, input_fingerprint, "
                    "created_by, status, finished_at) VALUES (%s,%s,'analyse',%s,'ci',%s,'succeeded',now()) RETURNING id",
                    (org, pid, vid, admin_id),
                ).fetchone()[0]
                run = db.execute(
                    "INSERT INTO public.analytical_runs (org_id, project_id, dataset_version_id, job_id, "
                    "engine_version, method_version, status, content_hash, completed_at) "
                    "VALUES (%s,%s,%s,%s,'ci','1.0.0','complete',repeat('0',64),now()) RETURNING id",
                    (org, pid, vid, job),
                ).fetchone()[0]
                findings[tag] = str(
                    db.execute(
                        "INSERT INTO public.findings (org_id, project_id, dataset_version_id, analytical_run_id, "
                        "category, severity, confidence, title, explanation, limitations, provenance) VALUES "
                        "(%s,%s,%s,%s,'observation','info','low','CI finding','CI seeded row.','CI only.','{}') "
                        "RETURNING id",
                        (org, pid, vid, run),
                    ).fetchone()[0]
                )
                sheet = uuid.uuid4()
                db.execute(
                    "INSERT INTO public.result_sheets (id, org_id, project_id, dataset_version_id, image_path, "
                    "image_sha256, original_sha256, image_bytes, extracted, created_by) VALUES "
                    "(%s,%s,%s,%s,%s,repeat('a',64),repeat('b',64),10,"
                    '\'{"status":"no_model","figures":{"party_votes":[]},"confidence":{},"legible":false}\',%s)',
                    (sheet, org, pid, vid, f"{org}/{pid}/{sheet}.jpg", admin_id),
                )
                db.execute(
                    "INSERT INTO public.collation_checks (org_id, project_id, dataset_version_id, filename, sha256, "
                    "engine_version, summary, areas, created_by) VALUES (%s,%s,%s,'ci.csv',repeat('c',64),'ci','{}',"
                    "'[]',%s)",
                    (org, pid, vid, admin_id),
                )
            up = http.post(
                f"{URL}/storage/v1/object/datasets/{paths[tag]}",
                headers={**key_headers(SERVICE), "Content-Type": "text/csv"},
                content=b"pu_code,state\nSY-1,Synthetica\n",
            )
            if up.status_code >= 300:
                check(
                    f"server upload to private bucket ({tag})",
                    False,
                    f"HTTP {up.status_code}: {up.text[:200]}",
                )
                return report()
    check("server-side seed (finding + stored file) per org", True)

    # --- 5. isolation through the Data API
    tables = [
        "organisations",
        "memberships",
        "projects",
        "datasets",
        "dataset_versions",
        "findings",
        "audit_events",
        "result_sheets",
        "collation_checks",
    ]
    leaks = []
    for (tag, role), u in users.items():
        other = "b" if tag == "a" else "a"
        for table in tables:
            col = "id" if table == "organisations" else "org_id"
            r = http.get(f"{URL}/rest/v1/{table}?select={col}", headers=user_headers(u["token"]))
            if r.status_code != 200:
                leaks.append(f"{role}-{tag} {table} HTTP {r.status_code}")
                continue
            seen = {row[col] for row in r.json()}
            if orgs[other] in seen or (orgs[tag] not in seen):
                leaks.append(f"{role}-{tag} {table} saw={len(seen)}")
    check(f"8 users × {len(tables)} tables: own org visible, other org never", not leaks, "; ".join(leaks[:5]))

    r = http.get(
        f"{URL}/rest/v1/findings?id=eq.{findings['a']}&select=id",
        headers=user_headers(users[("b", "admin")]["token"]),
    )
    check("org B admin cannot fetch org A finding by id", r.status_code == 200 and r.json() == [], r.text[:120])

    r = http.get(f"{URL}/rest/v1/projects?select=id", headers=key_headers(ANON))
    check(
        "anon key alone reads no projects",
        r.status_code in (401, 403) or r.json() == [],
        f"HTTP {r.status_code}",
    )

    # --- 6. role rules through the Data API
    def review(tag: str, role: str) -> int:
        u = users[(tag, role)]
        return http.post(
            f"{URL}/rest/v1/finding_reviews",
            headers=user_headers(u["token"]),
            json={
                "org_id": orgs[tag],
                "finding_id": findings[tag],
                "decision": "acknowledged",
                "reason": "CI role-matrix check.",
                "reviewer_id": u["id"],
            },
        ).status_code

    codes = {role: review("a", role) for role in ROLES}
    ok = (
        codes["admin"] == 201
        and codes["analyst"] == 201
        and codes["researcher"] in (401, 403)
        and codes["viewer"] in (401, 403)
    )
    check("review decisions: admin/analyst allowed, researcher/viewer denied", ok, str(codes))

    r = http.post(
        f"{URL}/rest/v1/finding_reviews",
        headers=user_headers(users[("b", "admin")]["token"]),
        json={
            "org_id": orgs["a"],
            "finding_id": findings["a"],
            "decision": "acknowledged",
            "reason": "Cross-org write attempt.",
            "reviewer_id": users[("b", "admin")]["id"],
        },
    )
    check("org B admin cannot write a review into org A", r.status_code in (401, 403), f"HTTP {r.status_code}")

    r = http.post(
        f"{URL}/rest/v1/investigation_notes",
        headers=user_headers(users[("a", "viewer")]["token"]),
        json={
            "org_id": orgs["a"],
            "finding_id": findings["a"],
            "body": "viewer note",
            "author_id": users[("a", "viewer")]["id"],
        },
    )
    check("viewer cannot add investigation notes", r.status_code in (401, 403), f"HTTP {r.status_code}")

    r = http.patch(
        f"{URL}/rest/v1/finding_reviews?finding_id=eq.{findings['a']}",
        headers=user_headers(users[("a", "admin")]["token"]),
        json={"reason": "Rewriting history attempt."},
    )
    check("reviews cannot be edited via API", r.status_code in (401, 403), f"HTTP {r.status_code}")

    # --- 6b. escalation (Phase 14): real tokens through the API, notifications read through the Data API
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient

    from app.main import app

    api = TestClient(app, raise_server_exceptions=False)

    def bearer(tag: str, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {users[(tag, role)]['token']}", "Idempotency-Key": str(uuid.uuid4())}

    r = api.post(f"/findings/{findings['a']}/reviews", headers=bearer("a", "analyst"),
                 json={"decision": "escalated", "reason": "CI: needs a senior decision."})  # fmt: skip
    eid = r.json().get("escalation_id") if r.status_code == 201 else None
    check("analyst escalates a finding; it goes to the admins", bool(eid), f"HTTP {r.status_code} {r.text[:120]}")
    note = http.get(f"{URL}/rest/v1/notifications?select=kind,link&link=eq./escalations/{eid}",
                    headers=user_headers(users[("a", "admin")]["token"]))  # fmt: skip
    check("the admin gets a notification", note.status_code == 200 and len(note.json()) == 1, note.text[:120])
    peek = http.get(f"{URL}/rest/v1/notifications?select=id&link=eq./escalations/{eid}",
                    headers=user_headers(users[("a", "viewer")]["token"]))  # fmt: skip
    check(
        "nobody else can read the admin's notification", peek.status_code == 200 and peek.json() == [], peek.text[:80]
    )
    codes = {
        role: api.post(
            f"/escalations/{eid}/resolve", headers=bearer("a", role), json={"note": "CI: resolve attempt."}
        ).status_code  # fmt: skip
        for role in ("viewer", "researcher", "analyst")
    }
    cross = api.get(f"/escalations/{eid}", headers=bearer("b", "admin")).status_code
    check("only admins can resolve; other organisations get 404",
          all(c == 403 for c in codes.values()) and cross == 404, f"{codes}, org B {cross}")  # fmt: skip
    r = api.post(f"/escalations/{eid}/resolve", headers=bearer("a", "admin"),
                 json={"note": "CI: checked the result sheet; resolved."})  # fmt: skip
    told = http.get(f"{URL}/rest/v1/notifications?select=kind&link=eq./escalations/{eid}",
                    headers=user_headers(users[("a", "analyst")]["token"]))  # fmt: skip
    check("admin resolves; the analyst who raised it is told",
          r.status_code == 200 and r.json().get("status") == "resolved"
          and [x["kind"] for x in told.json()] == ["escalation_resolved"], f"HTTP {r.status_code} {told.text[:120]}")  # fmt: skip
    edit = http.patch(f"{URL}/rest/v1/escalation_events?escalation_id=eq.{eid}",
                      headers=user_headers(users[("a", "admin")]["token"]), json={"note": "rewritten"})  # fmt: skip
    check("escalation history cannot be edited", edit.status_code in (401, 403), f"HTTP {edit.status_code}")

    # --- 6c. hierarchy (Phase 15): an LGA coordinator gets the escalation first and sends it up to the admins
    admin_a, coord = users[("a", "admin")], users[("a", "researcher")]
    r = http.post(f"{URL}/rest/v1/rpc/change_member_role", headers=user_headers(admin_a["token"]),
                  json={"p_org": orgs["a"], "p_user": coord["id"], "p_role": "coordinator"})  # fmt: skip
    area = {"org_id": orgs["a"], "user_id": coord["id"], "level": "lga", "state": "CI State", "lga": "CI LGA"}
    denied = http.post(f"{URL}/rest/v1/area_assignments", headers=user_headers(users[("a", "analyst")]["token"]),
                       json={**area, "created_by": users[("a", "analyst")]["id"]})  # fmt: skip
    made = http.post(f"{URL}/rest/v1/area_assignments", headers=user_headers(admin_a["token"]),
                     json={**area, "created_by": admin_a["id"]})  # fmt: skip
    check("only admins assign areas (role changed to coordinator, LGA assigned)",
          r.status_code in (200, 204) and made.status_code == 201 and denied.status_code in (401, 403),
          f"role {r.status_code}, admin {made.status_code}, analyst {denied.status_code}")  # fmt: skip
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as db:
        f2 = str(db.execute(
            """INSERT INTO public.findings (org_id, project_id, dataset_version_id, analytical_run_id, category,
               indicator, severity, confidence, title, explanation, limitations, provenance, state, lga, ward, pu_code)
               SELECT org_id, project_id, dataset_version_id, analytical_run_id, category, indicator, severity,
                      confidence, 'CI: unusual turnout in CI LGA', explanation, limitations, provenance,
                      'CI State', 'CI LGA', 'CI Ward', 'CI-PU-1'
               FROM public.findings WHERE id = %s RETURNING id""",
            (findings["a"],),
        ).fetchone()[0])  # fmt: skip
    r = api.post(f"/findings/{f2}/reviews", headers=bearer("a", "analyst"),
                 json={"decision": "escalated", "reason": "CI: turnout needs the LGA coordinator."})  # fmt: skip
    e2 = r.json().get("escalation_id") if r.status_code == 201 else None
    got = api.get(f"/escalations/{e2}", headers=bearer("a", "researcher")).json() if e2 else {}
    seen = http.get(f"{URL}/rest/v1/notifications?select=kind&link=eq./escalations/{e2}",
                    headers=user_headers(coord["token"])).json()  # fmt: skip
    check("escalation goes to the LGA coordinator first, who is notified",
          got.get("current_level") == "lga" and got.get("can_send_up") is True and len(seen) == 1, str(got)[:160])  # fmt: skip
    up = api.post(f"/escalations/{e2}/send-up", headers=bearer("a", "researcher"),
                  json={"note": "CI: needs the admins."})  # fmt: skip
    told = http.get(f"{URL}/rest/v1/notifications?select=kind&link=eq./escalations/{e2}",
                    headers=user_headers(admin_a["token"])).json()  # fmt: skip
    fin = api.post(f"/escalations/{e2}/resolve", headers=bearer("a", "admin"), json={"note": "CI: resolved by admin."})
    check("coordinator sends it up; admins are notified and resolve it",
          up.status_code == 200 and up.json().get("current_level") == "admin"
          and [x["kind"] for x in told] == ["escalation_sent_up"] and fin.json().get("status") == "resolved",
          f"up {up.status_code}, admin inbox {told}, resolve {fin.status_code}")  # fmt: skip

    # --- 6d. field reports (Phase 16): an agent reports only for their own polling unit; critical incidents escalate
    agent = users[("a", "viewer")]
    rpc = f"{URL}/rest/v1/rpc/change_member_role"
    r = http.post(rpc, headers=user_headers(admin_a["token"]),
                  json={"p_org": orgs["a"], "p_user": agent["id"], "p_role": "field_agent"})  # fmt: skip
    pu = {"org_id": orgs["a"], "user_id": agent["id"], "level": "polling_unit", "state": "CI State", "lga": "CI LGA",
          "ward": "CI Ward", "pu_code": "CI-PU-1", "created_by": admin_a["id"]}  # fmt: skip
    made = http.post(f"{URL}/rest/v1/area_assignments", headers=user_headers(admin_a["token"]), json=pu)
    check("admin makes a member a field agent with one polling unit",
          r.status_code in (200, 204) and made.status_code == 201, f"role {r.status_code}, assign {made.status_code}")  # fmt: skip
    agent_h = {"Authorization": f"Bearer {agent['token']}"}
    cid = uuid.uuid4().hex
    rep = {"client_id": cid, "project_id": projects["a"], "kind": "incident", "pu_code": "CI-PU-1",
              "captured_at": datetime.now(UTC).isoformat(), "location": None,
              "payload": {"category": "ballot_snatching", "severity": "critical",
                          "description": "CI: box taken at the unit.", "ongoing": False}}  # fmt: skip
    first = api.post("/field-reports", headers={**agent_h, "Idempotency-Key": cid}, json=rep)
    again = api.post("/field-reports", headers={**agent_h, "Idempotency-Key": cid}, json=rep)
    other = api.post("/field-reports", headers={**agent_h, "Idempotency-Key": cid + "x"},
                     json={**rep, "client_id": cid + "x", "pu_code": "CI-PU-2"})  # fmt: skip
    body = first.json() if first.status_code == 201 else {}
    check("agent sends a report once (repeat returns the same one) and only for their own unit",
          first.status_code == 201 and again.status_code == 200 and again.json().get("id") == body.get("id")
          and other.status_code == 403, f"first {first.status_code}, again {again.status_code}, other {other.status_code}")  # fmt: skip
    eid = body.get("escalation_id")
    esc = api.get(f"/escalations/{eid}", headers=bearer("a", "researcher")).json() if eid else {}
    check("a critical incident goes straight to the area coordinator",
          esc.get("current_level") == "lga" and esc.get("source_kind") == "field_report", str(esc)[:160])  # fmt: skip
    mine = api.get(f"/field-reports?project_id={projects['a']}", headers=agent_h).json().get("items", [])
    seen_b = api.get(f"/field-reports?project_id={projects['a']}", headers=bearer("b", "admin")).status_code
    check("agent sees their own report; another organisation cannot list them",
          [x["id"] for x in mine] == [body.get("id")] and seen_b in (403, 404), f"mine {len(mine)}, org B {seen_b}")  # fmt: skip
    if esc.get("id"):
        api.post(f"/escalations/{esc['id']}/resolve", headers=bearer("a", "admin"), json={"note": "CI: closed."})
    r = http.post(rpc, headers=user_headers(admin_a["token"]),
                  json={"p_org": orgs["a"], "p_user": agent["id"], "p_role": "viewer"})  # fmt: skip
    gone = http.get(f"{URL}/rest/v1/area_assignments?select=id&user_id=eq.{agent['id']}",
                    headers=user_headers(admin_a["token"])).json()  # fmt: skip
    check("changing the role back removes the polling unit", r.status_code in (200, 204) and gone == [], str(gone)[:80])

    # --- 6e. live view and signed submission pack (Phase 17)
    lv = api.get(f"/projects/{projects['a']}/live", headers=bearer("a", "viewer"))
    row = next((x for x in lv.json().get("areas", []) if x["lga"] == "CI LGA"), {}) if lv.status_code == 200 else {}
    check("live view counts the field report and its critical incident",
          row.get("incidents", 0) >= 1 and row.get("critical", 0) >= 1, f"HTTP {lv.status_code} {row}")  # fmt: skip
    made = api.post(f"/projects/{projects['a']}/submission-packs",
                    headers={**bearer("a", "admin"), "Idempotency-Key": uuid.uuid4().hex})  # fmt: skip
    pid = made.json().get("id") if made.status_code == 201 else None
    dl = api.get(f"/submission-packs/{pid}/download", headers=bearer("a", "viewer")) if pid else None
    other_org = api.get(f"/submission-packs/{pid}/download", headers=bearer("b", "admin")).status_code if pid else 0
    check("admin makes a signed pack; it is stored and only the organisation can download it",
          dl is not None and dl.status_code == 200 and other_org == 404,
          f"make {made.status_code} {made.text[:120] if made.status_code != 201 else ''}, "
          f"download {dl.status_code if dl else '-'}, org B {other_org}")  # fmt: skip
    blob = base64.b64decode(dl.json()["zip_base64"]) if dl is not None and dl.status_code == 200 else b""

    def verify_blob(b: bytes) -> dict:
        r = api.post("/submission-packs/verify", headers=bearer("b", "viewer"),
                     json={"zip_base64": base64.b64encode(b).decode()})  # fmt: skip
        return r.json() if r.status_code == 200 else {"valid": None, "status": r.status_code}

    good = verify_blob(blob) if blob else {}
    tampered = b""
    if blob:
        src, out = zipfile.ZipFile(io.BytesIO(blob)), io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for n in src.namelist():
                z.writestr(n, src.read(n) + (b"CI-PU-9,1\n" if n == "results_by_polling_unit.csv" else b""))
        tampered = out.getvalue()
    bad = verify_blob(tampered) if tampered else {}
    key = api.get("/signing-key")
    check("pack checks out (signature, key, files, recorded); a changed file is caught",
          good.get("valid") is True and bad.get("valid") is False and key.status_code == 200
          and key.json().get("key_id") == good.get("key_id"),
          f"good {good.get('valid')}, tampered {bad.get('valid')}, key {key.status_code}")  # fmt: skip

    # --- 7. storage
    def download(tag: str, role: str, path: str) -> int:
        return http.get(
            f"{URL}/storage/v1/object/authenticated/datasets/{path}",
            headers=user_headers(users[(tag, role)]["token"]),
        ).status_code

    own = download("a", "viewer", paths["a"])
    cross = download("b", "admin", paths["a"])
    check("member can read own org file", own == 200, f"HTTP {own}")
    check("other org cannot read the file", cross in (400, 403, 404), f"HTTP {cross}")
    up = http.post(
        f"{URL}/storage/v1/object/datasets/{orgs['a']}/direct-upload.csv",
        headers={**user_headers(users[("a", "admin")]["token"]), "Content-Type": "text/csv"},
        content=b"x\n1\n",
    )
    check(
        "browser cannot upload directly (server-issued signed URLs only)",
        up.status_code in (400, 403),
        f"HTTP {up.status_code}",
    )

    # --- 8. audit chain
    r = http.post(
        f"{URL}/rest/v1/rpc/audit_chain_status",
        headers=user_headers(users[("a", "viewer")]["token"]),
        json={"p_org": orgs["a"]},
    )
    body = r.json() if r.status_code == 200 else {}
    check(
        "audit chain intact for org A",
        body.get("intact") is True and body.get("events", 0) >= 8,
        str(body)[:160],
    )

    return report()


def report() -> int:
    lines = ["## Live Supabase security check", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {'PASS' if ok else 'FAIL'} | {d.replace('|', '/')} |" for n, ok, d in results]
    text = "\n".join(lines)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write(text + "\n")
    passed = all(ok for _, ok, _ in results) and len(results) > 0
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
