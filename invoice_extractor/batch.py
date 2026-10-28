"""Batch processing: many documents per request, each read under a profile by a shared worker pool.

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
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor

log = logging.getLogger(__name__)
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
BATCH_DIR = Path(os.environ.get("INVOICE_BATCH_DIR", ROOT / "work" / "batches"))
WORKERS = int(os.environ.get("INVOICE_BATCH_WORKERS", "4"))
RESULTS_IN_MEMORY = int(os.environ.get("INVOICE_BATCH_RESULTS_CACHED", "64"))   # results live on disk; this many stay in RAM


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
    cache_tag: str = ""              # profile, models and pipeline revision that produced (or will produce) the result
    profile: str = "auto"            # the document profile (profiles.py); "auto": the router picks


def _tag(profile: str) -> str:
    """Identity of what produces a result: a cached result is reused only for the same file under the same profile, models,
    checks and pipeline revision, so a model switch or a profile edit never serves a stale reading."""
    from .pipeline import PIPELINE_REV
    from .profiles import revision

    return f"{PIPELINE_REV}+profile:{profile}:{revision(profile)}"


@dataclass
class Batch:
    id: str
    created: float
    items: list[Item] = field(default_factory=list)


class BatchManager:
    def __init__(self, runner: Callable[[Path, str, str], dict], workers: int = WORKERS):
        """runner(path, filename, profile) -> {"result", "audit", "seconds"} (raises on failure)."""
        self.runner = runner
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="invoice-batch")
        self.batches: dict[str, Batch] = {}
        self.results: OrderedDict[str, dict] = OrderedDict()   # item id -> result, newest last; the rest is read from disk
        self._file_locks: dict[str, threading.Lock] = {}       # batch id -> one writer of its items.json at a time
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
                known = {f.name for f in fields(Item)}       # batches saved by an older version carry keys this one dropped
                b = Batch(meta["id"], meta["created"], [Item(**{k: v for k, v in i.items() if k in known}) for i in meta["items"]])
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
    def create(self) -> Batch:
        b = Batch(uuid.uuid4().hex[:12], time.time())
        with self.lock:
            self.batches[b.id] = b
        (BATCH_DIR / b.id).mkdir(parents=True, exist_ok=True)
        return b

    def add(self, batch_id: str, filename: str, data: bytes, saved_path: Path, profile: str = "auto") -> Item:
        sha = hashlib.sha256(data).hexdigest()
        it = Item(uuid.uuid4().hex[:12], filename, len(data), sha, str(saved_path), cache_tag=_tag(profile), profile=profile)
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
                res = self.runner(Path(it.path), it.filename, it.profile)
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
        home = self._home.get(item_id)
        try:
            return self.result(home, item_id) if home else None
        except (KeyError, OSError, ValueError):
            return None

    def _remember(self, item_id: str, res: dict) -> dict:
        with self.lock:
            self.results[item_id] = res
            self.results.move_to_end(item_id)
            while len(self.results) > RESULTS_IN_MEMORY:
                self.results.popitem(last=False)
        return res

    def _finish(self, batch_id: str, it: Item, res: dict, reused: bool = False) -> None:
        r = res.get("result") or {}
        it.summary = {"decision": r.get("decision", "REVIEW"), "title": r.get("title", ""), "reasons": len(r.get("reasons") or []),
                      "profile": (r.get("route") or {}).get("profile", it.profile), "values": len(r.get("fields") or []), "reused": reused}
        it.route = "AUTO_ACCEPT" if r.get("decision") == "ACCEPT" else "REVIEW"
        self._done(batch_id, it, res)

    def _done(self, batch_id: str, it: Item, res: dict) -> None:
        _atomic_write(BATCH_DIR / batch_id / f"{it.id}.json", json.dumps(res, default=str))   # on disk before it is findable
        it.status, it.finished = "done", time.time()
        if not it.started:
            it.started = it.finished
        self._remember(it.id, res)
        with self.lock:
            self.by_hash.setdefault(f"{it.sha256}:{it.cache_tag}", it.id)
            self._home[it.id] = batch_id
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
        with self.lock:                           # two retries at once start the item once
            if it.status not in ("failed", "cancelled"):
                return it
            it.status, it.error, it.started, it.finished = "queued", "", 0.0, 0.0
            it.cache_tag = _tag(it.profile)
            self.futures[it.id] = self.pool.submit(self._run, batch_id, it)
        self._persist(batch_id)
        return it

    def queued(self) -> dict[str, int]:
        """Items waiting and running across every batch (for /metrics)."""
        with self.lock:
            items = [i for b in self.batches.values() for i in b.items]
        return {"queued": sum(i.status == "queued" for i in items), "processing": sum(i.status == "processing" for i in items)}

    # ----------------------------------------------------------------- views --
    def status(self, batch_id: str) -> dict:
        b = self.batches[batch_id]
        counts: dict[str, int] = {}
        for it in b.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        routes = {r: sum(1 for i in b.items if i.status == "done" and i.route == r) for r in ("AUTO_ACCEPT", "REVIEW")}
        done_t = [i.finished - i.started for i in b.items if i.status == "done" and not i.summary.get("reused")]
        return {"id": b.id, "created": b.created, "total": len(b.items), "counts": counts, "routes": routes,
                "finished": all(i.status in ("done", "failed", "cancelled") for i in b.items) and bool(b.items),
                "avg_seconds": round(sum(done_t) / len(done_t), 1) if done_t else None,
                "items": [{k: v for k, v in asdict(i).items() if k != "path"} for i in b.items]}

    def result(self, batch_id: str, item_id: str) -> dict:
        with self.lock:
            hit = self.results.get(item_id)
        if hit is not None:
            return hit
        p = BATCH_DIR / batch_id / f"{item_id}.json"
        if not p.exists():
            raise KeyError(item_id)
        return self._remember(item_id, json.loads(p.read_text()))

    def _persist(self, batch_id: str) -> None:
        """items.json is what a restart reads back: written whole and atomically, one writer per batch, so a crash or two
        workers finishing together never leave a torn file (a torn file would drop the whole batch on the next start)."""
        with self.lock:
            fl = self._file_locks.setdefault(batch_id, threading.Lock())
        try:
            with fl:
                with self.lock:
                    b = self.batches[batch_id]
                    body = {"id": b.id, "created": b.created, "items": [asdict(i) for i in b.items]}
                _atomic_write(BATCH_DIR / batch_id / "items.json", json.dumps(body, default=str))
        except Exception:
            log.exception("batch %s: could not save its state", batch_id)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
