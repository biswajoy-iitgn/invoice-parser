"""Model servers on demand, under one memory budget.

Every model the app runs on the GPU (the reader, the table reader, a second table reader) is its own `mlx_vlm.server`
process holding exactly that model. A server starts the first time a document needs its model, and stops when it has been
idle for MODEL_IDLE_SECONDS. Before a start would push the loaded models past MODEL_MEMORY_GB, the least recently used
idle server is stopped first; when every loaded model is busy, the start waits for one to finish. So the machine never
holds more models than its budget, a model nobody uses costs nothing, and a model in steady use stays warm.

Why separate processes: an mlx_vlm server holds one model at a time and swaps on request, so one shared server would
reload a 9 GB reader every time a page alternated between reading and tables. Two warm servers cost memory once;
swapping costs seconds on every page.

The catalog (configs/models.yaml) gives each model `serve: {path, memory_gb}`; `path` is a Hugging Face id (read from the
local cache, never downloaded at request time) or a folder relative to the repository.

  MODEL_MEMORY_GB       budget for loaded models (default 40% of RAM: leaves the OS, the app, OCR workers and the user's
                        own programs room; the bake-off crashed this Mac twice by loading too much at once)
  MODEL_IDLE_SECONDS    stop a model unused this long (default 600)
  MODEL_START_SECONDS   give up on a start after this long (default 300)
  MLX_PYTHON            interpreter with mlx_vlm (default: this one)
  MODEL_POOL=off        start nothing (tests; a machine that must not load models)
"""
from __future__ import annotations

import atexit
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def _ram_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().total / 2**30
    except Exception:
        return 16.0


BUDGET_GB = float(os.environ.get("MODEL_MEMORY_GB") or round(0.4 * _ram_gb(), 1))
IDLE_SECONDS = float(os.environ.get("MODEL_IDLE_SECONDS", "600"))
START_SECONDS = float(os.environ.get("MODEL_START_SECONDS", "300"))
PYTHON = os.environ.get("MLX_PYTHON") or sys.executable
# Cap math threads in every model process: the bake-off's crashes came from processes each taking every core.
THREAD_CAPS = {k: os.environ.get(k, "8") for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS")}


@dataclass
class Server:
    id: str
    path: str
    memory_gb: float
    port: int = 0
    proc: subprocess.Popen | None = None
    busy: int = 0
    last_used: float = 0.0
    started: float = 0.0
    key: str = field(default_factory=lambda: secrets.token_urlsafe(24))   # guards the server's /unload and /cache routes

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class ModelPool:
    def __init__(self, budget_gb: float = BUDGET_GB, idle_seconds: float = IDLE_SECONDS):
        self.budget_gb, self.idle_seconds = budget_gb, idle_seconds
        self.servers: dict[str, Server] = {}
        self.cond = threading.Condition()
        self.starts = self.stops = 0
        self._reaper: threading.Thread | None = None

    # ------------------------------------------------------------ public --
    def register(self, model_id: str, path: str, memory_gb: float) -> None:
        with self.cond:
            if model_id not in self.servers:
                self.servers[model_id] = Server(model_id, _resolve(path), memory_gb)

    def available(self, model_id: str) -> bool:
        """The model's files are on this machine (a local folder, or a Hugging Face id in the cache)."""
        s = self.servers.get(model_id)
        return bool(s) and _on_disk(s.path)

    @contextmanager
    def use(self, model_id: str):
        """`with pool.use(id) as url:` the model's server, started if needed; it is not stopped while in use."""
        s = self._acquire(model_id)
        try:
            yield s.url
        finally:
            with self.cond:
                s.busy -= 1
                s.last_used = time.monotonic()
                self.cond.notify_all()

    def loaded(self) -> list[dict]:
        with self.cond:
            return [{"model": s.id, "memory_gb": s.memory_gb, "busy": s.busy, "idle_s": round(time.monotonic() - s.last_used)}
                    for s in self.servers.values() if s.alive()]

    def loaded_gb(self) -> float:
        return sum(s.memory_gb for s in self.servers.values() if s.alive())

    def stop_all(self) -> None:
        with self.cond:
            for s in self.servers.values():
                self._stop(s)

    # ----------------------------------------------------------- internals --
    def _acquire(self, model_id: str) -> Server:
        deadline = time.monotonic() + START_SECONDS
        with self.cond:
            s = self.servers[model_id]
            while not s.alive():
                if os.environ.get("MODEL_POOL", "on") == "off":
                    raise RuntimeError(f"model {model_id} not started: the model pool is off (MODEL_POOL=off)")
                if not _on_disk(s.path):
                    raise RuntimeError(f"model {model_id} is not on this machine ({s.path})")
                if s.memory_gb > self.budget_gb:
                    raise RuntimeError(f"model {model_id} needs {s.memory_gb} GB, more than MODEL_MEMORY_GB={self.budget_gb}")
                if self._make_room(s.memory_gb):
                    self._start(s)
                    break
                if not self.cond.wait(timeout=max(0.1, deadline - time.monotonic())) or time.monotonic() > deadline:
                    raise RuntimeError(f"no room to load {model_id}: every loaded model is busy")
            s.busy += 1
            s.last_used = time.monotonic()
        try:
            _wait_ready(s, deadline)
        except Exception:
            with self.cond:
                s.busy -= 1
                self._stop(s)
                self.cond.notify_all()
            raise
        return s

    def _make_room(self, need_gb: float) -> bool:
        """Stop least recently used idle servers until `need_gb` fits. False when busy servers alone leave no room."""
        while self.loaded_gb() + need_gb > self.budget_gb:
            idle = sorted((s for s in self.servers.values() if s.alive() and s.busy == 0), key=lambda s: s.last_used)
            if not idle:
                return False
            log.info("model pool: stopping %s to make room (%.1f GB loaded, budget %.1f GB)", idle[0].id, self.loaded_gb(), self.budget_gb)
            self._stop(idle[0])
        return True

    def _start(self, s: Server) -> None:
        s.port = _free_port()
        env = {**os.environ, **THREAD_CAPS, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        logs = ROOT / "work" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = open(logs / f"model-{s.id}.log", "ab")
        s.proc = subprocess.Popen([PYTHON, "-m", "mlx_vlm.server", "--model", s.path, "--host", "127.0.0.1", "--port", str(s.port),
                                   "--api-key", s.key], stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        out.close()
        s.started = time.monotonic()
        self.starts += 1
        log.info("model pool: starting %s on port %d (%.1f GB of %.1f GB budget)", s.id, s.port, self.loaded_gb(), self.budget_gb)
        self._ensure_reaper()

    def _stop(self, s: Server) -> None:
        if s.proc is not None and s.proc.poll() is None:
            try:
                os.killpg(s.proc.pid, signal.SIGTERM)
                s.proc.wait(timeout=20)
            except Exception:
                try:
                    os.killpg(s.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
            self.stops += 1
        s.proc = None

    def _ensure_reaper(self) -> None:
        if self._reaper is None or not self._reaper.is_alive():
            self._reaper = threading.Thread(target=self._reap, name="model-pool-reaper", daemon=True)
            self._reaper.start()

    def _reap(self) -> None:
        while True:
            time.sleep(min(30.0, max(1.0, self.idle_seconds / 4)))
            with self.cond:
                for s in self.servers.values():
                    if s.alive() and s.busy == 0 and time.monotonic() - s.last_used > self.idle_seconds:
                        log.info("model pool: %s idle for %.0f s, stopping", s.id, time.monotonic() - s.last_used)
                        self._stop(s)
                        self.cond.notify_all()
                if not any(s.alive() for s in self.servers.values()):
                    self._reaper = None
                    return


def _wait_ready(s: Server, deadline: float) -> None:
    """The server answers /health once its model is loaded (it is preloaded at start)."""
    while time.monotonic() < deadline:
        if not s.alive():
            raise RuntimeError(f"model {s.id} exited while loading (see work/logs/model-{s.id}.log)")
        try:
            req = urllib.request.Request(s.url + "/health", headers={"Authorization": f"Bearer {s.key}"})
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"model {s.id} did not load within {START_SECONDS:.0f} s")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _resolve(path: str) -> str:
    """An absolute or repository-relative folder as an absolute path; anything else is a Hugging Face id."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return str(p)
    return str(ROOT / p) if (ROOT / p).exists() else path


def _on_disk(path: str) -> bool:
    if Path(path).is_absolute():
        return Path(path).exists()
    cache = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub")
    snaps = cache / f"models--{path.replace('/', '--')}" / "snapshots"
    return snaps.exists() and any(any(d.glob("*.safetensors")) for d in snaps.iterdir() if d.is_dir())


_POOL: ModelPool | None = None
_POOL_LOCK = threading.Lock()


def pool() -> ModelPool:
    """The process-wide pool, with every model the catalog can serve registered (none started)."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            from .profiles import catalog

            _POOL = ModelPool()
            for kind in ("readers", "tables"):
                for e in catalog().get(kind, []):
                    if e.get("serve"):
                        _POOL.register(e["id"], e["serve"]["path"], float(e["serve"]["memory_gb"]))
            atexit.register(_POOL.stop_all)
        return _POOL
