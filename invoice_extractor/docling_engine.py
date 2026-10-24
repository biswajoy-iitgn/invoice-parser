"""Docling sheet engine (option): DoclingDocument texts/tables -> OUTPUT_FIELDS.

Runs in-process (docling is a project dependency). The layout model is pinned
to CPU: the MPS backend fails on float64 ops on Apple Silicon.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import sheet_fields

NAME = "docling"


def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def reduce_document(doc) -> tuple[dict, list, str, str, list, dict]:
    """DoclingDocument -> (kv, charge items, text, vendor, segments, extra tax/totals)."""
    kv: dict[str, str] = {}
    items: list[dict] = []
    segments: list[str] = []
    texts: list[str] = []
    tax_codes: dict = {}
    tax_totals: dict = {}
    for t in getattr(doc, "texts", []) or []:
        s = (getattr(t, "text", "") or "").strip()
        if not s:
            continue
        texts.append(s)
        segments.append(s)
    for k, v in sheet_fields.kv_from_lines(texts).items():
        kv.setdefault(k, v)
    for table in getattr(doc, "tables", []) or []:
        data = getattr(table, "data", None)
        cells = getattr(data, "table_cells", None) if data else None
        if not cells:
            continue
        grid: dict[int, dict[int, str]] = {}
        for c in cells:
            grid.setdefault(c.start_row_offset_idx, {})[c.start_col_offset_idx] = (c.text or "").strip()
        ncols = max((max(r) for r in grid.values()), default=-1) + 1
        rows = [[grid[r].get(ci, "") for ci in range(ncols)] for r in sorted(grid)]
        for row in rows:
            segments.extend(c for c in row if c)
        codes, totals = sheet_fields.tax_info_from_grid(rows)
        tax_codes.update(codes)
        tax_totals.update(totals)
        for k, v in sheet_fields.invoice_total_from_rows(rows).items():
            tax_totals.setdefault(k, v)
        rest = []
        for row in rows:
            if row and re.fullmatch(r"\d{3,5}", re.sub(r"\s+", "", row[0])) and len(row) >= 4:
                amts = re.findall(r"[\d,]+\.\d{2}", " ".join(row[1:]))
                tax = next((c.strip() for c in row[1:]
                            if re.fullmatch(r"[A-Z]", (c or "").strip())), "")
                if not amts:
                    continue
                try:
                    val = float(amts[-1].replace(",", ""))
                except ValueError:
                    continue
                items.append({"description": f"{row[0].strip()} {row[1].strip()}",
                              "amount_inr": val, "tax_code": tax})
                continue
            rest.append(row)
        # glued "Label value" cells first ("H B/L No./HAWB No. 70430050554")
        consumed: set = set()
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                lab, val = sheet_fields.split_known_label(cell)
                if not lab:
                    lab, val = sheet_fields.split_colon(cell)
                if lab and val:
                    kv.setdefault(lab, val)
                    consumed.add((r, c))
        # header/data pairs: a short label cell with a value cell below it.
        # (Runs before the generic pass so a header row is never read as
        # a label/value pair itself.)
        for r in range(len(rows) - 1):
            for c in range(min(len(rows[r]), len(rows[r + 1]))):
                if (r, c) in consumed:
                    continue
                lab, val = (rows[r][c] or "").strip(), (rows[r + 1][c] or "").strip()
                if lab and val and len(lab) < 45 and "\n" not in lab:
                    kv.setdefault(lab, val)
        gkv, gitems = sheet_fields.generic_html_kv_items(rest)
        for k, v in gkv.items():
            kv.setdefault(k, v)
        items.extend(gitems)
    text = "\n".join(texts + [s for s in segments if s not in texts])
    vendor = ""
    # Letterhead window (like paddle_engine's seen<8): later "LOGISTICS"
    # mentions (routes, branches) must not win.
    for s in texts[:8]:
        if 6 <= len(s) <= 80 and re.search(
                r"\b(PVT|LTD|LIMITED|GMBH|INC|LLC|SDN|PTE|BV|FORWARDING|LOGISTICS)\b", s, re.I):
            vendor = s
            break
    extra = sheet_fields.make_extra(text, tax_codes, tax_totals)
    return kv, items, text, vendor, segments, extra


def _prov_box(prov, heights: dict) -> tuple:
    """First usable provenance -> (page_no, bbox). Docling boxes are
    bottom-left-origin points; flip to top-left to match display space."""
    for pv in prov or []:
        box = getattr(pv, "bbox", None)
        page = getattr(pv, "page_no", None)
        h = heights.get(page) if isinstance(page, int) else None
        if box is None or h is None:
            continue
        try:
            return page, [round(float(box.l), 1), round(h - float(box.t), 1),
                          round(float(box.r), 1), round(h - float(box.b), 1)]
        except (TypeError, ValueError):
            continue
    return None, []


def document_evidence(doc) -> tuple[list, list]:
    """DoclingDocument -> (layout, reading) with measured boxes."""
    heights: dict = {}
    for k, v in (getattr(doc, "pages", {}) or {}).items():
        try:
            heights[int(k)] = float(getattr(v, "size").height)
        except (TypeError, ValueError, AttributeError):
            continue
    by_page: dict = {}
    reading: list = []

    def add(label: str, text: str, page, box: list) -> None:
        if box:
            by_page.setdefault(page, []).append({"label": label, "bbox": box})
        if (text or "").strip() or box:
            reading.append({"page": page, "label": label,
                            "content": (text or "")[:1500], "bbox": box})

    for t in getattr(doc, "texts", []) or []:
        s = (getattr(t, "text", "") or "").strip()
        if not s:
            continue
        lab = getattr(t, "label", "text")
        label = str(getattr(lab, "value", lab)).lower()
        page, box = _prov_box(getattr(t, "prov", None), heights)
        add(label, s, page, box)
    for table in getattr(doc, "tables", []) or []:
        data = getattr(table, "data", None)
        cells = getattr(data, "table_cells", None) if data else None
        if not cells:
            continue
        texts, boxes, page = [], [], None
        for c in cells:
            if (getattr(c, "text", "") or "").strip():
                texts.append(c.text.strip())
            pg, box = _prov_box(getattr(c, "prov", None), heights)
            if box:
                page = page or pg
                if pg == page:
                    boxes.append(box)
        union = ([min(b[0] for b in boxes), min(b[1] for b in boxes),
                  max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else [])
        add("table", "\n".join(texts), page, union)
    layout = [{"page": pg, "regions": regs} for pg, regs in sorted(by_page.items())
              if pg is not None]
    return layout, reading


def extract(path: str | Path) -> tuple[dict, dict]:
    """Returns (fields, audit) with fields covering exactly OUTPUT_FIELDS."""
    started = time.time()
    doc = _converter().convert(str(path)).document
    kv, items, text, vendor, segments, extra = reduce_document(doc)
    fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
    audit = sheet_fields.finalize_extract(NAME, fields, items, text, extra, started)
    layout, reading = document_evidence(doc)
    audit["layout"] = layout
    audit["reading"] = reading
    audit["reading_note"] = "Docling layout blocks in reading order"
    return fields, audit


def available() -> bool:
    try:
        import docling  # noqa: F401
    except ImportError:
        return False
    return True
