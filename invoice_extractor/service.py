"""The document-extraction service behind the Fluent UI (UI/, served at /).

Documents are read under a profile (profiles.py; "auto" lets router.py pick one), in batches (batch.py) or one at a time;
models run in the model pool (model_pool.py). Sign-in and roles: auth.py. Also the database browser, the audit log, engine
updates, and /health, /ready, /metrics.

Run with scripts/run_app.py (builds the UI, loads .env.local).
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
from typing import Dict, List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse

from contextlib import asynccontextmanager, contextmanager

# The Fluent UI (UI/, `npm --prefix UI run build`) is the app at /.
FLUENT_DIST = Path(os.environ.get("FLUENT_DIST") or Path(__file__).resolve().parents[1] / "UI" / "dist")
UPLOAD_DIR = Path(__file__).parent.parent / "work" / "uploads"

log = logging.getLogger("invoice_extractor.service")

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}   # what pipeline.load_pages reads
MAX_UPLOAD_BYTES = int(os.environ.get("INVOICE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_PDF_PAGES = int(os.environ.get("INVOICE_MAX_PDF_PAGES", "60"))

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
_EXTRACT_WAITING = 0
# Every extraction (single, profile read, batch item) takes a slot: no more documents in flight than the reader serves at once
# (run_app.py READER_SLOTS), so a folder of 200 files queues here instead of piling 200 renders and model calls into memory.
EXTRACT_SLOTS = int(os.environ.get("INVOICE_MAX_PARALLEL") or os.environ.get("INVOICE_BATCH_WORKERS") or "4")
_EXTRACT_SEM = threading.BoundedSemaphore(max(1, EXTRACT_SLOTS))


@contextmanager
def _track_extraction():
    global _EXTRACT_INFLIGHT, _EXTRACT_WAITING
    with _EXTRACT_LOCK:
        _EXTRACT_WAITING += 1
    try:
        _EXTRACT_SEM.acquire()
    finally:
        with _EXTRACT_LOCK:
            _EXTRACT_WAITING -= 1
            _EXTRACT_INFLIGHT += 1
    try:
        yield
    finally:
        _EXTRACT_SEM.release()
        with _EXTRACT_LOCK:
            _EXTRACT_INFLIGHT -= 1


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app):
    from . import auth

    if not auth.enabled():
        log.warning("SIGN-IN IS OFF (AUTH=off): anyone who can reach this service can read and change everything")
    elif auth.count_users() == 0:
        log.warning("no one can sign in yet: run  python -m invoice_extractor.auth create-admin EMAIL \"NAME\"  "
                    "or open the app on this machine to create the first administrator")
    # Nothing heavy loads at start unless asked: the reader with READER_WARM=1; otherwise each model loads when first needed.
    import threading

    def _warm():
        if os.environ.get("READER_WARM") == "1" and os.environ.get("READER_BACKEND", "pool") == "pool":
            try:
                from .model_pool import pool
                from .router import reader_id

                with pool().use(reader_id()):
                    log.info("reader warmed: %s", reader_id())
            except Exception as exc:
                log.warning("reader warm-up failed: %s", exc)
    threading.Thread(target=_warm, name="warm-models", daemon=True).start()
    yield
    from .model_pool import pool

    pool().stop_all()                     # a clean stop takes its model servers with it


_DOCS = os.environ.get("API_DOCS") == "1"         # the interactive API docs list every endpoint: off unless asked for
app = FastAPI(
    title="Invoice Extractor",
    version="1.0.0",
    description="Document extraction: text layer or PP-OCRv6, a Qwen3-VL reader, PaddleOCR-VL tables, every value cited.",
    lifespan=_lifespan,
    docs_url="/docs" if _DOCS else None, redoc_url=None, openapi_url="/openapi.json" if _DOCS else None,
)
_APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob:; "
            "frame-src blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
# Whole request bodies are capped before they reach a handler (the framework would otherwise spool any size to disk):
# a batch request carries at most INVOICE_MAX_BATCH_FILES uploads of MAX_UPLOAD_BYTES each.
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(300 * 1024 * 1024)))


ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost,::1,testserver").split(",") if h.strip()}


def _foreign_request(request: Request) -> str:
    """Why a request must be refused, else "". The API has no login and can reconfigure the database and install packages,
    so a web page the user happens to visit must not reach it: a foreign Host is DNS rebinding, a foreign Origin on a write is CSRF."""
    from urllib.parse import urlsplit

    host = (urlsplit("//" + request.headers.get("host", "")).hostname or "").lower()
    if "*" not in ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
        return f"host {host!r} is not allowed (ALLOWED_HOSTS)"
    origin = request.headers.get("origin")
    # the page's host, not host:port: the UI dev server (Vite proxy, :5173) writes to the API on another port
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and "*" not in ALLOWED_HOSTS \
            and (urlsplit(origin).hostname or "").lower() not in ALLOWED_HOSTS:
        return "cross-origin write refused"
    return ""


# Who may call what (auth.py). The app shell and its files are public (the sign-in screen is part of it); everything that
# reads or changes data needs a session; settings, the database, users, updates and the audit log need an administrator.
_PUBLIC = {"/", "/health", "/ready", "/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/setup", "/favicon.ico"}
_PUBLIC_PREFIX = ("/assets/", "/fonts/", "/pictograms/", "/samples/", "/boot.js", "/harsha-logo.png")
_ADMIN_PREFIX = ("/api/db/", "/api/updates", "/api/users", "/api/audit/", "/metrics")
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def _access(request: Request) -> tuple[int, str]:
    """(status, why) when the request must be refused, else (0, ""); sets request.state.user."""
    from . import auth

    request.state.user = None
    path = request.url.path
    if not auth.enabled():
        request.state.user = {"id": "local", "email": "", "name": "Local user", "role": "admin", "must_change": False}
        return 0, ""
    if path in _PUBLIC or path.startswith(_PUBLIC_PREFIX):
        request.state.user = auth.user_for(request.cookies.get(auth.COOKIE))
        return 0, ""
    if path == "/metrics" and request.client and request.client.host in _LOOPBACK:
        return 0, ""                                     # the local Prometheus scraper; anyone else signs in as an admin
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    if user is None:
        return 401, "sign in first"
    request.state.user = user
    if user["must_change"] and path != "/api/auth/password":
        return 403, "change your password first"
    needs_admin = path.startswith(_ADMIN_PREFIX) or (path.startswith("/api/profiles/") and path.endswith("/models"))
    if needs_admin and user["role"] != "admin":
        return 403, "only an administrator can do this"
    return 0, ""


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    if why := _foreign_request(request):
        log.warning("refused request_id=%s %s %s: %s", request_id, request.method, request.url.path, why)
        return JSONResponse(status_code=403, content={"detail": why})
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
        return JSONResponse(status_code=413, content={"detail": f"request larger than {MAX_REQUEST_BYTES} bytes"})
    status, why = _access(request)
    if status:
        return JSONResponse(status_code=status, content={"detail": why}, headers={"Cache-Control": "no-store"})
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error request_id=%s %s %s", request_id, request.method, request.url.path)
        raise
    elapsed = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")          # no clickjacking of Settings / Database
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=(), payment=(), usb=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.scheme == "https" or os.environ.get("COOKIE_SECURE") == "1":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path == "/":                     # the app shell: the same policy as its meta tag, plus framing (meta can't)
        response.headers.setdefault("Content-Security-Policy", _APP_CSP)
    if request.url.path.startswith("/assets/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"   # Vite names them by content hash
    log.info(
        "request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
        request_id, request.method, request.url.path,
        getattr(response, "status_code", "?"), elapsed * 1000.0,
    )
    return response


# ---------------------------------------------------------------------------
# Sign-in, sessions, people (auth.py)
# ---------------------------------------------------------------------------

_LOGIN_TRIES: dict[str, list[float]] = {}      # client address -> recent attempt times
_LOGIN_LOCK = threading.Lock()
LOGIN_PER_10_MIN = int(os.environ.get("LOGIN_PER_10_MIN", "20"))


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="send a JSON object")
    return body


def _set_session(response: JSONResponse, request: Request, token: str) -> None:
    from . import auth

    secure = request.url.scheme == "https" or os.environ.get("COOKIE_SECURE") == "1"
    response.set_cookie(auth.COOKIE, token, max_age=int(auth.MAX_S), httponly=True, samesite="strict", secure=secure, path="/")


@app.get("/api/auth/me")
def auth_me(request: Request) -> JSONResponse:
    """Who is signed in: {user | null, sign_in: on/off, needs_setup: no one exists yet (only this machine may create the first)}."""
    from . import auth

    if not auth.enabled():
        return JSONResponse(content={"user": request.state.user, "sign_in": False, "needs_setup": False})
    setup = auth.count_users() == 0 and _client_ip(request) in _LOOPBACK
    return JSONResponse(content={"user": request.state.user, "sign_in": True, "needs_setup": setup}, headers={"Cache-Control": "no-store"})


@app.post("/api/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    from . import auth

    ip = _client_ip(request)
    now = time.time()
    with _LOGIN_LOCK:
        tries = [t for t in _LOGIN_TRIES.get(ip, []) if now - t < 600]
        if len(tries) >= LOGIN_PER_10_MIN:
            raise HTTPException(status_code=429, detail="too many sign-in attempts from here; wait a few minutes")
        _LOGIN_TRIES[ip] = tries + [now]
    body = await _json(request)
    try:
        token, user = auth.login(str(body.get("email") or ""), str(body.get("password") or ""))
    except PermissionError as exc:
        log.warning("sign-in refused for %r from %s: %s", str(body.get("email") or "")[:80], ip, exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    log.info("signed in: %s from %s", user["email"], ip)
    resp = JSONResponse(content={"user": user})
    _set_session(resp, request, token)
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    from . import auth

    auth.logout(request.cookies.get(auth.COOKIE))
    resp = JSONResponse(content={"signed_out": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.post("/api/auth/password")
async def auth_password(request: Request) -> JSONResponse:
    """Change your own password (also the forced change after a temporary one). Every other session of yours ends."""
    from . import auth

    body = await _json(request)
    user = request.state.user
    try:
        auth.change_password(user, str(body.get("old") or ""), str(body.get("new") or ""))
        token, user = auth.login(user["email"], str(body.get("new") or ""))
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("password changed: %s", user["email"])
    resp = JSONResponse(content={"user": user})
    _set_session(resp, request, token)
    return resp


@app.post("/api/auth/setup")
async def auth_setup(request: Request) -> JSONResponse:
    """The first administrator, from this machine only, while no one exists."""
    from . import auth

    if not auth.enabled() or auth.count_users() or _client_ip(request) not in _LOOPBACK:
        raise HTTPException(status_code=403, detail="setup is done, or not from this machine")
    body = await _json(request)
    try:
        auth.create_user(str(body.get("email") or ""), str(body.get("name") or ""), "admin", str(body.get("password") or ""), must_change=False)
        token, user = auth.login(str(body.get("email")), str(body.get("password")))
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("first administrator created: %s", user["email"])
    resp = JSONResponse(content={"user": user})
    _set_session(resp, request, token)
    return resp


@app.get("/api/users")
def users_list() -> JSONResponse:
    from . import auth

    return JSONResponse(content={"users": auth.list_users()})


@app.post("/api/users")
async def users_create(request: Request) -> JSONResponse:
    """Add a person with a temporary password they must change at first sign-in."""
    from . import auth

    body = await _json(request)
    try:
        u = auth.create_user(str(body.get("email") or ""), str(body.get("name") or ""), str(body.get("role") or "user"),
                             str(body.get("password") or ""), must_change=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("person added: %s (%s) by %s", u["email"], u["role"], request.state.user["email"])
    return JSONResponse(content=u, status_code=201)


@app.patch("/api/users/{user_id}")
async def users_update(user_id: str, request: Request) -> JSONResponse:
    from . import auth

    body = await _json(request)
    try:
        u = auth.update_user(user_id, request.state.user, role=body.get("role"), disabled=body.get("disabled"), name=body.get("name"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such person") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("person changed: %s %s by %s", u["email"], {k: body[k] for k in ("role", "disabled") if k in body}, request.state.user["email"])
    return JSONResponse(content=u)


@app.post("/api/users/{user_id}/password")
async def users_reset_password(user_id: str, request: Request) -> JSONResponse:
    """An administrator sets a temporary password; the person changes it at next sign-in, and their sessions end."""
    from . import auth

    body = await _json(request)
    try:
        auth.reset_password(user_id, str(body.get("password") or ""), must_change=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such person") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("password reset for %s by %s", user_id, request.state.user["email"])
    return JSONResponse(content={"reset": True})


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness probe: always 200 when the process is serving."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """The Fluent UI. Its pages are hash routes (/#/review/...), so this one file serves all of them."""
    page = FLUENT_DIST / "index.html"
    if not page.exists():
        raise HTTPException(status_code=503, detail="The UI is not built: run npm --prefix UI run build (scripts/run_app.py does it)")
    return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-store"})


# Old addresses open the matching Fluent page (bookmarks, links in old exports and e-mails).
_FLUENT_ROUTE = {"audit": "audit", "settings": "engines", "database": "data"}


@app.get("/audit", include_in_schema=False)
@app.get("/audit/{rest:path}", include_in_schema=False)
@app.get("/settings", include_in_schema=False)
@app.get("/database", include_in_schema=False)
@app.get("/database/{rest:path}", include_in_schema=False)
def old_page(request: Request, rest: str = "") -> RedirectResponse:
    return RedirectResponse(f"/#/{_FLUENT_ROUTE[request.url.path.split('/')[1]]}", status_code=307)


LLAMACPP_HOST = os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080")


def _check_llamacpp(timeout: float = 5.0) -> Dict[str, object]:
    try:
        with urllib.request.urlopen(f"{LLAMACPP_HOST}/health", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return {"reachable": True, "status": body.get("status")}
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
def ready(request: Request) -> JSONResponse:
    """Readiness: can this service read a document now? OCR for scans, the reader the model pool would start, the database
    when one is configured, and the classic page's engine when it is set. Probes what exists; starts no model.
    The details (paths, errors, loaded models) go to administrators and this machine only; others get the status."""
    from . import auth, ppocr_client
    from .model_pool import pool

    checks: Dict[str, object] = {}
    ok = True
    checks["ocr"] = {"engine": "PP-OCRv6", "available": ppocr_client.available(), "error": ppocr_client.last_error()}
    backend = os.environ.get("READER_BACKEND", "pool").lower()
    if backend == "pool":
        from .router import reader_id

        rid = reader_id()
        checks["reader"] = {"backend": "pool", "model": rid, "on_disk": pool().available(rid)}
        ok &= pool().available(rid)
    else:
        checks["reader"] = {"backend": backend, **_check_llamacpp()}
        ok &= bool(checks["reader"].get("reachable"))
    checks["table_reader"] = {"model": "document-parser", "on_disk": pool().available("document-parser")}
    checks["models"] = {"loaded": pool().loaded(), "loaded_gb": pool().loaded_gb(), "budget_gb": pool().budget_gb}
    checks["database"] = _check_database()
    if checks["database"].get("configured") and not checks["database"].get("reachable"):
        ok = False
    checks["sign_in"] = "on" if auth.enabled() else "OFF (AUTH=off): anyone who reaches this service can use it"
    checks["status"] = "ok" if ok else "not-ready"
    user = getattr(request.state, "user", None)
    trusted = (user or {}).get("role") == "admin" or (request.client and request.client.host in _LOOPBACK)
    return JSONResponse(status_code=200 if ok else 503, content=checks if trusted else {"status": checks["status"]})


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    with _metrics_lock:
        snap = dict(_metrics)
    with _EXTRACT_LOCK:
        running, waiting = _EXTRACT_INFLIGHT, _EXTRACT_WAITING
    queue = _BATCHES.queued() if _BATCHES is not None else {"queued": 0, "processing": 0}
    from .model_pool import pool

    pool_gb, pool_budget, pool_starts = pool().loaded_gb(), pool().budget_gb, pool().starts
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
        "# HELP invoice_extractions_running documents being read now",
        "# TYPE invoice_extractions_running gauge",
        f"invoice_extractions_running {running}",
        "# HELP invoice_extractions_waiting documents waiting for a free slot",
        "# TYPE invoice_extractions_waiting gauge",
        f"invoice_extractions_waiting {waiting}",
        "# HELP invoice_extraction_slots documents read at once at most (INVOICE_MAX_PARALLEL)",
        "# TYPE invoice_extraction_slots gauge",
        f"invoice_extraction_slots {EXTRACT_SLOTS}",
        "# HELP invoice_batch_items_queued batch items not started yet",
        "# TYPE invoice_batch_items_queued gauge",
        f"invoice_batch_items_queued {queue['queued']}",
        "# HELP invoice_models_loaded_gb memory of the models loaded now (model pool)",
        "# TYPE invoice_models_loaded_gb gauge",
        f"invoice_models_loaded_gb {pool_gb}",
        "# HELP invoice_models_budget_gb most the model pool may load at once (MODEL_MEMORY_GB)",
        "# TYPE invoice_models_budget_gb gauge",
        f"invoice_models_budget_gb {pool_budget}",
        "# HELP invoice_model_starts_total model servers started",
        "# TYPE invoice_model_starts_total counter",
        f"invoice_model_starts_total {pool_starts}",
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


def _validate_upload(path: Path) -> List[str]:
    """Content validation beyond suffix: magic bytes + PDF page cap."""
    problems: List[str] = []
    head = path.read_bytes()[:16]
    suffix = path.suffix.lower()
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
            log.info("unreadable PDF %s: %s", path.name, exc)
            problems.append("unreadable PDF: the file is damaged or not a PDF")   # the error text carries the server path
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

        _BATCHES = BatchManager(lambda path, filename, profile: _profile_read(path, profile))
    return _BATCHES


def _profile_read(path: Path, profile_id: str) -> dict:
    """One stored upload read under a profile: {"result", "audit", "seconds"}. Counted in /metrics like every extraction."""
    from . import profiles

    problems = _validate_upload(path)
    if problems:
        raise UploadRejected(422, "; ".join(problems))
    profile = profile_id if profile_id == profiles.AUTO else profiles.get(profile_id)   # "auto": the router picks
    _bump("requests_total")
    started = time.perf_counter()
    try:
        with _track_extraction():
            result, audit = profiles.extract(str(path), profile)
    except Exception:
        _bump("requests_error")
        raise
    seconds = round(time.perf_counter() - started, 2)
    _bump("requests_ok")
    _bump("latency_sum", seconds)
    if result.get("decision") != "ACCEPT":
        _bump("needs_review")
    audit.pop("reader_raw", None)
    audit.pop("evidence", None)              # the text is in result["text"]; the raw reply stays in the reader log
    return {"result": result, "audit": audit, "seconds": seconds}


def _check_profile(profile: str) -> str:
    if not profile:
        return ""
    from . import profiles

    if profile == profiles.AUTO:                 # the router picks per document
        return profile

    try:
        profiles.get(profile)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return profile


async def _accept_files(batch_id: str, files: List[UploadFile], profile: str) -> list[dict]:
    accepted = []
    for f in files:
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        entry = {"filename": f.filename}
        if not data:
            entry["error"] = "empty upload"
        elif len(data) > MAX_UPLOAD_BYTES:
            entry["error"] = f"file exceeds {MAX_UPLOAD_BYTES} byte limit"
        else:
            try:
                path = _save_upload(f, data)
                it = batches().add(batch_id, f.filename or path.name, data, path, profile)
                entry.update(id=it.id, status=it.status)
            except HTTPException as exc:
                entry["error"] = exc.detail
        accepted.append(entry)
    return accepted


@app.post("/api/batches")
async def create_batch(files: List[UploadFile] = File(...), profile: str = Form("auto")) -> JSONResponse:
    """Start a batch with any number of PDF/image files, read in the background under `profile` ("auto": the router picks
    one per document)."""
    if not files:
        raise HTTPException(status_code=400, detail="no files")
    if len(files) > int(os.environ.get("INVOICE_MAX_BATCH_FILES", "500")):
        raise HTTPException(status_code=413, detail="too many files in one request")
    profile = _check_profile(profile)
    b = batches().create()
    accepted = await _accept_files(b.id, files, profile)
    return JSONResponse(content={"batch_id": b.id, "accepted": accepted, **batches().status(b.id)})


@app.post("/api/batches/{batch_id}/files")
async def add_to_batch(batch_id: str, files: List[UploadFile] = File(...), profile: str = Form("auto")) -> JSONResponse:
    if batch_id not in batches().batches:
        raise HTTPException(status_code=404, detail="unknown batch")
    accepted = await _accept_files(batch_id, files, _check_profile(profile))
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


# ---------------------------------------------------------------------------
# Profiles: document types as config (invoice_extractor/profiles.py, configs/profiles/*.yaml)
# ---------------------------------------------------------------------------

@app.get("/api/profiles")
def api_profiles() -> JSONResponse:
    from . import profiles

    return JSONResponse(content={"profiles": [profiles.describe(p) for p in profiles.load_all().values()]})


@app.get("/api/models")
def api_models() -> JSONResponse:
    from . import profiles

    return JSONResponse(content=profiles.catalog())


@app.put("/api/profiles/{profile_id}/models")
async def api_profile_models(profile_id: str, request: Request) -> JSONResponse:
    from . import profiles

    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be {reader?: id, tables?: id}")
    try:
        return JSONResponse(content=profiles.describe(profiles.save_models(profile_id, body)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/profiles/{profile_id}/extract")
async def api_profile_extract(profile_id: str, file: UploadFile = File(...)) -> JSONResponse:
    """Read one uploaded document under a profile: cited fields, tables and text, with the decision and why."""
    _check_profile(profile_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)   # bounded: never buffer an oversized upload whole
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file exceeds {MAX_UPLOAD_BYTES} byte limit")
    path = _save_upload(file, data)
    try:
        body = await _run_in_thread(lambda: _profile_read(path, profile_id))   # the slot is taken in the worker, never on the event loop
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except (RuntimeError, ValueError) as exc:     # reader down, or it returned no JSON object
        log.warning("profile extraction failed: %s", exc)
        raise HTTPException(status_code=502, detail="The reader failed. Check /ready and the reader server.") from exc
    return JSONResponse(content={**body, "file": file.filename})


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


def _write_env_local(lines: list[str]) -> None:
    """.env.local holds the database password: created owner-only (never briefly world-readable) and replaced atomically."""
    tmp = _ENV_LOCAL.with_name(f".{_ENV_LOCAL.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, _ENV_LOCAL)


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
    if any(c in url + org for c in "\r\n\0"):          # one line each in .env.local: no smuggled settings
        raise HTTPException(status_code=400, detail="url and org must be single lines")
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
    _write_env_local(out)
    return JSONResponse(content={"saved": True, "host": probe["host"], "organization": probe.get("organization"),
                                 "note": "saved to .env.local; restart the app to use it"})


# ---------------------------------------------------------------------------
# Engine updates: version checks + guarded upgrades
# ---------------------------------------------------------------------------

# Packages the app can upgrade itself, the role each serves, and the interpreter that runs it: OCR and the table reader's
# layout step run in the Paddle environment (PPOCR_PYTHON), the model servers in this one (model_pool.PYTHON).
def _paddle_python() -> str:
    return os.environ.get("PPOCR_PYTHON") or os.path.expanduser("~/paddle-env/bin/python")


_UPDATEABLE = (("paddleocr", "PP-OCRv6 text and PaddleOCR-VL tables", "paddle"), ("paddlex", "Paddle pipelines runtime", "paddle"),
               ("mlx-vlm", "Model servers (reader, table reader)", "app"))
_UPDATEABLE_NAMES = tuple(n for n, _, _ in _UPDATEABLE)
_ENV_OF = {n: env for n, _, env in _UPDATEABLE}
_UPDATES_CACHE: dict = {}
_UPDATES_TTL = 6 * 3600


def _interpreter(name: str) -> str:
    return _paddle_python() if _ENV_OF.get(name) == "paddle" else sys.executable


def _installed_version(name: str) -> str | None:
    """The version installed where the package actually runs (the Paddle environment for OCR and tables)."""
    import subprocess

    try:
        out = subprocess.run([_interpreter(name), "-c", f"import importlib.metadata as m; print(m.version({name!r}))"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
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
    for name, role, _env in _UPDATEABLE:
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


@app.get("/api/updates")
def updates_status(refresh: bool = False) -> JSONResponse:
    """Engine versions vs PyPI (cached 6h). ?refresh=1 bypasses the cache. `job` is the install running or last run."""
    with _UPGRADE_LOCK:
        job = dict(_UPGRADE_JOB)
    return JSONResponse(content={**_updates(hard=refresh), "job": job})


@app.get("/api/updates/refresh")
def updates_refresh() -> JSONResponse:
    return JSONResponse(content=_updates(hard=True))


def _updates_busy() -> str | None:
    """Refuse upgrades while extraction work is in flight (batches or singles)."""
    with _EXTRACT_LOCK:
        singles = _EXTRACT_INFLIGHT + _EXTRACT_WAITING   # waiting for a slot counts: it is about to run
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
        # each package where it runs; one pip per environment, stopping at the first failure
        by_env: dict[str, list[str]] = {}
        for name in packages:
            by_env.setdefault(_interpreter(name), []).append(name)
        for python, names in by_env.items():
            proc = subprocess.run([python, "-m", "pip", "install", "-U", *names], capture_output=True, text=True, timeout=1800)
            if proc.returncode:
                break
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
    _write_env_local(out)
    os.environ["ENGINE_AUTO_UPDATE"] = "1" if enabled else "0"     # /api/updates reports it before the restart too
    _UPDATES_CACHE.clear()
    return JSONResponse(content={"auto_update": enabled,
                                 "note": "takes effect at next start (run_app.py upgrades before serving)"})


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------




@app.get("/api/audit/verify")
def audit_verify() -> JSONResponse:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=409, detail="no database configured")
    from database.audit import verify_chain

    return JSONResponse(content=verify_chain())


@app.get("/api/audit/entries")
def audit_entries(limit: int = 200, before: int | None = None) -> JSONResponse:
    """The organization's audit log, newest first, `limit` at a time (`before`: the oldest seq already shown)."""
    org = os.environ.get("INVOICE_ORG_ID")
    if not (os.environ.get("DATABASE_URL") and org):
        raise HTTPException(status_code=409, detail="no database configured")
    from database.audit import entries

    return JSONResponse(content=json.loads(json.dumps(entries(org, max(1, min(limit, 1000)), before), default=str)))


# ---------------------------------------------------------------- evidence --


# Last, so every route above wins: the Fluent build's files (/assets, /fonts, /boot.js, ...). Missing until it is built.
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount("/", StaticFiles(directory=FLUENT_DIST, check_dir=False), name="fluent")


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # loopback unless HOST says otherwise: the API has no login (see ALLOWED_HOSTS)
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
