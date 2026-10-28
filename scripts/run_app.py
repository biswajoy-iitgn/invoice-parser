#!/usr/bin/env python3
"""Start the invoice app (same as scripts/run_app.sh, for launchers that cannot run shell scripts).

Loads .env.local (DATABASE_URL, INVOICE_ORG_ID), builds the UI when its sources changed, and serves the FastAPI app on
HOST:PORT (default 127.0.0.1:8000). Models are not started here: the model pool starts each one when a document first needs
it (READER_WARM=1 starts the reader with the service).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def healthy(host: str) -> bool:
    try:
        with urllib.request.urlopen(host + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ui_stale(ui: Path) -> bool:
    """The Fluent build is missing or older than any of its sources."""
    built = ui / "dist" / "index.html"
    if not built.exists():
        return True
    sources = [ui / "index.html", ui / "package.json", ui / "vite.config.js", *(ui / "src").rglob("*"), *(ui / "public").rglob("*")]
    return max((f.stat().st_mtime for f in sources if f.is_file()), default=0) > built.stat().st_mtime


def build_ui() -> None:
    """The app at / is the Fluent UI (UI/dist): build it when its sources changed. Without npm the last build is served."""
    ui = ROOT / "UI"
    if not ui_stale(ui):
        return
    npm = shutil.which("npm")
    if not npm:
        print("WARNING: the UI build is out of date and npm is not installed; serving the last build", flush=True)
        return
    if not (ui / "node_modules").exists():
        subprocess.run([npm, "ci"], cwd=ui, check=True, timeout=900)
    print("building the UI (npm run build)", flush=True)
    r = subprocess.run([npm, "run", "build"], cwd=ui, timeout=900)
    if r.returncode:
        print("WARNING: the UI build failed; serving the last build", flush=True)


def main() -> None:
    os.chdir(ROOT)
    env_file = ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip("'\"")
    if os.environ.get("ENGINE_AUTO_UPDATE", "0") == "1":
        # Upgrade engines before serving (never mid-batch): opt in via the UI's Updates page. Each package where it runs:
        # OCR and the table reader's layout step in the Paddle environment, the model servers in this one.
        paddle = os.environ.get("PPOCR_PYTHON") or str(Path.home() / "paddle-env/bin/python")
        for python, pkgs in ((paddle, ["paddleocr", "paddlex"]), (sys.executable, ["mlx-vlm"])):
            print(f"ENGINE_AUTO_UPDATE: {python} -m pip install -U {' '.join(pkgs)}", flush=True)
            subprocess.run([python, "-m", "pip", "install", "-U", *pkgs], timeout=1800)
    # Models start on demand in the model pool (invoice_extractor/model_pool.py), under MODEL_MEMORY_GB, and stop when idle:
    # nothing heavy is loaded until a document needs it. READER_BACKEND=llamacpp serves a GGUF reader instead.
    os.environ.setdefault("READER_BACKEND", "pool")
    if os.environ["READER_BACKEND"] == "llamacpp":
        start_llamacpp(os.environ.setdefault("LLAMACPP_HOST", "http://127.0.0.1:8080"))
    build_ui()
    prepare_models()
    os.environ.setdefault("PPOCR_PYTHON", str(Path.home() / "paddle-env/bin/python"))
    os.environ.setdefault("PPOCR_WORKERS", "2")
    os.environ.setdefault("INVOICE_BATCH_WORKERS", "2")      # documents in flight; the reader serves one at a time anyway
    host = os.environ.get("HOST", "127.0.0.1")                # put TLS and the public name on a reverse proxy in front
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "invoice_extractor.service:app", "--host", host,
                              "--port", os.environ.get("PORT", "8000"), "--no-server-header", "--timeout-graceful-shutdown", "30",
                              "--limit-concurrency", os.environ.get("HTTP_MAX_CONNECTIONS", "64"),
                              *(["--proxy-headers", "--forwarded-allow-ips", os.environ["TRUSTED_PROXY"]] if os.environ.get("TRUSTED_PROXY") else [])])


def prepare_models() -> None:
    """Adapted model copies the pool serves (scripts/prepare_nuextract.py), built when their download is present: without the
    copy the roster profile would fall back to a reader that returns no rows there."""
    if (ROOT / "work" / "models" / "NuExtract3-mlx-nvfp4" / "chat_template.jinja").exists():
        return
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "prepare_nuextract.py")], capture_output=True, text=True)
    print(r.stdout.strip() or f"NuExtract3 not prepared (download numind/NuExtract3-mlx-nvfp4 first): {r.stderr.strip()[-200:]}", flush=True)


def start_llamacpp(host: str) -> None:
    """READER_BACKEND=llamacpp only: a llama.cpp server for a GGUF reader."""
    if healthy(host):
        return
    gguf = Path(os.environ.get("READER_GGUF", Path.home() / "models/qwen3vl-8b/Qwen3VL-8B-Instruct-Q8_0.gguf"))
    server = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
    if not gguf.exists():
        sys.exit(f"missing reader model {gguf}")
    slots = os.environ.get("READER_SLOTS", "2")
    cmd = [server, "-m", str(gguf), "--host", "127.0.0.1", "--port", host.rsplit(":", 1)[-1], "-c", str(12288 * int(slots)),
           "-np", slots, "-ngl", "99", "--jinja", "-fa", "on", "--temp", "0"]
    (ROOT / "work").mkdir(exist_ok=True)
    subprocess.Popen(cmd, stdout=open(ROOT / "work" / "llama-server.log", "ab"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(90):
        if healthy(host):
            print(f"reader: {host}", flush=True)
            return
        time.sleep(2)
    sys.exit("llama-server failed to start (see work/llama-server.log)")


if __name__ == "__main__":
    main()
