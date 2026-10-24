"""What exactly produced an extraction: every model and rule file, identified by content hash.

The set gets a short name ("invoice-v2-<8 hex>") that is stored with each extraction run, so a value in
the database can always be traced to the exact reader adapter, OCR models, semantic models and rules.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "work" / "model_hashes.json"


def _sha256(path: Path) -> str:
    """SHA-256 of a file, cached by (path, size, mtime) because some checkpoints are gigabytes."""
    st = path.stat()
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    try:
        cache = json.loads(CACHE.read_text())
    except Exception:
        cache = {}
    if key not in cache:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""):
                h.update(chunk)
        cache[key] = h.hexdigest()
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache, indent=1))
        except OSError:
            pass
    return cache[key]


def _file(role: str, model: str, path: Path, **extra) -> dict | None:
    if not path.exists():
        return None
    digest = _sha256(path)
    return {"role": role, "model": model, "version": digest[:16], "checkpoint_uri": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
            "configuration": {"sha256": digest, "bytes": path.stat().st_size, **extra}}


def _specs() -> list[tuple[str, str, Path, dict]]:
    """Versioned (role, model, path, extra) rows; shared by the builder and the staleness snapshot."""
    home = Path.home()
    return [
        ("reader", "qwen3-vl-8b-instruct", Path(os.environ.get("READER_GGUF", home / "models/qwen3vl-8b/Qwen3VL-8B-Instruct-Q8_0.gguf")), {"license": "Apache-2.0"}),
        ("reader-adapter", "invoice-reader-lora", Path(os.environ.get("READER_LORA", ROOT / "checkpoints/reader-qwen3vl-8b/reader-lora.gguf")), {}),
        ("semantic-encoder", "bge-small-invoice", Path(os.environ.get("SEMANTIC_ENCODER_DIR", ROOT / "checkpoints/semantic-encoder")) / "model.safetensors", {"license": "MIT"}),
        ("semantic-laya", "laya-invoice-v2", Path(os.environ.get("LAYA_MODEL_DIR", ROOT / "checkpoints/laya-invoice-v2/laya-invoice-ft")) / "model.safetensors", {"license": "Apache-2.0"}),
        ("rules", "category-rules", ROOT / "configs/category_rules.json", {}),
        ("fusion", "semantic-fusion", ROOT / "configs/semantic_fusion.json", {}),
        ("learned-aliases", "reviewer-aliases", ROOT / "configs/learned_aliases.json", {}),
    ]


def _snapshot() -> tuple:
    """Cheap staleness key: (size, mtime) per versioned file + env-pinned model names. A rule edit
    (e.g. reviewer feedback rewriting learned_aliases.json) changes an mtime and busts the cache."""
    snap: list = []
    for _, _, path, _ in _specs():
        try:
            st = path.stat()
            snap.append((st.st_size, int(st.st_mtime)))
        except OSError:
            snap.append(None)
    snap.append((os.environ.get("PPOCR_DET_MODEL"), os.environ.get("PPOCR_REC_MODEL"),
                 os.environ.get("PPOCR_DET_LIMIT")))
    return tuple(snap)


_snap_lock = threading.Lock()
_last_snap: tuple | None = None


@lru_cache(maxsize=1)
def _build() -> dict:
    comps = [_file(role, model, path, **extra) for role, model, path, extra in _specs()]
    comps = [c for c in comps if c]
    comps.append({"role": "ocr", "model": "pp-ocrv6", "version": os.environ.get("PPOCR_DET_MODEL", "PP-OCRv6_medium_det") + "+" +
                  os.environ.get("PPOCR_REC_MODEL", "PP-OCRv6_medium_rec"), "checkpoint_uri": "paddlex official models",
                  "configuration": {"license": "Apache-2.0", "det_limit": os.environ.get("PPOCR_DET_LIMIT", "2560")}})
    digest = hashlib.sha256(json.dumps([(c["role"], c["version"]) for c in comps], sort_keys=True).encode()).hexdigest()
    return {"name": f"invoice-v2-{digest[:8]}", "components": comps}


def current() -> dict:
    """{"name": "invoice-v2-xxxxxxxx", "components": [...]}. Cached; rebuilt only when a versioned
    file's size/mtime or a model env pin changes, so per-file cache_clear() calls are unnecessary."""
    global _last_snap
    snap = _snapshot()
    with _snap_lock:
        if snap != _last_snap:
            _build.cache_clear()
            _last_snap = snap
    return _build()
