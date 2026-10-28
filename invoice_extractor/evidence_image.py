"""Evidence images: each page of a processed invoice with a box drawn on every extracted value.

One JPEG per page that carries anything that was read. Each of the 31 output values has a box and a small number
on the page (red = invoice details, blue = charge amounts); every other printed item that was captured (facts.py)
has a thin green box. A panel beside the page lists number, field, value and confidence, then everything else, so
nothing printed is hidden. A caption strip under the page says which
invoice, when and by which pipeline revision the page was read.

The files are written by the caller's choice of folder (database/persist.py stores them under EVIDENCE_DIR and
records path + SHA-256 in the table invoice_evidence); the app serves them at /evidence/<invoice id>.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

FIELD_RGB, CHARGE_RGB, INK = (200, 40, 40), (28, 100, 210), (60, 60, 60)
OTHER_RGB = (46, 139, 87)            # everything else that was read from the page (full capture)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_PAGES = int(os.environ.get("EVIDENCE_MAX_PAGES", "20"))
FONTS = ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
         "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")


def _font(size: int):
    from PIL import ImageFont

    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # older Pillow
        return ImageFont.load_default()


def marks(fields: dict, audit: dict) -> list[dict]:
    """Every extracted value with the boxes that support it, numbered in reading order of the output:
    {n, kind, name, value, confidence, boxes: [{page, bbox}]} in the pipeline's coordinates (PDF points / pixels)."""
    out = []
    expl = audit.get("fields_explained") or {}
    charge_fields = {i.get("category") for i in audit.get("line_items", [])}
    for name, hits in (audit.get("citations") or {}).items():
        if not fields.get(name) or name in charge_fields or not hits:
            continue
        out.append({"kind": "field", "name": name, "value": str(fields[name]),
                    "confidence": (expl.get(name) or {}).get("confidence"),
                    "boxes": [{"page": h.get("page", 1), "bbox": h["bbox"]} for h in hits[:8]]})
    for item, hits in zip(audit.get("line_items", []), audit.get("line_item_citations") or []):
        if item.get("excluded") or not hits:
            continue
        out.append({"kind": "charge", "name": str(item.get("category")),
                    "value": f"{float(item.get('amount_inr') or 0):,.2f}   ({str(item.get('description'))[:46]})",
                    "confidence": item.get("category_confidence"),
                    "boxes": [{"page": hits[0].get("page", 1), "bbox": hits[0]["bbox"]}]})
    for n, m in enumerate(out, 1):
        m["n"] = n
    return out


def _pages(path: str, dpi: int, pages: set[int] | None = None):
    """Yield (page number, PIL image, scale from pipeline coordinates to image pixels)."""
    from PIL import Image

    if Path(path).suffix.lower() in IMAGE_EXTS:
        with Image.open(path) as im:
            yield 1, im.convert("RGB"), 1.0
        return
    import fitz

    with fitz.open(path) as pdf:
        for n, page in enumerate(pdf, 1):
            if pages is not None and n not in pages:
                continue
            pix = page.get_pixmap(dpi=dpi)
            yield n, Image.frombytes("RGB", (pix.width, pix.height), pix.samples), dpi / 72.0


def _wrap(d, text: str, font, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur] if cur else lines


def render_evidence(path: str, fields: dict, audit: dict, out_dir: Path, dpi: int = 130, quality: int = 85,
                    revision: str = "") -> list[dict]:
    """Save the annotated pages into out_dir. Returns [{page, file, sha256, width, height, boxes}].

    The page itself stays readable: each value gets a thin box and a small number; a panel beside the page
    lists number, field, value and confidence."""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    todo = marks(fields, audit)
    with_boxes = {b["page"] for m in todo for b in m["boxes"]} | {f["page"] for f in audit.get("facts") or []} | {1}
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    caption = " · ".join(x for x in (
        f"Invoice {fields.get('Invoice No') or '(no number)'}", str(fields.get("Vendor Name") or "")[:50],
        f"route {audit.get('review_route', '')}", f"read {stamp}", f"pipeline {revision}" if revision else "") if x)
    results = []
    for number, image, scale in _pages(path, dpi, with_boxes):
        if number not in with_boxes or len(results) >= MAX_PAGES:
            continue
        w, h = image.size
        unit = max(12, round(w / 85))
        font, bold, tiny = _font(unit), _font(unit + 1), _font(max(10, unit - 2))
        panel, pad, strip = round(w * 0.46), unit, unit * 2 + 8
        here = [m for m in todo if any(b["page"] == number for b in m["boxes"])]
        others = [f for f in (audit.get("facts") or []) if f.get("page") == number and not f.get("field")]
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        rows, y = [], pad * 3 + unit
        for m in here:                                      # lay the legend out first: its height sets the canvas
            conf = m.get("confidence")
            head = m["name"] + (f"   {conf * 100:.0f}%" if isinstance(conf, (int, float)) else "")
            lines = _wrap(probe, m["value"], font, panel - pad * 4 - unit * 2)
            rows.append((y, m, head, lines))
            y += (unit + 5) * (1 + len(lines)) + pad // 2
        # everything else on the page: one short line each, grouped under its heading / table row
        extra, section, room = [], None, panel - pad * 2
        listed = [f for f in others if f.get("label") != "(column title)"]
        if listed:
            y += pad
            extra.append((y, "title", f"Everything else read from this page ({len(listed)} items)"))
            y += unit + 8
            for f in listed:
                if f.get("section", "") != section:
                    section = f.get("section", "")
                    if section:
                        extra.append((y, "section", section[:90]))
                        y += unit + 3
                text = (f"{f['label']}: " if f.get("label") else "") + f["value"]
                while probe.textlength(text, font=tiny) > room - unit and len(text) > 8:
                    text = text[:-6] + "…"
                extra.append((y, "item", text))
                y += unit + 2
        canvas = Image.new("RGB", (w + panel, max(h, y + pad) + strip), "white")
        canvas.paste(image, (0, 0))
        d = ImageDraw.Draw(canvas)
        badge = unit + 6
        for f in others:                                    # drawn first and thin, so the numbered boxes stay on top
            x0, y0, x1, y1 = (v * scale for v in f["bbox"])
            d.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], outline=OTHER_RGB, width=1)
        for m in here:
            color = FIELD_RGB if m["kind"] == "field" else CHARGE_RGB
            for b in (b for b in m["boxes"] if b["page"] == number):
                x0, y0, x1, y1 = (v * scale for v in b["bbox"])
                d.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], outline=color, width=max(2, unit // 6))
                label = str(m["n"])
                bw = max(badge, d.textlength(label, font=tiny) + 6)
                bx = x1 + 1 if x1 + 1 + bw < w else x0 - bw - 1      # at the box's top corner, mostly above the text line
                by = max(0, y0 - badge * 0.6)
                d.rectangle([bx, by, bx + bw, by + badge - 2], fill=color)
                d.text((bx + 3, by), label, fill="white", font=tiny)
        px = w + pad
        d.line([w, 0, w, canvas.height - strip], fill=(200, 200, 200), width=2)
        d.text((px, pad), "Values read from this page", fill=INK, font=bold)
        d.text((px, pad + unit + 6), "red = invoice details, blue = charges (% = confidence); green = everything else",
               fill=(120, 120, 120), font=tiny)
        for y, m, head, lines in rows:
            color = FIELD_RGB if m["kind"] == "field" else CHARGE_RGB
            label = str(m["n"])
            bw = max(badge, d.textlength(label, font=tiny) + 6)
            d.rectangle([px, y, px + bw, y + badge - 2], fill=color)
            d.text((px + 3, y), label, fill="white", font=tiny)
            d.text((px + unit * 2 + 4, y - 1), head, fill=color, font=bold)
            for k, line in enumerate(lines, 1):
                d.text((px + unit * 2 + 4, y - 1 + (unit + 5) * k), line, fill=INK, font=font)
        if not here:
            d.text((px, pad * 3 + unit), "None of the 31 output fields is on this page.", fill=(120, 120, 120), font=font)
        for y, kind, text in extra:
            if kind == "title":
                d.text((px, y), text, fill=OTHER_RGB, font=bold)
            elif kind == "section":
                d.text((px, y), text, fill=(110, 110, 110), font=tiny)
            else:
                d.text((px + unit, y), text, fill=INK, font=tiny)
        d.rectangle([0, canvas.height - strip, canvas.width, canvas.height], fill=(245, 245, 245))
        d.text((8, canvas.height - strip + 6),
               f"{caption} · page {number} · {len(here)} output values + {len(others)} other items marked", fill=INK, font=font)
        target = out_dir / f"page-{number}.jpg"
        canvas.save(target, "JPEG", quality=quality, optimize=True)
        results.append({"page": number, "file": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "width": canvas.width, "height": canvas.height, "boxes": len(here) + len(others)})
    return results
