#!/usr/bin/env python3
"""Probe Paddle stack: versions, PP-StructureV3 + PaddleOCR-VL output shapes.

Usage (GPU host): python3 scripts/probe_paddle.py "sample invoices/DSV.pdf"
Dumps JSON structure summaries (keys, block counts, text excerpts) — the
contract that paddle_backends.py normalizes.
"""
import json
import sys
import time


def summarize(obj, depth=0):
    if isinstance(obj, dict):
        return {k: summarize(v, depth + 1) for k, v in list(obj.items())[:12]}
    if isinstance(obj, (list, tuple)):
        return f"[{len(obj)}]" + (f" first={summarize(obj[0], depth+1)!r}"[:160] if obj else "")
    if isinstance(obj, str):
        return f"str({len(obj)}): {obj[:100]!r}"
    if isinstance(obj, float):
        return round(obj, 4)
    return f"{type(obj).__name__}:{str(obj)[:80]}"


def main() -> None:
    import paddle
    print("paddle", paddle.__version__, flush=True)
    target = sys.argv[1] if len(sys.argv) > 1 else "sample invoices/DSV.pdf"

    from paddlex import create_pipeline
    t0 = time.time()
    s3 = create_pipeline(pipeline="PP-StructureV3", device="gpu")
    print("s3 ready", round(time.time() - t0, 1), flush=True)
    t0 = time.time()
    pages = list(s3.predict(target))
    print("s3 pages", len(pages), "infer", round(time.time() - t0, 1), flush=True)
    print("S3_PAGE_STRUCTURE_BEGIN")
    print(json.dumps(summarize(pages[0]), indent=1)[:3000])
    print("S3_PAGE_STRUCTURE_END", flush=True)

    from paddleocr import PaddleOCRVL
    import os
    ver = os.environ.get("PADDLE_VL_VERSION", "v1.6")
    try:
        vl = PaddleOCRVL(pipeline_version=ver)
    except Exception as exc:
        print(f"vl {ver} unavailable ({exc}); trying v1.6", flush=True)
        vl = PaddleOCRVL(pipeline_version="v1.6")
    t0 = time.time()
    vpages = list(vl.predict(target))
    print("vl pages", len(vpages), "infer", round(time.time() - t0, 1), flush=True)
    print("VL_PAGE_STRUCTURE_BEGIN")
    print(json.dumps(summarize(vpages[0]), indent=1)[:3000])
    print("VL_PAGE_STRUCTURE_END", flush=True)


if __name__ == "__main__":
    main()
