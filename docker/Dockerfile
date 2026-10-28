# Production image: invoice extractor API on Blackwell (sm_120, e.g. RTX PRO 6000).
#
# Base is CUDA 12.9 runtime (matches the cu129 Paddle wheel). The Paddle
# install lines follow the official PaddlePaddle "Support Blackwell" guide:
# cu129 paddlepaddle>=3.2.1, plus NCCL restore for multi-GPU collectives.
# CPU-only build: pass PADDLE_PACKAGE=paddlepaddle (any recent 3.x).
#
# Build:  docker build -t invoice-extractor:1.0 .
# Run:    docker run --gpus all -p 8000:8000 \
#           -e OLLAMA_HOST=http://host.docker.internal:11434 \
#           -v laya-ckpt:/app/checkpoints/laya-invoice \
#           invoice-extractor:1.0

ARG CUDA_IMAGE=nvidia/cuda:12.9.0-runtime-ubuntu22.04
FROM ${CUDA_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INVOICE_INGEST=auto \
    OLLAMA_HOST=http://host.docker.internal:11434 \
    LAYA_MODEL_DIR=/app/checkpoints/laya-invoice/laya-invoice-ft \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip libgomp1 libglib2.0-0 libgl1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Paddle first (largest layer, changes least). cu129 wheel index per the
# official Blackwell guide; NCCL restore keeps multi-GPU collectives working.
ARG PADDLE_PACKAGE="paddlepaddle-gpu==3.2.1"
ARG PADDLE_INDEX="https://www.paddlepaddle.org.cn/packages/stable/cu129/"
RUN pip install --no-cache-dir --index-url ${PADDLE_INDEX} ${PADDLE_PACKAGE} \
 && python3 -c "import paddle; assert paddle.__version__ >= '3.2.1', paddle.__version__" \
 && pip install --no-cache-dir --force-reinstall --no-deps \
      "https://paddle-whl.bj.bcebos.com/nccl/cu129/nccl-2.27.5.1-py3-none-manylinux2014_x86_64.whl" \
 || pip install --no-cache-dir ${PADDLE_PACKAGE}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY invoice_extractor/ invoice_extractor/
COPY configs/ configs/
COPY scripts/eval_laya.py scripts/eval_laya.py

# Non-root runtime user; checkpoint + cache dirs stay writable volumes.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/checkpoints/laya-invoice /home/app/.cache \
 && chown -R app:app /app /home/app/.cache
USER app

# LAYA encoder (ModernBERT-large) + PaddleX official models are downloaded on
# first use into the container cache; pre-warm at deploy time (see RUNBOOK).
VOLUME ["/app/checkpoints/laya-invoice"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python3", "-m", "uvicorn", "invoice_extractor.service:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
