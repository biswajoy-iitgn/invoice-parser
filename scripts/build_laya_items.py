#!/usr/bin/env python3
"""Build LAYA fine-tuning decisions from the invoice corpus.

Each text region becomes one Choice decision: state = the region text plus
neighbour-region context, criteria = the 31 canonical fields with ontology
descriptions, gold = the region's ground-truth field. Decoy regions are
excluded (they belong to no field; they stay a held-out distractor test).

Usage:
  .venv/bin/python scripts/build_laya_items.py --split train --output work/laya_train.jsonl
  .venv/bin/python scripts/build_laya_items.py --split val --output work/laya_val.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INSTRUCTIONS = "Which canonical invoice field does this document line describe?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-per-doc", type=int, default=0, help="0 = all regions")
    a = ap.parse_args()
    onto = json.loads((ROOT / "configs" / "field_ontology.json").read_text())
    criteria = {n: s["semantic_description"] + ". Aliases: " + ", ".join(s["aliases"][:6])
                for n, s in onto.items()}
    crit_path = a.output + ".criteria.json"
    json.dump({"instructions": INSTRUCTIONS, "criteria": criteria},
              open(crit_path, "w"), ensure_ascii=False)
    n, skipped = 0, 0
    with open(a.output, "w") as f:
        for line in open(Path(a.root) / f"{a.split}.jsonl"):
            rec = json.loads(line)
            regions = [b for b in rec.get("regions", []) if b["field"] in onto]
            if a.max_per_doc:
                regions = regions[:a.max_per_doc]
            texts = [b["text"] for b in regions]
            for i, b in enumerate(regions):
                ctx = " | ".join(texts[max(0, i - 2):i] + texts[i + 1:i + 3])
                state = f"Line: {b['text']}\nNearby: {ctx}"
                f.write(json.dumps({"state": state, "gold": b["field"],
                                    "source_id": rec["id"]}) + "\n")
                n += 1
            skipped += sum(1 for b in rec.get("regions", []) if b["field"] not in onto)
    print(f"wrote {n} decisions -> {a.output} + {crit_path} (skipped {skipped} decoys)")


if __name__ == "__main__":
    main()
