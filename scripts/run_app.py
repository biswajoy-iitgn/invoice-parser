#!/usr/bin/env python3
"""Start the invoice app (same as scripts/run_app.sh, for launchers that cannot run shell scripts).

Loads .env.local (DATABASE_URL, INVOICE_ORG_ID), makes sure the llama.cpp reader server is up
(base Qwen3-VL-8B GGUF + the fine-tuned LoRA), then serves the FastAPI app on PORT (default 8000).
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


def main() -> None:
    os.chdir(ROOT)
    env_file = ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip("'\"")
    if os.environ.get("ENGINE_AUTO_UPDATE", "0") == "1":
        # Upgrade engines before serving (never mid-batch): opt in via the UI's update panel.
        pkgs = [p.strip() for p in os.environ.get(
            "ENGINE_AUTO_UPDATE_PKGS", "docling,paddleocr,paddlex,laya").split(",") if p.strip()]
        if pkgs:
            print(f"ENGINE_AUTO_UPDATE: pip install -U {' '.join(pkgs)}", flush=True)
            subprocess.run([sys.executable, "-m", "pip", "install", "-U", *pkgs], timeout=1800)
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        # the LAYA semantic model takes ~16 s per invoice on CPU and under 3 s on the Apple GPU
        os.environ.setdefault("LAYA_DEVICE", "mps")
    engine = os.environ.get("INVOICE_ENGINE", "mineru").lower()
    host = os.environ.setdefault("LLAMACPP_HOST", "http://127.0.0.1:8080")
    slots = os.environ.get("READER_SLOTS", "2")
    if engine in ("mineru", "paddle", "ppdoclayout", "docling"):
        print(f"engine: {engine} (no reader server needed)", flush=True)
        if engine == "mineru":
            mineru = (os.environ.get("MINERU_BIN") or shutil.which("mineru")
                       or "/Users/dtadmin/Desktop/DevelopmentProjects/Table_Parsing/sample/.venv/bin/mineru")
            if Path(mineru).exists() or shutil.which(mineru):
                subprocess.run([mineru, "server", "start"], capture_output=True, timeout=120)
            else:
                print("WARNING: mineru CLI not found (set MINERU_BIN)", flush=True)
    elif not healthy(host):
        gguf = Path(os.environ.get("READER_GGUF", Path.home() / "models/qwen3vl-8b/Qwen3VL-8B-Instruct-Q8_0.gguf"))
        lora = Path(os.environ.get("READER_LORA", ROOT / "checkpoints/reader-qwen3vl-8b/reader-lora.gguf"))
        server = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
        if not gguf.exists():
            sys.exit(f"missing reader base model {gguf}")
        cmd = [server, "-m", str(gguf), "--host", "127.0.0.1", "--port", "8080", "-c", str(12288 * int(slots)),
               "-np", slots, "-ngl", "99", "--jinja", "-fa", "on", "--temp", "0"]
        if lora.exists():
            cmd[3:3] = ["--lora", str(lora)]
        else:
            print(f"WARNING: no LoRA adapter at {lora} (base model only)", flush=True)
        (ROOT / "work").mkdir(exist_ok=True)
        subprocess.Popen(cmd, stdout=open(ROOT / "work" / "llama-server.log", "ab"), stderr=subprocess.STDOUT,
                         start_new_session=True)
        for _ in range(90):
            if healthy(host):
                break
            time.sleep(2)
        else:
            sys.exit("llama-server failed to start (see work/llama-server.log)")
    if engine not in ("mineru", "paddle", "ppdoclayout", "docling"):
        print(f"reader: {host}", flush=True)
    os.environ.update(INVOICE_PIPELINE="v2", READER_BACKEND="llamacpp")
    os.environ.setdefault("PPOCR_PYTHON", str(Path.home() / "paddle-env/bin/python"))
    os.environ.setdefault("PPOCR_WORKERS", "2")
    os.environ.setdefault("INVOICE_BATCH_WORKERS", slots)
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "invoice_extractor.service:app", "--host", "127.0.0.1",
                              "--port", os.environ.get("PORT", "8000")])


if __name__ == "__main__":
    main()
