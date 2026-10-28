"""Document parsing with PaddleOCR-VL 1.6 (Apache-2.0): the page as blocks in reading order.

The 31 fields come from the reader, which works on words and lines. For "everything else on the page" a line-based
view breaks paragraphs at every printed line, cannot tell a table from a form, and misses text that is part of a
picture (a logo, a stamp). PaddleOCR-VL reads the page the way a person does: it finds the blocks (text, titles,
tables, headers, footers, seals, pictures), reads each one, keeps a paragraph in one piece, returns tables as rows
and cells, and gives the blocks in reading order with their boxes.

Paddle has no wheel for the app's Python, so the pipeline runs in one persistent worker process started with the
interpreter in PPOCR_PYTHON (default ~/paddle-env/bin/python). Layout detection runs there on CPU; the VLM that reads each
block runs on the model pool's GPU server (model_pool.py, catalog id `document-parser`), started only when a page needs
it. Without the pool's model on this machine, the whole pipeline runs on CPU: about 90 s per page.
Results are cached on disk by the SHA-256 of the page image, so a page is parsed once.

  DOC_PARSER=paddleocr-vl | off     (default: paddleocr-vl when the Paddle interpreter exists)
"""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import select
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("vl_worker.py")
PYTHON = os.environ.get("PPOCR_PYTHON", os.path.expanduser("~/paddle-env/bin/python"))
TIMEOUT = float(os.environ.get("DOC_PARSER_TIMEOUT", "600"))
CACHE = Path(os.environ.get("DOC_PARSER_CACHE", ROOT / "work" / "vl_cache"))
VERSION = os.environ.get("PADDLEOCR_VL_VERSION", "v1.6")
MODEL = f"PaddleOCR-VL {VERSION.lstrip('v')}"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_proc_server: str | None = None           # the GPU server the running worker was started against


def enabled() -> bool:
    mode = os.environ.get("DOC_PARSER", "paddleocr-vl").lower()
    return mode not in ("off", "0", "none", "") and Path(PYTHON).exists()


def _start(server: tuple[str, str] | None = None) -> subprocess.Popen:
    env = dict(os.environ, PADDLE_DEVICE=os.environ.get("PADDLE_DEVICE", "cpu"), PADDLEOCR_VL_VERSION=VERSION)
    if server:
        env.update(VL_SERVER_URL=server[0] + "/v1", VL_SERVER_MODEL=server[1])
    proc = subprocess.Popen([PYTHON, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
    while True:
        if not select.select([proc.stdout], [], [], 300)[0]:
            proc.kill()
            raise RuntimeError("PaddleOCR-VL worker did not start within 300 s")
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"PaddleOCR-VL worker failed to start ({PYTHON})")
        if line.startswith("{") and '"ready"' in line:
            return proc


def _close() -> None:
    global _proc
    if _proc is not None:
        try:
            _proc.stdin.close()
            _proc.terminate()
        except Exception:
            pass
        _proc = None


atexit.register(_close)


def parse_image(png: str) -> dict:
    """Blocks of one page image: {"width", "height", "elapsed", "blocks": [...], "cached": bool}."""
    global _proc
    key = hashlib.sha256(Path(png).read_bytes()).hexdigest() + "-" + VERSION
    hit = CACHE / f"{key}.json"
    if hit.exists():
        return {**json.loads(hit.read_text()), "cached": True}
    with _lock, _gpu() as server:                 # one page at a time: layout detection uses every CPU core
        global _proc_server
        if _proc is not None and _proc.poll() is None and _proc_server != (server and server[0]):
            _close()                              # the GPU server moved (restarted after an idle stop): reconnect
        if _proc is None or _proc.poll() is not None:
            _proc = _start(server)
            _proc_server = server and server[0]
        _proc.stdin.write(json.dumps({"image": str(png)}) + "\n")
        _proc.stdin.flush()
        if not select.select([_proc.stdout], [], [], TIMEOUT)[0]:
            _close()
            raise RuntimeError(f"PaddleOCR-VL timed out after {TIMEOUT:.0f} s on {png}")
        line = _proc.stdout.readline()
    if not line:
        _close()
        raise RuntimeError("PaddleOCR-VL worker died")
    out = json.loads(line)
    if "error" in out:
        raise RuntimeError(f"PaddleOCR-VL failed: {out['error']}")
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = hit.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.replace(hit)
    return {**out, "cached": False}


@contextlib.contextmanager
def _gpu():
    """(url, model) of the pool's PaddleOCR-VL server, held for this page; None when the pool can't serve it here."""
    try:
        from .model_pool import pool

        if os.environ.get("DOC_PARSER_GPU", "1") != "1" or not pool().available("document-parser"):
            yield None
            return
    except Exception:
        yield None
        return
    with pool().use("document-parser") as url:
        yield url, pool().servers["document-parser"].path


def parse_document(path: str, dpi: int = 150) -> list[dict]:
    """Every page of a PDF or image as blocks, in the pipeline's coordinates (PDF points, or pixels for an image).

    Returns [{"page", "seconds", "cached", "blocks": [{"id", "label", "order", "bbox", "content"}]}]."""
    import tempfile

    ext = os.path.splitext(path)[1].lower()
    pages = []
    if ext != ".pdf":
        res = parse_image(path)
        return [{"page": 1, "seconds": res.get("elapsed"), "cached": res["cached"], "blocks": res["blocks"]}]
    import fitz

    with fitz.open(path) as pdf, tempfile.TemporaryDirectory() as tmp:
        for n, page in enumerate(pdf, 1):
            png = os.path.join(tmp, f"p{n}.png")
            page.get_pixmap(dpi=dpi).save(png)
            res = parse_image(png)
            scale = 72.0 / dpi
            pages.append({"page": n, "seconds": res.get("elapsed"), "cached": res["cached"],
                          "blocks": [{**b, "bbox": [round(v * scale, 2) for v in b["bbox"]]} for b in res["blocks"]]})
    return pages
