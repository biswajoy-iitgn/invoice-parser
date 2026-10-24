"""Production HTTP API for the invoice-extraction pipeline.

Endpoints
---------
POST /extract   multipart file upload (PDF/PNG/JPG/TIFF) -> extracted fields
GET  /health    liveness: process is up
GET  /ready     readiness: ingest mode, paddle stack, LAYA checkpoint, LLM
GET  /metrics   Prometheus exposition (hand-rolled, no extra deps)

Run::

    uvicorn invoice_extractor.service:app --host 0.0.0.0 --port 8000

The pipeline entry point is :func:`extract.extract_invoice`; this service
adds upload validation, structured logging, request IDs and metrics. The
VLM call is the latency bottleneck, so deploy with multiple workers
(``--workers N``) or behind a queue for batch jobs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from contextlib import asynccontextmanager, contextmanager

from .extract import DEFAULT_MODEL, extract_invoice

UI_INDEX = Path(__file__).parent / "ui" / "index.html"
UI_AUDIT = Path(__file__).parent / "ui" / "audit.html"
UI_SETTINGS = Path(__file__).parent / "ui" / "settings.html"
UI_DATABASE = Path(__file__).parent / "ui" / "database.html"
UPLOAD_DIR = Path(__file__).parent.parent / "work" / "uploads"
PDF_RENDER_ZOOM = 2.0
_UPLOAD_NAME_RE = re.compile(
    r"[0-9a-f]{32}(?:_p\d+)?\.(?:pdf|docx|png|jpg|jpeg|tif|tiff|bmp|webp)")

log = logging.getLogger("invoice_extractor.service")

# Sheet engines: mineru (MinerU 4, default), paddle (PaddleOCR-VL 1.6),
# ppdoclayout (PP-DocLayoutV3 layout-first + PaddleOCR-VL 1.6) and docling
# map a parsed document straight onto the sheet columns. v2 runs the current
# cited pipeline (pipeline.extract_invoice); legacy keeps extract.extract_invoice
# for comparison.
ENGINES = ("mineru", "paddle", "ppdoclayout", "docling", "v2", "legacy")
ENGINE_LABELS = {"mineru": "MinerU 4", "paddle": "PaddleOCR-VL", "ppdoclayout": "PP-DocLayoutV3 + VL",
                 "docling": "Docling", "v2": "v2 pipeline", "legacy": "legacy"}
DEFAULT_ENGINE = os.environ.get("INVOICE_ENGINE", "mineru").lower()
if DEFAULT_ENGINE not in ENGINES:
    DEFAULT_ENGINE = "mineru"

ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_UPLOAD_BYTES = int(os.environ.get("INVOICE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_PDF_PAGES = int(os.environ.get("INVOICE_MAX_PDF_PAGES", "60"))
MODEL = os.environ.get("INVOICE_MODEL", DEFAULT_MODEL)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# ---------------------------------------------------------------------------
# Minimal in-process metrics (Prometheus text exposition, no dependency)
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics = {
    "requests_total": 0,
    "requests_ok": 0,
    "requests_error": 0,
    "needs_review": 0,
    "latency_sum": 0.0,
}


def _bump(key: str, amount: float = 1.0) -> None:
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + amount


# In-flight single/batch extractions, so upgrades wait for true idle.
_EXTRACT_LOCK = threading.Lock()
_EXTRACT_INFLIGHT = 0


@contextmanager
def _track_extraction():
    global _EXTRACT_INFLIGHT
    with _EXTRACT_LOCK:
        _EXTRACT_INFLIGHT += 1
    try:
        yield
    finally:
        with _EXTRACT_LOCK:
            _EXTRACT_INFLIGHT -= 1


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app):
    # Pre-warm the semantic engine off the request path: first /ready would
    # otherwise pay the torch/LAYA model load inside a page-load fetch.
    import threading

    def _warm():
        try:
            from .semantic import engine as _semantic_engine
            _semantic_engine()
        except Exception:
            pass
    threading.Thread(target=_warm, name="warm-semantic", daemon=True).start()
    yield


app = FastAPI(
    title="Invoice Extractor",
    version="1.0.0",
    description="OmniSR + PP-StructureV3 + PaddleOCR-VL + LAYA invoice extraction API.",
    lifespan=_lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error request_id=%s %s %s", request_id, request.method, request.url.path)
        raise
    elapsed = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    log.info(
        "request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
        request_id, request.method, request.url.path,
        getattr(response, "status_code", "?"), elapsed * 1000.0,
    )
    return response


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness probe: always 200 when the process is serving."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Single-page UI: upload an invoice, review fields, download Excel."""
    if not UI_INDEX.exists():
        raise HTTPException(status_code=500, detail="UI not installed")
    return FileResponse(UI_INDEX, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/audit", include_in_schema=False)
def audit_page() -> FileResponse:
    """Audit-trail UI: hash-chain status + per-invoice trail (needs DATABASE_URL)."""
    if not UI_AUDIT.exists():
        raise HTTPException(status_code=500, detail="UI not installed")
    return FileResponse(UI_AUDIT, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/audit/{invoice_number:path}", include_in_schema=False)
def audit_invoice_page(invoice_number: str) -> FileResponse:
    """Same audit UI with the invoice number in the path: /audit/INV-2024-001."""
    return audit_page()


@app.get("/settings", include_in_schema=False)
def settings_page() -> FileResponse:
    """Settings UI: database connection + engine updates."""
    if not UI_SETTINGS.exists():
        raise HTTPException(status_code=500, detail="UI not installed")
    return FileResponse(UI_SETTINGS, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/database", include_in_schema=False)
def database_page() -> FileResponse:
    """Database browser UI: tables, rows, and how tables link together."""
    if not UI_DATABASE.exists():
        raise HTTPException(status_code=500, detail="UI not installed")
    return FileResponse(UI_DATABASE, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/database/{table}", include_in_schema=False)
def database_table_page(table: str) -> FileResponse:
    """Same browser with the table in the path: /database/invoices."""
    return database_page()


_UI_ASSETS = {"mineru.png": "image/png", "paddle.png": "image/png",
              "docling.svg": "image/svg+xml", "ppdoclayout.svg": "image/svg+xml"}


@app.get("/ui/assets/{name}", include_in_schema=False)
def ui_asset(name: str) -> FileResponse:
    """Serve bundled engine logos (safelist; no path traversal)."""
    if name not in _UI_ASSETS:
        raise HTTPException(status_code=404, detail="unknown asset")
    return FileResponse(Path(__file__).parent / "ui" / "assets" / name, media_type=_UI_ASSETS[name])


@app.get("/uploads/{name}", include_in_schema=False)
def uploads(name: str) -> FileResponse:
    """Serve kept uploads / rendered pages back to the UI viewer."""
    if not _UPLOAD_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="not found")
    path = UPLOAD_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


@app.post("/api/export")
async def export_excel(request: Request) -> StreamingResponse:
    """Download extracted fields as a one-sheet .xlsx workbook."""
    import io

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    fields = payload.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="body must be {fields: {name: value}}")
    filename = str(payload.get("filename") or "invoice")

    import openpyxl

    from .schema import OUTPUT_FIELDS

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.append(["Field", "Value"])
    known = set(OUTPUT_FIELDS)
    for key in [k for k in OUTPUT_FIELDS if k in fields] + [k for k in sorted(fields) if k not in known]:
        ws.append([key, fields[key]])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 48
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    out_name = Path(filename).stem or "invoice"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{out_name}.xlsx"'},
    )


def _check_ollama(timeout: float = 2.0) -> Dict[str, object]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in body.get("models", [])]
        return {"reachable": True, "models": names, "want_model": MODEL, "want_present": any(MODEL in n for n in names)}
    except Exception as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


LLAMACPP_HOST = os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080")


def _check_llamacpp(timeout: float = 5.0) -> Dict[str, object]:
    try:
        with urllib.request.urlopen(f"{LLAMACPP_HOST}/health", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return {"reachable": True, "status": body.get("status"), "model": MODEL}
    except Exception as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def _check_database() -> Dict[str, object]:
    """Is persistence switched on, and does the configured database answer for this organization?"""
    url, org = os.environ.get("DATABASE_URL"), os.environ.get("INVOICE_ORG_ID")
    if not url or not org:
        return {"configured": False, "note": "DATABASE_URL / INVOICE_ORG_ID not set: extractions are NOT stored"}
    started = time.perf_counter()
    try:
        from database.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM organizations WHERE id = %s", (org,))
            row = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM schema_migrations")
            migrations = cur.fetchone()["n"]
        if not row:
            return {"configured": True, "reachable": True, "organization": None,
                    "note": "INVOICE_ORG_ID does not exist in this database"}
        return {"configured": True, "reachable": True, "organization": row["name"], "migrations": migrations,
                "latency_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: pipeline mode, OCR (PP-OCRv6), reader server, semantic engine layers."""
    checks: Dict[str, object] = {}
    ok = True
    needs_reader = DEFAULT_ENGINE in ("v2", "legacy")
    pipeline = os.environ.get("INVOICE_PIPELINE", "v2").lower()
    checks["pipeline"] = pipeline
    if pipeline == "legacy":
        from .laya_client import DEFAULT_MODEL_DIR
        from .paddle_backends import paddle_available

        checks["ingest_mode"] = os.environ.get("INVOICE_INGEST", "hunyuan")
        checks["paddle"] = {"available": paddle_available()}
        checks["laya_checkpoint"] = {"path": str(DEFAULT_MODEL_DIR), "present": Path(DEFAULT_MODEL_DIR).exists()}
    else:
        from . import ppocr_client

        checks["ocr"] = {"engine": "PP-OCRv6", "python": ppocr_client.PYTHON, "available": ppocr_client.available(),
                         "error": ppocr_client.last_error()}
        try:
            from . import vl_client

            checks["doc_parser"] = {"model": vl_client.MODEL, "enabled": vl_client.enabled()}
        except Exception as exc:
            checks["doc_parser"] = {"enabled": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
        if needs_reader and not ppocr_client.available():
            ok = False
        try:
            from .semantic import engine

            checks["semantic_backends"] = engine().backends
        except Exception as exc:
            checks["semantic_backends"] = [f"error: {type(exc).__name__}"]
    backend = os.environ.get("READER_BACKEND", "llamacpp" if pipeline != "legacy" else "ollama").lower()
    checks["reader_backend"] = backend
    if backend == "llamacpp":
        llm = _check_llamacpp()
        checks["llamacpp"] = llm
    elif backend == "ollama":
        llm = _check_ollama()
        checks["ollama"] = llm
    else:
        llm = {"reachable": True}
    if needs_reader and not llm.get("reachable"):
        ok = False
    checks["database"] = _check_database()
    if checks["database"].get("configured") and not checks["database"].get("reachable"):
        ok = False
    engines: Dict[str, object] = {"default": DEFAULT_ENGINE}
    try:
        from . import mineru_engine

        engines["mineru"] = {"bin": mineru_engine.resolve_bin()}
    except Exception as exc:
        engines["mineru"] = {"bin": None, "error": f"{type(exc).__name__}: {exc}"[:160]}
    try:
        from . import paddle_vl_client

        engines["paddle"] = {"python": paddle_vl_client.PYTHON,
                             "available": paddle_vl_client.available(),
                             "error": paddle_vl_client.last_error()}
    except Exception as exc:
        engines["paddle"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
    try:
        from . import paddle_vl_client as _pvl
        from . import ppdoclayout_engine

        engines["ppdoclayout"] = {"python": _pvl.PYTHON,
                                  "available": ppdoclayout_engine.available(),
                                  "error": _pvl.last_error()}
    except Exception as exc:
        engines["ppdoclayout"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
    try:
        from . import docling_engine

        engines["docling"] = {"available": docling_engine.available()}
    except Exception as exc:
        engines["docling"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
    if DEFAULT_ENGINE == "mineru" and not engines["mineru"].get("bin"):
        ok = False
    if DEFAULT_ENGINE == "paddle" and not engines["paddle"].get("available"):
        ok = False
    if DEFAULT_ENGINE == "ppdoclayout" and not engines["ppdoclayout"].get("available"):
        ok = False
    if DEFAULT_ENGINE == "docling" and not engines["docling"].get("available"):
        ok = False
    checks["engines"] = engines
    checks["status"] = "ok" if ok else "not-ready"
    return JSONResponse(status_code=200 if ok else 503, content=checks)


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    with _metrics_lock:
        snap = dict(_metrics)
    lines = [
        "# HELP invoice_requests_total total extraction requests",
        "# TYPE invoice_requests_total counter",
        f"invoice_requests_total {snap['requests_total']}",
        "# HELP invoice_requests_ok successful extractions",
        "# TYPE invoice_requests_ok counter",
        f"invoice_requests_ok {snap['requests_ok']}",
        "# HELP invoice_requests_error failed extractions",
        "# TYPE invoice_requests_error counter",
        f"invoice_requests_error {snap['requests_error']}",
        "# HELP invoice_needs_review extractions routed to review",
        "# TYPE invoice_needs_review counter",
        f"invoice_needs_review {snap['needs_review']}",
        "# HELP invoice_latency_seconds_sum total extraction latency",
        "# TYPE invoice_latency_seconds_sum counter",
        f"invoice_latency_seconds_sum {snap['latency_sum']:.3f}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def _save_upload(upload: UploadFile, data: bytes) -> Path:
    suffix = Path(upload.filename or "upload.pdf").suffix.lower() or ".pdf"
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {suffix!r}; allowed: {sorted(ALLOWED_SUFFIXES)}",
        )
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Kept (not temp): the UI viewer serves these back. Clean work/uploads to purge.
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return path


def _display_pages(path: Path) -> tuple[list[dict], float]:
    """Render browser-displayable pages. Returns (pages, bbox_scale).

    Images convert to PNG at native size (scale 1, coordinates exact);
    PDFs render at PDF_RENDER_ZOOM (bboxes must be multiplied by it).
    Office files have no page render (no preview).
    """
    from PIL import Image

    pages: list[dict] = []
    if path.suffix.lower() == ".docx":
        return pages, 1.0
    if path.suffix.lower() == ".pdf":
        import fitz

        with fitz.open(path) as pdf:
            for i, page in enumerate(pdf):
                pix = page.get_pixmap(matrix=fitz.Matrix(PDF_RENDER_ZOOM, PDF_RENDER_ZOOM))
                name = f"{path.stem}_p{i + 1}.png"
                pix.save(UPLOAD_DIR / name)
                pages.append({"number": i + 1, "url": f"/uploads/{name}",
                              "width": pix.width, "height": pix.height})
        return pages, PDF_RENDER_ZOOM
    img = Image.open(path)
    name = f"{path.stem}_p1.png"
    img.save(UPLOAD_DIR / name)
    w, h = img.size
    pages.append({"number": 1, "url": f"/uploads/{name}", "width": w, "height": h})
    return pages, 1.0


BOX_PAD_PX = 5
BOX_MIN_WH = (30, 22)


def _display_hits(hits: list[dict], scale: float, page: dict) -> list[dict]:
    """Scale raw boxes to display pixels + pad to a readable minimum.

    Raw hits are tight word unions; the viewer needs breathing room.
    Stored/DB evidence keeps the exact boxes — this is display-only.
    """
    out = []
    pw, ph = page["width"], page["height"]
    for h in hits:
        h = dict(h)
        box = h.get("bbox") or []
        if len(box) != 4:
            continue  # malformed hit: skip it, never kill the whole render
        x1, y1, x2, y2 = box
        x1, y1, x2, y2 = x1 * scale, y1 * scale, x2 * scale, y2 * scale
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        w = max(x2 - x1 + 2 * BOX_PAD_PX, BOX_MIN_WH[0])
        hgt = max(y2 - y1 + 2 * BOX_PAD_PX, BOX_MIN_WH[1])
        h["bbox"] = [max(0, cx - w / 2), max(0, cy - hgt / 2),
                     min(pw, cx + w / 2), min(ph, cy + hgt / 2)]
        out.append(h)
    return out


def _validate_upload(path: Path) -> List[str]:
    """Content validation beyond suffix: magic bytes + PDF page cap."""
    problems: List[str] = []
    head = path.read_bytes()[:16]
    suffix = path.suffix.lower()
    if suffix == ".docx":
        if not head.startswith(b"PK\x03\x04"):
            problems.append("file suffix is .docx but content is not an Office document")
        return problems
    if suffix == ".pdf":
        if not head.startswith(b"%PDF"):
            problems.append("file suffix is .pdf but content is not a PDF")
            return problems
        try:
            import fitz

            with fitz.open(path) as pdf:
                n = len(pdf)
            if n > MAX_PDF_PAGES:
                problems.append(f"PDF has {n} pages, limit is {MAX_PDF_PAGES}")
            if n == 0:
                problems.append("PDF has no pages")
        except ImportError:
            pass
        except Exception as exc:
            problems.append(f"unreadable PDF: {exc}"[:200])
    else:
        magic_ok = head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*",
                                    b"BM", b"RIFF"))
        if not magic_ok:
            problems.append(f"file suffix {suffix} does not match content magic bytes")
    return problems


class UploadRejected(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _scale_hits(hits, scale, by_no, pages):
    if not pages:
        return []  # docx: no page render, nothing to scale to
    return [h for hit in hits or [] for h in _display_hits([hit], scale, by_no.get(hit.get("page", 1), pages[0]))]


def process_file(path: Path, filename: str, data: bytes | None = None, content_type: str = "",
                 engine: str = "") -> dict:
    """Validate + extract + display rendering for one stored upload (used by /extract and batches)."""
    problems = _validate_upload(path)
    if problems:
        raise UploadRejected(422, "; ".join(problems))
    started = time.perf_counter()
    engine = (engine or DEFAULT_ENGINE).lower()
    if engine == "mineru":
        from . import mineru_engine

        fields, audit = mineru_engine.extract(path)
    elif engine == "paddle":
        from . import paddle_engine

        fields, audit = paddle_engine.extract(path)
    elif engine == "ppdoclayout":
        from . import ppdoclayout_engine

        fields, audit = ppdoclayout_engine.extract(path)
    elif engine == "docling":
        from . import docling_engine

        fields, audit = docling_engine.extract(path)
    elif engine == "v2":
        from . import pipeline as v2_pipeline

        fields, audit = v2_pipeline.extract_invoice(str(path), model=MODEL)
    else:  # "legacy" (or an unknown default): the original cited pipeline
        fields, audit = extract_invoice(str(path), model=MODEL)
    audit.setdefault("engine", engine)
    if engine in ("mineru", "paddle", "ppdoclayout", "docling") and path.suffix.lower() == ".pdf" \
            and not audit.get("citations"):
        try:
            from . import textboxes

            cites = textboxes.citations_for(path, fields)
            if cites:
                audit["citations"] = cites
                audit.setdefault("fields_explained", {}).update(textboxes.explained_for(cites))
        except Exception as exc:
            audit.setdefault("warnings", []).append(f"page boxes unavailable: {exc}"[:160])
    elapsed = time.perf_counter() - started
    audit_out = {k: v for k, v in audit.items() if k not in ("evidence", "layout_text")}
    audit_out["evidence_chars"] = len(audit.get("evidence") or "")
    try:
        pages, scale = _display_pages(path)
        by_no = {p["number"]: p for p in pages}
        audit_out["pages"] = pages
        audit_out["citations"] = {k: _scale_hits(v, scale, by_no, pages)
                                  for k, v in (audit.get("citations") or {}).items()}
        audit_out["line_item_citations"] = [_scale_hits(v, scale, by_no, pages)
                                            for v in (audit.get("line_item_citations") or [])]
        for e in (audit_out.get("fields_explained") or {}).values():
            e["citations"] = _scale_hits(e.get("citations"), scale, by_no, pages)
            for c in e.get("contributions", []):
                c["citations"] = _scale_hits(c.get("citations"), scale, by_no, pages)
        # full capture: every printed item, with its box in the same display coordinates as the page images
        audit_out["facts"] = [{**f, "bbox": [round(v * scale, 1) for v in f["bbox"]],
                               "label_bbox": [round(v * scale, 1) for v in f.get("label_bbox") or []]}
                              for f in audit.get("facts") or [] if len(f.get("bbox") or []) == 4]
        audit_out["facts_scaled"] = True
        # layout-first engines: detected regions + per-region VL readings,
        # scaled the same way for the overlay and reading panel
        audit_out["layout"] = [
            {"page": e.get("page"),
             "regions": [{"label": r.get("label", ""),
                          "bbox": [round(v * scale, 1) for v in r["bbox"]]}
                         for r in e.get("regions") or [] if len(r.get("bbox") or []) == 4]}
            for e in audit.get("layout") or []]
        audit_out["reading"] = [
            {"page": b.get("page"), "label": b.get("label", ""),
             "content": (b.get("content", "") or "")[:1500],
             "bbox": ([round(v * scale, 1) for v in b["bbox"]]
                       if len(b.get("bbox") or []) == 4 else [])}
            # keep unread-but-boxed regions (empty text shows as "—") so row
            # indexes stay aligned with the layout overlay
            for b in audit.get("reading") or []
            if (b.get("content", "") or "").strip() or len(b.get("bbox") or []) == 4]
        audit_out["reading_note"] = audit.get("reading_note", "")
        if isinstance(audit.get("organized"), dict):
            audit_out["organized"] = {**audit["organized"], "details": [
                {**d, "bbox": [round(v * scale, 1) for v in d["bbox"]]}
                for d in audit["organized"].get("details", []) if len(d.get("bbox") or []) == 4]}
    except Exception as exc:
        log.warning("display render failed (extraction unaffected): %s", exc)
        audit_out["pages"] = []
        audit_out["facts_scaled"] = False
        audit_out["render_error"] = f"{type(exc).__name__}: {exc}"[:200]
    persistence: Dict[str, object] = {"persisted": False}
    if os.environ.get("DATABASE_URL") and os.environ.get("INVOICE_ORG_ID"):
        try:
            from database.persist import persist_extraction, sha256_bytes

            if data is None:        # batch items arrive as stored files, not request bodies
                import mimetypes

                data = path.read_bytes()
                content_type = content_type or (mimetypes.guess_type(filename or path.name)[0] or "")

            persistence = persist_extraction(
                filename=filename or "upload", mime=content_type or "", size=len(data),
                data_sha256=sha256_bytes(data), fields=fields, audit=audit,
                model_config=os.environ.get("INVOICE_MODEL_CONFIG"), source_path=str(path))
        except Exception as exc:
            log.warning("persistence failed (extraction unaffected): %s", exc)
            persistence = {"persisted": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
    # Raw (print-scale) boxes travel with the result so batch_persist stores the
    # same coordinates as single extracts; "audit" stays display-scaled for the UI.
    # persist_extraction reads neither blob, so both are dropped to keep results small.
    audit_raw = {k: v for k, v in audit.items() if k not in ("evidence", "layout_text")}
    body = {"filename": filename, "fields": fields, "audit": audit_out, "audit_raw": audit_raw,
            "persistence": persistence,
            "timing": {"total_s": round(elapsed, 3)},
            "limits": {"max_pdf_pages": MAX_PDF_PAGES, "max_upload_bytes": MAX_UPLOAD_BYTES}}
    return json.loads(json.dumps(body, default=str))


@app.post("/extract")
async def extract(request: Request, file: UploadFile = File(...), engine: str = "") -> JSONResponse:
    """Extract structured fields from one uploaded invoice document."""
    _bump("requests_total")
    started = time.perf_counter()
    data = await file.read()
    if not data:
        _bump("requests_error")
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        _bump("requests_error")
        raise HTTPException(status_code=413, detail=f"file exceeds {MAX_UPLOAD_BYTES} byte limit")
    try:
        tmp_path = _save_upload(file, data)
        eng = _check_engine(engine)
        with _track_extraction():
            body = await _run_in_thread(lambda: process_file(tmp_path, file.filename, data, file.content_type or "",
                                                              engine=eng))
        elapsed = time.perf_counter() - started
        _bump("requests_ok")
        _bump("latency_sum", elapsed)
        if body["audit"].get("needs_review"):
            _bump("needs_review")
        body["timing"]["total_s"] = round(elapsed, 3)
        return JSONResponse(content=body)
    except HTTPException:
        raise
    except UploadRejected as exc:
        _bump("requests_error")
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except RuntimeError as exc:
        # Pipeline errors (LLM unreachable, backend failure): client-visible 502.
        _bump("requests_error")
        log.warning("extraction backend failure: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=502,
                            detail="The extraction backend failed. "
                                   "Retry once; if it repeats check /ready.") from exc
    except Exception as exc:  # pragma: no cover - defensive
        _bump("requests_error")
        rid = request.headers.get("X-Request-ID")
        log.exception("extraction failed%s: %s", f" request_id={rid}" if rid else "", exc)
        hint = f" (request {rid})" if rid else ""
        raise HTTPException(status_code=500,
                            detail=f"Extraction failed{hint}. "
                                   "Retry once; if it repeats check /ready.") from exc
    # NOTE: uploads are kept (not deleted): the UI viewer serves them back.


async def _run_in_thread(fn):
    """Run blocking work in a worker thread (the reader call is slow)."""
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn)


# ---------------------------------------------------------------------------
# Batches: many invoices at once
# ---------------------------------------------------------------------------

_BATCHES = None


def batches():
    global _BATCHES
    if _BATCHES is None:
        from .batch import BatchManager

        def runner(path: Path, filename: str, engine: str = "") -> dict:
            _bump("requests_total")
            try:
                with _track_extraction():
                    body = process_file(path, filename, engine=engine)
            except Exception:
                _bump("requests_error")
                raise
            _bump("requests_ok")
            _bump("latency_sum", body["timing"]["total_s"])
            if body["audit"].get("needs_review"):
                _bump("needs_review")
            return body

        _BATCHES = BatchManager(runner)
    return _BATCHES


def _check_engine(engine: str) -> str:
    engine = (engine or "").lower()
    if engine and engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"unknown engine {engine!r}; choose from {list(ENGINES)}")
    return engine


async def _accept_files(batch_id: str, files: List[UploadFile], engine: str = "") -> list[dict]:
    accepted = []
    for f in files:
        data = await f.read()
        entry = {"filename": f.filename}
        if not data:
            entry["error"] = "empty upload"
        elif len(data) > MAX_UPLOAD_BYTES:
            entry["error"] = f"file exceeds {MAX_UPLOAD_BYTES} byte limit"
        else:
            try:
                path = _save_upload(f, data)
                it = batches().add(batch_id, f.filename or path.name, data, path, engine=engine)
                entry.update(id=it.id, status=it.status)
            except HTTPException as exc:
                entry["error"] = exc.detail
        accepted.append(entry)
    return accepted


@app.post("/api/batches")
async def create_batch(files: List[UploadFile] = File(...), engine: str = Form("")) -> JSONResponse:
    """Start a batch with any number of PDF/DOCX/image files; processing runs in the background."""
    if not files:
        raise HTTPException(status_code=400, detail="no files")
    if len(files) > int(os.environ.get("INVOICE_MAX_BATCH_FILES", "500")):
        raise HTTPException(status_code=413, detail="too many files in one request")
    b = batches().create(engine=_check_engine(engine) or DEFAULT_ENGINE)
    accepted = await _accept_files(b.id, files)
    return JSONResponse(content={"batch_id": b.id, "accepted": accepted, **batches().status(b.id)})


@app.post("/api/batches/{batch_id}/files")
async def add_to_batch(batch_id: str, files: List[UploadFile] = File(...), engine: str = Form("")) -> JSONResponse:
    if batch_id not in batches().batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    accepted = await _accept_files(batch_id, files, engine=_check_engine(engine))
    return JSONResponse(content={"accepted": accepted, **batches().status(batch_id)})


@app.get("/api/batches/{batch_id}")
def batch_status(batch_id: str) -> JSONResponse:
    if batch_id not in batches().batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    return JSONResponse(content=json.loads(json.dumps(batches().status(batch_id), default=str)))


@app.get("/api/batches/{batch_id}/items/{item_id}")
def batch_item(batch_id: str, item_id: str) -> JSONResponse:
    try:
        return JSONResponse(content=batches().result(batch_id, item_id))
    except (KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="result not available")


@app.post("/api/batches/{batch_id}/cancel")
def batch_cancel(batch_id: str) -> JSONResponse:
    if batch_id not in batches().batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    return JSONResponse(content={"cancelled": batches().cancel(batch_id)})


@app.post("/api/batches/{batch_id}/items/{item_id}/retry")
def batch_retry(batch_id: str, item_id: str) -> JSONResponse:
    try:
        it = batches().retry(batch_id, item_id)
    except (KeyError, StopIteration):
        raise HTTPException(status_code=404, detail="unknown item")
    return JSONResponse(content={"id": it.id, "status": it.status})


@app.get("/api/batches/{batch_id}/export.xlsx")
def batch_export_xlsx(batch_id: str) -> StreamingResponse:
    import io

    from .batch import export_workbook

    if batch_id not in batches().batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    data = export_workbook(batches().done_results(batch_id))
    return StreamingResponse(io.BytesIO(data),
                             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="invoices_{batch_id}.xlsx"'})


@app.get("/api/batches/{batch_id}/export.json")
def batch_export_json(batch_id: str) -> JSONResponse:
    if batch_id not in batches().batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    out = {it.filename: res.get("fields", {}) for it, res in batches().done_results(batch_id)}
    return JSONResponse(content=out, headers={"Content-Disposition": f'attachment; filename="invoices_{batch_id}.json"'})


@app.get("/api/batches/{batch_id}/sheet")
def batch_sheet(batch_id: str) -> JSONResponse:
    """One row per invoice with every sheet column; unfinished rows carry fields=null (skeleton)."""
    from .schema import OUTPUT_FIELDS

    mgr = batches()
    if batch_id not in mgr.batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    rows = []
    for sl, it in enumerate(mgr.batches[batch_id].items, 1):
        fields = None
        if it.status == "done":
            try:
                fields = mgr.result(batch_id, it.id).get("fields")
            except (KeyError, FileNotFoundError):
                fields = None
        rows.append({"sl": sl, "item_id": it.id, "filename": it.filename, "status": it.status,
                     "route": it.route, "error": it.error, "fields": fields})
    return JSONResponse(content={"columns": ["Sl.No.", "Invoice", *OUTPUT_FIELDS], "rows": rows})


@app.post("/api/batches/{batch_id}/persist")
def batch_persist(batch_id: str) -> JSONResponse:
    """Upload every finished invoice of the batch to the configured database."""
    import mimetypes

    from database.persist import persist_extraction, sha256_bytes

    from .batch import BATCH_DIR

    mgr = batches()
    if batch_id not in mgr.batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    if not (os.environ.get("DATABASE_URL") and os.environ.get("INVOICE_ORG_ID")):
        raise HTTPException(status_code=409, detail="no database configured (DATABASE_URL / INVOICE_ORG_ID)")
    stored, failed = [], []
    for it, res in mgr.done_results(batch_id):
        try:
            data = Path(it.path).read_bytes()
        except OSError as exc:
            log.warning("batch persist upload missing for %s: %s: %s",
                        it.filename, type(exc).__name__, exc)
            failed.append({"filename": it.filename,
                           "error": "The uploaded file is missing on this server. "
                                    "Re-upload it and retry."})
            continue
        try:
            out = persist_extraction(
                filename=it.filename,
                mime=mimetypes.guess_type(it.filename)[0] or "", size=len(data),
                data_sha256=sha256_bytes(data), fields=res.get("fields", {}),
                # Raw print-scale boxes, like single extracts persist; fall back to
                # the display audit for results stored before audit_raw existed.
                audit=res.get("audit_raw") or res.get("audit", {}), source_path=it.path)
        except Exception as exc:
            log.warning("batch persist failed for %s: %s: %s",
                        it.filename, type(exc).__name__, exc)
            failed.append({"filename": it.filename,
                           "error": "Couldn't store this invoice. "
                                    "Check Settings → Database and retry."})
            continue
        if out.get("persisted"):
            res["persistence"] = out
            it.summary["persisted"] = True
            it.summary["persist_error"] = ""
            try:
                (BATCH_DIR / batch_id / f"{it.id}.json").write_text(json.dumps(res, default=str))
            except OSError:
                pass
            stored.append(it.filename)
        else:
            failed.append({"filename": it.filename, "error": out.get("reason", "not stored")})
    mgr._persist(batch_id)
    return JSONResponse(content={"stored": stored, "failed": failed,
                                 "done": len(stored) + len(failed)})


@app.post("/api/semantic/feedback")
async def semantic_feedback(request: Request) -> JSONResponse:
    """A reviewer confirms what a printed label/charge means; the alias bank learns it immediately."""
    from .semantic import CLASSES, engine

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    kind, text, fld = body.get("kind", "charge"), str(body.get("text", "")).strip(), body.get("field")
    if kind not in ("label", "charge") or not text or fld not in CLASSES:
        raise HTTPException(status_code=400, detail="need kind (label|charge), text, field (canonical)")
    reviewer = str(body.get("reviewer", ""))[:80]
    engine().learn(kind, text, fld, reviewer=reviewer)
    audited = False
    if os.environ.get("DATABASE_URL") and os.environ.get("INVOICE_ORG_ID"):
        try:      # who taught the system what, and when (the alias then applies to every later invoice)
            from database.db import Store

            Store(os.environ["INVOICE_ORG_ID"]).log_audit(
                entity="semantic_alias", entity_id=str(uuid.uuid4()), action="REVIEW",
                old={"previous_field": body.get("previous_field"), "invoice_number": body.get("invoice_number")},
                new={"kind": kind, "text": text, "field": fld, "reviewer": reviewer})
            audited = True
        except Exception as exc:
            log.warning("audit log write failed for a category correction: %s", exc)
    return JSONResponse(content={"learned": True, "kind": kind, "text": text, "field": fld, "audited": audited})


@app.post("/api/semantic/revert")
async def semantic_revert(request: Request) -> JSONResponse:
    """Undo a reviewer-confirmed alias (pairs with /api/semantic/feedback)."""
    from .semantic import engine

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    kind, text = body.get("kind", "charge"), str(body.get("text", "")).strip()
    if kind not in ("label", "charge") or not text:
        raise HTTPException(status_code=400, detail="need kind (label|charge), text")
    return JSONResponse(content={"reverted": engine().revert(kind, text)})


@app.get("/ui/app.js", include_in_schema=False)
def ui_app_js() -> FileResponse:
    """Shared vanilla helpers (esc/toast/confirmDialog/pill), one copy for all pages."""
    return FileResponse(Path(__file__).parent / "ui" / "app.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


@app.get("/ui/app.css", include_in_schema=False)
def ui_app_css() -> FileResponse:
    """Shared design tokens + application shell, one copy for all pages."""
    return FileResponse(Path(__file__).parent / "ui" / "app.css", media_type="text/css",
                        headers={"Cache-Control": "no-store"})


@app.get("/ui/icons.svg", include_in_schema=False)
def ui_icons_svg() -> FileResponse:
    """Shared SVG icon sprite referenced via <use href="/ui/icons.svg#i-…">."""
    return FileResponse(Path(__file__).parent / "ui" / "icons.svg", media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/schema")
def api_schema() -> JSONResponse:
    from .schema import CHARGE_FIELDS, OUTPUT_FIELDS

    return JSONResponse(content={"output_fields": OUTPUT_FIELDS, "charge_fields": CHARGE_FIELDS,
                                 "engines": list(ENGINES),
                                 "engine_labels": ENGINE_LABELS,
                                 "default_engine": DEFAULT_ENGINE})


# ---------------------------------------------------------------------------
# Database: status, tables, configure
# ---------------------------------------------------------------------------

# Plain-language browser metadata: (friendly name, one-line what-it-holds, main section?).
# The browser shows "main" tables first; the rest sit under "behind the scenes".
_TABLE_INFO = {
    "invoices": ("Invoices", "Each invoice, its values, and whether it still needs a look.", True),
    "documents": ("Files", "The files the invoices were read from.", True),
    "vendors": ("Vendors", "Who sent the invoices.", True),
    "extraction_runs": ("Reads", "Every attempt at reading a file, and what came out.", True),
    "extraction_field_results": ("Values found", "Where on the page each value was spotted.", True),
    "audit_logs": ("History", "Everything that happened, in order.", True),
    "organizations": ("Accounts", "Keeps one company's data separate from another's.", False),
    "models": ("AI programs", "The programs that read invoices.", False),
    "model_versions": ("AI versions", "Which version of each program did what.", False),
    "model_configurations": ("AI setups", "Named combinations used for a read.", False),
    "model_configuration_models": ("Setup parts", "Which versions make up each setup.", False),
    "field_definitions": ("Field list", "The values the app looks for.", False),
    "charge_types": ("Charge names", "The kinds of charges the app knows.", False),
    "invoice_charges": ("Charge lines", "The charge lines found on invoices.", False),
    "invoice_facts": ("Printed items", "Everything printed on each invoice, item by item.", False),
    "invoice_attributes": ("Grouped details", "Values grouped under headings like Shipment and Totals.", False),
    "invoice_evidence": ("Marked pages", "Page images with the found values boxed.", False),
    "schema_migrations": ("Bookkeeping", "Keeps the database structure itself current.", False),
}


def _table_info(name: str) -> dict:
    friendly, blurb, main = _TABLE_INFO.get(name, (None, None, False))
    return {"friendly": friendly or name.replace("_", " ").title(),
            "blurb": blurb or "", "main": main}

_ENV_LOCAL = Path(__file__).parent.parent / ".env.local"


def _masked_host(url: str) -> str:
    """scheme://host for display. The password never leaves the server."""
    from urllib.parse import parse_qs, urlparse

    try:
        p = urlparse(url)
        host = p.hostname or parse_qs(p.query).get("host", [None])[0]
        if not host or host.startswith("/"):
            return "this computer"
        return f"{p.scheme}://{host}" + (f":{p.port}" if p.port else "")
    except Exception:
        return "(unparseable)"


def _try_db(url: str, org: str) -> dict:
    """Connect with an explicit URL (stored or candidate). Read-only probes."""
    from urllib.parse import urlparse

    from psycopg.rows import dict_row

    if urlparse(url).scheme not in ("postgres", "postgresql"):
        return {"reachable": False, "host": _masked_host(url),
                "error": "url must start with postgres:// or postgresql://"}
    import psycopg

    from database.db import _is_local

    conn_url = url if ("sslmode=" in url or _is_local(url)) else \
        url + ("&" if "?" in url else "?") + "sslmode=require"
    started = time.perf_counter()
    try:
        with psycopg.connect(conn_url, connect_timeout=8, row_factory=dict_row) as conn, \
                conn.cursor() as cur:
            org_name = None
            if org:
                cur.execute("SELECT name FROM organizations WHERE id = %s", (org,))
                row = cur.fetchone()
                org_name = row["name"] if row else None
            cur.execute("SELECT count(*) AS n FROM schema_migrations")
            migrations = cur.fetchone()["n"]
    except Exception as exc:
        return {"reachable": False, "host": _masked_host(url),
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    out = {"reachable": True, "host": _masked_host(url),
           "latency_ms": round((time.perf_counter() - started) * 1000),
           "migrations": migrations}
    if org:
        out["organization"] = org_name
        out["org_found"] = org_name is not None
    return out


@app.get("/api/db/status")
def db_status() -> JSONResponse:
    """Database status for the UI. The connection string itself is never returned."""
    info = dict(_check_database())
    url = os.environ.get("DATABASE_URL", "")
    info["host"] = _masked_host(url) if url else None
    info["has_org"] = bool(os.environ.get("INVOICE_ORG_ID"))
    return JSONResponse(content=info)


@app.post("/api/db/test")
async def db_test(request: Request) -> JSONResponse:
    """Try a connection string without storing it. Body: {url?, org?} (defaults: current env)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    url = str(body.get("url") or os.environ.get("DATABASE_URL") or "").strip()
    org = str(body.get("org") or os.environ.get("INVOICE_ORG_ID") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="need url (or set DATABASE_URL)")
    return JSONResponse(content=_try_db(url, org))


@app.get("/api/db/schema")
def db_schema() -> JSONResponse:
    """Tables with columns, row counts, and how tables link together. Read-only."""
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=409, detail="no database configured (DATABASE_URL)")
    import psycopg.sql

    from database.db import connect

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT table_name FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                            ORDER BY table_name""")
            names = [r["table_name"] for r in cur.fetchall()]
            tables = []
            for name in names:
                cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                                WHERE table_schema = 'public' AND table_name = %s
                                ORDER BY ordinal_position""", (name,))
                cols = [{"name": r["column_name"], "type": r["data_type"]} for r in cur.fetchall()]
                try:
                    cur.execute(psycopg.sql.SQL("SELECT count(*) AS n FROM {}")
                                .format(psycopg.sql.Identifier(name)))
                    rows = cur.fetchone()["n"]
                except Exception:
                    conn.rollback()
                    rows = None
                tables.append({"name": name, "columns": cols, "rows": rows, **_table_info(name)})
            cur.execute("""SELECT kcu.table_name, kcu.column_name,
                                  ccu.table_name AS ref_table, ccu.column_name AS ref_column
                             FROM information_schema.table_constraints tc
                             JOIN information_schema.key_column_usage kcu
                               ON kcu.constraint_name = tc.constraint_name
                              AND kcu.table_schema = tc.table_schema
                             JOIN information_schema.constraint_column_usage ccu
                               ON ccu.constraint_name = tc.constraint_name
                            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'""")
            links = [{"table": r["table_name"], "column": r["column_name"],
                      "ref_table": r["ref_table"], "ref_column": r["ref_column"]} for r in cur.fetchall()]
    except Exception as exc:
        log.warning("database unreachable for db schema: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=502,
                            detail="Can't reach the database. "
                                   "Check Settings → Database and retry.")
    return JSONResponse(content={"tables": tables, "links": links})


@app.get("/api/db/tables/{name}")
def db_table_preview(name: str) -> JSONResponse:
    """First 25 rows of one table, newest-first when the table tracks time. Read-only."""
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=409, detail="no database configured (DATABASE_URL)")
    import psycopg.sql

    from database.db import connect

    # extraction_field_results has no time column (ordering by its run uuid
    # would not be newest-first), so it intentionally has no ORDER BY.
    order_col = {"audit_logs": "seq", "extraction_runs": "started_at",
                 "invoices": "updated_at", "documents": "created_at"}.get(name)
    order = psycopg.sql.SQL("ORDER BY {} DESC NULLS LAST").format(psycopg.sql.Identifier(order_col)) \
        if order_col else psycopg.sql.SQL("")
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT 1 FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_name = %s""", (name,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="unknown table")
            cur.execute(psycopg.sql.SQL("SELECT * FROM {} {} LIMIT 25").format(
                psycopg.sql.Identifier(name), order))
            cols = [d.name for d in cur.description]
            # connect() uses dict_row, so map dicts to values (iterating a dict yields keys).
            rows = [[(str(v)[:200] if v is not None else None)
                     for v in (r.values() if isinstance(r, dict) else r)] for r in cur.fetchall()]
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("database unreachable for table preview %s: %s: %s",
                    name, type(exc).__name__, exc)
        raise HTTPException(status_code=502,
                            detail="Can't reach the database. "
                                   "Check Settings → Database and retry.")
    return JSONResponse(content={"name": name, "columns": cols, "rows": rows})


@app.post("/api/db/configure")
async def db_configure(request: Request) -> JSONResponse:
    """Validate a bare connection string, then save it to .env.local (mode 600, other lines
    kept). The org id is optional: without it extractions are read, just not stored. Takes
    effect on restart; the URL is never echoed back."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    url = str(body.get("url") or "").strip()
    org = str(body.get("org") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="need url")
    probe = _try_db(url, org)
    if not probe.get("reachable"):
        raise HTTPException(status_code=502, detail="candidate database unreachable: " +
                            probe.get("error", "unknown error"))
    if org and not probe.get("org_found"):
        raise HTTPException(status_code=400, detail="connected, but INVOICE_ORG_ID is not in organizations")
    lines = _ENV_LOCAL.read_text().splitlines() if _ENV_LOCAL.exists() else []
    updates = {"DATABASE_URL": url}
    if org:
        updates["INVOICE_ORG_ID"] = org
    seen, out = set(), []
    for line in lines:
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            out.append(f"{m.group(1)}={updates[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    _ENV_LOCAL.write_text("\n".join(out) + "\n")
    os.chmod(_ENV_LOCAL, 0o600)
    return JSONResponse(content={"saved": True, "host": probe["host"], "organization": probe.get("organization"),
                                 "note": "saved to .env.local; restart the app to use it"})


# ---------------------------------------------------------------------------
# Engine updates: version checks + guarded upgrades
# ---------------------------------------------------------------------------

# Pip packages the app can upgrade itself, with the UI role each serves.
_UPDATEABLE = (("docling", "Docling engine"), ("paddleocr", "PaddleOCR-VL engine"),
               ("paddlex", "PaddleX runtime"), ("laya", "LAYA semantic layer"),
               ("sentence-transformers", "Embedding layer"), ("MINERU", None))

_UPDATEABLE_NAMES = tuple(n for n, _ in _UPDATEABLE if not n.isupper())
_UPDATES_CACHE: dict = {}
_UPDATES_TTL = 6 * 3600


def _installed_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def _pypi_latest(name: str) -> str | None:
    import httpx

    r = httpx.get(f"https://pypi.org/pypi/{name}/json", timeout=10.0)
    r.raise_for_status()
    return r.json()["info"]["version"]


def _updates(hard: bool = False) -> dict:
    """Current vs latest per engine. PyPI answers are cached 6h; a failed check
    reports per-package instead of failing the whole call."""
    now = time.monotonic()
    if not hard and _UPDATES_CACHE and now - _UPDATES_CACHE.get("at", 0) < _UPDATES_TTL:
        return _UPDATES_CACHE["report"]
    pkgs = []
    for name, role in _UPDATEABLE:
        if name.isupper():      # external binary, version display only
            pkgs.append(_external_version(name, role))
            continue
        current = _installed_version(name)
        try:
            latest = _pypi_latest(name)
        except Exception as exc:
            pkgs.append({"name": name, "role": role, "current": current, "latest": None,
                         "error": f"{type(exc).__name__}"})
            continue
        pkgs.append({"name": name, "role": role, "current": current, "latest": latest,
                     "update_available": bool(current and latest and latest != current)})
    report = {"packages": pkgs, "auto_update": os.environ.get("ENGINE_AUTO_UPDATE", "0") == "1"}
    _UPDATES_CACHE.clear()
    _UPDATES_CACHE.update({"at": now, "report": report})
    return report


def _external_version(key: str, role: str | None) -> dict:
    if key == "MINERU":
        try:
            from . import mineru_engine

            b = mineru_engine.resolve_bin()
        except Exception:
            b = None
        if not b:
            return {"name": "mineru", "role": "MinerU engine (external)", "current": None,
                    "latest": None, "note": "binary not found (MINERU_BIN)"}
        import subprocess

        try:
            import re
            out = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=30)
            ver = (out.stdout or out.stderr or "").strip().splitlines()
            raw = ver[0][:80] if ver else "?"
            m = re.search(r"\d+\.\d+(?:\.\d+)?", raw)
            return {"name": "mineru", "role": "MinerU engine (external)", "current": m.group(0) if m else raw,
                    "latest": None, "note": "external binary: upgrade outside the app"}
        except Exception as exc:
            return {"name": "mineru", "role": "MinerU engine (external)", "current": None,
                    "latest": None, "error": f"{type(exc).__name__}"}
    return {"name": key, "role": role, "current": None, "latest": None}


@app.get("/api/updates")
def updates_status(refresh: bool = False) -> JSONResponse:
    """Engine versions vs PyPI (cached 6h). ?refresh=1 bypasses the cache."""
    return JSONResponse(content=_updates(hard=refresh))


@app.get("/api/updates/refresh")
def updates_refresh() -> JSONResponse:
    return JSONResponse(content=_updates(hard=True))


def _updates_busy() -> str | None:
    """Refuse upgrades while extraction work is in flight (batches or singles)."""
    with _EXTRACT_LOCK:
        singles = _EXTRACT_INFLIGHT
    if singles:
        return f"{singles} single extraction(s)"
    try:
        mgr = batches()
    except Exception:
        return None
    for bid in list(mgr.batches):
        try:
            if not mgr.status(bid).get("finished"):
                return f"batch {bid}"
        except Exception:
            continue
    return None


# One pip upgrade at a time, off the request handler: pip takes minutes and the
# async handler would freeze the server (and its 30-min timeout with it).
_UPGRADE_LOCK = threading.Lock()
_UPGRADE_JOB: dict = {"running": False, "packages": [], "started_at": None,
                       "returncode": None, "log_tail": "", "error": ""}


def _run_upgrade(packages: list) -> None:
    """Blocking pip install for the background thread; records the outcome on _UPGRADE_JOB."""
    import subprocess

    try:
        proc = subprocess.run([sys.executable, "-m", "pip", "install", "-U", *packages],
                              capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        with _UPGRADE_LOCK:
            _UPGRADE_JOB.update(running=False, returncode=None,
                                error=f"upgrade failed to start: {type(exc).__name__}: {exc}"[:200])
    else:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        with _UPGRADE_LOCK:
            _UPGRADE_JOB.update(running=False, returncode=proc.returncode, log_tail=tail[-500:],
                                error="" if proc.returncode == 0 else f"pip exited {proc.returncode}"[:200])
    finally:
        _UPDATES_CACHE.clear()


@app.post("/api/updates/apply")
async def updates_apply(request: Request) -> JSONResponse:
    """Start pip install -U of the named engines (safelist only) in the background.

    409 while an upgrade is already running or extraction work is in flight.
    Returns 202 at once; the outcome lands on _UPGRADE_JOB and the version
    cache is cleared when pip finishes.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    want = body.get("packages") or []
    if not isinstance(want, list) or not want:
        raise HTTPException(status_code=400, detail="need packages: [names]")
    unknown = [p for p in want if p not in _UPDATEABLE_NAMES]
    if unknown:
        raise HTTPException(status_code=400, detail="not updateable: " + ", ".join(unknown))
    with _UPGRADE_LOCK:
        already = _UPGRADE_JOB["running"]
    if already:
        raise HTTPException(status_code=409, detail="an upgrade is already running")
    busy = _updates_busy()
    if busy:
        raise HTTPException(status_code=409, detail=f"{busy} unfinished; updates wait for idle")
    with _UPGRADE_LOCK:
        _UPGRADE_JOB.update(running=True, packages=list(want), started_at=time.time(),
                            returncode=None, log_tail="", error="")
    threading.Thread(target=_run_upgrade, args=(list(want),), daemon=True).start()
    return JSONResponse(status_code=202,
                        content={"started": True, "packages": want, "restart_required": True,
                                 "note": "upgrade running in the background; restart the app to load it. "
                                         "requirements.txt stays pinned: reinstalling it reverts."})


@app.post("/api/updates/auto")
async def updates_auto(request: Request) -> JSONResponse:
    """Toggle upgrades at next app start (ENGINE_AUTO_UPDATE in .env.local, mode 600)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    enabled = bool(body.get("enabled"))
    lines = _ENV_LOCAL.read_text().splitlines() if _ENV_LOCAL.exists() else []
    seen, out = False, []
    for line in lines:
        if re.match(r"\s*ENGINE_AUTO_UPDATE\s*=", line):
            out.append(f"ENGINE_AUTO_UPDATE={'1' if enabled else '0'}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"ENGINE_AUTO_UPDATE={'1' if enabled else '0'}")
    _ENV_LOCAL.write_text("\n".join(out) + "\n")
    os.chmod(_ENV_LOCAL, 0o600)
    _UPDATES_CACHE.clear()
    return JSONResponse(content={"auto_update": enabled,
                                 "note": "takes effect at next start (run_app.py upgrades before serving)"})


# ---------------------------------------------------------------------------
# Analytics (periodic) and audit trail
# ---------------------------------------------------------------------------

ANALYTICS_EVERY_MIN = float(os.environ.get("ANALYTICS_EVERY_MINUTES", "360"))
_ANALYTICS_STATE: Dict[str, object] = {"last": None, "running": False}
_ANALYTICS_LOCK = threading.Lock()


def _analytics_run(trigger: str, if_changed: bool = False) -> dict:
    from analytics.run import run

    if not _ANALYTICS_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "an analysis is already running"}
    _ANALYTICS_STATE["running"] = True
    try:
        res = run(trigger=trigger, if_changed=if_changed)
        _ANALYTICS_STATE["last"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "trigger": trigger, **res}
        return res
    finally:
        _ANALYTICS_STATE["running"] = False
        _ANALYTICS_LOCK.release()


def _analytics_loop() -> None:
    """Re-analyse on a schedule, but only when an invoice was added or changed since the last report."""
    time.sleep(20)
    while True:
        try:
            res = _analytics_run("schedule", if_changed=True)
            log.info("scheduled analytics: %s", res.get("reason") if res.get("skipped") else res.get("dir"))
        except Exception as exc:
            log.warning("scheduled analytics failed: %s", exc)
            _ANALYTICS_STATE["last"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": f"{type(exc).__name__}: {exc}"[:200]}
        time.sleep(max(60.0, ANALYTICS_EVERY_MIN * 60.0))


if ANALYTICS_EVERY_MIN > 0 and os.environ.get("DATABASE_URL") and os.environ.get("INVOICE_ORG_ID"):
    threading.Thread(target=_analytics_loop, name="analytics-scheduler", daemon=True).start()


def _analytics_file(name: str) -> Path:
    from analytics.run import OUT_ROOT

    return OUT_ROOT / "latest" / name


@app.get("/analytics", include_in_schema=False)
def analytics_page():
    f = _analytics_file("dashboard.html")
    if not f.exists():
        return HTMLResponse("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Analytics — Invoice Extractor</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%230052cc'/%3E%3C/svg%3E">
<link rel="stylesheet" href="/ui/app.css"></head><body>
<header class="app-header"><a class="skip" href="#main">Skip to content</a>
<a class="brand" href="/" aria-label="Invoice Extractor home"><svg class="brand-mark" aria-hidden="true"><use href="/ui/icons.svg#i-logo"/></svg><span class="brand-name">Invoice Extractor</span></a>
<nav class="app-nav" aria-label="Primary"><a href="/">Extract</a><a href="/database">Database</a><a href="/analytics" aria-current="page">Analytics</a><a href="/audit">Audit trail</a><a href="/settings">Settings</a></nav></header>
<main class="content narrow" id="main"><div class="page-head"><h1>Analytics</h1>
<p class="page-desc">Spend trends, vendor breakdowns, and data-quality findings.</p></div>
<div class="card"><div class="empty-state" role="status">
<svg class="art" aria-hidden="true"><use href="/ui/icons.svg#i-file"/></svg>
<h2>No report yet</h2><p>The report appears once invoices are stored in the database.</p>
<p><button class="btn" id="runAnalytics" type="button">Run analytics now</button></p>
<p class="meta" id="runMsg" aria-live="polite"></p></div></div></main>
<script>(function(){var b=document.getElementById("runAnalytics"),m=document.getElementById("runMsg");
b.addEventListener("click",function(){b.disabled=true;m.textContent="Running — this can take a minute…";
fetch("/api/analytics/run",{method:"POST"}).then(function(r){return r.json().then(function(d){return{r:r,d:d}})})
.then(function(x){if(!x.r.ok)throw new Error(x.d.detail||("Failed ("+x.r.status+")"));
if(x.d.skipped){m.textContent=x.d.reason||"Nothing new to report.";b.disabled=false;return}location.reload()})
.catch(function(e){m.textContent=e.message;m.classList.add("err");b.disabled=false})})})();</script>
</body></html>""")
    return FileResponse(f, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/analytics/latest")
def analytics_latest() -> JSONResponse:
    f = _analytics_file("insights.json")
    if not f.exists():
        raise HTTPException(status_code=404, detail="no analytics run yet")
    return JSONResponse(content=json.loads(f.read_text()))


@app.get("/api/analytics/latest/mis_report.xlsx")
def analytics_mis() -> FileResponse:
    f = _analytics_file("mis_report.xlsx")
    if not f.exists():
        raise HTTPException(status_code=404, detail="no analytics run yet")
    return FileResponse(f, filename="mis_report.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/analytics/run")
async def analytics_run_now() -> JSONResponse:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=409, detail="no database configured")
    try:
        res = await _run_in_thread(lambda: _analytics_run("api"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"analytics failed: {type(exc).__name__}: {exc}"[:300])
    return JSONResponse(content=json.loads(json.dumps(res, default=str)))


@app.get("/api/analytics/status")
def analytics_status() -> JSONResponse:
    return JSONResponse(content={"every_minutes": ANALYTICS_EVERY_MIN, "running": _ANALYTICS_STATE["running"],
                                 "last": _ANALYTICS_STATE["last"], "report_available": _analytics_file("dashboard.html").exists()})


@app.get("/api/audit/verify")
def audit_verify() -> JSONResponse:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=409, detail="no database configured")
    from database.audit import verify_chain

    return JSONResponse(content=verify_chain())


@app.get("/api/audit/invoice/{invoice_number:path}")
def audit_invoice(invoice_number: str) -> JSONResponse:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=409, detail="no database configured")
    from database.audit import invoice_trail

    trail = invoice_trail(invoice_number)
    if not trail["matches"]:
        raise HTTPException(status_code=404, detail="invoice not found in the database")
    return JSONResponse(content=json.loads(json.dumps(trail, default=str)))


# ---------------------------------------------------------------- evidence --
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def _evidence(invoice_id: str) -> dict:
    if not _UUID_RE.fullmatch(invoice_id):
        raise HTTPException(status_code=400, detail="invalid invoice id")
    if not (os.environ.get("DATABASE_URL") and os.environ.get("INVOICE_ORG_ID")):
        raise HTTPException(status_code=409, detail="no database configured")
    from database.db import Store

    ev = Store(os.environ["INVOICE_ORG_ID"]).evidence(invoice_id)
    if not ev:
        raise HTTPException(status_code=404, detail="unknown invoice")
    return ev


@app.get("/evidence/{invoice_id}/page/{page}.jpg", include_in_schema=False)
def evidence_page(invoice_id: str, page: int) -> FileResponse:
    """One stored page image (boxes on the extracted values); checked against the hash recorded when it was made."""
    import hashlib

    from database.persist import evidence_dir

    row = next((p for p in _evidence(invoice_id)["pages"] if p["page_number"] == page), None)
    if not row:
        raise HTTPException(status_code=404, detail="no evidence image for this page")
    root = evidence_dir().resolve()
    target = (root / row["storage_uri"]).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="the stored image is missing on this server")
    intact = hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]
    return FileResponse(target, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Evidence-Sha256": row["sha256"],
                                 "X-Evidence-Intact": "yes" if intact else "NO - file differs from the recorded hash"})


@app.get("/api/evidence/{invoice_id}")
def evidence_json(invoice_id: str) -> JSONResponse:
    ev = _evidence(invoice_id)
    return JSONResponse(json.loads(json.dumps(ev, default=str)))


@app.get("/evidence/{invoice_id}", include_in_schema=False)
def evidence_view(invoice_id: str) -> HTMLResponse:
    """What Excel's "Open" link shows: the invoice's values and the pages they were read from."""
    import hashlib
    import html
    from urllib.parse import quote

    from database.persist import evidence_dir

    ev = _evidence(invoice_id)
    inv, pages = ev["invoice"], ev["pages"]
    from database.db import Store

    facts = Store(os.environ["INVOICE_ORG_ID"]).facts(invoice_id)
    attrs = Store(os.environ["INVOICE_ORG_ID"]).attributes(invoice_id)
    final = inv.get("final_output_json") or {}
    fields = final.get("fields") or {}
    esc = lambda x: html.escape(str(x if x is not None else ""))
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v) if v else '—'}</td></tr>" for k, v in fields.items())
    warn = "".join(f"<li>{esc(w)}</li>" for w in final.get("warnings") or [])
    root = evidence_dir().resolve()
    figs = []
    for p in pages:
        f = (root / p["storage_uri"]).resolve()
        state = ("missing on this server" if not f.is_file() else
                 "matches the recorded hash" if hashlib.sha256(f.read_bytes()).hexdigest() == p["sha256"] else
                 "DIFFERS from the recorded hash")
        bad = state != "matches the recorded hash"
        figs.append(f'<figure><img src="/evidence/{invoice_id}/page/{p["page_number"]}.jpg" alt="page {p["page_number"]}">'
                    f'<figcaption>Page {p["page_number"]} · {p["boxes"]} values marked · stored {esc(p["created_at"])[:19]} UTC'
                    f' · SHA-256 {esc(p["sha256"])[:16]}… <strong class="{"bad" if bad else "ok"}">({state})</strong>'
                    f'{" — re-extract the invoice and compare with the audit trail." if bad else ""}</figcaption></figure>')
    desc = " · ".join(p for p in [
        esc(inv.get("vendor_name")), esc(inv.get("invoice_date")),
        ("review status " + esc(inv.get("review_status"))) if inv.get("review_status") else "",
        ("result " + esc(final.get("review_route"))) if final.get("review_route") else "",
        ("last updated " + esc(inv.get("updated_at"))[:19] + " UTC") if inv.get("updated_at") else "",
    ] if p)
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Evidence · {esc(inv.get("invoice_number"))}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%230052cc'/%3E%3C/svg%3E">
<link rel="stylesheet" href="/ui/app.css">
<style>
 .ev-grid {{ display:grid; grid-template-columns:minmax(260px,380px) 1fr; gap:20px; align-items:start; }}
 @media (max-width:900px) {{ .ev-grid {{ grid-template-columns:1fr; }} }}
 .ev-fields th {{ width:44%; }} .ev-fields td {{ overflow-wrap:anywhere; }}
 figure {{ margin:0 0 22px; }} figure img {{ width:100%; height:auto; border:1px solid var(--line); background:#fff; }}
 figcaption {{ color:var(--muted); font-size:13px; margin-top:4px; overflow-wrap:anywhere; }}
 ul {{ padding-left:18px; }}
 table.all th, table.all td {{ font-size:12.5px; padding:3px 6px; width:auto; font-weight:400; }}
 table.all tr:first-child th {{ font-weight:600; }} em {{ color:var(--muted); }}
 .bad {{ color:var(--red); }} .ok {{ color:var(--green); }}
</style></head><body>
<header class="app-header"><a class="skip" href="#main">Skip to content</a>
<a class="brand" href="/" aria-label="Invoice Extractor home"><svg class="brand-mark" aria-hidden="true"><use href="/ui/icons.svg#i-logo"/></svg><span class="brand-name">Invoice Extractor</span></a>
<nav class="app-nav" aria-label="Primary"><a href="/">Extract</a><a href="/database">Database</a><a href="/analytics">Analytics</a><a href="/audit">Audit trail</a><a href="/settings">Settings</a></nav></header>
<main class="content" id="main" style="max-width:1500px">
<p class="crumbs"><a href="/audit/{quote(str(inv.get("invoice_number") or ""), safe="")}">Audit trail</a><span class="sep">/</span><span aria-current="page">Evidence</span></p>
<div class="page-head"><h1>Invoice {esc(inv.get("invoice_number") or "(no number)")}</h1>
<p class="page-desc">{desc}</p></div>
<div class="ev-grid"><section><div class="card"><table class="ev-fields">{rows}</table></div>
{"<div class='card'><h2>Why it needs review</h2><ul>" + warn + "</ul></div>" if warn else ""}
{("<h3>Details, arranged</h3><p class='page-desc'>Arranged by " + esc(attrs[0]["arranged_by"]) + " from the extracted items; each value is text of an extracted item, with its page.</p><table class='all'><tr><th>Group</th><th>Detail</th><th>Value</th><th>Page</th></tr>" + "".join(
    f"<tr><td>{esc(a['grp'])}</td><td>{esc(a['detail'].replace('_', ' '))}</td><td>{esc(a['value'])}"
    + ("" if a['valid'] else " <em>(check failed)</em>") + f"</td><td>{a['page_number']}</td></tr>" for a in attrs) + "</table>") if attrs else ""}
{("<details><summary>Everything read from the document (" + str(len(facts)) + " items)</summary>"
"<table class='all'><tr><th>Page</th><th>Where</th><th>Label</th><th>Value</th></tr>" + "".join(
    f"<tr><td>{f['page_number']}</td><td>{esc(f['section'])}</td><td>{esc(f['label'])}</td><td>{esc(f['value'])}"
    + (f" <em>({esc(f['output_field'])})</em>" if f['output_field'] else "") + "</td></tr>" for f in facts) + "</table></details>") if facts else ""}
</section>
<section>{"".join(figs) or "<p>No evidence image stored for this invoice.</p>"}</section>
</div></main></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
