#!/usr/bin/env python3
"""Visual debugging: annotated images for failed invoices.

For each mismatched document writes reports/visual_errors/<id>.png showing the
invoice with GT region boxes (green) plus a side panel listing gold vs pred
per mismatched field. Also copies the LR and (if present) SR images alongside.

Usage:
  .venv/bin/python scripts/visual_errors.py dataset/val.jsonl work/pred_val_orig.jsonl
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate import FIELDS, field_equal  # noqa: E402


def _font(size: int):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ground_truth")
    ap.add_argument("predictions")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--out-dir", default="reports/visual_errors")
    ap.add_argument("--max", type=int, default=25)
    a = ap.parse_args()
    root = Path(a.root)
    gt = {json.loads(l)["id"]: json.loads(l) for l in open(a.ground_truth) if l.strip()}
    pr = {json.loads(l)["id"]: json.loads(l) for l in open(a.predictions) if l.strip()}
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    done = 0
    for i, g in gt.items():
        if i not in pr or done >= a.max:
            continue
        gold, pred = g["ground_truth"], pr[i]["ground_truth"]
        bad = [(k, gold.get(k), pred.get(k)) for k in FIELDS
               if not field_equal(k, gold.get(k), pred.get(k))]
        if not bad:
            continue
        img = Image.open(root / g["image"]).convert("RGB")
        d = ImageDraw.Draw(img)
        for b in g.get("regions", []):
            x1, y1, x2, y2 = b["bbox"]
            d.rectangle([x1, y1, x2, y2], outline=(34, 139, 34), width=2)
        panel_w = 640
        panel = Image.new("RGB", (panel_w, img.height), (24, 28, 33))
        pd = ImageDraw.Draw(panel)
        y = 12
        pd.text((10, y), f"{i}  {len(bad)} mismatches", font=_font(20), fill=(255, 210, 63))
        y += 34
        for k, gv, pv in bad[:22]:
            pd.text((10, y), f"{k}", font=_font(15), fill=(120, 200, 255))
            y += 22
            pd.text((24, y), f"gold: {gv}", font=_font(13), fill=(140, 230, 140))
            y += 20
            pd.text((24, y), f"pred: {pv}", font=_font(13), fill=(255, 140, 140))
            y += 26
            if y > img.height - 30:
                break
        combo = Image.new("RGB", (img.width + panel_w, img.height), (24, 28, 33))
        combo.paste(img, (0, 0))
        combo.paste(panel, (img.width, 0))
        combo.save(out / f"{i}.png")
        pair = g.get("sr_pair")
        if pair:
            for key in ("lr_path", "hr_path"):
                src = root / pair[key]
                if src.exists():
                    Image.open(src).save(out / f"{i}_{Path(key).stem}{src.suffix}")
        done += 1
    print(f"wrote {done} visual error cases to {out}")


if __name__ == "__main__":
    main()
