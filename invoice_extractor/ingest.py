"""Turn a document (PDF or image) into page text for the extraction LLM.

Follows the two-tier blueprint from research.md:

  Tier 1  IBM Docling (RT-DETR layout + TableFormer), reading the PDF's own text
          cells, exported as markdown with 2D tables.
  Tier 2  HunyuanOCR Q8_0 (llama.cpp, Metal) for pages without a text layer
          (scans / photos) and for image inputs.

When Docling's table structure degrades on a digital page (fewer than 3 columns,
or several amounts merged into one cell) the page's layout-preserving text layer
is appended, because it carries the exact printed digits; OCR of a rendered
digital page can corrupt Indian digit grouping (8,00,806.36 -> 8,007.80636).

`Page.raw_text` always holds the most literal text available (PDF text layer, or
the OCR output) and is used to verify that extracted amounts really exist.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz

MIN_TEXT_CHARS = 80
RENDER_DPI = 200
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

HUNYUAN_MODEL = Path(os.environ.get("HUNYUAN_GGUF", Path.home() / "models/HunyuanOCR-GGUF/HunyuanOCR-Q8_0.gguf"))
HUNYUAN_MMPROJ = Path(os.environ.get("HUNYUAN_MMPROJ", Path.home() / "models/HunyuanOCR-GGUF/mmproj-HunyuanOCR-Q8_0.gguf"))
# Vendor document-parsing prompt; the model was trained on these exact strings.
HUNYUAN_PARSE_PROMPT = (
    "提取文档图片中正文的所有信息用markdown格式表示，其中页眉、页脚部分忽略，"
    "表格用html格式表达，文档中公式用latex格式表示，按照阅读顺序组织进行解析。"
)
HUNYUAN_TOKEN_JUNK = re.compile(r"<｜hy_[^｜]*｜>(\(\d+,\d+\),\(\d+,\d+\))?")
AMOUNT_RE = re.compile(r"\d[\d,]*\.\d{2}\b")


@dataclass
class Page:
    number: int
    text: str          # what the LLM reads
    raw_text: str      # most literal text, used for verification
    source: str        # docling / docling+textlayer / hunyuan-ocr
    notes: list[str] = field(default_factory=list)


def _alnum_count(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def hunyuan_available() -> bool:
    return bool(shutil.which("llama-mtmd-cli")) and HUNYUAN_MODEL.exists() and HUNYUAN_MMPROJ.exists()


def hunyuan_ocr(png_path: str, timeout: float = 600.0) -> str:
    if not hunyuan_available():
        raise RuntimeError(f"HunyuanOCR unavailable (need llama-mtmd-cli and {HUNYUAN_MODEL})")
    proc = subprocess.run(
        ["llama-mtmd-cli", "-m", str(HUNYUAN_MODEL), "--mmproj", str(HUNYUAN_MMPROJ),
         "--image", png_path, "-p", HUNYUAN_PARSE_PROMPT, "-ngl", "99", "-c", "8192",
         "--temp", "0", "--repeat-penalty", "1.08", "-n", "4096", "--no-warmup"],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"llama-mtmd-cli failed: {(proc.stderr or '')[-400:]}")
    return HUNYUAN_TOKEN_JUNK.sub("", proc.stdout).strip()


_converter = None


def _docling_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # CPU: the RT-DETR layout model builds float64 tensors, which MPS rejects.
        opts = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
        )
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
        _converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _converter


def _degraded_tables(doc, page_no: int) -> list[str]:
    issues = []
    for i, table in enumerate(doc.tables):
        if not table.prov or table.prov[0].page_no != page_no:
            continue
        df = table.export_to_dataframe(doc=doc)
        if df.shape[1] < 3 and df.shape[0] >= 2:
            issues.append(f"table {i}: collapsed to {df.shape[1]} columns")
            continue
        merged = sum(len(AMOUNT_RE.findall(str(v))) >= 2 for v in df.to_numpy().ravel())
        if merged:
            issues.append(f"table {i}: {merged} cells with merged amounts")
    return issues


def _ocr_page(page_number: int, png: str) -> Page:
    text = hunyuan_ocr(png)
    return Page(page_number, text, text, "hunyuan-ocr")


def load_pages(path: str, force_ocr: bool = False) -> list[Page]:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return [_ocr_page(1, path)]

    with fitz.open(path) as pdf:
        layer = [p.get_text("text", sort=True) for p in pdf]
        scanned = [force_ocr or _alnum_count(t) < MIN_TEXT_CHARS for t in layer]
        ocr_pages: dict[int, Page] = {}
        if any(scanned):
            with tempfile.TemporaryDirectory() as tmp:
                for i, is_scan in enumerate(scanned):
                    if is_scan:
                        png = os.path.join(tmp, f"p{i + 1}.png")
                        pdf[i].get_pixmap(dpi=RENDER_DPI).save(png)
                        ocr_pages[i + 1] = _ocr_page(i + 1, png)

    if all(scanned):
        return [ocr_pages[n] for n in sorted(ocr_pages)]

    doc = _docling_converter().convert(path).document
    pages = []
    for n in range(1, len(layer) + 1):
        if n in ocr_pages:
            pages.append(ocr_pages[n])
            continue
        md = doc.export_to_markdown(page_no=n).strip()
        issues = _degraded_tables(doc, n)
        if issues or _alnum_count(md) < MIN_TEXT_CHARS:
            text = f"{md}\n\n[Exact PDF text layer of this page]\n{_squeeze(layer[n - 1])}"
            pages.append(Page(n, text, layer[n - 1], "docling+textlayer", issues or ["docling output too short"]))
        else:
            pages.append(Page(n, md, layer[n - 1], "docling"))
    return pages


def _squeeze(text: str) -> str:
    body = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def pages_to_prompt_text(pages: list[Page]) -> str:
    return "\n\n".join(f"===== PAGE {p.number} =====\n{_squeeze(p.text)}" for p in pages)


def pages_to_raw_text(pages: list[Page]) -> str:
    return "\n".join(p.raw_text for p in pages)
