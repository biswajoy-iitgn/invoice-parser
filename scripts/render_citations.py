#!/usr/bin/env python3
"""Draw every citation box with its label on the page images of a processed batch (visual QA).

  .venv/bin/python scripts/render_citations.py <batch_id> [--out work/visual] [--host http://127.0.0.1:8000]
Header fields are red (label above the box); charge lines are blue (category and amount to the left).
"""
from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

FIELD, ITEM = (208, 44, 44), (30, 104, 214)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--out", default="work/visual")
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font = ImageFont.load_default()
    status = httpx.get(f"{a.host}/api/batches/{a.batch}", timeout=30).json()
    for it in status["items"]:
        if it["status"] != "done":
            continue
        body = httpx.get(f"{a.host}/api/batches/{a.batch}/items/{it['id']}", timeout=30).json()
        body = body.get("result", body)
        audit, fields = body["audit"], body["fields"]
        pages = {p["number"]: p for p in audit["pages"]}
        canvas: dict[int, tuple] = {}

        def page(n: int):
            if n not in canvas:
                im = Image.open(io.BytesIO(httpx.get(a.host + pages[n]["url"], timeout=60).content)).convert("RGB")
                canvas[n] = (im, ImageDraw.Draw(im))
            return canvas[n]

        def draw(h: dict, label: str, color, side: str) -> None:
            _, d = page(h["page"])
            x0, y0, x1, y1 = h["bbox"]
            d.rectangle([x0, y0, x1, y1], outline=color, width=2)
            tw = d.textlength(label, font=font)
            lx, ly = (x0 - tw - 12, y0 + (y1 - y0) / 2 - 9) if side == "left" else (x0, y0 - 18 if y0 > 20 else y1 + 2)
            d.rectangle([lx, ly, lx + tw + 8, ly + 17], fill=color)
            d.text((lx + 4, ly + 1), label, fill="white", font=font)

        header = list(fields)[:8] + ["Ex Rate"]
        for f in header:
            for h in (audit["citations"].get(f) or [])[:2]:
                if fields.get(f):
                    draw(h, f"{f}: {fields[f][:30]}", FIELD, "top")
        for li, hits in zip(audit["line_items"], audit["line_item_citations"]):
            if not li.get("excluded") and hits:
                draw(hits[0], f"{li['category']}  {li['amount_inr']:,.2f}", ITEM, "left")
        stem = re.sub(r"[^A-Za-z0-9]+", "_", it["filename"].rsplit(".", 1)[0]).strip("_")
        for n, (im, _) in sorted(canvas.items()):
            im.save(out / f"{stem}_p{n}.png")
            print(out / f"{stem}_p{n}.png", im.size)
        (out / f"{stem}.json").write_text(json.dumps({"fields": fields, "warnings": audit.get("warnings"),
                                                      "route": audit.get("review_route")}, indent=1))


if __name__ == "__main__":
    main()
