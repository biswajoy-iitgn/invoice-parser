#!/bin/sh
# Start the invoice app: llama.cpp reader (Qwen3-VL-8B-Instruct + fine-tuned LoRA) + FastAPI service.
#
# Optional env:
#   READER_GGUF     base model     (default ~/models/qwen3vl-8b/Qwen3VL-8B-Instruct-Q8_0.gguf)
#   READER_LORA     LoRA adapter   (default <repo>/checkpoints/reader-qwen3vl-8b/reader-lora.gguf; skipped if absent)
#   READER_SLOTS    parallel reader requests (default 2)  ·  INVOICE_BATCH_WORKERS (default 2)
#   PPOCR_PYTHON    Paddle interpreter for PP-OCRv6 (default ~/paddle-env/bin/python) · PPOCR_WORKERS (default 2)
#   DATABASE_URL + INVOICE_ORG_ID   persist every extraction (read from .env.local when present)
#   PORT (default 8000), LLAMACPP_HOST (default http://127.0.0.1:8080)
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Local secrets (DATABASE_URL, INVOICE_ORG_ID): gitignored, never committed.
if [ -f "$ROOT/.env.local" ]; then set -a; . "$ROOT/.env.local"; set +a; fi
PORT="${PORT:-8000}"
LLAMACPP_HOST="${LLAMACPP_HOST:-http://127.0.0.1:8080}"
READER_GGUF="${READER_GGUF:-$HOME/models/qwen3vl-8b/Qwen3VL-8B-Instruct-Q8_0.gguf}"
READER_LORA="${READER_LORA:-$ROOT/checkpoints/reader-qwen3vl-8b/reader-lora.gguf}"
READER_SLOTS="${READER_SLOTS:-2}"
INVOICE_ENGINE="${INVOICE_ENGINE:-mineru}"

if [ "$INVOICE_ENGINE" = "mineru" ] || [ "$INVOICE_ENGINE" = "paddle" ] || [ "$INVOICE_ENGINE" = "docling" ]; then
  echo "engine: $INVOICE_ENGINE (no reader server needed)"
  if [ "$INVOICE_ENGINE" = "mineru" ]; then
    MINERU_BIN="${MINERU_BIN:-$(command -v mineru || echo /Users/dtadmin/Desktop/DevelopmentProjects/Table_Parsing/sample/.venv/bin/mineru)}"
    if [ -x "$MINERU_BIN" ]; then "$MINERU_BIN" server start >/dev/null 2>&1 || true; else echo "WARNING: mineru CLI not found (set MINERU_BIN)"; fi
    export MINERU_BIN
  fi
elif ! curl -sf -m 2 "$LLAMACPP_HOST/health" >/dev/null 2>&1; then
  [ -f "$READER_GGUF" ] || { echo "missing reader base model $READER_GGUF"; exit 1; }
  LORA_ARGS=""
  if [ -f "$READER_LORA" ]; then LORA_ARGS="--lora $READER_LORA"; else echo "WARNING: no LoRA adapter at $READER_LORA (base model only)"; fi
  echo "starting llama-server: $READER_GGUF $LORA_ARGS"
  mkdir -p "$ROOT/work"
  # shellcheck disable=SC2086
  llama-server -m "$READER_GGUF" $LORA_ARGS --host 127.0.0.1 --port 8080 -c $((12288 * READER_SLOTS)) \
    -np "$READER_SLOTS" -ngl 99 --jinja -fa on --temp 0 >>"$ROOT/work/llama-server.log" 2>&1 &
  for _ in $(seq 1 90); do
    curl -sf -m 2 "$LLAMACPP_HOST/health" >/dev/null 2>&1 && break
    sleep 2
  done
  curl -sf -m 2 "$LLAMACPP_HOST/health" >/dev/null || { echo "llama-server failed (see work/llama-server.log)"; exit 1; }
fi
if [ "$INVOICE_ENGINE" != "mineru" ] && [ "$INVOICE_ENGINE" != "paddle" ] && [ "$INVOICE_ENGINE" != "docling" ]; then echo "reader: $LLAMACPP_HOST"; fi
cd "$ROOT"
export INVOICE_ENGINE INVOICE_PIPELINE=v2 READER_BACKEND=llamacpp LLAMACPP_HOST
export PPOCR_PYTHON="${PPOCR_PYTHON:-$HOME/paddle-env/bin/python}" PPOCR_WORKERS="${PPOCR_WORKERS:-2}"
export INVOICE_BATCH_WORKERS="${INVOICE_BATCH_WORKERS:-$READER_SLOTS}"
# One process: the batch queue lives in memory (results also persisted under work/batches/).
exec .venv/bin/python -m uvicorn invoice_extractor.service:app --host 127.0.0.1 --port "$PORT"
