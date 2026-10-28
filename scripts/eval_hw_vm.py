#!/usr/bin/env python3
"""VM-side handwriting eval: PP-OCRv6 rec on annotation crops.

Reads work/hw_sample/refs.json + hw_*.png, transcribes via
invoice_extractor.handwriting.second_opinion, reports exact match and CER.
Also prints which rec model the shared OCR engine resolved (must be v6).
"""
import json
import sys
import time

sys.path.insert(0, "/home/invoice_parsing")
from PIL import Image

from invoice_extractor.handwriting import second_opinion
from invoice_extractor.paddle_backends import ocr_engine


def cer(hyp: str, ref: str) -> float:
    h, r = hyp.strip().casefold(), ref.casefold()
    m, n = len(h), len(r)
    if n == 0:
        return 0.0 if m == 0 else 1.0
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (h[i - 1] != r[j - 1]))
        prev = cur
    return prev[n] / n


def main() -> None:
    eng = ocr_engine()
    # Surface the resolved recognition model for the v6 audit.
    try:
        print("OCR-ENGINE:", type(eng).__name__, flush=True)
        cfg = getattr(getattr(eng, "paddlex_pipeline", None), "config", None)
        print("PIPELINE-CONFIG:", str(cfg)[:300], flush=True)
    except Exception as exc:
        print("ENGINE-INTROSPECT:", type(exc).__name__, str(exc)[:150], flush=True)

    refs = json.loads(open("/home/invoice_parsing/work/hw_sample/refs.json").read())
    crops = [Image.open(f"/home/invoice_parsing/work/hw_sample/{r['file']}").convert("RGB") for r in refs]
    t0 = time.time()
    hyps = second_opinion(crops)
    dt = time.time() - t0
    exact = sum(h.strip().casefold() == r["text"].casefold() for h, r in zip(hyps, refs))
    mean_cer = sum(cer(h, r["text"]) for h, r in zip(hyps, refs)) / max(1, len(refs))
    for h, r in zip(hyps, refs):
        ok = h.strip().casefold() == r["text"].casefold()
        print(f"{r['id']}: gold={r['text']!r} pred={h!r} {'OK' if ok else 'MISS'}", flush=True)
    print(f"HW-RESULT exact={exact}/{len(refs)} cer={mean_cer:.3f} time={dt:.1f}s", flush=True)


if __name__ == "__main__":
    main()
