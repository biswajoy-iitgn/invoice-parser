#!/usr/bin/env python3
"""Build LayoutLMv3 token-classification data from the corpus.

Words come from Tesseract TSV; each word takes the field label of the GT
region containing its center (else 'O'). Labels are the 31 canonical codes
+ 'O'. Output: HF-ready JSONL with words/bboxes/labels.

Usage:
  .venv/bin/python scripts/build_layoutlmv3_items.py --split train --limit 500
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.grounding import _tesseract_words  # noqa: E402
from invoice_extractor.ontology import EXTERNAL_TO_CANONICAL  # noqa: E402


def label_words(rec: dict, words: list[dict]) -> list[str]:
    regions = [b for b in rec.get("regions", [])
               if b["field"] in EXTERNAL_TO_CANONICAL]
    out = []
    for w in words:
        cx = (w["bbox"][0] + w["bbox"][2]) / 2
        cy = (w["bbox"][1] + w["bbox"][3]) / 2
        lab = "O"
        for b in regions:
            x1, y1, x2, y2 = b["bbox"]
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                lab = EXTERNAL_TO_CANONICAL[b["field"]]
                break
        out.append(lab)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--output", default="")
    a = ap.parse_args()
    root = Path(a.root)
    out = a.output or f"work/layoutlmv3_{a.split}.jsonl"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out, "w") as f:
        for line in open(root / f"{a.split}.jsonl"):
            if n >= a.limit:
                break
            rec = json.loads(line)
            words = _tesseract_words(str(root / rec["image"]))
            if not words:
                continue
            labels = label_words(rec, words)
            f.write(json.dumps({
                "id": rec["id"],
                "words": [w["text"] for w in words],
                "bboxes": [w["bbox"] for w in words],
                "labels": labels,
            }) + "\n")
            n += 1
            if n % 100 == 0:
                print(f"  {n}/{a.limit}", flush=True)
    print(f"wrote {n} docs -> {out}")


if __name__ == "__main__":
    main()
