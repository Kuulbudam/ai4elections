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

import os
import secrets
import sys
import uuid

import httpx
import psycopg

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
                check(f"sign in {role}-{tag}", False, f"HTTP {t.status_code}")
                return report()
    check("register + password sign-in for 8 users", True)

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
                    "created_by) VALUES (%s,%s,'analyse',%s,'ci',%s) RETURNING id",
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
    check("8 users × 7 tables: own org visible, other org never", not leaks, "; ".join(leaks[:5]))

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
