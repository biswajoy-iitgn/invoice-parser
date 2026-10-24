#!/usr/bin/env python3
"""Persistent PaddleOCR-VL 1.6 worker (runs under ~/paddle-env/bin/python).

Protocol (same shape as ppocr_worker.py): stdin JSON lines
``{"id": ..., "file": "/path/to/invoice.pdf"}`` (optional ``"mode"``:
``"vl"`` default, or ``"layout"``) -> stdout JSON lines
``{"id": ..., "pages": [{"page_index": n, "blocks": [{"label", "content", "bbox"}]}]}``
or ``{"id": ..., "error": "..."}``. Models load once (~1 min CPU),
then serve files over this pipe.

``"layout"`` mode runs PP-DocLayoutV3 first (layout regions in reading
order), then reads every region with PaddleOCR-VL 1.6 on the cropped
region (tables keep their HTML, text arrives as plain lines); PP-OCR on
the crop is only a fallback when VL returns nothing. Block labels are
mapped to the same vocabulary the ``"vl"`` mode emits
(table/doc_title/header/text/figure) so sheet engines consume both modes
identically.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


LAYOUT_MODEL_ENV = "PPDOCLAYOUT_MODEL"
LAYOUT_MODEL_DEFAULT = "PP-DocLayoutV3"

# PP-DocLayoutV3 region labels -> the block vocabulary sheet engines use.
_TABLE_LABELS = {"table"}
_TITLE_LABELS = {"title", "doc_title"}
_HEADER_LABELS = {"header"}
_FIGURE_LABELS = {"figure", "fig_caption", "figure_caption", "seal", "stamp"}


def map_layout_label(label: str) -> str:
    """Map a raw layout region label onto table/doc_title/header/text/figure."""
    lab = (label or "").strip().lower()
    if lab in _TABLE_LABELS:
        return "table"
    if lab in _TITLE_LABELS:
        return "doc_title"
    if lab in _HEADER_LABELS:
        return "header"
    if lab in _FIGURE_LABELS:
        return "figure"
    return "text"


def _order_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _layout_regions(layout_json: dict) -> list[dict]:
    """Normalize one page of LayoutDetection JSON to [{bbox, label, order}]."""
    boxes = layout_json.get("boxes", layout_json.get("regions", []))
    regions = []
    for i, b in enumerate(boxes or []):
        if not isinstance(b, dict):
            continue
        coord = b.get("coordinate", b.get("bbox", b.get("points", b.get("poly", []))))
        xs = [p[0] for p in coord] if coord and isinstance(coord[0], (list, tuple)) else []
        if xs:
            ys = [p[1] for p in coord]
            bbox = [min(xs), min(ys), max(xs), max(ys)]
        else:
            bbox = [float(v) for v in (coord or [0, 0, 0, 0])][:4]
            bbox += [0] * (4 - len(bbox))
        label = b.get("label", b.get("cls_name", b.get("category", "")))
        regions.append({"bbox": bbox, "label": map_layout_label(str(label)),
                        "order": _order_int(
                            b.get("reading_order", b.get("order", b.get("index", i))), i)})
    regions.sort(key=lambda r: (r["order"], r["bbox"][1], r["bbox"][0]))
    return regions


def _render_page_images(src: str, tmp: str) -> list[str]:
    """Render PDF pages to PNGs (PyMuPDF) or pass single images through."""
    if src.lower().endswith(".pdf"):
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError(
                "layout mode needs PyMuPDF in the paddle env to render PDFs "
                "(pip install pymupdf)") from exc
        out = []
        with fitz.open(src) as doc:
            for i, page in enumerate(doc):
                png = str(Path(tmp) / f"page-{i}.png")
                page.get_pixmap(dpi=150).save(png)
                out.append(png)
        return out
    return [src]


def _vl_crop_blocks(pipe, crop: str, tmp: str) -> list[dict]:
    """Raw PaddleOCR-VL parsing blocks for one region crop."""
    out = []
    for res in pipe.predict(crop):
        res.save_to_json(save_path=tmp)
        jf = sorted(Path(tmp).glob("*.json"))[-1]
        data = json.loads(jf.read_text())
        out.extend(data.get("parsing_res_list", []))
        jf.unlink()
    return out


def _vl_crop_text(pipe, crop: str, tmp: str) -> str:
    """Plain-text reading of one region crop via PaddleOCR-VL (tags stripped)."""
    import re

    parts = []
    for b in _vl_crop_blocks(pipe, crop, tmp):
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b.get("block_content", "") or "")).strip()
        if txt:
            parts.append(txt)
    return "\n".join(parts)


def _parse_layout_page(pipe, ocr, layout, img: str, tmp: str, page_index: int) -> dict:
    results = list(layout.predict(input=img, batch_size=1))
    regions: list[dict] = []
    for res in results:
        res.save_to_json(save_path=tmp)
        jf = sorted(Path(tmp).glob("*.json"))[-1]
        data = json.loads(jf.read_text())
        regions.extend(_layout_regions(data))
        jf.unlink()
    from PIL import Image

    page = Image.open(img).convert("RGB")
    blocks = []
    for n, r in enumerate(regions):
        x0, y0, x1, y1 = [int(v) for v in r["bbox"]]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(page.width, x1), min(page.height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = str(Path(tmp) / f"p{page_index}-r{n}.png")
        page.crop((x0, y0, x1, y1)).save(crop)
        if r["label"] == "table":
            content = "\n".join(
                (b.get("block_content", "") or "") for b in _vl_crop_blocks(pipe, crop, tmp))
            if not content.strip():
                content = _ocr_texts(ocr, crop)
        else:
            # Layout segments, VL reads: every region (text, title, header,
            # figure/logo) goes through the VL model; PP-OCR is only the
            # fallback when VL returns nothing.
            content = _vl_crop_text(pipe, crop, tmp) or _ocr_texts(ocr, crop)
        blocks.append({"label": r["label"], "content": content.strip(),
                       "bbox": [x0, y0, x1, y1]})
    return {"page_index": page_index, "blocks": blocks}


def _ocr_texts(ocr, img: str) -> str:
    lines: list[str] = []
    for res in ocr.predict(input=img):
        payload = getattr(res, "json", None)
        if isinstance(payload, dict):
            for key in ("rec_texts", "rec_text", "texts"):
                vals = payload.get(key) or []
                lines.extend(str(v) for v in (vals if isinstance(vals, list) else [vals]) if v)
            for para in payload.get("parsing_res_list", []) or []:
                if isinstance(para, dict) and para.get("block_content"):
                    lines.append(str(para["block_content"]))
        else:
            try:
                for line in res:
                    lines.append(str(line))
            except TypeError:
                lines.append(str(res))
    seen, out = set(), []
    for ln in (l.strip() for l in lines):
        if ln and ln not in seen:
            seen.add(ln)
            out.append(ln)
    return "\n".join(out)


def main() -> None:
    from paddleocr import PaddleOCRVL

    pipe = PaddleOCRVL(pipeline_version="v1.6")
    layout = None
    ocr = None

    def ensure_layout():
        nonlocal layout, ocr
        if layout is None:
            from paddleocr import LayoutDetection, PaddleOCR

            layout = LayoutDetection(
                model_name=os.environ.get(LAYOUT_MODEL_ENV, LAYOUT_MODEL_DEFAULT))
            ocr = PaddleOCR()
        return layout, ocr

    sys.stdout.write(json.dumps({"ready": True, "models": ["PaddleOCR-VL-1.6"]}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        try:
            if req.get("mode") == "layout":
                lay, o = ensure_layout()
                pages = []
                with tempfile.TemporaryDirectory(prefix="ppdoclayout-") as tmp:
                    for i, img in enumerate(_render_page_images(req["file"], tmp)):
                        pages.append(_parse_layout_page(pipe, o, lay, img, tmp, i))
            else:
                pages = []
                with tempfile.TemporaryDirectory(prefix="paddlevl-") as tmp:
                    for i, res in enumerate(pipe.predict(req["file"])):
                        res.save_to_json(save_path=tmp)
                        jf = sorted(Path(tmp).glob("*.json"))[-1]
                        data = json.loads(jf.read_text())
                        pages.append({
                            "page_index": data.get("page_index", i),
                            "blocks": [{"label": b.get("block_label", ""),
                                        "content": b.get("block_content", ""),
                                        "bbox": b.get("block_bbox", [])}
                                       for b in data.get("parsing_res_list", [])],
                        })
                        jf.unlink()
            sys.stdout.write(json.dumps({"id": rid, "pages": pages}) + "\n")
        except Exception as exc:
            sys.stdout.write(json.dumps({
                "id": rid, "error": f"{type(exc).__name__}: {exc}"[:500],
                "traceback": traceback.format_exc(limit=3)}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
