"""Audit trail: everything the database knows about one invoice, and the integrity of the audit log.

An invoice's trail answers: which file it came from (hash), every extraction run (pipeline and model
versions, time taken, routing, warnings), where each value was read on the document (page and box),
and every later action (review, correction, export) with who and when.
"""
from __future__ import annotations

import os

from .db import connect


def verify_chain() -> dict:
    """Recompute the audit log's hash chain. intact=False names the first entry that was altered."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT checked, first_bad_seq FROM audit_chain_verify()")
        row = cur.fetchone()
        cur.execute("SELECT count(*) AS n, min(created_at) AS first, max(created_at) AS last FROM audit_logs")
        info = cur.fetchone()
    return {"intact": row["first_bad_seq"] is None, "entries": info["n"], "checked": row["checked"],
            "first_altered_entry": row["first_bad_seq"], "first": str(info["first"] or ""), "last": str(info["last"] or "")}


def invoice_trail(invoice_number: str, org: str | None = None) -> dict:
    org = org or os.environ.get("INVOICE_ORG_ID")
    with connect() as conn, conn.cursor() as cur:
        if org:
            cur.execute("SELECT set_config('app.current_org', %s, TRUE)", (org,))
        cur.execute(
            """SELECT i.id, i.invoice_number, i.invoice_date, v.name::text AS vendor, i.status, i.review_status,
                      i.extraction_status, i.created_at, i.updated_at, i.document_id, i.current_extraction_run_id
                 FROM invoices i LEFT JOIN vendors v ON v.id = i.vendor_id
                WHERE i.invoice_number = %s""" + (" AND i.organization_id = %s" if org else ""),
            (invoice_number, org) if org else (invoice_number,))
        invoices = cur.fetchall()
        out = []
        for inv in invoices:
            cur.execute("""SELECT original_filename, mime_type, file_size, document_hash, page_count, created_at
                             FROM documents WHERE id = %s""", (inv["document_id"],))
            doc = cur.fetchone()
            cur.execute(
                """SELECT r.id, r.pipeline_version, mc.name AS model_configuration, r.status, r.started_at, r.completed_at,
                          r.processing_time_ms, r.input_hash, r.error_message,
                          r.final_output_json->>'review_route' AS route, r.final_output_json->'warnings' AS warnings,
                          r.final_output_json->'line_items' AS line_items
                     FROM extraction_runs r LEFT JOIN model_configurations mc ON mc.id = r.model_configuration_id
                    WHERE r.document_id = %s
                       OR r.id IN (SELECT (new_json->>'extraction_run_id')::uuid FROM audit_logs
                                    WHERE entity_id = %s AND new_json ? 'extraction_run_id')
                    ORDER BY r.started_at""", (inv["document_id"], inv["id"]))
            runs = cur.fetchall()
            cur.execute(
                """SELECT canonical_code, value, confidence, source_text, source_page, source_bbox, route
                     FROM extraction_field_results WHERE extraction_run_id = %s ORDER BY canonical_code""",
                (inv["current_extraction_run_id"],))
            fields = cur.fetchall()
            models = []
            cfg = next((r["model_configuration"] for r in runs if r["id"] == inv["current_extraction_run_id"]), None)
            if cfg:
                cur.execute(
                    """SELECT cm.role, m.name AS model, mv.version, mv.checkpoint_uri, mv.configuration
                         FROM model_configurations c
                         JOIN model_configuration_models cm ON cm.model_configuration_id = c.id
                         JOIN model_versions mv ON mv.id = cm.model_version_id JOIN models m ON m.id = mv.model_id
                        WHERE c.name = %s ORDER BY cm.role""", (cfg,))
                models = cur.fetchall()
            cur.execute("""SELECT seq, action, entity, old_json, new_json, created_at, row_hash FROM audit_logs
                            WHERE entity_id = %s ORDER BY seq""", (inv["id"],))
            log = cur.fetchall()
            out.append({"invoice": inv, "document": doc, "runs": runs, "current_run_fields": fields,
                        "models_of_current_run": models, "audit_log": log})
    return {"invoice_number": invoice_number, "matches": len(out), "trails": out}
