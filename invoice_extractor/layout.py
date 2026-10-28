"""Line-numbered layout text: the single representation the reader sees and cites.

Both page sources produce *units* (text + box in page pixels/points):
  * digital PDFs -> the PDF's own text layer (PyMuPDF words; exact, no OCR)
  * scans/images -> PP-OCRv6 text segments (ppocr_client)

`serialize()` deskews (median text-line angle), groups units into visual lines, keeps
column structure with proportional spacing, and numbers every line:

    === PAGE 1 ===
    [L1] GRANITE GLOBAL LOGISTICS & CO.                 FREIGHT INVOICE
    [L2] TAX INVOICE NO.  E/18330/26-27   INVOICE ISSUE DATE  25/10/26

The reader cites these ids; `Line.box` / `Line.units` turn a cited id back into the
measured bounding boxes shown to the user. Training data is built with this exact code.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field



@dataclass
class Unit:
    text: str
    box: tuple[float, float, float, float]   # x0, y0, x1, y1 in page coordinates
    page: int = 1
    score: float = 1.0
    angle: float = 0.0                       # degrees, from OCR polygons
    tags: list = field(default_factory=list)  # training only: ground-truth roles


@dataclass
class Line:
    id: str
    page: int
    text: str
    box: tuple[float, float, float, float]
    units: list[Unit]


def _poly_angle(poly) -> float:
    (x0, y0), (x1, y1) = poly[0], poly[1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0))


def estimate_skew(units: list[Unit]) -> float:
    """Median angle of wide, near-horizontal units (degrees)."""
    angs = sorted(u.angle for u in units if abs(u.angle) < 15 and (u.box[2] - u.box[0]) > 3 * (u.box[3] - u.box[1]))
    return angs[len(angs) // 2] if angs else 0.0


def _group(units: list[Unit], skew: float) -> list[list[tuple[float, Unit]]]:
    if not units:
        return []
    a = math.radians(-skew)
    ca, sa = math.cos(a), math.sin(a)

    def rot(u: Unit) -> tuple[float, float, float]:
        cx, cy = (u.box[0] + u.box[2]) / 2, (u.box[1] + u.box[3]) / 2
        return cx * ca - cy * sa, cx * sa + cy * ca, u.box[3] - u.box[1]

    rs = [(rot(u), u) for u in units]
    hs = sorted(h for (_, _, h), _ in rs if h > 0)
    med_h = hs[len(hs) // 2] if hs else 10.0
    rs.sort(key=lambda t: t[0][1])
    lines: list[dict] = []
    for (x, y, h), u in rs:
        best = None
        for ln in lines[-6:]:
            if abs(ln["y"] - y) <= 0.55 * max(med_h, min(h, 2 * med_h)):
                if best is None or abs(ln["y"] - y) < abs(best["y"] - y):
                    best = ln
        if best is None:
            lines.append({"y": y, "items": [(x, u)]})
        else:
            best["items"].append((x, u))
            best["y"] = sum(rot(v)[1] for _, v in best["items"]) / len(best["items"])
    lines.sort(key=lambda ln: ln["y"])
    # x positions are returned in the deskewed frame (minus the unit's half width) so columns align
    out = []
    for ln in lines:
        items = sorted(ln["items"], key=lambda t: t[0])
        out.append([(x - (u.box[2] - u.box[0]) / 2, u) for x, u in items])
    return out


def _char_width(units: list[Unit]) -> float:
    ws = sorted((u.box[2] - u.box[0]) / max(1, len(u.text)) for u in units if u.text.strip())
    return ws[len(ws) // 2] if ws else 6.0


def _join(items: list[tuple[float, Unit]], cw: float, x_min: float) -> str:
    """Column-aligned: each unit starts at round((x - page_left) / char_width).

    Runs of spaces are single tokens for the Qwen tokenizer, so alignment costs ~5-7% tokens
    and keeps label rows vertically aligned with the value rows printed below them."""
    out, prev = "", None
    for x, u in items:
        h = u.box[3] - u.box[1]
        if prev is not None and (u.box[0] - prev.box[2]) <= 0.45 * max(h, prev.box[3] - prev.box[1]):
            out += " " + u.text.strip()   # same phrase: ordinary word gap
        else:
            col = int(round((x - x_min) / cw))
            out += " " * max(1 if out else 0, col - len(out)) + u.text.strip()
        prev = u
    return out.rstrip()


def serialize(pages: list[tuple[int, list[Unit]]], start: int = 1) -> tuple[str, dict[str, Line]]:
    """pages: [(page_no, units)] -> (prompt text, {line_id: Line})."""
    chunks, index, n = [], {}, start
    for page_no, units in pages:
        units = [u for u in units if u.text.strip() and abs(u.angle) < 45]
        skew = estimate_skew(units)
        cw = _char_width(units)
        chunks.append(f"=== PAGE {page_no} ===")
        groups = _group(units, skew)
        x_min = min((x for g in groups for x, _ in g), default=0.0)
        for items in groups:
            lid = f"L{n}"
            n += 1
            text = _join(items, cw, x_min)
            grp = [u for _, u in items]
            box = (min(u.box[0] for u in grp), min(u.box[1] for u in grp),
                   max(u.box[2] for u in grp), max(u.box[3] for u in grp))
            index[lid] = Line(lid, page_no, text, box, grp)
            chunks.append(f"[{lid}] {text}")
    return "\n".join(chunks), index


def pdf_units(pdf_path: str) -> list[tuple[int, list[Unit], tuple[float, float]]]:
    """Text-layer words per page (PDF points), built from character boxes.

    Rotated text (diagonal watermarks, stamps, vertical margin notes) is skipped line by line;
    only its own characters are dropped, never the horizontal text underneath it."""
    import fitz

    out = []
    with fitz.open(pdf_path) as pdf:
        for i, page in enumerate(pdf):
            units = []
            raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            for b in raw.get("blocks", []):
                for ln in b.get("lines", []):
                    dx, dy = ln.get("dir", (1, 0))
                    if abs(dy) > 0.2 or dx < 0:
                        continue
                    for sp in ln.get("spans", []):
                        cur, box = "", None
                        for ch in sp.get("chars", []) + [{"c": " ", "bbox": None}]:
                            c = ch["c"]
                            if c.isspace():
                                if cur.strip():
                                    units.append(Unit(cur, tuple(box), i + 1))
                                cur, box = "", None
                                continue
                            x0, y0, x1, y1 = ch["bbox"]
                            box = [x0, y0, x1, y1] if box is None else [min(box[0], x0), min(box[1], y0),
                                                                        max(box[2], x1), max(box[3], y1)]
                            cur += c
            out.append((i + 1, units, (page.rect.width, page.rect.height)))
    return out


def ocr_units(result: dict, page_no: int) -> list[Unit]:
    """PP-OCRv6 worker response -> units."""
    units = []
    for ln in result.get("lines", []):
        poly = ln.get("poly")
        units.append(Unit(ln["text"], tuple(ln["box"]), page_no, float(ln.get("score", 1.0)),
                          _poly_angle(poly) if poly else 0.0))
    return units


def has_text_layer(units: list[Unit], min_chars: int = 80) -> bool:
    return sum(len(re.sub(r"\W", "", u.text)) for u in units) >= min_chars
