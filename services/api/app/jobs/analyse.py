"""Analysis job: validated records → analytical run, findings, not-scored list.

The run, its findings and the dataset version's pointer to it are written in one transaction
together with the job's success, so a failed or retried job never leaves a half-written run.
Each run is new; earlier runs and their findings (and any reviews of them) are kept.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb

from app.analysis.engine import ENGINE_VERSION, METHOD_VERSION, PARAMETERS, Unit, analyse
from app.jobs import ledger

if TYPE_CHECKING:
    from app.jobs.worker import JobContext


def run_analyse(ctx: JobContext) -> None:
    from app.jobs.worker import JobError

    job, conn = ctx.job, ctx.conn
    v = conn.execute(
        "SELECT * FROM public.dataset_versions WHERE id = %s AND org_id = %s AND project_id = %s",
        (str(job["target_id"]), str(job["org_id"]), str(job["project_id"])),
    ).fetchone()
    if v is None:
        raise JobError("JOB_TARGET_MISMATCH", retryable=False)
    want = (job["payload"] or {}).get("validation_job_id")
    if v["validation_job_id"] is None:
        raise JobError("VERSION_NOT_VALIDATED", retryable=False)
    if want and str(v["validation_job_id"]) != want:
        raise JobError("VALIDATION_CHANGED", retryable=False)  # re-validated since the job was queued

    rows = conn.execute(
        """SELECT r.raw_row_id::text AS raw_row_id, w.source_row_number, r.pu_code, r.state, r.lga, r.ward,
                  r.registered_voters, r.accredited_voters, r.valid_votes, r.party_votes, r.row_status,
                  r.excluded_from, r.exclusion_reasons
           FROM public.result_records r JOIN public.raw_rows w ON w.id = r.raw_row_id
           WHERE r.dataset_version_id = %s ORDER BY w.source_row_number""",
        (str(v["id"]),),
    ).fetchall()
    issues = conn.execute(
        """SELECT raw_row_id::text AS raw_row_id, rule_id, severity, source_row_number, source_column,
                  original_value, message
           FROM public.validation_issues WHERE dataset_version_id = %s
           ORDER BY source_row_number, rule_id, source_column""",
        (str(v["id"]),),
    ).fetchall()
    ctx.heartbeat(0.3, force=True)
    units = [Unit(**r) for r in rows]
    result = analyse(units, [dict(i) for i in issues])
    ctx.heartbeat(0.6, force=True)

    vs = v["validation_summary"] or {}
    provenance = {
        "dataset_version_id": str(v["id"]),
        "validation_job_id": str(v["validation_job_id"]),
        "ruleset_version": vs.get("ruleset_version"),
        "schema_version": vs.get("schema_version"),
        "engine_version": ENGINE_VERSION,
        "method_version": METHOD_VERSION,
    }
    org, vid, jid = str(v["org_id"]), str(v["id"]), str(job["id"])
    summary = result.summary | {"content_hash": result.content_hash}
    with conn.transaction():
        run_id = conn.execute(
            """INSERT INTO public.analytical_runs (org_id, project_id, dataset_version_id, job_id, engine_version,
                   method_version, rule_versions, parameters, status, content_hash, completed_at, summary, not_scored)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'complete',%s,now(),%s,%s) RETURNING id::text""",
            (org, str(v["project_id"]), vid, jid, ENGINE_VERSION, METHOD_VERSION,
             Jsonb({"ruleset": vs.get("ruleset_version"), "schema": vs.get("schema_version")}),
             Jsonb(PARAMETERS), result.content_hash, Jsonb(summary), Jsonb(result.not_scored)),
        ).fetchone()["id"]  # fmt: skip
        with conn.cursor().copy(
            "COPY public.findings (org_id, project_id, dataset_version_id, analytical_run_id, category, indicator,"
            " rule_id, raw_row_id, pu_code, state, lga, ward, severity, confidence, title, explanation, limitations,"
            " peer_group, statistics, exclusions, evidence, provenance) FROM STDIN"
        ) as cp:
            for f in result.findings:
                cp.write_row((
                    org, str(v["project_id"]), vid, run_id, f["category"], f["indicator"], f["rule_id"],
                    f["raw_row_id"], f["pu_code"], f["state"], f["lga"], f["ward"], f["severity"], f["confidence"],
                    f["title"], f["explanation"], f["limitations"],
                    Jsonb(f["peer_group"]) if f["peer_group"] is not None else None,
                    Jsonb(f["statistics"]) if f["statistics"] is not None else None,
                    Jsonb(f["exclusions"]), Jsonb(f["evidence"]), Jsonb(provenance),
                ))  # fmt: skip
        conn.execute(
            "UPDATE public.dataset_versions SET latest_analysis_run_id = %s, analysed_at = now() WHERE id = %s",
            (run_id, vid),
        )
        ledger.mark_succeeded(conn, jid, ctx.worker_id)
