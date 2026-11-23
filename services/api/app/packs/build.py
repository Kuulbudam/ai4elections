"""Build and check signed submission packs (Phase 17).

A pack is a zip with:
  manifest.json   what the pack is, who made it, and the SHA-256 of every other data file
  manifest.sig    Ed25519 signature over the exact bytes of manifest.json (base64)
  public_key.txt  the public key (base64, raw 32 bytes)
  VERIFY.txt      how to check the pack without this platform
  summary.json, results_by_polling_unit.csv, field_reports.csv, incidents.csv, escalations.csv

Result-sheet photos are not included (they carry names and signatures); each row carries the photo's SHA-256 so a
photo shown later can be matched to it. Field agents are listed by a short reference, not by name.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

import psycopg

from app.live import service as live
from app.packs import signing
from app.reports.build import safe_text

FORMAT = "eaas-submission-pack/1"
NOTICE = (
    "Prepared by an independent observer organisation using Election as a System (EaaS). This is not an INEC document. "
    "Figures are what the organisation's observers reported and confirmed; they are not official results."
)
DATA_FILES = ("summary.json", "results_by_polling_unit.csv", "field_reports.csv", "incidents.csv", "escalations.csv")
FIXED = {"manifest.json", "manifest.sig", "public_key.txt", "VERIFY.txt"}
MAX_ZIP_BYTES = 20 * 1024 * 1024

VERIFY_TXT = """How to check this pack
======================

1. manifest.json lists every data file in this zip with its SHA-256. Work out the SHA-256 of each file and compare.
2. manifest.sig is an Ed25519 signature over the exact bytes of manifest.json. Check it with the key in
   public_key.txt. The same key is published by the platform at GET {api}/signing-key (key id {key_id}).

If any file was changed, added or removed after the pack was made, one of these checks fails.

With Python 3 and the "cryptography" package:

    import base64, hashlib, json, zipfile
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    z = zipfile.ZipFile("pack.zip")
    m = z.read("manifest.json")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(z.read("public_key.txt")))
    key.verify(base64.b64decode(z.read("manifest.sig")), m)   # raises if the signature is wrong
    for f in json.loads(m)["files"]:
        assert hashlib.sha256(z.read(f["name"])).hexdigest() == f["sha256"], f["name"]
    print("pack is intact")
"""


def _ref(user_id: str) -> str:
    return "agent-" + hashlib.sha256(user_id.encode()).hexdigest()[:8]


def _csv(header: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([safe_text("" if v is None else v) for v in r])
    return buf.getvalue().encode("utf-8")


def _iso(v: Any) -> str:
    return v.astimezone(UTC).isoformat() if isinstance(v, datetime) else str(v or "")


def files_for(db: psycopg.Connection, org_id: str, project_id: str) -> tuple[dict[str, bytes], dict[str, Any]]:
    s = live.summary(db, org_id, project_id)
    results = live.result_rows(db, project_id)
    parties = [p["party"] for p in s["results"]["party_totals"]]
    res_rows = []
    for r in results:
        pv = {str(p.get("party", "")).upper(): p.get("votes") for p in r["party_votes"]}
        res_rows.append([r["pu_code"], r["state"], r["lga"], r["ward"], *[pv.get(p) for p in parties],
                         *[r[k] for k in live.COUNTS], "; ".join(r["checks_failed"]), r["image_sha256"],
                         _iso(r["confirmed_at"]), "field report" if r["from_field"] else "uploaded"])  # fmt: skip
    reports = db.execute(
        """SELECT id::text AS id, kind, pu_code, state, lga, ward, payload, severity, captured_at, received_at,
                  agent_id::text AS agent_id, sheet_id::text AS sheet_id
           FROM public.field_reports WHERE project_id = %s ORDER BY received_at""",
        (project_id,),
    ).fetchall()
    escs = db.execute(
        """SELECT id::text AS id, source_kind, title, area, current_level, status, created_at, resolved_at, resolution
           FROM public.escalation_overview WHERE project_id = %s ORDER BY created_at""",
        (project_id,),
    ).fetchall()
    incidents = [r for r in reports if r["kind"] == "incident"]
    files = {
        "summary.json": json.dumps(s, default=_iso, indent=2, ensure_ascii=False).encode("utf-8"),
        "results_by_polling_unit.csv": _csv(
            [
                "pu_code",
                "state",
                "lga",
                "ward",
                *parties,
                *live.COUNTS,
                "checks_failed",
                "sheet_photo_sha256",
                "confirmed_at",
                "source",
            ],
            res_rows,
        ),  # fmt: skip
        "field_reports.csv": _csv(
            [
                "report_id",
                "kind",
                "pu_code",
                "state",
                "lga",
                "ward",
                "severity",
                "captured_at",
                "received_at",
                "agent_ref",
                "details_json",
            ],
            [
                [
                    r["id"],
                    r["kind"],
                    r["pu_code"],
                    r["state"],
                    r["lga"],
                    r["ward"],
                    r["severity"],
                    _iso(r["captured_at"]),
                    _iso(r["received_at"]),
                    _ref(r["agent_id"]),
                    json.dumps(r["payload"], ensure_ascii=False, sort_keys=True),
                ]
                for r in reports
            ],
        ),  # fmt: skip
        "incidents.csv": _csv(
            [
                "report_id",
                "pu_code",
                "state",
                "lga",
                "ward",
                "category",
                "severity",
                "ongoing",
                "description",
                "captured_at",
            ],
            [
                [
                    r["id"],
                    r["pu_code"],
                    r["state"],
                    r["lga"],
                    r["ward"],
                    r["payload"].get("category"),
                    r["severity"],
                    r["payload"].get("ongoing"),
                    r["payload"].get("description"),
                    _iso(r["captured_at"]),
                ]
                for r in incidents
            ],
        ),  # fmt: skip
        "escalations.csv": _csv(
            [
                "escalation_id",
                "source",
                "title",
                "state",
                "lga",
                "ward",
                "pu_code",
                "with",
                "status",
                "raised_at",
                "resolved_at",
                "resolution",
            ],
            [
                [
                    e["id"],
                    e["source_kind"],
                    e["title"],
                    *[(e["area"] or {}).get(k) for k in ("state", "lga", "ward", "pu_code")],
                    e["current_level"],
                    e["status"],
                    _iso(e["created_at"]),
                    _iso(e["resolved_at"]),
                    e["resolution"],
                ]
                for e in escs
            ],
        ),  # fmt: skip
    }
    counts = {
        "polling_units_with_results": len(results),
        "field_reports": len(reports),
        "incidents": len(incidents),
        "critical_incidents": sum(1 for r in incidents if r["severity"] == "critical"),
        "escalations": len(escs),
        "open_escalations": sum(1 for e in escs if e["status"] == "open"),
    }
    return files, counts


def build(
    db: psycopg.Connection, *, pack_id: str, org: dict[str, Any], project: dict[str, Any], made_by: str,
    key: signing.Key, api_url: str,
) -> dict[str, Any]:  # fmt: skip
    files, counts = files_for(db, org["id"], project["id"])
    manifest = {
        "format": FORMAT,
        "pack_id": pack_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "organisation": {"id": org["id"], "name": org["name"]},
        "election": {"project_id": project["id"], "name": project["election_name"], "year": project["election_year"]},
        "made_by": made_by,
        "key_id": key.key_id,
        "algorithm": "Ed25519",
        "counts": counts,
        "files": [
            {"name": n, "sha256": hashlib.sha256(files[n]).hexdigest(), "bytes": len(files[n])} for n in DATA_FILES
        ],
        "notice": NOTICE,
    }
    m = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    sig = signing.sign(key, m)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", m)
        z.writestr("manifest.sig", sig)
        z.writestr("public_key.txt", key.public_b64)
        z.writestr("VERIFY.txt", VERIFY_TXT.format(api=api_url or "<the platform's API>", key_id=key.key_id))
        for n in DATA_FILES:
            z.writestr(n, files[n])
    data = buf.getvalue()
    return {"zip": data, "manifest": manifest, "manifest_sha256": hashlib.sha256(m).hexdigest(), "signature": sig,
            "zip_sha256": hashlib.sha256(data).hexdigest(), "counts": counts}  # fmt: skip


def check(data: bytes, server_key: signing.Key | None) -> dict[str, Any]:
    """Check a pack: every result is a plain sentence a person can read."""
    out: list[dict[str, Any]] = []

    def add(id_: str, ok: bool, title: str, detail: str) -> bool:
        out.append({"id": id_, "ok": ok, "title": title, "detail": detail})
        return ok

    manifest: dict[str, Any] = {}
    raw = b""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        raw = z.read("manifest.json")
        manifest = json.loads(raw)
        sig = z.read("manifest.sig").decode().strip()
        pub = z.read("public_key.txt").decode().strip()
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError):
        add("readable", False, "Pack can be opened", "This is not an EaaS submission pack, or it is damaged.")
        return {"valid": False, "checks": out, "manifest": None, "manifest_sha256": None}
    add("readable", manifest.get("format") == FORMAT, "Pack can be opened", f"Format {manifest.get('format')}.")
    good_sig = signing.verify(pub, raw, sig)
    add("signature", good_sig, "Signature matches the manifest",
        "The manifest was signed with the key inside the pack." if good_sig
        else "The manifest was changed after it was signed, or the signature is wrong.")  # fmt: skip
    if server_key is not None:
        ours = pub == server_key.public_b64
        add("key", ours, "Signed by this platform",
            f"Key {signing.key_id_of(pub)} is this platform's signing key." if ours
            else f"Key {signing.key_id_of(pub)} is not this platform's key ({server_key.key_id}).")  # fmt: skip
    listed = {f.get("name"): f for f in manifest.get("files", []) if isinstance(f, dict)}
    bad = [n for n, f in listed.items() if n not in names or hashlib.sha256(z.read(n)).hexdigest() != f.get("sha256")]
    extra = sorted(names - set(listed) - FIXED)
    add("files", not bad and bool(listed), "Every file matches the manifest",
        f"All {len(listed)} data files are unchanged." if not bad
        else f"Changed or missing: {', '.join(sorted(bad))}.")  # fmt: skip
    add("extra", not extra, "No files were added",
        "Nothing extra in the zip." if not extra else f"Not in the manifest: {', '.join(extra)}.")  # fmt: skip
    return {"valid": all(c["ok"] for c in out), "checks": out, "manifest": manifest,
            "manifest_sha256": hashlib.sha256(raw).hexdigest()}  # fmt: skip
