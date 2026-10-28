#!/usr/bin/env bash
# Stages of the retraining run on the Lightning studio (each idempotent, logs to logs/<stage>.log).
#   bash scripts/remote_stage.sh unpack      # code + app + semantic items from data/upload2
#   bash scripts/remote_stage.sh render      # InvoiceLab render (PNG pass) + GT check + leakage audit
#   bash scripts/remote_stage.sh derive      # text-layer PDFs + scan-degraded copies + OCR request list
#   bash scripts/remote_stage.sh ocr         # PP-OCRv6 (GPU) over every scan page
#   bash scripts/remote_stage.sh semantic    # bi-encoder + LAYA training
#   bash scripts/remote_stage.sh readerdata  # reader examples from text layer + OCR output
#   bash scripts/remote_stage.sh reader      # Qwen3-VL-8B-Instruct LoRA
#   bash scripts/remote_stage.sh swinir      # SwinIR-light x2 document fine-tune
set -euo pipefail
S=/teamspace/studios/this_studio
R=$S/invoice_parsing
D=$S/data
DS=$D/dataset_v2
VP=$S/venv-paddle/bin/python
NPROC=$(nproc)
mkdir -p $R/logs
cd $R
export INVOICELAB_DIR=$D/invoicelab PYTHONUNBUFFERED=1 HF_HUB_ENABLE_HF_TRANSFER=0

serve_app() {
  if ! curl -s -o /dev/null http://127.0.0.1:8901/invoice-maker.html; then
    (cd $D/invoicelab && nohup python -m http.server 8901 --bind 127.0.0.1 > /dev/null 2>&1 &)
    sleep 2
  fi
}

case "${1:-}" in
unpack)
  tar -xzf $D/upload2/code.tgz -C $R
  mkdir -p $D && tar -xf $D/upload2/invoicelab.tar -C $D
  mkdir -p work/sem_v2 && for s in train val test; do gunzip -c $D/upload2/sem_$s.jsonl.gz > work/sem_v2/$s.jsonl; done
  tar -xzf $D/upload2/laya_v2.tgz -C work
  ln -sfn $DS dataset_v2
  echo UNPACK_DONE ;;
fonts)
  if ! fc-match "URW Gothic" | grep -q URWGothic; then
    sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fonts-liberation fonts-liberation2 \
      fonts-dejavu fonts-noto-core fonts-urw-base35 fonts-freefont-ttf fonts-crosextra-carlito fonts-crosextra-caladea \
      fonts-open-sans fonts-roboto fonts-lato fontconfig > /dev/null
    fc-cache -f > /dev/null
  fi
  python -m playwright install --with-deps chromium > /dev/null
  fc-match Futura; fc-match Georgia
  echo FONTS_OK ;;
render)
  bash $0 fonts
  serve_app
  W=$(( NPROC > 4 ? NPROC - 2 : 4 ))
  python scripts/build_invoicelab_v2.py --train 12000 --val 1500 --test 1500 --workers $W --out $DS
  python scripts/check_invoicelab_v2.py $DS
  python scripts/check_leakage.py $DS --output $DS/leakage_report.json
  echo RENDER_DONE ;;
derive)
  serve_app
  W=$(( NPROC > 4 ? NPROC - 2 : 4 ))
  python scripts/build_invoicelab_v2.py --pdf --train 12000 --val 1500 --test 1500 --workers $W --out $DS
  python scripts/make_scans.py $DS --fraction-train 0.6 --workers $W
  python - <<'EOF'
import json, os
root = "/teamspace/studios/this_studio/data/dataset_v2"
with open(os.path.join(root, "ocr_requests.jsonl"), "w") as f:
    for split in ("train", "val", "test"):
        for line in open(os.path.join(root, f"scans_{split}.jsonl")):
            r = json.loads(line)
            for j, pg in enumerate(r["pages"]):
                f.write(json.dumps({"id": f"{r['scan_id']}_p{j+1}", "image": os.path.join(root, pg["image"])}) + "\n")
EOF
  wc -l $DS/ocr_requests.jsonl
  echo DERIVE_DONE ;;
ocr)
  python scripts/run_ppocr_batch.py $DS/ocr_requests.jsonl $DS/ocr_results.jsonl --python $VP --workers ${OCR_WORKERS:-6} --device gpu ;;
semantic)
  python training/train_semantic_encoder.py --data work/sem_v2 --output checkpoints/semantic-encoder
  python training/train_laya.py --train-items work/laya_v2/train.jsonl --val-items work/laya_v2/val.jsonl \
      --criteria work/laya_v2/criteria.json --output checkpoints/laya-invoice-v2 --epochs 3 --batch-size 16 \
      --max-minutes 75 --base-dir $S/laya_base
  echo SEMANTIC_DONE ;;
readerdata)
  python scripts/build_reader_v2.py $DS --ocr $DS/ocr_results.jsonl --out work/reader_v2
  echo READERDATA_DONE ;;
reader)
  python training/train_reader_qwen3vl.py --train work/reader_v2/train.jsonl --val work/reader_v2/val.jsonl \
      --output checkpoints/reader-qwen3vl-8b --max-train ${READER_MAX_TRAIN:-8000} ;;
swinir)
  python - <<'EOF'
import json, random, os
root = "/teamspace/studios/this_studio/data/dataset_v2"
rnd = random.Random(5)
os.makedirs("work/sr_v2", exist_ok=True)
for split, n in (("train", 1800), ("val", 120)):
    rows = [json.loads(l) for l in open(f"{root}/{split}.jsonl")]
    rnd.shuffle(rows)
    with open(f"work/sr_v2/{split}_hr.txt", "w") as f:
        for r in rows[:n]:
            f.write(os.path.join(root, r["image"]) + "\n")
EOF
  python training/train_swinir.py --hr-list work/sr_v2/train_hr.txt --val-list work/sr_v2/val_hr.txt \
      --swinir-ref $S/SwinIR --output checkpoints/swinir-doc-x2 ;;
*) echo "unknown stage: ${1:-}"; exit 2 ;;
esac
