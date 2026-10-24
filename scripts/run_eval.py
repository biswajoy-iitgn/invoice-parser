#!/usr/bin/env python3
"""Run the production pipeline over dataset records and write predictions JSONL.

Variants:
  orig    - the rendered invoice image as-is
  lr      - the degraded low-resolution image from the SR pair
  bicubic - LR upscaled with bicubic to HR size (standard-preprocessing control)
  srx2    - SUPERSEDED (licensing, use SwinIR via scripts/eval_sr_ocr.py):
            LR upscaled with pretrained official OmniSR x2 (no fine-tune)

Usage:
  .venv/bin/python scripts/run_eval.py --split val --count 20 --variant orig \\
      --output work/pred_val_orig.jsonl
  .venv/bin/python scripts/evaluate.py dataset/val.jsonl work/pred_val_orig.jsonl

The superseded srx2 variant needs OMNISR_REF set (and OMNISR_PRETRAINED).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.extract import extract_invoice  # noqa: E402


def prepare_image(rec: dict, root: Path, variant: str, tmp: Path) -> str:
    if variant == "orig":
        return str(root / rec["image"])
    pair = rec.get("sr_pair")
    if not pair:
        raise SystemExit(f"record {rec['id']} has no sr_pair; regenerate with --sr-pairs")
    lr = root / pair["lr_path"]
    if variant == "lr":
        return str(lr)
    from PIL import Image
    hr = Image.open(root / pair["hr_path"])
    out = str(tmp / f"{rec['id']}_{variant}.png")
    if variant == "bicubic":
        Image.open(lr).convert("RGB").resize(hr.size, Image.BICUBIC).save(out)
        return out
    if variant == "srx2":
        import subprocess
        ckpt = os.environ.get("OMNISR_PREDICT_CKPT", "")
        cmd = [sys.executable, str(ROOT / "scripts" / "super_resolve.py"),
               "--input", str(lr), "--output", out, "--scale", "2", "--tile", "512"]
        if ckpt:
            cmd += ["--checkpoint", ckpt]
        subprocess.run(cmd, check=True, capture_output=True, text=True,
                       cwd=str(ROOT), timeout=600)
        # Match HR canvas for a fair OCR comparison.
        Image.open(out).convert("RGB").resize(hr.size, Image.BICUBIC).save(out)
        return out
    raise SystemExit(f"unknown variant {variant}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--variant", default="orig", choices=["orig", "lr", "bicubic", "srx2"])
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", default="qwen3-vl:8b-instruct")
    ap.add_argument("--allow-test", action="store_true",
                    help="permit evaluating on the frozen test split (run-once, never tune)")
    a = ap.parse_args()
    if a.split == "test" and not a.allow_test:
        raise SystemExit("refusing to evaluate on the frozen test split "
                         "(dataset/test.jsonl is run-once, never tune); "
                         "pass --allow-test to override")
    if a.split == "test":
        print("WARNING: evaluating on the frozen test split (--allow-test); "
              "do not tune on these results", file=sys.stderr)
    root = Path(a.root)
    rows = [json.loads(x) for x in open(root / f"{a.split}.jsonl") if x.strip()]
    rows = rows[a.offset:a.offset + a.count]
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="eval-variant-") as td:
        with open(a.output, "w") as f:
            for i, rec in enumerate(rows):
                try:
                    img = prepare_image(rec, root, a.variant, Path(td))
                    fields, audit = extract_invoice(img, model=a.model)
                except Exception as exc:  # record failure, keep going
                    fields, audit = ({}, {"error": str(exc)[:300], "timing_sec": {}})
                f.write(json.dumps({
                    "id": rec["id"],
                    "ground_truth": fields,
                    "evidence": audit.get("evidence", ""),
                    "timing_sec": audit.get("timing_sec", {}),
                    "review_route": audit.get("review_route", "REVIEW"),
                    "warnings": audit.get("warnings", []),
                    "error": audit.get("error", ""),
                }, ensure_ascii=False) + "\n")
                f.flush()
                dt = time.time() - t0
                print(f"[{i + 1}/{len(rows)}] {rec['id']} "
                      f"{audit.get('timing_sec', {})} elapsed={dt:.0f}s", flush=True)
    print(f"wrote {a.output} total={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
