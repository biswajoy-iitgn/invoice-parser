"""Persist one extraction to PostgreSQL (Neon in prod, local for tests).

No-op unless DATABASE_URL and INVOICE_ORG_ID are both set: the API must
serve extractions without a database. Failures to persist are reported to
the caller (never silent); the extraction JSON itself is unaffected.
"""

from __future__ import annotations

import hashlib
import json
import os


def evidence_dir():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return Path(os.environ.get("EVIDENCE_DIR", root / "data" / "evidence"))


def evidence_url(invoice_id: str) -> str:
    """The address stored in the database and shown in Excel. PUBLIC_BASE_URL is how other computers reach the app."""
    base = os.environ.get("PUBLIC_BASE_URL", f"http://127.0.0.1:{os.environ.get('PORT', '8000')}").rstrip("/")
    return f"{base}/evidence/{invoice_id}"


def persist_extraction(*, filename: str, mime: str, size: int, data_sha256: str,
                       fields: dict, audit: dict, pipeline_version: str = "invoice-extraction-v1",
                       model_config: str | None = None, source_path: str | None = None) -> dict:
    from database.db import Store
    from invoice_extractor.evidence import build_candidates
    from invoice_extractor.ontology import to_canonical

    org = os.environ.get("INVOICE_ORG_ID", "")
    if not org or not os.environ.get("DATABASE_URL"):
        return {"persisted": False, "reason": "DATABASE_URL/INVOICE_ORG_ID not set"}
    store = Store(org)
    if model_config is None and audit.get("pipeline") == "v2":
        try:      # which exact models and rules produced this result (see invoice_extractor/versions.py)
            from invoice_extractor.versions import current

            cfg = current()
            store.register_model_config(cfg["name"], cfg["components"], "invoice pipeline v2")
            model_config = cfg["name"]
        except Exception:
            model_config = None
    # CPU-heavy evidence rendering happens BEFORE opening the transaction, so the
    # single per-invoice connection is never held open while drawing pictures.
    # The staged files are moved into their final invoice/run directory inside
    # the transaction (cheap renames); only add_evidence touches the database.
    import tempfile
    from pathlib import Path as _Path

    staging, staged, evidence_error = None, [], ""
    if source_path and audit.get("pipeline") == "v2" and os.environ.get("EVIDENCE_IMAGES", "1") == "1":
        try:
            from invoice_extractor.evidence_image import render_evidence
            from invoice_extractor.pipeline import PIPELINE_REV

            staging = _Path(tempfile.mkdtemp(prefix="evidence-"))
            staged = render_evidence(source_path, fields, audit, staging, revision=PIPELINE_REV)
        except Exception as exc:      # the extraction is saved even when a picture cannot be drawn
            import shutil as _shutil

            if staging is not None:
                _shutil.rmtree(staging, ignore_errors=True)
            staging, staged, evidence_error = None, [], f"{type(exc).__name__}: {exc}"[:200]
    with store.transaction():      # one connection, all-or-nothing per invoice
        doc = store.upsert_document(filename=filename, mime=mime, size=size,
                                    uri=f"upload:{filename}", sha256=data_sha256,
                                    pages=len(audit.get("ingest_pages") or audit.get("pages", [])) or None)
        if audit.get("pipeline") == "v2" and pipeline_version == "invoice-extraction-v1":
            pipeline_version = "invoice-extraction-v2"
        run = store.create_run(document_id=str(doc["id"]),
                               pipeline_version=pipeline_version,
                               model_config=model_config, input_hash=data_sha256)
        canonical = to_canonical(fields)
        inv = store.save_invoice(run_id=str(run["id"]), document_id=str(doc["id"]),
                                 canonical=canonical,
                                 vendor_name=str(fields.get("Vendor Name", "")),
                                 review_route=audit.get("review_route"))
        if audit.get("pipeline") == "v2":
            # provenance straight from the pipeline: the value, where it was read, and how sure it was
            cands = []
            for name, e in (audit.get("fields_explained") or {}).items():
                code = to_canonical({name: 1})
                if not fields.get(name) or not code:
                    continue
                hit = (e.get("citations") or [{}])[0]
                parts = e.get("contributions") or []
                cands.append({"canonical_field": next(iter(code)), "value": fields[name], "confidence": e.get("confidence"),
                              "source_text": (parts[0].get("description") if parts else hit.get("text")),
                              "bbox": hit.get("bbox"), "page": hit.get("page"), "evidence_score": hit.get("score"),
                              "semantic_score": e.get("confidence") if parts else None, "validation_score": None})
        else:
            cands = [c.to_dict() for c in build_candidates(fields, audit, citations=audit.get("citations"))]
        for c in cands:
            c["route"] = audit.get("review_route", "REVIEW")
        store.add_field_results(str(run["id"]), cands)
        timing = audit.get("timing_sec") or {}
        final = {"fields": fields, "review_route": audit.get("review_route"), "warnings": audit.get("warnings", []),
                 "citation_coverage": audit.get("citation_coverage"), "subtotal_reconciled": audit.get("subtotal_reconciled"),
                 "pages": audit.get("ingest_pages"), "timing_sec": timing,
                 "line_items": [{"description": i.get("description"), "amount_inr": i.get("amount_inr"),
                                 "category": i.get("category"), "confidence": i.get("category_confidence"),
                                 "reader_category": i.get("reader_category"), "basis": (i.get("semantic") or {}).get("basis"),
                                 "excluded": i.get("excluded")} for i in audit.get("line_items", [])]}
        store.finish_run(str(run["id"]), ok=True, ms=int(sum(v for v in timing.values() if isinstance(v, (int, float))) * 1000),
                         raw=json.loads(json.dumps(audit.get("reader_raw"), default=str)) if audit.get("reader_raw") else None,
                         final=json.loads(json.dumps(final, default=str)))
        # full capture: every printed item of the document with its box (invoice_extractor/facts.py)
        facts_stored, facts_error = 0, ""
        if audit.get("facts"):
            try:
                facts_stored = store.add_facts(invoice_id=str(inv["id"]), run_id=str(run["id"]), facts=audit["facts"])
            except Exception as exc:      # auxiliary capture must not roll back the invoice
                facts_error = f"{type(exc).__name__}: {exc}"[:200]
        # the same items arranged under named details by the organizer (invoice_extractor/organizer.py)
        org = audit.get("organized") or {}
        details_stored = 0
        if org.get("details"):
            details_stored = store.add_attributes(invoice_id=str(inv["id"]), run_id=str(run["id"]),
                                                  details=org["details"], model=org.get("model", ""))
        # evidence: pre-rendered pages moved into place; only path + hash go to the database
        evidence = staged
        if evidence:
            import shutil

            rel = f"{inv['id']}/{run['id']}"
            dest = evidence_dir() / rel
            dest.mkdir(parents=True, exist_ok=True)
            for p in evidence:
                shutil.move(str(staging / p["file"]), dest / p["file"])
                p["storage_uri"] = f"{rel}/{p['file']}"
            shutil.rmtree(staging, ignore_errors=True)
            store.add_evidence(invoice_id=str(inv["id"]), run_id=str(run["id"]), pages=evidence,
                               url=evidence_url(str(inv["id"])))
        store.log_audit(entity="invoice", entity_id=str(inv["id"]),
                        action="CREATE" if inv.get("inserted", True) else "REPROCESS",
                        new={"invoice_number": canonical.get("invoice_number"), "extraction_run_id": str(run["id"]),
                             "document_sha256": data_sha256, "file": filename, "route": audit.get("review_route"),
                             "model_configuration": model_config, "fields": fields,
                             "evidence": [{"page": p["page"], "sha256": p["sha256"], "boxes": p["boxes"]} for p in evidence],
                             "items_captured": facts_stored, "details_arranged": details_stored,
                             "arranged_by": org.get("model", "") if details_stored else ""})
    out = {"persisted": True, "invoice_id": str(inv["id"]),
           "extraction_run_id": str(run["id"]), "document_id": str(doc["id"]),
           "evidence_pages": len(evidence), "evidence_url": evidence_url(str(inv["id"])) if evidence else "",
           "items_captured": facts_stored, "details_arranged": details_stored}
    if evidence_error:
        out["evidence_error"] = evidence_error
    if facts_error:
        out["facts_error"] = facts_error
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
