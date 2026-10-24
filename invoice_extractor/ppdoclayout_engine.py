"""PP-DocLayoutV3 + PaddleOCR-VL 1.6 sheet engine: layout-first parse -> OUTPUT_FIELDS.

Runs the worker in ``"layout"`` mode (see paddle_vl_worker.py): PP-DocLayoutV3
detects layout regions in reading order, then PaddleOCR-VL 1.6 reads every
region on its crop (PP-OCR only as fallback). Blocks arrive with the same
labels the whole-page VL mode emits, so the paddle reduce step is reused
unchanged. .docx files fall back to python-docx text (no layout).
"""

from __future__ import annotations

import time
from pathlib import Path

from . import paddle_engine, paddle_vl_client, sheet_fields

NAME = "ppdoclayout"

# Worker renders PDFs at this DPI (see _render_page_images); region boxes
# arrive in those pixels and are normalized to print-scale points here.
WORKER_DPI = 150
READING_CHARS = 1500


def _to_points(bbox: list, is_pdf: bool) -> list:
    s = 72.0 / WORKER_DPI if is_pdf else 1.0
    return [round(v * s, 1) for v in bbox]


def layout_evidence(pages: list, is_pdf: bool) -> tuple[list, list]:
    """Split worker pages into layout regions and per-region VL readings.

    layout: [{page, regions: [{label, bbox}]}] for the overlay.
    reading: [{page, label, content, bbox}] for the reading panel.
    Boxes are print-scale points; content is truncated for payload size.
    """
    layout, reading = [], []
    for p in pages or []:
        page_no = p.get("page_index", len(layout)) + 1
        regions = []
        for b in p.get("blocks", []):
            box = b.get("bbox") or []
            if len(box) != 4:
                continue
            box = _to_points([float(v) for v in box], is_pdf)
            label = b.get("label", "") or "text"
            regions.append({"label": label, "bbox": box})
            content = (b.get("content", "") or "").strip()
            if len(content) > READING_CHARS:
                content = content[:READING_CHARS] + "…"
            reading.append({"page": page_no, "label": label,
                            "content": content, "bbox": box})
        layout.append({"page": page_no, "regions": regions})
    return layout, reading


def extract(path: str | Path) -> tuple[dict, dict]:
    """Returns (fields, audit) with fields covering exactly OUTPUT_FIELDS."""
    started = time.time()
    src = Path(path)
    note = ""
    segments: list = []
    extra = {"tax_codes": {}, "totals": {}}
    layout: list = []
    reading: list = []
    if src.suffix.lower() == ".docx":
        kv, items, text, vendor = sheet_fields.docx_blocks(str(src))
        note = "docx read as text (no layout)"
    else:
        out = paddle_vl_client.parse_layout(src)
        pages = out.get("pages", [])
        if not pages:
            note = "layout parse returned no pages"
        layout, reading = layout_evidence(pages, is_pdf=src.suffix.lower() == ".pdf")
        kv, items, text, vendor, segments, extra = paddle_engine.reduce_blocks(pages)
    fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
    audit = sheet_fields.finalize_extract(NAME, fields, items, text, extra, started, note=note)
    audit["layout"] = layout
    audit["reading"] = reading
    audit["reading_note"] = "PP-DocLayoutV3 regions in reading order, read by PaddleOCR-VL"
    return fields, audit


def available() -> bool:
    return paddle_vl_client.available()
