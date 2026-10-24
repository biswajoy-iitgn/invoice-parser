"""Batch processing: many invoices per request, processed concurrently by a shared worker pool.

A batch is a list of items (one uploaded file each). Items move queued -> processing ->
done | failed | cancelled. Identical files (same SHA-256) are extracted once and reused.
Results are persisted under work/batches/<batch_id>/ so a page reload or a service restart
does not lose finished work.

Concurrency: INVOICE_BATCH_WORKERS (default 4) invoices in flight. OCR workers and the reader
server are shared and thread-safe; the CPU-bound PDF steps run in these threads.
MinerU parses run as subprocesses against the managed server, so they parallelize
across these workers; duplicate files (same SHA-256) are extracted once and reused.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor

log = logging.getLogger(__name__)
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .schema import OUTPUT_FIELDS

ROOT = Path(__file__).resolve().parents[1]
BATCH_DIR = Path(os.environ.get("INVOICE_BATCH_DIR", ROOT / "work" / "batches"))
WORKERS = int(os.environ.get("INVOICE_BATCH_WORKERS", "4"))


@dataclass
class Item:
    id: str
    filename: str
    size: int
    sha256: str
    path: str
    status: str = "queued"           # queued | processing | done | failed | cancelled
    error: str = ""
    duplicate_of: str = ""
    route: str = ""
    started: float = 0.0
    finished: float = 0.0
    summary: dict = field(default_factory=dict)
    cache_tag: str = ""              # models + rules + pipeline revision that produced (or will produce) the result
    engine: str = ""                 # mineru | paddle | v2 | legacy ("" = batch default)


def result_tag(engine: str = "") -> str:
    """Identity of what produces a result. A cached result is reused only for the same file AND the same tag,
    so a new adapter, a rule edit or a pipeline change never serves stale extractions."""
    try:
        from .pipeline import PIPELINE_REV
        from .versions import current

        # current() invalidates itself on file mtime / env-pin change; no per-file clear here.
        base = f"{current()['name']}+{PIPELINE_REV}"
    except Exception:
        base = "unversioned"
    return f"{base}+{engine or 'v2'}"


@dataclass
class Batch:
    id: str
    created: float
    items: list[Item] = field(default_factory=list)
    engine: str = ""                 # default engine for items added without one


class BatchManager:
    def __init__(self, runner: Callable[[Path, str, str], dict], workers: int = WORKERS):
        """runner(path, filename, engine) -> result dict {"fields", "audit", ...} (raises on failure)."""
        self.runner = runner
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="invoice-batch")
        self.batches: dict[str, Batch] = {}
        self.results: dict[str, dict] = {}        # item id -> result
        self.by_hash: dict[str, str] = {}         # sha256 -> item id holding the result
        self.futures: dict[str, Future] = {}       # item id -> pool future (cancel() stops queued ones only)
        self._home: dict[str, str] = {}           # item id -> batch id (to load a result from disk)
        self.lock = threading.Lock()
        BATCH_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        """Bring back batches from disk after a restart: finished results (and the duplicate cache
        built from them) are kept; anything that was queued or mid-flight is marked failed so it can
        be retried from the UI."""
        for f in sorted(BATCH_DIR.glob("*/items.json")):
            try:
                meta = json.loads(f.read_text())
                b = Batch(meta["id"], meta["created"], [Item(**i) for i in meta["items"]])
            except Exception:
                continue
            for it in b.items:
                if it.status in ("queued", "processing"):
                    it.status, it.error, it.finished = "failed", "interrupted by a service restart; retry", time.time()
                elif it.status == "done" and (BATCH_DIR / b.id / f"{it.id}.json").exists():
                    self.by_hash.setdefault(f"{it.sha256}:{it.cache_tag}", it.id)
                    self._home[it.id] = b.id
            self.batches[b.id] = b

    # ---------------------------------------------------------------- create --
    def create(self, engine: str = "") -> Batch:
        b = Batch(uuid.uuid4().hex[:12], time.time(), engine=engine)
        with self.lock:
            self.batches[b.id] = b
        (BATCH_DIR / b.id).mkdir(parents=True, exist_ok=True)
        return b

    def add(self, batch_id: str, filename: str, data: bytes, saved_path: Path, engine: str = "") -> Item:
        sha = hashlib.sha256(data).hexdigest()
        eng = engine or self.batches[batch_id].engine
        it = Item(uuid.uuid4().hex[:12], filename, len(data), sha, str(saved_path),
                  cache_tag=result_tag(eng), engine=eng)
        with self.lock:
            b = self.batches[batch_id]
            b.items.append(it)
            dup = self.by_hash.get(f"{sha}:{it.cache_tag}")
        cached = self._cached(dup)
        if cached is not None:
            it.duplicate_of = dup
            self._finish(batch_id, it, cached, reused=True)
        else:
            fut = self.pool.submit(self._run, batch_id, it)
            with self.lock:
                self.futures[it.id] = fut
        self._persist(batch_id)
        return it

    # ------------------------------------------------------------------- run --
    def _run(self, batch_id: str, it: Item) -> None:
        try:
            with self.lock:
                if it.status == "cancelled":
                    return
                dup = self.by_hash.get(f"{it.sha256}:{it.cache_tag}")
            cached = self._cached(dup)
            if cached is not None:
                it.duplicate_of = dup
                self._finish(batch_id, it, cached, reused=True)
                return
            with self.lock:
                if it.status == "cancelled":  # lost the race with cancel(); never start
                    return
                it.status, it.started = "processing", time.time()
            self._persist(batch_id)
            try:
                res = self.runner(Path(it.path), it.filename, it.engine)
                self._finish(batch_id, it, res)
            except Exception as exc:  # one bad file never stops the batch
                it.status, it.error, it.finished = "failed", f"{type(exc).__name__}: {exc}"[:400], time.time()
                self._persist(batch_id)
        finally:
            with self.lock:
                self.futures.pop(it.id, None)

    def _cached(self, item_id: str | None) -> dict | None:
        """Result of an earlier identical file: from memory, or from disk after a restart."""
        if not item_id:
            return None
        if item_id in self.results:
            return self.results[item_id]
        home = self._home.get(item_id)
        p = BATCH_DIR / home / f"{item_id}.json" if home else None
        if p and p.exists():
            self.results[item_id] = json.loads(p.read_text())
            return self.results[item_id]
        return None

    def _finish(self, batch_id: str, it: Item, res: dict, reused: bool = False) -> None:
        fields = res.get("fields", {})
        audit = res.get("audit", {})
        charges = sum(float(fields[k]) for k in OUTPUT_FIELDS[8:] if k != "Ex Rate" and _isnum(fields.get(k)))
        it.summary = {"invoice_no": fields.get("Invoice No", ""), "vendor": fields.get("Vendor Name", ""),
                      "date": fields.get("Invoice Date", ""), "charges_total": round(charges, 2),
                      "filled": sum(1 for v in fields.values() if v), "warnings": len(audit.get("warnings", [])),
                      "pages": len(audit.get("pages", [])), "reused": reused,
                      # None = no database configured; False = extracted but NOT stored (see persist_error)
                      "persisted": (res.get("persistence") or {}).get("persisted") if "persistence" in res else None,
                      "persist_error": (res.get("persistence") or {}).get("reason", "")}
        it.route = audit.get("review_route", "REVIEW")
        it.status, it.finished = "done", time.time()
        if not it.started:
            it.started = it.finished
        with self.lock:
            self.results[it.id] = res
            self.by_hash.setdefault(f"{it.sha256}:{it.cache_tag}", it.id)
            self._home[it.id] = batch_id
        (BATCH_DIR / batch_id / f"{it.id}.json").write_text(json.dumps(res, default=str))
        self._persist(batch_id)

    # --------------------------------------------------------------- control --
    def cancel(self, batch_id: str) -> int:
        """Cancel queued items (pool futures are dropped so they never start). Items already
        processing run to completion — in-flight work is uncancelable, so only queued items count."""
        n = 0
        with self.lock:
            for it in self.batches[batch_id].items:
                if it.status == "queued":
                    it.status = "cancelled"
                    fut = self.futures.get(it.id)
                    if fut is not None:
                        fut.cancel()  # best-effort: True while still queued, False once running
                    n += 1
        self._persist(batch_id)
        return n

    def retry(self, batch_id: str, item_id: str) -> Item:
        it = next(i for i in self.batches[batch_id].items if i.id == item_id)
        if it.status in ("failed", "cancelled"):
            it.status, it.error, it.started, it.finished = "queued", "", 0.0, 0.0
            it.cache_tag = result_tag(it.engine)
            fut = self.pool.submit(self._run, batch_id, it)
            with self.lock:
                self.futures[it.id] = fut
            self._persist(batch_id)
        return it

    # ----------------------------------------------------------------- views --
    def status(self, batch_id: str) -> dict:
        b = self.batches[batch_id]
        counts: dict[str, int] = {}
        for it in b.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        routes = {r: sum(1 for i in b.items if i.status == "done" and i.route == r) for r in ("AUTO_ACCEPT", "REVIEW")}
        done_t = [i.finished - i.started for i in b.items if i.status == "done" and not i.summary.get("reused")]
        return {"id": b.id, "created": b.created, "engine": b.engine, "total": len(b.items), "counts": counts, "routes": routes,
                "finished": all(i.status in ("done", "failed", "cancelled") for i in b.items) and bool(b.items),
                "avg_seconds": round(sum(done_t) / len(done_t), 1) if done_t else None,
                "items": [{k: v for k, v in asdict(i).items() if k != "path"} for i in b.items]}

    def result(self, batch_id: str, item_id: str) -> dict:
        if item_id not in self.results:
            p = BATCH_DIR / batch_id / f"{item_id}.json"
            if p.exists():
                self.results[item_id] = json.loads(p.read_text())
        return self.results[item_id]

    def done_results(self, batch_id: str) -> list[tuple[Item, dict]]:
        """Results for finished items. An item whose result file went missing from disk is skipped
        (and logged) instead of failing the whole export — same per-item tolerance as batch_sheet."""
        rows: list[tuple[Item, dict]] = []
        for i in self.batches[batch_id].items:
            if i.status != "done":
                continue
            try:
                rows.append((i, self.result(batch_id, i.id)))
            except (KeyError, OSError, ValueError) as exc:
                log.warning("batch %s: result for %s (%s) unreadable, skipped: %s",
                            batch_id, i.id, i.filename, exc)
        return rows

    def _persist(self, batch_id: str) -> None:
        try:
            (BATCH_DIR / batch_id / "batch.json").write_text(json.dumps(self.status(batch_id), default=str))
            b = self.batches[batch_id]
            (BATCH_DIR / batch_id / "items.json").write_text(json.dumps(
                {"id": b.id, "created": b.created, "items": [asdict(i) for i in b.items]}, default=str))
        except Exception:
            pass


def _isnum(v) -> bool:
    try:
        float(v)
        return v not in ("", None)
    except (TypeError, ValueError):
        return False


def export_workbook(rows: list[tuple[Item, dict]]) -> bytes:
    """One workbook for a batch: Invoices (31 fields per row), Line items, Citations, Review."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    inv = wb.active
    inv.title = "Invoices"
    inv.append(["File", "Route", "Warnings", *OUTPUT_FIELDS])
    li = wb.create_sheet("Line items")
    li.append(["File", "Invoice No", "Description", "Amount (INR)", "Category", "Confidence", "Why", "Page",
               "Box (x0,y0,x1,y1)", "Excluded"])
    ci = wb.create_sheet("Citations")
    ci.append(["File", "Field", "Value", "Basis", "Confidence", "Page", "Box (x0,y0,x1,y1)", "Printed text", "Label link"])
    rv = wb.create_sheet("Review")
    rv.append(["File", "Invoice No", "Route", "Warning"])
    review_fill = PatternFill("solid", fgColor="FFF4E5")
    for it, res in rows:
        f, a = res.get("fields", {}), res.get("audit", {})
        inv.append([it.filename, a.get("review_route", ""), len(a.get("warnings", [])), *[f.get(k, "") for k in OUTPUT_FIELDS]])
        if a.get("review_route") != "AUTO_ACCEPT":
            for c in inv[inv.max_row][:2]:
                c.fill = review_fill
        for x in a.get("line_items", []):
            cit = (x.get("amount_citations") or [{}])[0]
            sem = x.get("semantic", {})
            li.append([it.filename, f.get("Invoice No", ""), x.get("description"), x.get("amount_inr"), x.get("category"),
                       x.get("category_confidence"), sem.get("basis", ""), cit.get("page"),
                       ",".join(str(round(v)) for v in cit.get("bbox", [])), x.get("excluded", "")])
        for fld, e in (a.get("fields_explained") or {}).items():
            if not f.get(fld):
                continue
            link = (e.get("label_links") or [{}])[0]
            for h in e.get("citations", [])[:3] or [{}]:
                ci.append([it.filename, fld, f.get(fld), e.get("basis", ""), e.get("confidence"), h.get("page"),
                           ",".join(str(round(v)) for v in h.get("bbox", [])), h.get("text", ""),
                           f"{link.get('label', '')} -> {link.get('field', '')}" if link else ""])
        for w in a.get("warnings", []):
            rv.append([it.filename, f.get("Invoice No", ""), a.get("review_route", ""), w])
    for ws in wb.worksheets:
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.freeze_panes = "B2"
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in list(col)[:200])
            ws.column_dimensions[col[0].column_letter].width = min(48, max(10, width + 2))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
