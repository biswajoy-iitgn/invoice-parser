"""Citation boxes: ground extracted values to page bounding boxes.

Two grounders (no VLM coordinate guessing — every box comes from a measured
OCR/text-layer hit):

- PDF pages with a text layer: PyMuPDF word boxes, exact search.
- Images / scans: Tesseract TSV word boxes with fuzzy token matching.

``cite_value`` returns scored box hits; ``cite_fields`` grounds a whole
extraction. Values that cannot be grounded return no box (never a guessed
box): downstream, unboxed critical values route to REVIEW.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass
class Hit:
    bbox: list[float]  # [x1, y1, x2, y2] in page pixels (image) or points (PDF)
    page: int
    score: float       # 0..1 token-match score
    source_text: str   # matched page text
    source: str        # 'text-layer' | 'tesseract'


def _norm_token(tok: str) -> str:
    return re.sub(r"[^a-z0-9./-]", "", tok.casefold())


def _value_tokens(value: str) -> list[str]:
    toks = [_norm_token(t) for t in re.split(r"\s+", value.strip())]
    return [t for t in toks if t]


def _tesseract_words(image_path: str) -> list[dict]:
    """Word boxes via TSV. Raises if tesseract is missing."""
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract not found (brew install tesseract)")
    with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as f:
        tsv = f.name
    try:
        subprocess.run(
            ["tesseract", image_path, tsv.replace(".tsv", ""),
             "--psm", "3", "-c", "tessedit_create_tsv=1"],
            capture_output=True, text=True, timeout=120, check=True,
        )
        rows = Path(tsv).read_text().splitlines()
    finally:
        Path(tsv).unlink(missing_ok=True)
    words = []
    for line in rows[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            conf = -1.0
        if conf < 0:
            continue
        x, y, w, h = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        words.append({"text": parts[11].strip(), "bbox": [x, y, x + w, y + h],
                      "conf": conf, "line": int(parts[4])})
    return words


def _match_number_spans(value: str, words: list[dict]) -> list[Hit]:
    """Ground numeric values: full-token equality on normalized numbers."""
    m = re.search(r"-?[\d,]+\.\d+|-?\d[\d,]*", value or "")
    if not m:
        return []
    target = m.group(0).replace(",", "").rstrip("0").rstrip(".")
    hits = []
    for w in words:
        for num in re.findall(r"-?[\d,]+\.\d+|-?\d[\d,]*", w["text"]):
            if num.replace(",", "").rstrip("0").rstrip(".") == target:
                hits.append(Hit(w["bbox"], 1, min(1.0, w["conf"] / 100 + 0.2),
                                w["text"], "tesseract"))
    return hits


def cite_image_value(image_path: str, value: str, page: int = 1,
                     _words: list[dict] | None = None) -> list[Hit]:
    """Ground one value on an image page. Empty list = not grounded."""
    words = _words if _words is not None else _tesseract_words(image_path)
    if not words:
        return []
    if re.search(r"\d", value or ""):
        num_hits = _match_number_spans(value, [dict(w, ) for w in words])
        for h in num_hits:
            h.page = page
        if num_hits:
            # For labeled amounts ("THC INR 19,658.74"), extend the box to the
            # full row so the citation covers label + value. Rows are grouped
            # by y-overlap (TSV line ids are unreliable under PSM 6).
            anchor = num_hits[0].bbox
            ah = anchor[3] - anchor[1]
            seg = [w for w in words
                   if min(w["bbox"][3], anchor[3]) - max(w["bbox"][1], anchor[1]) > 0.3 * ah]
            if seg:
                x1 = min(w["bbox"][0] for w in seg)
                y1 = min(w["bbox"][1] for w in seg)
                x2 = max(w["bbox"][2] for w in seg)
                y2 = max(w["bbox"][3] for w in seg)
                num_hits[0].bbox = [x1, y1, x2, y2]
                num_hits[0].source_text = " ".join(
                    w["text"] for w in sorted(seg, key=lambda w: w["bbox"][0]))
            return sorted(num_hits, key=lambda h: -h.score)[:3]
    want = _value_tokens(value)
    if not want:
        return []
    # Sliding window over same-row words, token-set overlap score.
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["bbox"][1], w["bbox"][0])):
        placed = False
        for row in rows:
            r = row[0]["bbox"]
            if min(w["bbox"][3], r[3]) - max(w["bbox"][1], r[1]) > 0.3 * (r[3] - r[1]):
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    hits: list[Hit] = []
    for seg in rows:
        toks = [_norm_token(w["text"]) for w in seg]
        n = len(want)
        for i in range(max(1, len(toks) - n + 1)):
            window = toks[i:i + n] if len(toks) >= n else toks
            joined = " ".join(window)
            target = " ".join(want)
            score = SequenceMatcher(None, joined, target).ratio()
            if score >= 0.8:
                span = seg[i:i + n] if len(seg) >= n else seg
                x1 = min(w["bbox"][0] for w in span)
                y1 = min(w["bbox"][1] for w in span)
                x2 = max(w["bbox"][2] for w in span)
                y2 = max(w["bbox"][3] for w in span)
                hits.append(Hit([x1, y1, x2, y2], page, round(score, 3),
                                " ".join(w["text"] for w in span), "tesseract"))
    # Fallback: single best token overlap for short values.
    if not hits and len(want) == 1:
        for w in words:
            s = SequenceMatcher(None, _norm_token(w["text"]), want[0]).ratio()
            if s >= 0.85:
                hits.append(Hit(w["bbox"], page, round(s, 3), w["text"], "tesseract"))
    return sorted(hits, key=lambda h: -h.score)[:3]


def _search_pdf_page(page, want: str, page_no: int) -> list[Hit]:
    """Exact search + longest-word fallback on one open PDF page."""
    hits: list[Hit] = []
    for inst in page.search_for(want)[:3]:
        hits.append(Hit([inst.x0, inst.y0, inst.x1, inst.y1], page_no, 1.0,
                        want, "text-layer"))
    if not hits and len(want.split()) > 1:
        # Fallback: longest word runs (handles line-break splits).
        for tok in sorted(want.split(), key=len, reverse=True)[:3]:
            if len(tok) < 4:
                continue
            for inst in page.search_for(tok)[:1]:
                hits.append(Hit([inst.x0, inst.y0, inst.x1, inst.y1], page_no,
                                0.7, tok, "text-layer"))
    return hits


def cite_fields(values: dict, source: str, is_pdf: bool = False,
                page_no: int = 1) -> dict[str, list[Hit]]:
    """Ground every non-empty field value. Values without hits map to []."""
    out: dict[str, list[Hit]] = {}
    if is_pdf:
        # One open per page, not per value: the legacy caller grounds ~40
        # values per page, and every fitz.open re-parses the xref table.
        import fitz

        try:
            pdf = fitz.open(source)
        except Exception:
            return {k: [] for k in values}
        with pdf:
            if page_no > len(pdf):
                return {k: [] for k in values}
            page = pdf[page_no - 1]
            for key, val in values.items():
                want = re.sub(r"\s+", " ", str(val or "").strip())
                if not want:
                    out[key] = []
                    continue
                try:
                    out[key] = _search_pdf_page(page, want, page_no)
                except Exception:
                    out[key] = []
        return out
    try:
        words = _tesseract_words(source)
    except Exception:
        return {k: [] for k in values}
    for key, val in values.items():
        text = str(val or "")
        if not text:
            out[key] = []
            continue
        try:
            out[key] = cite_image_value(source, text, page_no, _words=words)
        except Exception:
            out[key] = []
    return out
