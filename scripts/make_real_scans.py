#!/usr/bin/env python3
"""Scanned versions of the real benchmark invoices (image-only PDFs, no text layer).

Each real PDF is rasterized at 200 DPI and pushed through two fixed scan recipes from
make_scans.py (an office copier scan and a phone capture). The gold labels are unchanged, so
eval_real.py --scanned measures the scanned-PDF path on real documents.

  .venv/bin/python scripts/make_real_scans.py
Output: eval/real_invoices/scanned/<ID>__office.pdf, <ID>__phone.pdf
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from make_scans import degrade  # noqa: E402

RECIPES = {
    "office": {"kind": "office_mfp", "dpi": 200, "rotation": 0.8, "shear": 0.004, "keystone": 0.0, "blur": 0.3,
               "motion": False, "ink": "normal", "illum": 0.06, "paper": (250, 248, 240), "shadow": False,
               "noise": 3.0, "speckle": 0.0, "gray": True, "binarize": False, "jpeg": 75},
    "phone": {"kind": "phone", "dpi": 150, "rotation": -1.6, "shear": 0.006, "keystone": 0.03, "blur": 0.5,
              "motion": False, "ink": "normal", "illum": 0.25, "paper": (245, 240, 228), "shadow": False,
              "noise": 4.5, "speckle": 0.0, "gray": False, "binarize": False, "jpeg": 65},
}


def main() -> None:
    import fitz
    from PIL import Image

    gold = json.loads((ROOT / "eval" / "real_invoices" / "gold.json").read_text())
    out = ROOT / "eval" / "real_invoices" / "scanned"
    out.mkdir(parents=True, exist_ok=True)
    for inv in gold["invoices"]:
        src = ROOT / "sample invoices" / inv["files"][0]
        with fitz.open(src) as pdf:
            pages = []
            for pg in pdf:
                pix = pg.get_pixmap(dpi=198)
                pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        for name, recipe in RECIPES.items():
            rnd = random.Random(f"{inv['id']}-{name}")
            imgs = [degrade(p, [], dict(recipe), rnd)[0].convert("RGB") for p in pages]
            dst = out / f"{inv['id']}__{name}.pdf"
            imgs[0].save(dst, save_all=True, append_images=imgs[1:], resolution=float(recipe["dpi"]))
            print(dst.name, len(imgs), "pages")


if __name__ == "__main__":
    main()
