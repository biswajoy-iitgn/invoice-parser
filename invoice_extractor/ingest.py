"""Document -> pages of word boxes (+ text for the LLM backend).

  * Digital PDF page (has a text layer): exact words and boxes from PyMuPDF. No OCR, so no
    OCR error can enter; this is also what makes digital PDFs the 'oracle OCR' stratum.
  * Raster page (image file, or image-only PDF page): the page image at native resolution is
    OCR'd by the selected engine (see ocr.py).
  * For the LLM backend, `Page.text` is reading-order text rebuilt from the word boxes. When the
    research.md stack is installed, Docling markdown (digital pages) or HunyuanOCR markdown
    (raster pages) replaces it, as in the two-tier blueprint.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf as fitz

from . import ocr as ocr_mod
from .layout import Word, build_rows, reading_order_text

MIN_TEXT_CHARS = 80
RENDER_DPI = 300
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class Page:
    number: int
    width: float
    height: float
    words: list[Word]
    source: str                      # pdf-text | tesseract | rapidocr | hunyuan
    text: str = ""                   # what the LLM backend reads
    notes: dict = field(default_factory=dict)


def _alnum(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def _pdf_page_image(doc, page) -> np.ndarray:
    """Native-resolution image when the page is a single full-page raster, else a 300 DPI render."""
    imgs = page.get_images(full=True)
    if len(imgs) == 1:
        info = doc.extract_image(imgs[0][0])
        arr = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_COLOR)
        if arr is not None and arr.shape[1] >= 800:
            return arr
    pix = page.get_pixmap(dpi=RENDER_DPI)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)


def _ocr_pages(number: int, img: np.ndarray, readings: list[str]) -> dict[str, Page]:
    if readings == ["hunyuan"]:
        with tempfile.TemporaryDirectory() as tmp:
            png = os.path.join(tmp, f"p{number}.png")
            cv2.imwrite(png, img)
            text = ocr_mod.hunyuan_text(png)
        return {"hunyuan": Page(number, img.shape[1], img.shape[0], [], "hunyuan", text)}
    out = {}
    for name, (words, w, h, meta) in ocr_mod.ocr_readings(img, readings).items():
        out[name] = Page(number, w, h, words, name, reading_order_text(build_rows(words)), meta)
    return out


def load_document_readings(path: str, readings: list[str], force_ocr: bool = False) -> dict[str, list[Page]]:
    """One page list per OCR reading. Digital pages carry their exact text layer in every reading."""
    ext = os.path.splitext(path)[1].lower()
    per: dict[str, list[Page]] = {r: [] for r in readings}
    if ext in IMAGE_EXTS:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"cannot read image {path}")
        for r, pg in _ocr_pages(1, img, readings).items():
            per[r].append(pg)
        return per
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            raw = page.get_text("text")
            if not force_ocr and _alnum(raw) >= MIN_TEXT_CHARS:
                words = [Word(w[4], w[0], w[1], w[2], w[3], 1.0) for w in page.get_text("words")]
                pg = Page(i + 1, page.rect.width, page.rect.height, words, "pdf-text",
                          reading_order_text(build_rows(words)))
                for r in readings:
                    per[r].append(pg)
            else:
                for r, pg in _ocr_pages(i + 1, _pdf_page_image(doc, page), readings).items():
                    per[r].append(pg)
    return per


def is_digital(pages: list[Page]) -> bool:
    return all(p.source == "pdf-text" for p in pages)


def load_document(path: str, ocr: str = "tesseract", force_ocr: bool = False) -> list[Page]:
    return load_document_readings(path, [ocr], force_ocr)[ocr]


def attach_docling_markdown(path: str, pages: list[Page]) -> None:
    """Optional (LLM backend): replace text of digital pages with Docling markdown with 2D tables."""
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError:
        return
    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True,
                              accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU))
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    doc = conv.convert(path).document
    for p in pages:
        if p.source == "pdf-text":
            md = doc.export_to_markdown(page_no=p.number).strip()
            if _alnum(md) >= MIN_TEXT_CHARS:
                p.text = f"{md}\n\n[Exact PDF text layer of this page]\n{p.text}"
                p.notes["docling"] = True


def pages_to_prompt_text(pages: list[Page]) -> str:
    return "\n\n".join(f"===== PAGE {p.number} =====\n{p.text}" for p in pages)


def pages_to_raw_text(pages: list[Page]) -> str:
    return "\n".join(p.text for p in pages)
