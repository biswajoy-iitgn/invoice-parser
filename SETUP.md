# SETUP.md — environment

## Mac (measured path)

```sh
cd /Users/dtadmin/Desktop/DevelopmentProjects/invoice_parsing
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests   # 13 tests, must be green
```

External services (all verified present 2026-10-01):

- `ollama` daemon + `qwen3-vl:8b-instruct` (6.1 GB, `ollama pull qwen3-vl:8b-instruct`)
- `llama-mtmd-cli` (homebrew) + `~/models/HunyuanOCR-GGUF/HunyuanOCR-Q8_0.gguf`
  and `mmproj-HunyuanOCR-Q8_0.gguf` (override with `HUNYUAN_GGUF`/`HUNYUAN_MMPROJ`)
- `tesseract` (fallback), `gdown` (pip, for OmniSR weights)

## OmniSR reference (not vendored; no upstream LICENSE found)

```sh
git clone --depth 1 https://github.com/Francis0625/Omni-SR.git /opt/omni-sr-ref
export OMNISR_REF=/opt/omni-sr-ref
.venv/bin/python -m gdown "https://drive.google.com/uc?id=12EvHRof0-kA2Wt_BzfFJBK1J0jbzfz-4" \
  -O $OMNISR_REF/train_logs/OmniSR_X2_DF2K.zip
(cd $OMNISR_REF/train_logs && unzip -o -q OmniSR_X2_DF2K.zip)
export OMNISR_PRETRAINED=$OMNISR_REF/train_logs/OmniSR_X2_DF2K/checkpoints/epoch885_OmniSR.pth
```

## GPU host (Linux/CUDA, RTX PRO 6000 Blackwell via JarvisLabs)

Same as above, plus the Blackwell Paddle runtime (driver must support CUDA
12.9+; check with `nvidia-smi`; ours: 595.58.03):

```sh
python3 -m pip install paddlepaddle-gpu==3.2.1 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
python3 -m pip install -U "paddleocr[doc-parser]" "paddlex[ocr]"
```

Or prefer the official sm120 Docker image (Paddle's recommendation):

```sh
docker run --rm -it --gpus all \
  ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:latest-nvidia-gpu-sm120 \
  /bin/bash
```

Paddle weights download on first use (`~/.paddlex`). Verify with
`python3 scripts/probe_paddle.py "sample invoices/DSV.pdf"` (expects
PP-StructureV3 + PaddleOCR-VL 1.5 output structures, not errors).
