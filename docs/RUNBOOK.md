# Production Runbook — Invoice Extractor API

## Architecture (production path)

```
upload -> FastAPI /extract -> PP-StructureV3 (layout/tables/reading order)
                              PP-OCRv6 rec (handwritten annotation crops)
                              PaddleOCR-VL 1.6 page markdown: OPT-IN ONLY
                                (INVOICE_VL=on; native backend hangs on the
                                Blackwell/cu129 stack — see benchmark addendum)
                           -> Qwen3-VL 8B via Ollama (schema-constrained read)
                           -> deterministic guards (lexicon, ISO 6346, amount grounding)
                           -> LAYA fine-tuned Choice (ambiguous line classification)
                           -> fixed-schema JSON + audit
```

`INVOICE_INGEST=legacy` (Docling/HunyuanOCR) is Mac-debug only and never
reported in metrics. Production is Paddle-only.

## Deploy (single Blackwell host, e.g. RTX PRO 6000)

1. Copy the LAYA checkpoint to `docker/volumes/laya-invoice-ft/`:
   `model.safetensors`, `rl_agent_config.json`, `tokenizer/`.
2. `docker compose -f docker/docker-compose.yml up -d --build`
3. `docker compose -f docker/docker-compose.yml exec ollama ollama pull qwen3-vl:8b-instruct`
4. Pre-warm (downloads PaddleX official models + ModernBERT encoder, ~10 GB):
   `curl -s localhost:8000/ready` until `"status": "ok"`, then POST one
   sample invoice to `/extract`.
5. Smoke test:
   `curl -F file=@sample.pdf localhost:8000/extract | jq .fields`

## Health model

| Endpoint   | Meaning |
|------------|---------|
| `/health`  | Liveness. 200 = process serving. |
| `/ready`   | Readiness. 200 only when ingest mode resolves, Paddle imports (non-legacy), and Ollama is reachable. LAYA checkpoint absence degrades to the deterministic fallback (not fatal). |
| `/metrics` | Prometheus counters: requests, errors, needs_review, latency sum. |

Orchestrator: liveness → `/health`; readiness gate → `/ready`
(Kubernetes `readinessProbe` or compose `depends_on` + retry).

## Scaling

- The VLM call dominates latency (~60–120 s/invoice on one GPU for long
  docs). Throughput scales with API replicas × GPU-backed Ollama.
- For batch backlogs, put a queue (SQS/RabbitMQ) in front and run workers
  with `--workers 1` per GPU. Never share one Ollama GPU across more
  concurrent long-context requests than VRAM allows (8B @ 32k ctx ≈ 20 GB).
- LAYA semantic calls are CPU-cheap (~0.05 s/field) and scale horizontally.

## Failure modes

| Symptom | Cause | Action |
|---------|-------|--------|
| `/ready` 503, `ollama.reachable=false` | Ollama down / model missing | Restart ollama service; `ollama pull qwen3-vl:8b-instruct`. |
| `/ready` 503, `paddle.available=false` | Paddle install broken / wrong CUDA | Rebuild image; verify `paddlepaddle-gpu==3.2.1` + cu129 on sm_120. |
| `502 extraction backend failure` | LLM timeout / OOM mid-request | Retry once; reduce `num_ctx` if persistent; check Ollama VRAM. |
| All outputs `needs_review=true` | Evidence guards firing (expected until calibrated) | Review queue is the control; tune only after measuring precision on the review sample. |
| Slow first request after deploy | Cold model caches | Pre-warm step above; keep volumes persistent. |

## Rollback

- Image tags are immutable (`invoice-extractor:1.0`, `:1.1`, …). Roll back
  with `docker compose up -d` pinned to the previous tag.
- The LAYA checkpoint is a mounted volume: keep the previous checkpoint
  directory and re-point `LAYA_MODEL_DIR` to roll back the semantic layer
  independently of the image.

## Security notes

- Uploads are size-capped (25 MB default), type-checked (suffix + magic
  bytes), page-capped (60), and processed from temp files deleted after
  each request.
- No raw document bytes leave the host: Ollama, Paddle and LAYA all run
  locally. Audit payloads exclude the evidence blob (`evidence_chars` only).
- Put auth (API key / mTLS) and TLS termination at the ingress; the API
  itself is intentionally auth-free for service-mesh injection.
