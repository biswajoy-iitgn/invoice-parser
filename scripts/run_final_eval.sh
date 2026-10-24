#!/bin/bash
# Final evaluation battery (local Mac: llama.cpp reader + PP-OCRv6 workers). Each step writes a report.
#   bash scripts/run_final_eval.sh            # everything
#   bash scripts/run_final_eval.sh real       # one step: semantic | real | base | synth | sr
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
BASE=${READER_GGUF:-$HOME/models/qwen3vl-8b/Qwen3VL-8B-Instruct-Q8_0.gguf}
LORA=${READER_LORA:-checkpoints/reader-qwen3vl-8b/reader-lora.gguf}
export INVOICE_PIPELINE=v2 READER_BACKEND=llamacpp PPOCR_WORKERS=${PPOCR_WORKERS:-2}
mkdir -p reports

server() {   # $1 = with-lora | base
  pkill -f "llama-server -m $BASE" 2>/dev/null; sleep 1
  local lora=""; [ "$1" = "with-lora" ] && lora="--lora $LORA"
  # shellcheck disable=SC2086
  llama-server -m "$BASE" $lora --host 127.0.0.1 --port 8080 -c 24576 -np 2 -ngl 99 --jinja -fa on --temp 0 \
    > work/llama-server-eval.log 2>&1 &
  until curl -sf -m 2 http://127.0.0.1:8080/health >/dev/null; do sleep 2; done
  echo "reader server up ($1)"
}

step() { [ "${1}" = "${ONLY:-$1}" ]; }
ONLY=${1:-}

if [ -z "$ONLY" ] || [ "$ONLY" = semantic ]; then
  $PY scripts/eval_semantic.py --sem work/sem_v2 --n 4000 --tune --out reports/semantic_eval.json
fi
if [ -z "$ONLY" ] || [ "$ONLY" = real ]; then
  server with-lora
  $PY scripts/eval_real.py --tag v2_final
  $PY scripts/eval_real.py --tag v2_final_scanned --scanned office,phone
fi
if [ -z "$ONLY" ] || [ "$ONLY" = base ]; then
  server base
  $PY scripts/eval_real.py --tag v2_basemodel
  $PY scripts/eval_real.py --tag v2_basemodel_scanned --scanned office,phone
fi
if [ -z "$ONLY" ] || [ "$ONLY" = synth ]; then
  server with-lora
  $PY scripts/eval_synth.py dataset_v2 --split test --source digital --n ${SYNTH_N:-100} --workers 2 --out reports/synth_test_digital.json
  $PY scripts/eval_synth.py dataset_v2 --split test --source scan --n ${SYNTH_N:-100} --workers 2 --out reports/synth_test_scan.json
fi
if [ -z "$ONLY" ] || [ "$ONLY" = sr ]; then
  $PY scripts/eval_sr_ocr.py dataset_v2 --n ${SR_N:-24} --out reports/sr_ocr_eval.json
fi
pkill -f "llama-server -m $BASE" 2>/dev/null
echo FINAL_EVAL_DONE
