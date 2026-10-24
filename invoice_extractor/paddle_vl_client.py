"""Client for persistent PaddleOCR-VL 1.6 workers (see paddle_vl_worker.py).

The VL model runs under PADDLEVL_PYTHON (default ~/paddle-env/bin/python):
PADDLEVL_WORKERS (default 1) files can be parsed concurrently.

parse() runs the whole-page VL parse; parse_layout() runs the
PP-DocLayoutV3-first mode (layout regions in reading order, every region
read via VL on the crop, PP-OCR only as fallback). Both share one worker
pool. Layout model name via PPDOCLAYOUT_MODEL (default PP-DocLayoutV3).
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import select
import subprocess
import threading
import uuid
from pathlib import Path

WORKER = Path(__file__).with_name("paddle_vl_worker.py")
PYTHON = os.environ.get("PADDLEVL_PYTHON", os.path.expanduser("~/paddle-env/bin/python"))
WORKERS = int(os.environ.get("PADDLEVL_WORKERS", "1"))
TIMEOUT = float(os.environ.get("PADDLEVL_TIMEOUT", "1200"))
# Single-file response wait: shorter than TIMEOUT so a wedged worker is
# killed and retried on a fresh one instead of stalling a batch.
PARSE_TIMEOUT = float(os.environ.get("PADDLEVL_PARSE_TIMEOUT", "900"))


class _Worker:
    def __init__(self) -> None:
        env = dict(os.environ, PADDLE_DEVICE=os.environ.get("PADDLE_DEVICE", "cpu"),
                   HF_HUB_OFFLINE=os.environ.get("HF_HUB_OFFLINE", "1"))
        self.proc = subprocess.Popen(
            [PYTHON, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
        # model load takes a while on CPU; wait up to TIMEOUT for readiness
        if not select.select([self.proc.stdout], [], [], TIMEOUT)[0]:
            self.proc.kill()
            raise RuntimeError(f"PaddleOCR-VL worker failed to start ({PYTHON})")
        line = self.proc.stdout.readline()
        if not line or "ready" not in line:
            raise RuntimeError(f"PaddleOCR-VL worker failed to start ({PYTHON}): {line[:200]}")

    def parse(self, path: str, mode: str = "vl") -> dict:
        rid = uuid.uuid4().hex[:8]
        self.proc.stdin.write(json.dumps({"id": rid, "file": str(path), "mode": mode}) + "\n")
        self.proc.stdin.flush()
        if not select.select([self.proc.stdout], [], [], PARSE_TIMEOUT)[0]:
            self.proc.kill()
            raise RuntimeError(
                f"PaddleOCR-VL worker timed out after {PARSE_TIMEOUT:.0f}s on {path}")
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("PaddleOCR-VL worker died")
        out = json.loads(line)
        if "error" in out:
            raise RuntimeError(f"PaddleOCR-VL failed: {out['error']}")
        return out

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


class _Pool:
    def __init__(self, n: int) -> None:
        self.q: queue.Queue[_Worker] = queue.Queue()
        self.n = max(1, n)
        self.made = 0
        self.lock = threading.Lock()

    def _get(self) -> _Worker:
        try:
            return self.q.get_nowait()
        except queue.Empty:
            pass
        with self.lock:
            if self.made < self.n:
                self.made += 1
                try:
                    return _Worker()
                except Exception as exc:
                    global _LAST_ERROR
                    _LAST_ERROR = f"{type(exc).__name__}: {exc}"[:300]
                    self.made -= 1
                    raise
        return self.q.get()

    def parse(self, path: str, mode: str = "vl") -> dict:
        # One retry on a fresh worker for worker crashes/timeouts only
        # (idle but unresponsive, no CPU burn). Deterministic parse failures
        # raise immediately: retrying would just burn another ~15 min and
        # overwrite _LAST_ERROR with a duplicate.
        try:
            return self._parse_once(path, mode)
        except RuntimeError as exc:
            if "timed out" not in str(exc) and "died" not in str(exc):
                raise
            return self._parse_once(path, mode)

    def _parse_once(self, path: str, mode: str) -> dict:
        w = self._get()
        try:
            return w.parse(path, mode)
        except Exception as exc:
            global _LAST_ERROR
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"[:300]
            with self.lock:
                self.made -= 1
            w.close()
            raise
        else:
            self.q.put(w)

    def close(self) -> None:
        while True:
            try:
                self.q.get_nowait().close()
            except queue.Empty:
                return


_POOL: _Pool | None = None
_POOL_LOCK = threading.Lock()


def pool() -> _Pool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = _Pool(WORKERS)
                atexit.register(_POOL.close)
    return _POOL


def parse(path: str | Path) -> dict:
    return pool().parse(str(path))


def parse_layout(path: str | Path) -> dict:
    """PP-DocLayoutV3-first parse (same pages/blocks shape as parse)."""
    return pool().parse(str(path), "layout")


_LAST_ERROR: str | None = None


def last_error() -> str | None:
    """Last worker spawn/parse failure, if any (available() only checks the interpreter exists)."""
    return _LAST_ERROR


def available() -> bool:
    # ponytail: interpreter-exists check only; a failed worker spawn (missing
    # weights/deps) flips this to False via _LAST_ERROR instead of a probe
    # spawn, which would pay the ~1 min model load on every /ready call.
    if _LAST_ERROR and "failed to start" in _LAST_ERROR:
        return False
    return Path(PYTHON).exists() and WORKER.exists()
