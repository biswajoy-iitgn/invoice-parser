#!/usr/bin/env bash
# Retraining stages on a JarvisLabs GPU container (home=/home). Each stage logs to /home/logs/<stage>.log.
#   setup | unpack | ocr | readerdata | reader | export
set -euo pipefail
H=/home; R=$H/invoice_parsing; D=$H/data/dataset_v2; VP=$H/venv-paddle/bin/python
mkdir -p $H/logs $D
cd $R
export PYTHONUNBUFFERED=1
case "${1:-}" in
setup)
  pip install -q "transformers>=4.57,<6" "peft>=0.17" "accelerate>=1.10" pymupdf safetensors "huggingface_hub>=0.34" gguf sentencepiece
  python -m venv $H/venv-paddle
  $VP -m pip install -q --upgrade pip
  $VP -m pip install -q paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
  $VP -m pip install -q "paddleocr==3.7.0" "paddlex[ocr]==3.7.2" pillow
  $VP -c "import paddle; paddle.utils.run_check(); print('PADDLE', paddle.__version__)"
  [ -d $H/llama.cpp ] || git clone -q --depth 1 https://github.com/ggml-org/llama.cpp $H/llama.cpp
  python -c "import torch, transformers, peft; print('MAIN', torch.__version__, torch.cuda.get_device_name(0), transformers.__version__, peft.__version__)"
  echo SETUP_DONE ;;
unpack)
  tar -xf $H/upload/scans_subset.tar -C $D
  cp $H/upload/dataset_v2/*.jsonl $D/
  python - <<'PY'
import json, os
root = "/home/data/dataset_v2"
with open(os.path.join(root, "ocr_requests.jsonl"), "w") as f:
    for split in ("train", "val"):
        for line in open(os.path.join(root, f"scans_{split}.jsonl")):
            r = json.loads(line)
            for j, pg in enumerate(r["pages"]):
                f.write(json.dumps({"id": f"{r['scan_id']}_p{j+1}", "image": os.path.join(root, pg["image"])}) + "\n")
PY
  wc -l $D/ocr_requests.jsonl; echo UNPACK_DONE ;;
ocr)
  python scripts/run_ppocr_batch.py $D/ocr_requests.jsonl $D/ocr_results.jsonl --python $VP --workers ${OCR_WORKERS:-6} --device gpu ;;
readerdata)
  python scripts/build_reader_v2.py $D --ocr $D/ocr_results.jsonl --out work/reader_v2_scan
  python - <<'PY'
import json, random
rnd = random.Random(91731)
def rows(p): return [l for l in open(p) if l.strip()]
dig_tr, scan_tr = rows("/home/upload/reader_v2_digital/train.jsonl"), rows("work/reader_v2_scan/train.jsonl")
dig_va, scan_va = rows("/home/upload/reader_v2_digital/val.jsonl"), rows("work/reader_v2_scan/val.jsonl")
for x in (dig_tr, scan_tr, dig_va, scan_va): rnd.shuffle(x)
n = int(__import__("os").environ.get("READER_PER_SOURCE", "2500"))
train = dig_tr[:n] + scan_tr[:n]; rnd.shuffle(train)
val = dig_va[:120] + scan_va[:120]
import os; os.makedirs("work/reader_v2", exist_ok=True)
open("work/reader_v2/train.jsonl", "w").writelines(train); open("work/reader_v2/val.jsonl", "w").writelines(val)
print("train", len(train), "(digital", min(n, len(dig_tr)), "scan", min(n, len(scan_tr)), ") val", len(val))
PY
  echo READERDATA_DONE ;;
reader)
  python training/train_reader_qwen3vl.py --train work/reader_v2/train.jsonl --val work/reader_v2/val.jsonl \
      --output checkpoints/reader-qwen3vl-8b --max-train 100000 --eval-steps ${EVAL_STEPS:-40} ;;
export)
  BASE=$(python -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-VL-8B-Instruct', allow_patterns=['*.json','*.txt','*.model','tokenizer*']))")
  pip install -q -e $H/llama.cpp/gguf-py 2>/dev/null || true
  python $H/llama.cpp/convert_lora_to_gguf.py checkpoints/reader-qwen3vl-8b/adapter --base "$BASE" \
      --outfile checkpoints/reader-qwen3vl-8b/reader-lora.gguf --outtype f16
  ls -la checkpoints/reader-qwen3vl-8b/; echo EXPORT_DONE ;;
*) echo "unknown stage ${1:-}"; exit 2 ;;
esac
