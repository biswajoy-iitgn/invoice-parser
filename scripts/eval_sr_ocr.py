#!/usr/bin/env python3
"""Does SwinIR help OCR? Decision eval on low-resolution val pages (never PSNR alone).

For N val-family pages: downscale x2 + JPEG (a low-DPI scan), then OCR (PP-OCRv6)
  (a) the low-res page as-is, (b) bicubic x2, (c) SwinIR x2 (fine-tuned)
and score word recall / precision and key-value recall against the DOM ground truth.
SR is only enabled in production (USE_SWINIR=1) if (c) beats both (a) and (b).

  .venv/bin/python scripts/eval_sr_ocr.py dataset_v2 --n 24 --out reports/sr_ocr_eval.json
"""
from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEY = ("invoice_number", "invoice_date", "container_number", "bill_of_lading_hbl", "weight", "li_amount",
       "mawb_number", "hawb_number", "exchange_rate")


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def score(ocr: dict, words: list[dict]) -> tuple[float, float, int, int]:
    got = Counter(norm(t) for ln in ocr["lines"] for t in ln["text"].split() if norm(t))
    gt = Counter(norm(w["t"]) for w in words if norm(w["t"]))
    tp = sum((gt & got).values())
    keys = [norm(w["t"]) for w in words if w["k"].split(":")[0] in KEY and w["k"].endswith(":value") and norm(w["t"])]
    return tp / max(1, sum(gt.values())), tp / max(1, sum(got.values())), sum(k in got for k in keys), len(keys)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--out", default="reports/sr_ocr_eval.json")
    a = ap.parse_args()
    from PIL import Image

    from invoice_extractor import ppocr_client, sr_swinir

    root = Path(a.root)
    rows = [json.loads(l) for l in (root / "val.jsonl").open()]
    random.Random(4).shuffle(rows)
    acc = {v: [0.0, 0.0, 0, 0] for v in ("lowres", "bicubic", "swinir")}
    with tempfile.TemporaryDirectory() as tmp:
        for i, r in enumerate(rows[:a.n]):
            ann = json.loads((root / r["ann"]).read_text())
            hr = Image.open(root / r["pages"][0]["image"]).convert("RGB")
            lr = hr.resize((hr.width // 2, hr.height // 2), Image.Resampling.BICUBIC)
            buf = io.BytesIO()
            lr.save(buf, format="JPEG", quality=70)
            lr = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
            p_lr, p_bic, p_sr = (f"{tmp}/{i}_{k}.png" for k in ("lr", "bic", "sr"))
            lr.save(p_lr)
            lr.resize(hr.size, Image.Resampling.BICUBIC).save(p_bic)
            sr_swinir.upscale(p_lr, p_sr)
            for name, p in (("lowres", p_lr), ("bicubic", p_bic), ("swinir", p_sr)):
                rec, prec, k, kn = score(ppocr_client.ocr_image(p), ann["pages"][0]["words"])
                acc[name][0] += rec
                acc[name][1] += prec
                acc[name][2] += k
                acc[name][3] += kn
            print(f"{i + 1}/{a.n}", {k: round(v[0] / (i + 1), 4) for k, v in acc.items()}, flush=True)
    n = a.n
    res = {k: {"word_recall": round(v[0] / n, 4), "word_precision": round(v[1] / n, 4),
               "key_value_recall": round(v[2] / max(1, v[3]), 4)} for k, v in acc.items()}
    better = res["swinir"]["key_value_recall"] > max(res["bicubic"]["key_value_recall"], res["lowres"]["key_value_recall"])
    res["decision"] = "enable USE_SWINIR=1 for low-res inputs" if better else "keep SR off (no OCR gain)"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
