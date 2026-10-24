#!/usr/bin/env python3
"""Validate citation-box grounding against dataset GT bboxes.

For each labeled region, cites the stripped value text on the page image
and measures IoU vs the ground-truth box. Reports grounding rate (IoU>0.3)
and mean best-IoU, overall and for critical header fields.

Usage:
  .venv/bin/python scripts/eval_grounding.py --split val --count 10 --output work/grounding.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.grounding import cite_image_value  # noqa: E402
from invoice_extractor.schema import HEADER_FIELDS  # noqa: E402


def containment(hit, gt) -> float:
    """inter(hit, gt)/area(hit): GT boxes span the full row width while
    citations are tight, so IoU is the wrong metric; a citation counts
    when it points inside the true region."""
    x1, y1 = max(hit[0], gt[0]), max(hit[1], gt[1])
    x2, y2 = min(hit[2], gt[2]), min(hit[3], gt[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = (hit[2] - hit[0]) * (hit[3] - hit[1])
    return inter / area if area > 0 else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--output", default="work/grounding.json")
    a = ap.parse_args()
    root = Path(a.root)
    rows = [json.loads(x) for x in open(root / f"{a.split}.jsonl") if x.strip()][:a.count]
    total = hit = crit_total = crit_hit = 0
    ious: list[float] = []
    misses: list[list] = []
    for rec in rows:
        img = str(root / rec["image"])
        for b in rec.get("regions", []):
            if b["field"] in ("ANNOTATION", "DECOY"):
                continue
            val = re.sub(r"^[^:]{1,40}:\s*", "", b["text"].strip())
            if not val:
                continue
            total += 1
            is_crit = b["field"] in HEADER_FIELDS
            crit_total += is_crit
            hits = cite_image_value(img, val)
            best = max([containment(h.bbox, b["bbox"]) for h in hits], default=0.0)
            ious.append(best)
            ok = best > 0.5
            hit += ok
            crit_hit += ok and is_crit
            if not ok and len(misses) < 10:
                misses.append([rec["id"], b["field"], val[:50], round(best, 2)])
    report = {
        "docs": len(rows),
        "regions": total,
        "grounding_rate": round(hit / total, 4) if total else 0,
        "mean_best_containment": round(sum(ious) / len(ious), 4) if ious else 0,
        "critical_regions": crit_total,
        "critical_grounding_rate": round(crit_hit / crit_total, 4) if crit_total else 0,
        "misses": misses,
    }
    Path(a.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
