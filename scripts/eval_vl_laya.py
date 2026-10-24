#!/usr/bin/env python3
"""LAYA-gated VL eval: score a Qwen3-VL (+adapter) checkpoint on val images.

For each image: generate extraction JSON, then measure:
  - json_valid_rate (parse success)
  - category accuracy (pred line-items aligned to GT by amount, category match)
  - laya_agreement (fine-tuned LAYA vote on each pred description == pred category)
  - header recall (invoice_no/vendor/mawbs present and correct)

Used as the early-stopping signal for QLoRA: stop when val loss rises AND
these metrics plateau/degrade over consecutive checkpoints. LAYA judges
semantics; GT judges verbatim correctness — both must hold.

Usage:
  .venv/bin/python scripts/eval_vl_laya.py --adapter checkpoints/vl-qwen3-8b --limit 20
  (omit --adapter for the base model)
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_reader_items import VL_QUESTION, build_target  # noqa: E402
from invoice_extractor.laya_client import semantic_field  # noqa: E402
from invoice_extractor.mlx_reader import _extract_json  # noqa: E402


def _norm_amount(x) -> str:
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-VL-8B-Instruct-4bit")
    ap.add_argument("--adapter", default="")
    ap.add_argument("--split", default="val")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--output", default="work/vl_laya_eval.json")
    a = ap.parse_args()

    from mlx_vlm import generate, load

    print(f"loading {a.model} adapter={a.adapter or 'none'}", flush=True)
    model, processor = load(a.model, adapter_path=a.adapter or None)

    # 4-bit MLX Qwen3-VL collapses on full-res + long prompt; 784x1008
    # (28px-aligned) is the validated stable size. Same for training.
    from PIL import Image as _Image

    def _sized(src: str) -> str:
        dst = f"/tmp/vl_eval_{Path(src).stem}.png"
        _Image.open(src).convert("RGB").resize((784, 1008), _Image.LANCZOS).save(dst)
        return dst

    recs = [json.loads(l) for l in open(Path(a.root) / f"{a.split}.jsonl")][:a.limit]
    valid = cat_ok = cat_n = agree = agree_n = 0
    hdr_ok = hdr_n = 0
    t0 = time.time()
    for i, rec in enumerate(recs):
        img = _sized(str(Path(a.root) / rec["image"]))
        _, gold = build_target(rec)
        try:
            msgs = [{"role": "user", "content": [
                {"type": "image"}, {"type": "text", "text": VL_QUESTION}]}]
            prompt = processor.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True)
            res = generate(model, processor, prompt, image=img,
                           max_tokens=a.max_tokens, verbose=False)
            pred = _extract_json(res.text)
            valid += 1
        except Exception as exc:
            print(f"  [{i}] {rec['id']} INVALID: {type(exc).__name__}", flush=True)
            continue
        for k in ("invoice_no", "vendor_name", "mawb_no", "hawb_no"):
            if gold.get(k):
                hdr_n += 1
                hdr_ok += str(pred.get(k, "")).strip() == str(gold[k]).strip()
        gitems = {(g.get("category"), _norm_amount(g.get("amount_inr")))
                  for g in gold.get("line_items", [])}
        for it in pred.get("line_items", []) or []:
            desc = str(it.get("description", ""))
            cat = it.get("category")
            amt = _norm_amount(it.get("amount_inr"))
            if not desc or not amt:
                continue
            cat_n += 1
            cat_ok += (cat, amt) in gitems
            vote, backend = semantic_field(desc)
            agree_n += 1
            agree += (vote == cat)
        print(f"  [{i}] {rec['id']} items={len(pred.get('line_items', []) or [])}", flush=True)

    report = {
        "model": a.model,
        "adapter": a.adapter,
        "n": len(recs),
        "json_valid_rate": round(valid / len(recs), 4),
        "category_accuracy": round(cat_ok / cat_n, 4) if cat_n else 0,
        "category_n": cat_n,
        "laya_agreement": round(agree / agree_n, 4) if agree_n else 0,
        "laya_n": agree_n,
        "header_recall": round(hdr_ok / hdr_n, 4) if hdr_n else 0,
        "elapsed_s": round(time.time() - t0, 1),
    }
    Path(a.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
