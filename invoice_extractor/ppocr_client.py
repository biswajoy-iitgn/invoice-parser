"""Client for PP-OCRv6 worker processes (Apache-2.0 OCR for scanned PDFs and images).

Paddle has no wheel for the app's Python, so OCR runs in persistent worker processes started
with the interpreter in PPOCR_PYTHON (default ~/paddle-env/bin/python). Workers are pooled:
PPOCR_WORKERS (default 2) pages can be OCR'd concurrently, which is what batch jobs use.
Each worker loads the models once (~3 s) and then serves pages over stdin/stdout JSON.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import select
import subprocess
import threading
from pathlib import Path

WORKER = Path(__file__).with_name("ppocr_worker.py")
PYTHON = os.environ.get("PPOCR_PYTHON", os.path.expanduser("~/paddle-env/bin/python"))
TIMEOUT = float(os.environ.get("PPOCR_TIMEOUT", "300"))


class _Worker:
    def __init__(self) -> None:
        env = dict(os.environ, PADDLE_DEVICE=os.environ.get("PADDLE_DEVICE", "cpu"))
        self.proc = subprocess.Popen([PYTHON, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"PP-OCRv6 worker failed to start ({PYTHON})")
            if line.startswith("{") and '"ready"' in line:
                self.models = json.loads(line).get("models", [])
                break

    def ocr(self, image: str) -> dict:
        self.proc.stdin.write(json.dumps({"id": "x", "image": str(image)}) + "\n")
        self.proc.stdin.flush()
        # a page takes ~20 s on CPU; a worker that says nothing for PPOCR_TIMEOUT seconds is stuck
        if not select.select([self.proc.stdout], [], [], TIMEOUT)[0]:
            self.proc.kill()
            raise RuntimeError(f"PP-OCRv6 worker timed out after {TIMEOUT:.0f}s on {image}")
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("PP-OCRv6 worker died")
        out = json.loads(line)
        if "error" in out:
            raise RuntimeError(f"PP-OCRv6 failed: {out['error']}")
        return out

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


class _Pool:
    def __init__(self, size: int) -> None:
        self.size = size
        self.idle: queue.Queue = queue.Queue()
        self.started = 0
        self.lock = threading.Lock()

    def _get(self) -> _Worker:
        try:
            return self.idle.get_nowait()
        except queue.Empty:
            with self.lock:
                if self.started < self.size:
                    self.started += 1
                    try:
                        return _Worker()
                    except Exception as exc:
                        global _LAST_ERROR
                        _LAST_ERROR = f"{type(exc).__name__}: {exc}"[:300]
                        self.started -= 1
                        raise
            return self.idle.get()

    def ocr(self, image: str) -> dict:
        w = self._get()
        try:
            out = w.ocr(image)
        except Exception as exc:
            global _LAST_ERROR
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"[:300]
            w.close()
            with self.lock:
                self.started -= 1
            raise
        self.idle.put(w)
        return out

    def close(self) -> None:
        while not self.idle.empty():
            self.idle.get_nowait().close()


_POOL: _Pool | None = None
_POOL_LOCK = threading.Lock()


def pool() -> _Pool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = _Pool(int(os.environ.get("PPOCR_WORKERS", "2")))
            atexit.register(_POOL.close)
        return _POOL


_LAST_ERROR: str | None = None


def last_error() -> str | None:
    """Last worker spawn/OCR failure, if any (available() only checks the interpreter exists)."""
    return _LAST_ERROR


def available() -> bool:
    return Path(PYTHON).exists()


def ocr_image(image_path: str) -> dict:
    """{"width", "height", "lines": [{"text", "score", "box", "poly"}], "elapsed", "models"}."""
    if not available():
        raise RuntimeError(f"PP-OCRv6 interpreter not found at {PYTHON} (set PPOCR_PYTHON)")
    return pool().ocr(image_path)
