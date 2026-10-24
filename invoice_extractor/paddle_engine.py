"""PaddleOCR-VL 1.6 sheet engine (option): VL blocks -> OUTPUT_FIELDS.

Parsing runs in persistent workers under PADDLEVL_PYTHON (see
paddle_vl_client.py); .docx files fall back to python-docx text (no layout).
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

from . import paddle_vl_client, sheet_fields

NAME = "paddle"


def _split_cell(cell: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\\n|\n", html.unescape(cell or ""))]
    return [p for p in parts if p]


def _table_rows(block_html: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr.*?>(.*?)</tr>", block_html or "", re.S | re.I):
        cells = [html.unescape(re.sub(r"\s+", " ", c).strip())
                 for c in re.findall(r"<td.*?>(.*?)</td>", tr, re.S | re.I)]
        if cells:
            rows.append(cells)
    return rows


def reduce_blocks(pages: list[dict]) -> tuple[dict, list, str, str, list, dict]:
    """VL pages -> (kv, charge items, text, vendor, segments, extra tax/totals)."""
    kv: dict[str, str] = {}
    items: list[dict] = []
    texts: list[str] = []
    segments: list[str] = []
    tax_codes: dict = {}
    tax_totals: dict = {}
    vendor = ""
    seen = 0
    for page in pages or []:
        for b in page.get("blocks", []):
            content = b.get("content") or ""
            label = b.get("label") or ""
            texts.append(re.sub(r"<[^>]+>", " ", html.unescape(content)))
            segments.extend(ln.strip() for ln in _split_cell(content) if ln.strip())
            if label != "table":
                for k, v in sheet_fields.kv_from_lines(_split_cell(content)).items():
                    kv.setdefault(k, v)
                plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(content))).strip()
                for k, v in sheet_fields.glued_kv(plain).items():
                    kv.setdefault(k, v)
            # vendor: letterhead sits in the first blocks; later "LOGISTICS"
            # mentions (routes, branches) must not win.
            if not vendor and seen < 8 and label in ("header", "paragraph_title", "doc_title", "text"):
                t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)).strip()
                if 6 <= len(t) <= 80 and re.search(
                        r"\b(PVT|LTD|LIMITED|GMBH|INC|LLC|SDN|PTE|BV|FORWARDING|LOGISTICS)\b", t, re.I):
                    vendor = sheet_fields.clean_vendor(t)
            seen += 1
            if label != "table":
                continue
            grid = _table_rows(content)
            codes, totals = sheet_fields.tax_info_from_grid(grid)
            tax_codes.update(codes)
            tax_totals.update(totals)
            for k, v in sheet_fields.invoice_total_from_rows(grid).items():
                tax_totals.setdefault(k, v)
            rest = []
            for row in grid:
                segments.extend(c for c in row if c)
                if row and re.fullmatch(r"\d{3,5}", re.sub(r"\s+", "", row[0])) and len(row) >= 6:
                    amt = re.search(r"[\d,]+\.\d{2}", row[-2])
                    if not amt:
                        continue
                    try:
                        val = float(amt.group(0).replace(",", ""))
                    except ValueError:
                        continue
                    tax = row[-1].strip() if re.fullmatch(r"[A-Z]", row[-1].strip()) else ""
                    items.append({"description": f"{row[0].strip()} {' '.join(_split_cell(row[1]))}",
                                  "amount_inr": val, "tax_code": tax})
                    continue
                rest.append(row)
                for cell in row:
                    parts = _split_cell(cell)
                    if len(parts) >= 2 and len(parts[0]) < 45:
                        kv.setdefault(parts[0], " ".join(parts[1:]))
                    else:
                        lab, val = sheet_fields.split_colon(cell)
                        if lab and val:
                            kv.setdefault(lab, val)
            gkv, gitems = sheet_fields.generic_html_kv_items(rest)
            for k, v in gkv.items():
                kv.setdefault(k, v)
            items.extend(gitems)
    text = "\n".join(texts)
    extra = sheet_fields.make_extra(text, tax_codes, tax_totals)
    return kv, items, text, vendor, segments, extra


# PaddleOCR-VL renders PDF pages at 2x points (144dpi): block_bbox * 0.5
# lands within a point of Docling's bottom-left point boxes and the layout
# worker's 150dpi boxes on the same invoice. Image inputs stay native pixels.
VL_PDF_POINTS = 0.5


def blocks_reading(pages: list, is_pdf: bool = True) -> list:
    """VL pages -> reading entries (label + plain text + box for the overlay)."""
    reading = []
    for p in pages or []:
        page_no = p.get("page_index", 0) + 1
        for b in p.get("blocks", []):
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(b.get("content") or ""))).strip()
            if not text:
                continue
            box = b.get("bbox") or []
            reading.append({"page": page_no, "label": b.get("label", "") or "text",
                            "content": text[:1500],
                            "bbox": ([round(float(v) * (VL_PDF_POINTS if is_pdf else 1.0), 1)
                                      for v in box] if len(box) == 4 else [])})
    return reading


def extract(path: str | Path) -> tuple[dict, dict]:
    """Returns (fields, audit) with fields covering exactly OUTPUT_FIELDS."""
    started = time.time()
    src = Path(path)
    note = ""
    segments: list = []
    extra = {"tax_codes": {}, "totals": {}}
    reading: list = []
    if src.suffix.lower() == ".docx":
        kv, items, text, vendor = sheet_fields.docx_blocks(str(src))
        note = "docx read as text (no layout)"
    else:
        out = paddle_vl_client.parse(src)
        pages = out.get("pages", [])
        reading = blocks_reading(pages, is_pdf=src.suffix.lower() == ".pdf")
        kv, items, text, vendor, segments, extra = reduce_blocks(pages)
    fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
    audit = sheet_fields.finalize_extract(NAME, fields, items, text, extra, started, note=note)
    audit["reading"] = reading
    audit["reading_note"] = "PaddleOCR-VL blocks in page order"
    by_page: dict = {}
    for r in reading:
        if r.get("bbox"):
            by_page.setdefault(r["page"], []).append({"label": r["label"], "bbox": r["bbox"]})
    audit["layout"] = [{"page": pg, "regions": regs} for pg, regs in sorted(by_page.items())]
    return fields, audit
