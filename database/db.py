"""Neon/Local PostgreSQL access layer.

- DSN from DATABASE_URL (read-write) / DATABASE_URL_READONLY; SSL required
  for non-local hosts. Refuses to run migrations against a URL whose host
  looks like production unless ALLOW_PROD_MIGRATE=1.
- Every transactional call sets `app.current_org` (RLS) from the explicit
  organization_id argument. No implicit tenant.
- All queries are parameterised (psycopg); identifiers for the reporting
  view are static SQL in migrations, never interpolated.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = sorted(Path(__file__).parent.glob("migrations/*.sql"))


def _dsn(readonly: bool = False) -> str:
    url = os.environ.get("DATABASE_URL_READONLY" if readonly else "DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (and no readonly URL for read path)")
    return url


def _is_local(url: str) -> bool:
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1", "") or url.startswith("postgres:///"):
        return True
    if host:
        return False
    # hostless URL: only a unix-socket dir passed as a host= query param counts
    vals = [v for qs in parse_qs(parts.query).values() for v in qs]
    return any(v.startswith("/tmp") for v in vals)


def connect(readonly: bool = False):
    url = _dsn(readonly)
    if not _is_local(url) and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    # ponytail: 8s bound so one hung database cannot stall /ready or page loads.
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=8)


def migrate(url: str | None = None) -> list[str]:
    """Apply pending migrations in filename order. Returns applied files."""
    target = url or _dsn(False)
    host = (urlparse(target).hostname or "").lower()
    if not _is_local(target) and os.environ.get("ALLOW_PROD_MIGRATE") != "1":
        raise RuntimeError(f"refusing to migrate non-local host {host!r} without ALLOW_PROD_MIGRATE=1")
    applied = []
    with psycopg.connect(target) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
            done = {r[0] for r in cur.execute("SELECT filename FROM schema_migrations").fetchall()}
            for path in MIGRATIONS:
                if path.name in done:
                    continue
                cur.execute(path.read_text())
                cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
                applied.append(path.name)
    return applied


_LEGAL_FORMS = (("pvt", "private"), ("ltd", "limited"), ("co", "company"), ("corp", "corporation"), ("inc", "incorporated"))


def vendor_key(name: str) -> str:
    """What makes two spellings the same vendor: case, punctuation, "&" / "and", and Pvt Ltd / Private Limited."""
    import re as _re

    words = _re.sub(r"[^a-z0-9]+", " ", str(name).lower().replace("&", " and ")).split()
    forms = dict(_LEGAL_FORMS)
    return " ".join(forms.get(w, w) for w in words)


class Store:
    """Organization-scoped persistence. One instance per request/tenant."""

    def __init__(self, organization_id: str, readonly: bool = False):
        self.org = organization_id
        self.readonly = readonly
        self._tx = None

    @contextmanager
    def _conn(self):
        """Tenant-scoped connection. Inside `transaction()` every call shares one connection and
        commits together; otherwise each call is its own short transaction."""
        if self._tx is not None:
            yield self._tx
            return
        with connect(self.readonly) as conn:      # commit (or rollback on error), then close
            # SET takes no bind params; set_config(..., is_local=TRUE) is equivalent
            # and runs inside the caller's transaction.
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_org', %s, TRUE)", (self.org,))
            yield conn

    @contextmanager
    def transaction(self):
        """Group several calls into ONE connection and ONE all-or-nothing transaction
        (one TLS handshake per invoice instead of six; no half-written invoices)."""
        with self._conn() as conn:
            self._tx = conn
            try:
                yield self
            finally:
                self._tx = None

    # -- documents / versions (idempotent on content hash) -----------------
    def upsert_document(self, *, filename: str, mime: str, size: int, uri: str,
                        sha256: str, pages: int | None, created_by=None) -> dict:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (organization_id, original_filename, mime_type,
                       file_size, storage_uri, document_hash, page_count, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (organization_id, document_hash) DO UPDATE
                     SET original_filename = EXCLUDED.original_filename,
                         updated_at = now()
                   RETURNING *""",
                (self.org, filename, mime, size, uri, sha256, pages, created_by),
            )
            return cur.fetchone()

    # -- model registry (what produced a run) --------------------------------
    def register_model_config(self, name: str, components: list[dict], description: str = "") -> None:
        """Idempotent: store the named set of model/rule versions so runs can point at it."""
        import json

        with self._conn() as conn, conn.cursor() as cur:
            # Race-safe: concurrent first-runs converge on one row; the loser
            # re-reads it and returns (components are already stored).
            cur.execute("INSERT INTO model_configurations (name, description) VALUES (%s, %s) "
                        "ON CONFLICT (name) DO NOTHING RETURNING id",
                        (name, description))
            row = cur.fetchone()
            if row is None:
                return
            cfg = row["id"]
            for c in components:
                cur.execute("INSERT INTO models (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
                            "RETURNING id", (c["model"],))
                mid = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO model_versions (model_id, version, checkpoint_uri, configuration, status)
                       VALUES (%s, %s, %s, %s, 'PRODUCTION')
                       ON CONFLICT (model_id, version) DO UPDATE SET checkpoint_uri = EXCLUDED.checkpoint_uri
                       RETURNING id""",
                    (mid, c["version"], c.get("checkpoint_uri"), json.dumps(c.get("configuration") or {})))
                cur.execute("INSERT INTO model_configuration_models (model_configuration_id, model_version_id, role) "
                            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (cfg, cur.fetchone()["id"], c["role"]))

    # -- extraction runs (append-only) --------------------------------------
    def create_run(self, *, document_id: str,
                   pipeline_version: str, model_config: str | None,
                   input_hash: str) -> dict:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM model_configurations WHERE name = %s", (model_config,))
            row = cur.fetchone()
            cur.execute(
                """INSERT INTO extraction_runs (document_id,
                       pipeline_version, model_configuration_id, input_hash)
                   VALUES (%s,%s,%s,%s) RETURNING *""",
                (document_id, pipeline_version, row["id"] if row else None, input_hash),
            )
            return cur.fetchone()

    def finish_run(self, run_id: str, *, ok: bool, ms: int,
                   raw: dict | None = None, final: dict | None = None,
                   error_code: str | None = None, error_message: str | None = None) -> None:
        import json

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE extraction_runs SET status = %s, completed_at = now(),
                       processing_time_ms = %s, raw_output_json = %s,
                       final_output_json = %s, error_code = %s, error_message = %s
                   WHERE id = %s""",
                ("COMPLETED" if ok else "FAILED", ms,
                 json.dumps(raw) if raw is not None else None,
                 json.dumps(final) if final is not None else None,
                 error_code, (error_message or "")[:2000] if error_message else None, run_id),
            )

    def add_field_results(self, run_id: str, candidates: list[dict]) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO extraction_field_results
                   (extraction_run_id, canonical_code, value, confidence, source_text,
                    source_bbox, source_page, evidence_score, semantic_score,
                    validation_score, route)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(run_id, c.get("canonical_field"), c.get("value"), c.get("confidence"),
                   c.get("source_text"), c.get("bbox"), c.get("page"),
                   c.get("evidence_score"), c.get("semantic_score"),
                   c.get("validation_score"), c.get("route")) for c in candidates],
            )

    # -- invoices / fields / charges ----------------------------------------
    def save_invoice(self, *, run_id: str, document_id: str,
                     canonical: dict, vendor_name: str = "", review_route: str | None = None) -> dict:
        """Persist one invoice from canonical-code fields. Returns the invoice row."""
        from datetime import date as _date

        def _num(s):
            try:
                return float(str(s).replace(",", "")) if str(s).strip() else None
            except ValueError:
                return None

        def _dat(s):
            try:
                return _date.fromisoformat(str(s).strip()) if str(s).strip() else None
            except ValueError:
                return None

        with self._conn() as conn, conn.cursor() as cur:
            vendor_id = None
            if vendor_name:
                # one vendor, one row: "JEENA & COMPANY" and "JEENA AND COMPANY" (both printed on the same
                # invoice) or "... Pvt Ltd" / "... Private Limited" must not become two vendors.
                # The spelling-insensitive key lives in vendors.normalized_name (migration 011) so the
                # match is one indexed lookup and concurrent inserts converge via ON CONFLICT.
                key = vendor_key(vendor_name)
                row = cur.execute("SELECT id FROM vendors WHERE organization_id = %s AND normalized_name = %s",
                                  (self.org, key)).fetchone()
                vendor_id = row["id"] if row else None
                if vendor_id is None:
                    cur.execute(
                        """INSERT INTO vendors (organization_id, name, normalized_name) VALUES (%s,%s,%s)
                           ON CONFLICT (organization_id, normalized_name) DO NOTHING
                           RETURNING id""",
                        (self.org, vendor_name, key),
                    )
                    row = cur.fetchone()
                    if row is None:  # lost a race: re-read the winner
                        row = cur.execute("SELECT id FROM vendors WHERE organization_id = %s AND normalized_name = %s",
                                          (self.org, key)).fetchone()
                    vendor_id = row["id"]
            # The same document read again (after a model or rule change) updates its own invoice row, even when
            # the invoice number or vendor it yields has changed; it never leaves a second row behind.
            number = canonical.get("invoice_number") or None
            same_key = number and vendor_id and cur.execute(
                "SELECT id FROM invoices WHERE organization_id = %s AND vendor_id = %s AND invoice_number = %s",
                (self.org, vendor_id, number)).fetchone()
            same_doc = None if same_key else cur.execute(
                "SELECT id FROM invoices WHERE organization_id = %s AND document_id = %s ORDER BY updated_at DESC LIMIT 1",
                (self.org, document_id)).fetchone()
            # Re-extracting the same document updates its own row (even when the
            # number/vendor changed) and never leaves a second row behind.
            _cols = (number,
                     _dat(canonical.get("invoice_date", "")), vendor_id,
                     canonical.get("container_number") or None,
                     canonical.get("mawb_number") or None, canonical.get("hawb_number") or None,
                     canonical.get("bill_of_lading_hbl") or None,
                     canonical.get("weight") or None, run_id)
            if same_doc:
                cur.execute(
                    """UPDATE invoices SET invoice_number = %s, invoice_date = %s, vendor_id = %s,
                           container_number = %s, mawb_no = %s, hawb_no = %s,
                           bill_of_lading_hbl = %s, weight = %s,
                           current_extraction_run_id = %s, updated_at = now()
                       WHERE id = %s RETURNING *, FALSE AS inserted""",
                    (*_cols, same_doc["id"]),
                )
            else:
                cur.execute(
                    """INSERT INTO invoices (organization_id, document_id,
                           invoice_number, invoice_date, vendor_id, container_number, mawb_no,
                           hawb_no, bill_of_lading_hbl, weight, current_extraction_run_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (organization_id, vendor_id, invoice_number)
                         WHERE invoice_number IS NOT NULL AND vendor_id IS NOT NULL
                         DO UPDATE SET document_id = EXCLUDED.document_id,
                           invoice_date = EXCLUDED.invoice_date,
                           container_number = EXCLUDED.container_number,
                           mawb_no = EXCLUDED.mawb_no, hawb_no = EXCLUDED.hawb_no,
                           bill_of_lading_hbl = EXCLUDED.bill_of_lading_hbl,
                           weight = EXCLUDED.weight,
                           current_extraction_run_id = EXCLUDED.current_extraction_run_id,
                           updated_at = now()
                       RETURNING *, (xmax = 0) AS inserted""",
                    (self.org, document_id, *_cols),
                )
            inv = cur.fetchone()
            # Status columns feed reporting.invoice_export (what Excel shows): the extraction is complete,
            # and an invoice that passed every check needs no review.
            cur.execute("UPDATE invoices SET extraction_status = 'COMPLETED', review_status = %s WHERE id = %s",
                        ("APPROVED" if review_route == "AUTO_ACCEPT" else "PENDING", inv["id"]))
            # Latest extraction wins: replace child rows (insert path has none yet).
            # Header values live on the invoice row and in the run's stored fields;
            # only charges get child rows.
            cur.execute("DELETE FROM invoice_charges WHERE invoice_id = %s", (inv["id"],))
            cts = {r["canonical_code"]: r["id"]
                   for r in cur.execute("SELECT id, canonical_code FROM charge_types").fetchall()}
            for code, val in canonical.items():
                if val is None or str(val).strip() == "":
                    continue
                if code in cts:
                    cur.execute(
                        """INSERT INTO invoice_charges (invoice_id, charge_type_id, amount, currency)
                           VALUES (%s,%s,%s,'INR')""",
                        (inv["id"], cts[code], _num(val)),
                    )
            return inv

    # -- reads ----------------------------------------------------------------
    def add_evidence(self, *, invoice_id: str, run_id: str, pages: list[dict], url: str) -> None:
        """Record the stored evidence images of one extraction and point the invoice at them."""
        with self._conn() as conn, conn.cursor() as cur:
            for p in pages:
                cur.execute(
                    """INSERT INTO invoice_evidence (organization_id, invoice_id, extraction_run_id, page_number,
                           storage_uri, sha256, width, height, boxes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (extraction_run_id, page_number) DO UPDATE
                         SET storage_uri = EXCLUDED.storage_uri, sha256 = EXCLUDED.sha256, width = EXCLUDED.width,
                             height = EXCLUDED.height, boxes = EXCLUDED.boxes""",
                    (self.org, invoice_id, run_id, p["page"], p["storage_uri"], p["sha256"], p.get("width"),
                     p.get("height"), p.get("boxes", 0)))
            cur.execute("UPDATE invoices SET evidence_url = %s WHERE id = %s AND organization_id = %s",
                        (url, invoice_id, self.org))

    def add_facts(self, *, invoice_id: str, run_id: str, facts: list[dict]) -> int:
        """Store everything that was read from the document (invoice_extractor/facts.py), one row per item."""
        import json as _json

        rows = [(self.org, invoice_id, run_id, f["seq"], f["kind"], f.get("section", "")[:300], f.get("label", "")[:300],
                 f["value"][:4000], f.get("value_type") or "text", f.get("field", ""), f["page"],
                 _json.dumps(f["bbox"]), _json.dumps(f["label_bbox"]) if f.get("label_bbox") else None)
                for f in facts if f.get("value")]
        if not rows:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO invoice_facts (organization_id, invoice_id, extraction_run_id, seq, kind, section, label,
                       value, value_type, output_field, page_number, bbox, label_bbox)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (extraction_run_id, seq) DO NOTHING""", rows)
        return len(rows)

    def add_attributes(self, *, invoice_id: str, run_id: str, details: list[dict], model: str) -> int:
        """Store the arranged details of one extraction (invoice_extractor/organizer.py)."""
        import json as _json

        rows = [(self.org, invoice_id, run_id, d["seq"], d["group"], d["detail"], d["value"][:4000],
                 str(d.get("normalized", ""))[:4000], d.get("type", "text"), bool(d.get("valid", True)), d["page"],
                 _json.dumps(d["bbox"]), _json.dumps(d.get("items", [])), d.get("printed_label", "")[:300],
                 d.get("grounding", "")[:300], model) for d in details]
        if not rows:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO invoice_attributes (organization_id, invoice_id, extraction_run_id, seq, grp, detail,
                       value, normalized, value_type, valid, page_number, bbox, source_items, printed_label,
                       grounding, arranged_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (extraction_run_id, seq) DO NOTHING""", rows)
        return len(rows)

    def attributes(self, invoice_id: str) -> list[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT a.seq, a.grp, a.detail, a.value, a.normalized, a.valid, a.page_number, a.printed_label,
                          a.grounding, a.arranged_by
                   FROM invoice_attributes a JOIN invoices i ON i.id = a.invoice_id
                        AND i.current_extraction_run_id = a.extraction_run_id
                   WHERE a.invoice_id = %s AND i.organization_id = %s ORDER BY a.seq""", (invoice_id, self.org))
            return [dict(r) for r in cur.fetchall()]

    def facts(self, invoice_id: str) -> list[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT f.seq, f.page_number, f.kind, f.section, f.label, f.value, f.value_type, f.output_field
                   FROM invoice_facts f JOIN invoices i ON i.id = f.invoice_id
                        AND i.current_extraction_run_id = f.extraction_run_id
                   WHERE f.invoice_id = %s AND i.organization_id = %s ORDER BY f.seq""", (invoice_id, self.org))
            return [dict(r) for r in cur.fetchall()]

    def evidence(self, invoice_id: str) -> dict | None:
        """The invoice's current evidence: its number, vendor, result and the stored pages of its latest run."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT i.id, i.invoice_number, i.invoice_date, v.name::text AS vendor_name, i.review_status,
                          i.updated_at, i.current_extraction_run_id, r.final_output_json, r.pipeline_version
                   FROM invoices i LEFT JOIN vendors v ON v.id = i.vendor_id
                   LEFT JOIN extraction_runs r ON r.id = i.current_extraction_run_id
                   WHERE i.id = %s AND i.organization_id = %s""", (invoice_id, self.org))
            inv = cur.fetchone()
            if not inv:
                return None
            cur.execute(
                """SELECT page_number, storage_uri, sha256, width, height, boxes, created_at FROM invoice_evidence
                   WHERE invoice_id = %s AND extraction_run_id IS NOT DISTINCT FROM %s ORDER BY page_number""",
                (invoice_id, inv["current_extraction_run_id"]))
            pages = cur.fetchall()
            if not pages:           # the newest run stored no images: fall back to the last run that did
                cur.execute(
                    """SELECT page_number, storage_uri, sha256, width, height, boxes, created_at FROM invoice_evidence
                       WHERE invoice_id = %s AND extraction_run_id = (
                           SELECT extraction_run_id FROM invoice_evidence WHERE invoice_id = %s
                           ORDER BY created_at DESC LIMIT 1) ORDER BY page_number""", (invoice_id, invoice_id))
                pages = cur.fetchall()
        return {"invoice": dict(inv), "pages": [dict(p) for p in pages]}

    def invoice_by_number(self, number: str) -> list[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM reporting.invoice_export WHERE invoice_number = %s AND organization_id = %s",
                        (number, self.org))
            return cur.fetchall()

    def audit(self, entity: str, entity_id: str) -> list[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM audit_logs WHERE entity = %s AND entity_id = %s AND organization_id = %s "
                        "ORDER BY created_at",
                        (entity, entity_id, self.org))
            return cur.fetchall()

    def log_audit(self, *, user_id=None, entity: str, entity_id: str,
                  action: str, old=None, new=None) -> None:
        import json

        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_logs (organization_id, user_id, entity, entity_id,
                       action, old_json, new_json)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (self.org, user_id, entity, entity_id, action,
                 json.dumps(old) if old is not None else None,
                 json.dumps(new) if new is not None else None),
            )
