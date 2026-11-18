"""Validation job: raw rows + mapping → canonical records, validation issues, row statuses.

The previous validation of the same version (if any) is replaced atomically together with the
job's success, so a retry or a re-run never duplicates records or issues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb

from app.jobs import ledger
from app.validation.engine import SCHEMA_VERSION, validate
from app.validation.rules import RULESET_VERSION, definitions

if TYPE_CHECKING:
    from app.jobs.worker import JobContext


def run_validate(ctx: JobContext) -> None:
    from app.jobs.worker import JobError

    job, conn = ctx.job, ctx.conn
    v = conn.execute(
        "SELECT * FROM public.dataset_versions WHERE id = %s AND org_id = %s AND project_id = %s",
        (str(job["target_id"]), str(job["org_id"]), str(job["project_id"])),
    ).fetchone()
    if v is None:
        raise JobError("JOB_TARGET_MISMATCH", retryable=False)
    if v["status"] != "ingested":
        raise JobError("VERSION_NOT_INGESTED", retryable=False)
    mapping = (job["payload"] or {}).get("mapping")
    if not mapping:
        raise JobError("MAPPING_MISSING", retryable=False)

    raw = conn.execute(
        "SELECT id::text, source_row_number, cells FROM public.raw_rows WHERE dataset_version_id = %s "
        "ORDER BY source_row_number",
        (str(v["id"]),),
    ).fetchall()
    units = conn.execute(
        "SELECT state, lga, ward FROM public.admin_units WHERE project_id = %s AND org_id = %s",
        (str(v["project_id"]), str(v["org_id"])),
    ).fetchall()
    ctx.heartbeat(0.3, force=True)
    result = validate(
        list(v["header"]["columns"]),
        [(r["id"], r["source_row_number"], r["cells"]) for r in raw],
        mapping,
        [(u["state"], u["lga"], u["ward"]) for u in units] or None,
    )
    if result.mapping_issues:
        raise JobError("MAPPING_INCOMPLETE", retryable=False)
    ctx.heartbeat(0.6, force=True)

    org, vid, jid = str(v["org_id"]), str(v["id"]), str(job["id"])
    summary = result.summary() | {"ruleset_version": RULESET_VERSION, "schema_version": SCHEMA_VERSION}
    with conn.transaction():
        for d in definitions():
            conn.execute(
                "INSERT INTO public.validation_rules (rule_id, version, definition) VALUES (%s,%s,%s) "
                "ON CONFLICT (rule_id, version) DO NOTHING",
                (d["rule_id"], d["version"], Jsonb(d)),
            )
        conn.execute("DELETE FROM public.validation_issues WHERE dataset_version_id = %s", (vid,))
        conn.execute("DELETE FROM public.result_records WHERE dataset_version_id = %s", (vid,))
        with conn.cursor().copy(
            "COPY public.result_records (org_id, dataset_version_id, raw_row_id, schema_version, pu_code, state, lga,"
            " ward, registered_voters, accredited_voters, valid_votes, rejected_votes, total_cast, party_votes,"
            " latitude, longitude, row_status, excluded_from, exclusion_reasons) FROM STDIN"
        ) as cp:
            for r in result.records:
                x = r.values
                cp.write_row((
                    org, vid, r.raw_row_id, SCHEMA_VERSION, x["pu_code"], x["state"], x["lga"], x["ward"],
                    x["registered_voters"], x["accredited_voters"], x["valid_votes"], x["rejected_votes"],
                    x["total_cast"], Jsonb(r.party_votes), x["latitude"], x["longitude"], r.row_status,
                    r.excluded_from, Jsonb(r.exclusion_reasons),
                ))  # fmt: skip
        with conn.cursor().copy(
            "COPY public.validation_issues (org_id, dataset_version_id, job_id, raw_row_id, rule_id, rule_version,"
            " severity, source_row_number, source_column, original_value, message) FROM STDIN"
        ) as cp:
            for i in result.issues:
                cp.write_row((org, vid, jid, i.raw_row_id, i.rule_id, RULESET_VERSION, i.severity,
                              i.source_row_number, i.source_column, i.original_value, i.message))  # fmt: skip
        conn.execute(
            "UPDATE public.dataset_versions SET validation_job_id = %s, validated_at = now(), validation_summary = %s "
            "WHERE id = %s",
            (jid, Jsonb(summary), vid),
        )
        ledger.mark_succeeded(conn, jid, ctx.worker_id)
