"""Invoice -> fixed 31-key JSON with a bounding-box citation and an explanation for every value.

  1. ingest      digital PDF pages -> text-layer words (exact boxes, no OCR);
                 scanned pages / images -> PP-OCRv6 text lines (boxes from the same pass)
  2. layout      one line-numbered, column-aligned text for all pages (layout.serialize)
  3. reader      fine-tuned Qwen3-VL-8B-Instruct returns cited JSON (reader.py contract)
  4. citations   every cited line id is resolved to the measured box of the exact value;
                 a value that cannot be found on the page is blanked, never boxed by guess
  5. semantics   charge descriptions and header labels are linked to canonical fields by the
                 semantic engine (alias bank + embedding + LAYA + lexicon + reader vote), with
                 confidence and an explanation
  6. checks      line items vs subtotal, ISO 6346 container and MAWB check digits, dates
  7. routing     AUTO_ACCEPT only when every value is cited and every check passes, else REVIEW

Coordinates: PDF pages in PDF points, images in pixels (same convention as grounding.py, so the
UI's existing page renderer scales them unchanged).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .layout import Line, Unit, has_text_layer, ocr_units, pdf_units, serialize
from .reader import JSON_SCHEMA, SYSTEM_PROMPT, clean_lines, parse_output, user_message
from .schema import CHARGE_FIELDS, OUTPUT_FIELDS
from .validation import _norm_num, normalize_date

OCR_DPI = int(os.environ.get("OCR_DPI", "200"))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
READER_MODEL = os.environ.get("READER_MODEL", "invoice-reader-qwen3vl8b")
CONTAINER_RE = re.compile(r"\b([A-Z]{3}[UJZ])\s?(\d{6})\s?(\d)\b")
NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")
# Bump when the extraction logic changes in a way that can alter results: cached results are keyed by it.
PIPELINE_REV = "2026-10-04.7"
SEM_ACCEPT = float(os.environ.get("SEMANTIC_ACCEPT", "0.90"))
OCR_TRUST = float(os.environ.get("OCR_TRUST", "0.95"))     # below this OCR score a header value goes to review
LEGAL_RE = re.compile(r"\b(pvt\.?|private|ltd\.?|limited|llp|llc|inc\.?|gmbh|corp\.?|plc|company|co\.)(?=\s|$|[,.)])",
                      re.IGNORECASE)


@dataclass
class PageData:
    number: int
    units: list[Unit]
    source: str                 # text-layer | ppocr
    width: float
    height: float
    notes: list[str] = field(default_factory=list)


LEGAL_TAIL_RE = re.compile(r"[\s.,]*((pvt|private|ltd|limited|llp|llc|inc|corp|company|co)\.?[\s.,]*)+", re.IGNORECASE)
# words a stamp or a copy marker adds next to a document number; never part of the number itself
COPY_MARK_RE = re.compile(r"(?:^|\s+)\(?(original|duplicate|triplicate|copy|draft|non[- ]negotiable|"
                          r"for (?:recipient|transporter|supplier))\)?$", re.IGNORECASE)
DOC_NO_FIELDS = {"Invoice No", "MAWB No", "HAWB No", "Bill of Lading / HBL"}
# a printed label that names the master / house air waybill, alone or in a dual label ("M B/L No. / MAWB No.")
MAWB_LABEL_RE = re.compile(r"\bM\.?\s?A\.?\s?W\.?\s?B\b|master\s+air\s?way\s?bill", re.IGNORECASE)
HAWB_LABEL_RE = re.compile(r"\bH\.?\s?A\.?\s?W\.?\s?B\b|house\s+air\s?way\s?bill", re.IGNORECASE)
AWB_RE = re.compile(r"(?<!\d)\d{3}[-\s]?\d{4}[-\s]?\d{4}(?!\d)")        # a master air waybill: 3 + 8 digits


def _doc_no_like(value: str) -> bool:
    """A printed reference number: has a digit, is not an amount, a date, or an HSN/SAC code."""
    v = value.strip()
    if not (5 <= len(v) <= 32) or not re.search(r"\d", v) or v.count(" ") > 1:
        return False
    if re.fullmatch(r"[\d,]+\.\d{1,3}", v) or re.fullmatch(r"\d{4,8}", v):      # amount / HSN-SAC code
        return False
    return not re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", v)


# ------------------------------------------------------------------ ingest --
def _ocr_page_image(png: str, page_no: int, scale: float) -> tuple[list[Unit], dict]:
    from . import ppocr_client

    res = ppocr_client.ocr_image(png)
    units = ocr_units(res, page_no)
    if scale != 1.0:
        for u in units:
            u.box = tuple(v * scale for v in u.box)
    return units, res


def load_pages(path: str, force_ocr: bool = False) -> list[PageData]:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        from PIL import Image

        from . import sr_swinir

        with Image.open(path) as im:
            w0, h0 = im.size
        if sr_swinir.should_upscale(w0, h0):
            # low-resolution image: OCR the SwinIR x2 upscale, map boxes back to original pixels
            with tempfile.TemporaryDirectory() as tmp:
                up = os.path.join(tmp, "sr.png")
                sr_swinir.upscale(path, up)
                units, res = _ocr_page_image(up, 1, 0.5)
            return [PageData(1, units, "ppocr+swinir", w0, h0, [f"ocr {res.get('elapsed', 0)}s on SwinIR x2"])]
        units, res = _ocr_page_image(path, 1, 1.0)
        return [PageData(1, units, "ppocr", res["width"], res["height"],
                         [f"ocr {res.get('elapsed', 0)}s", "models=" + ",".join(res.get("models", []))])]
    import fitz

    pages = []
    text_pages = pdf_units(path)
    with fitz.open(path) as pdf, tempfile.TemporaryDirectory() as tmp:
        todo = []   # scanned pages: (number, png, width, height), OCR'd concurrently below
        for (pno, units, (w, h)) in text_pages:
            if not force_ocr and has_text_layer(units):
                pages.append(PageData(pno, units, "text-layer", w, h))
                continue
            png = os.path.join(tmp, f"p{pno}.png")
            pdf[pno - 1].get_pixmap(dpi=OCR_DPI).save(png)
            todo.append((pno, png, w, h))
        if todo:
            # OCR dominates per-page cost (CPU-bound workers); render stays sequential (fitz
            # documents are not thread-safe) and only the worker round-trips run concurrently.
            # The OCR runs inside this block: the page PNGs live in tmp.
            from concurrent.futures import ThreadPoolExecutor

            jobs = max(1, min(len(todo), int(os.environ.get("PPOCR_WORKERS", "2"))))

            def _safe_ocr(job):
                try:
                    return _ocr_page_image(job[1], job[0], 72.0 / OCR_DPI)
                except Exception as exc:      # one bad page must not abort the whole invoice
                    return [], {"error": f"{type(exc).__name__}: {exc}"[:200]}

            with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="invoice-ocr") as ex:
                results = ex.map(_safe_ocr, todo)  # pixels -> PDF points
                for (pno, _png, w, h), (ounits, res) in zip(todo, results):
                    if "error" in res:
                        pages.append(PageData(pno, [], "ppocr", w, h, [f"ocr failed: {res['error']}"]))
                    else:
                        pages.append(PageData(pno, ounits, "ppocr", w, h,
                                              [f"ocr {res.get('elapsed', 0)}s at {OCR_DPI} dpi"]))
            pages.sort(key=lambda p: p.number)
    return pages


# ------------------------------------------------------------------ reader --
def call_reader(layout_text: str, model: str | None = None) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message(layout_text)}]
    model = model or READER_MODEL
    backend = os.environ.get("READER_BACKEND", "llamacpp").lower()
    if backend == "llamacpp":
        import httpx

        host = os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080").rstrip("/")
        body = {"model": model, "messages": messages, "temperature": 0, "seed": 7, "max_tokens": 6144,
                "response_format": {"type": "json_schema", "json_schema": {"name": "invoice", "schema": JSON_SCHEMA}}}
        last = None
        for _ in range(2):
            try:
                r = httpx.post(f"{host}/v1/chat/completions", json=body, timeout=900.0)
                r.raise_for_status()
                return parse_output(r.json()["choices"][0]["message"]["content"])
            except Exception as exc:  # transport / decode
                last = exc
        raise RuntimeError(f"reader (llama.cpp at {host}) failed: {last}")
    if backend == "ollama":
        import ollama

        resp = ollama.chat(model=model, messages=messages, format=JSON_SCHEMA, think=False,
                           options={"temperature": 0, "seed": 7, "num_ctx": 16384, "num_predict": 6144},
                           keep_alive="30m")
        return parse_output(resp["message"]["content"])
    if backend == "transformers":
        from .reader_hf import generate

        return parse_output(generate(messages))
    raise RuntimeError(f"unknown READER_BACKEND {backend!r}")


# --------------------------------------------------------------- citations --
def _an(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").casefold())


def _num_key(s: str) -> str | None:
    m = NUM_RE.search(s or "")
    return _norm_num(m.group(0)) if m else None


def _num_forms(v: float) -> list[str]:
    """Ways a two-decimal amount is printed: 530,230.50 / 5,30,230.50 (Indian grouping) / 530230.50."""
    whole, dec = f"{abs(v):.2f}".split(".")
    west = f"{int(whole):,}"
    head, tail, parts = whole[:-3], whole[-3:], []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    indian = ",".join(([head] if head else []) + parts + [tail])
    out = []
    for w in (west, indian, whole):
        if f"{w}.{dec}" not in out:
            out.append(f"{w}.{dec}")
    return out


def _char_w(ch: str) -> float:
    """Rough relative glyph width in a proportional font (enough to place a box inside an OCR segment)."""
    if ch in " .,:;'|!ilIj()[]/-":
        return 0.5
    if ch in "mwMW@%":
        return 1.5
    return 1.15 if ch.isupper() else 1.0


def _sub_box(u: Unit, start: int, end: int) -> list[float]:
    """Box of characters [start, end) of an OCR segment, sliced by approximate glyph widths."""
    x0, y0, x1, y1 = u.box
    w = [_char_w(c) for c in u.text] or [1.0]
    total = sum(w)
    return [x0 + (x1 - x0) * sum(w[:start]) / total, y0, x0 + (x1 - x0) * sum(w[:end]) / total, y1]


def _split_merged(want: float, lines: list[Line]) -> list[dict]:
    """OCR can merge neighbouring table cells into one token ("72,555.109.00" is 72,555.10 next to 9.00).
    Find the amount as a complete number inside such a token; what is left of the token on either side
    must itself be a number, so a longer number is never cut in two."""
    out = []
    for ln in lines:
        for u in ln.units:
            for tok in re.finditer(r"\S+", u.text):
                t = tok.group(0)
                if t.count(".") < 2 or not re.fullmatch(r"[\d,.]+", t):
                    continue
                span = None
                for f in _num_forms(want):
                    k = t.find(f)
                    while k != -1 and span is None:
                        left, right = t[:k], t[k + len(f):]
                        if ((not left or re.fullmatch(r"\d[\d,]*\.\d+", left)) and
                                (not right or re.fullmatch(r"\d[\d,]*(\.\d+)?", right))):
                            span = (tok.start() + k, tok.start() + k + len(f), f)
                        k = t.find(f, k + 1)
                    if span:
                        break
                if span:
                    out.append({"page": ln.page, "bbox": [round(b, 2) for b in _sub_box(u, span[0], span[1])],
                                "line": ln.id, "text": span[2], "score": 0.9, "match": "split-merged-number",
                                "merged_token": t})
    return out


def locate(value, lines: list[Line], numeric: bool = False) -> list[dict]:
    """Measured box(es) of `value` inside the given lines; [] when it is not printed there.

    Matches whole units (words / OCR segments): exact on alnum-normalized text, numbers by
    normalized value, otherwise a close fuzzy match (>= 0.88, for OCR noise) flagged as such."""
    if value in (None, ""):
        return []
    v = str(value)
    want_num = _norm_num(f"{float(value):.2f}") if numeric and isinstance(value, (int, float)) else None
    target = _an(v)
    hits, fuzzy = [], []
    # A value may wrap onto the next printed line (long vendor names, narrow cells): also scan
    # windows that continue into the following line of the same page.
    seqs = []
    for k, ln in enumerate(lines):
        nxt = lines[k + 1] if k + 1 < len(lines) and lines[k + 1].page == ln.page else None
        seqs.append((ln, ln.units + (nxt.units if nxt is not None and want_num is None else [])))
    for ln, us in seqs:
        for i in range(len(us)):
            acc = ""
            for j in range(i, min(len(us), i + 12)):
                acc += _an(us[j].text)
                text = " ".join(u.text for u in us[i:j + 1])
                ok, span = False, None
                if want_num is not None:
                    if j == i:
                        m = next((m for m in NUM_RE.finditer(text) if _norm_num(m.group(0)) == want_num), None)
                        ok, span = m is not None, (m.span() if m else None)
                elif target and target in acc and (j == i or target not in _an(" ".join(u.text for u in us[i + 1:j + 1]))):
                    ok = True
                    if j == i:      # where inside this unit's text the value sits
                        pos = [k for k, ch in enumerate(text) if _an(ch)]
                        at = _an(text).find(target)
                        if at != -1 and pos:
                            span = (pos[at], pos[at + len(target) - 1] + 1)
                if ok:
                    if span and (span[1] - span[0]) < len(text.strip()) - 1:
                        box = _sub_box(us[i], span[0], span[1])     # the value is part of a longer OCR segment
                    else:
                        box = [min(u.box[0] for u in us[i:j + 1]), min(u.box[1] for u in us[i:j + 1]),
                               max(u.box[2] for u in us[i:j + 1]), max(u.box[3] for u in us[i:j + 1])]
                    hits.append({"page": ln.page, "bbox": [round(b, 2) for b in box], "line": ln.id,
                                 "text": text, "score": 1.0, "match": "exact",
                                 "ocr_score": round(min(u.score for u in us[i:j + 1]), 4)})
                    break
                if want_num is None and target and len(acc) >= len(target) * 0.8:
                    s = SequenceMatcher(None, acc, target).ratio()
                    if s >= 0.88:
                        box = [min(u.box[0] for u in us[i:j + 1]), min(u.box[1] for u in us[i:j + 1]),
                               max(u.box[2] for u in us[i:j + 1]), max(u.box[3] for u in us[i:j + 1])]
                        fuzzy.append({"page": ln.page, "bbox": [round(b, 2) for b in box], "line": ln.id,
                                      "text": text, "score": round(s, 3), "match": "fuzzy",
                                      "ocr_score": round(min(u.score for u in us[i:j + 1]), 4)})
                    if len(acc) > len(target) * 1.4:
                        break
    if hits:
        # keep only the tightest boxes: drop any hit that encloses another hit on the same page
        def inside(a, b):
            return (a["page"] == b["page"] and a["bbox"] != b["bbox"] and b["bbox"][0] >= a["bbox"][0] - 0.5 and
                    b["bbox"][1] >= a["bbox"][1] - 0.5 and b["bbox"][2] <= a["bbox"][2] + 0.5 and
                    b["bbox"][3] <= a["bbox"][3] + 0.5)
        tight = [h for h in hits if not any(inside(h, o) for o in hits)]
        uniq = {}
        for h in tight:
            uniq.setdefault((h["page"], tuple(h["bbox"])), h)
        return list(uniq.values())
    if want_num is not None:
        return _split_merged(float(value), lines)
    return sorted(fuzzy, key=lambda h: -h["score"])[:1]


def cite(value, line_ids, index: dict[str, Line], numeric: bool = False) -> tuple[list[dict], str]:
    """(hits, basis): cited lines first; if the reader mis-cited, search every line."""
    lids = clean_lines(line_ids, set(index))
    if lids:
        hits = locate(value, [index[x] for x in lids], numeric)
        if hits:
            return hits, "reader-cited"
    hits = locate(value, list(index.values()), numeric)
    return hits, ("relocated" if hits else "not-found")


# ------------------------------------------------------------------ checks --
def facts_reading(facts: list) -> list:
    """Extraction facts -> reading entries (label + kept value + box)."""
    reading = []
    for f in facts or []:
        if not isinstance(f, dict):
            continue
        label = (f.get("label") or "").strip()
        value = str(f.get("value", "") or "").strip()
        if not label and not value:
            continue
        content = (f"{label}: {value}" if label and value else (value or label))[:1500]
        box = f.get("bbox") or []
        reading.append({"page": f.get("page"),
                        "label": label or f.get("kind", "") or "fact",
                        "content": content,
                        "bbox": [round(float(v), 1) for v in box] if len(box) == 4 else []})
    return reading


def iso6346_ok(cn: str) -> bool:
    m = CONTAINER_RE.fullmatch(cn.replace(" ", "").upper())
    if not m:
        return False
    vals, v = {}, 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if v % 11 == 0:
            v += 1
        vals[ch] = v
        v += 1
    code = m.group(1) + m.group(2)
    total = sum((vals[c] if c.isalpha() else int(c)) * (2 ** i) for i, c in enumerate(code))
    return (total % 11) % 10 == int(m.group(3))


def mawb_ok(s: str) -> bool | None:
    d = re.sub(r"\D", "", s or "")
    if len(d) != 11:
        return None
    return int(d[3:10]) % 7 == int(d[10])


# --------------------------------------------------------------- pipeline --
WEIGHT_UNIT_RE = re.compile(r"\b(kgs?|kilograms?|lbs?|mt|tons?|tonnes?)\b", re.IGNORECASE)
WEIGHT_LABEL_RE = re.compile(r"weight|\bwt\b|\bwgh?t\b|\bg\.?\s?w\b|gross", re.IGNORECASE)


def _weight_like(label: str, value: str) -> bool:
    """A number counts as a weight only if its label says weight or the value carries a weight unit
    (a 'W/M' freight-basis column or an HSN/SAC code under a wide header is not a weight)."""
    return bool(WEIGHT_LABEL_RE.search(label or "") or WEIGHT_UNIT_RE.search(value or ""))


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def extract_invoice(path: str, model: str | None = None, force_ocr: bool = False) -> tuple[dict, dict]:
    t0 = time.time()
    pages = load_pages(path, force_ocr=force_ocr)
    t_ingest = time.time() - t0
    text, index = serialize([(p.number, p.units) for p in pages])
    t1 = time.time()
    raw = call_reader(text, model)
    t_llm = time.time() - t1
    t2 = time.time()
    try:
        from . import vl_client

        use_blocks = vl_client.enabled()
    except Exception:                # without the document parser the line rules' facts stand alone
        vl_client, use_blocks = None, False
    out = assemble(raw, text, index, pages, skip_facts=use_blocks)
    fields, audit = out
    audit["file"] = path
    audit["pipeline_rev"] = PIPELINE_REV
    audit["timing_sec"] = {"ingest": round(t_ingest, 2), "llm": round(t_llm, 2), "post": round(time.time() - t2, 2)}
    # document parser: PaddleOCR-VL reads the page as blocks (paragraphs kept whole, tables as cells, text inside
    # pictures); when it is available its blocks replace the line rules as the source of "everything on the page".
    try:
        if vl_client is not None and use_blocks:
            t3 = time.time()
            parsed = vl_client.parse_document(path)
            from .facts import facts_from_blocks

            audit["facts_by_rules"] = len(audit.get("facts") or [])
            audit["facts"] = facts_from_blocks(parsed, index, audit.get("citations"), audit.get("line_items"))
            audit["blocks"] = parsed
            audit["doc_parser"] = {"model": vl_client.MODEL, "pages": len(parsed), "seconds": round(time.time() - t3, 2),
                                   "from_cache": all(p.get("cached") for p in parsed)}
            audit["timing_sec"]["parse"] = audit["doc_parser"]["seconds"]
    except Exception as exc:                 # the line rules' items stay in place
        audit["doc_parser"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        if use_blocks and "facts" not in audit:    # skipped in assemble, and the blocks never arrived
            try:
                from .facts import document_facts

                audit["facts"] = document_facts(index, audit.get("citations"), audit.get("line_items"))
            except Exception as exc2:
                audit["facts"], audit["facts_error"] = [], f"{type(exc2).__name__}: {exc2}"[:200]
    # organizer: a language model arranges the extracted items under named details (organizer.py). Optional and
    # never blocking: ORGANIZE=0 switches it off, and a failure leaves the extraction as it is.
    if os.environ.get("ORGANIZE", "1") == "1" and audit.get("facts"):
        try:
            from .organizer import organize

            org = organize(audit["facts"], index)
            audit["organized"] = org
            audit["timing_sec"]["organize"] = org["seconds"]
        except Exception as exc:
            audit["organized"] = {"details": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
    audit["reading"] = facts_reading(audit.get("facts") or [])
    audit["reading_note"] = "Cited extraction facts (what the pipeline kept)"
    return fields, audit


def assemble(raw: dict, text: str, index: dict[str, Line], pages: list[PageData],
               skip_facts: bool = False) -> tuple[dict, dict]:
    """Reader JSON + layout index -> (31-key fields, audit with citations and explanations)."""
    from .kv import candidates
    from .semantic import engine, region_of

    ev = raw.get("evidence") or {}
    mode = raw.get("transport_mode") or "unknown"
    fields = {k: "" for k in OUTPUT_FIELDS}
    expl: dict[str, dict] = {}
    warnings: list[str] = []

    def put(field_name: str, value: str, hits: list[dict], basis: str, conf: float, extra: dict | None = None):
        fields[field_name] = value
        expl[field_name] = {"value": value, "citations": hits, "basis": basis, "confidence": round(conf, 4),
                            **(extra or {})}

    text_layer = {p.number for p in pages if p.source == "text-layer"}

    def header(field_name: str, value: str, ev_key: str):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        extra = {}
        if field_name in DOC_NO_FIELDS:
            bare = COPY_MARK_RE.sub("", value).strip()
            if bare and bare != value and len(bare) >= 3:
                extra, value = {"reader_value": value, "note": "copy marker next to the number removed"}, bare
        if not value:
            return
        hits, basis = cite(value, ev.get(ev_key), index)
        if not hits:
            warnings.append(f"{field_name} '{value}' is not printed on the document; blanked")
            expl[field_name] = {"value": "", "rejected": value, "basis": "not-found", "citations": []}
            return
        conf = 1.0 if hits[0]["match"] == "exact" else hits[0]["score"] * 0.9
        if field_name in DOC_NO_FIELDS and hits[0]["match"] == "exact":
            # The reader can stop inside a printed token ("E/88835/26-" of "E/88835/26-27"): a reference number is
            # the whole token as printed.
            token = hits[0]["text"].strip().rstrip(".,;:")
            if (token != value and len(token.split()) == len(value.split()) and _an(token).startswith(_an(value))
                    and 0 < len(_an(token)) - len(_an(value)) <= 4 and len(_an(value)) >= 5
                    and len(token) <= 40):
                whole = locate(token, [index[hits[0]["line"]]])
                if whole:
                    extra = {**extra, "reader_value": value, "note": "completed to the whole printed number"}
                    value, hits = token, whole
        if hits[0]["match"] == "fuzzy" and hits[0]["page"] in text_layer:
            # A text-layer page is exact, so a near match means the reader reworded the value ("Pvt Ltd" ->
            # "Private Limited"). The output is what the document prints: take the printed words.
            printed = re.sub(r"\s+", " ", hits[0]["text"]).strip()
            exact = locate(printed, [index[hits[0]["line"]]]) or hits
            extra = {**extra, "reader_value": value, "note": "reader's wording replaced by the printed text"}
            value, hits, basis, conf = printed, exact, "printed-text", 0.95
        put(field_name, value, hits, basis, conf, extra)

    header("Invoice No", raw.get("invoice_no"), "invoice_no")
    header("Vendor Name", raw.get("vendor_name"), "vendor_name")
    vn = fields["Vendor Name"]
    if vn and len(_an(vn)) >= 5:
        # A scan can lead the reader to the logo ("KUEHNE+NAGEL"); the registered name printed on the
        # document ("KUEHNE NAGEL PRIVATE LIMITED") is the vendor of record. The reader can also stop one
        # word early ("... SERVICES PVT" for "... SERVICES PVT LTD"): the printed name is completed.
        cut_short = bool(LEGAL_RE.search(vn))
        for line in index.values():
            cell = None
            for c in (x.strip() for x in re.split(r"\s{2,}", line.text)):
                if not LEGAL_RE.search(c) or len(c) > 80 or _an(c) == _an(vn):
                    continue
                if not cut_short and _an(vn) in _an(c):
                    cell = c
                elif cut_short and c.lower().startswith(vn.lower()) and LEGAL_TAIL_RE.fullmatch(c[len(vn):]):
                    cell = c
                if cell:
                    break
            if cell:
                lhits = locate(cell, [line])
                if lhits:
                    put("Vendor Name", cell, lhits, "legal-name-on-document", 0.95, {"reader_value": vn})
                    break
    if raw.get("invoice_date"):
        printed = str(raw["invoice_date"]).strip()
        hits, basis = cite(printed, ev.get("invoice_date"), index)
        if hits:
            iso = normalize_date(printed)
            put("Invoice Date", iso, hits, basis, 1.0 if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else 0.7,
                {"printed": printed})
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
                warnings.append(f"Invoice Date '{printed}' could not be normalized")
        else:
            warnings.append(f"Invoice Date '{printed}' is not printed on the document; blanked")
    # containers: reader's list (validated + cited)
    conts, chits = [], []
    for c in raw.get("container_numbers") or []:
        c = re.sub(r"\s+", "", str(c)).upper()
        if not CONTAINER_RE.fullmatch(c):
            continue
        hits, _ = cite(c, ev.get("container_numbers"), index)
        if not hits:
            warnings.append(f"Container {c} not printed; dropped")
            continue
        if not iso6346_ok(c):
            warnings.append(f"Container {c} fails the ISO 6346 check digit")
        conts.append(c)
        chits += hits
    if conts:
        put("Container Number", ", ".join(conts), chits, "reader-cited",
            1.0 if all(iso6346_ok(c) for c in conts) else 0.6)
    else:
        # Safety net: the reader can skip a container that is printed without a label inside the cargo
        # description. A code with a valid ISO 6346 check digit is unambiguous, so scan every line for one
        # (ignoring anything that is one of the document's own reference numbers).
        refs = {re.sub(r"[^0-9A-Z]", "", str(raw.get(k) or "").upper())
                for k in ("invoice_no", "house_bl_no", "master_bl_no", "mawb_no", "hawb_no")}
        found = []
        for line in index.values():
            for m in CONTAINER_RE.finditer(line.text.upper()):
                c = m.group(1) + m.group(2) + m.group(3)
                if c not in found and c not in refs and iso6346_ok(c):
                    found.append(c)
        for c in found:
            hits = locate(c, list(index.values()), False)
            if hits:
                conts.append(c)
                chits += hits
        if conts:
            put("Container Number", ", ".join(conts), chits, "iso6346-scan", 0.9,
                {"note": "not returned by the reader; found by scanning for a valid ISO 6346 check digit"})
            if len(conts) > 1:
                warnings.append(f"Container numbers {', '.join(conts)} were found by check-digit scan, not by the "
                                "reader; several candidates — review")
            else:
                warnings.append(f"Container number {conts[0]} was found by check-digit scan, not by the "
                                "reader — review")
    if mode != "sea":
        mawb = re.sub(r"\s+", " ", str(raw.get("mawb_no") or "")).strip()
        if mawb:
            found = AWB_RE.search(mawb)
            if found and found.group(0) != mawb:
                mawb = found.group(0)                    # stray words next to the number ("GS T 176-66263886")
            elif not found and re.search(r"\d", mawb):
                # fewer than 11 digits: the reader stopped at a space ("160-9052" of "160-9052 2913")
                digits = re.sub(r"\D", "", mawb)
                lids = clean_lines(ev.get("mawb_no"), set(index))
                pool = [index[x] for x in lids] or list(index.values())
                full = {m.group(0) for ln in pool for m in AWB_RE.finditer(ln.text)
                        if re.sub(r"\D", "", m.group(0)).startswith(digits)}
                if len(full) == 1:
                    mawb = full.pop()
        header("MAWB No", mawb, "mawb_no")
        header("HAWB No", raw.get("hawb_no"), "hawb_no")
        if fields["MAWB No"] and mawb_ok(fields["MAWB No"]) is False:
            warnings.append(f"MAWB {fields['MAWB No']} fails the mod-7 check digit")
            expl["MAWB No"]["confidence"] = 0.6
        elif fields["MAWB No"] and len(re.sub(r"\D", "", fields["MAWB No"])) != 11:
            warnings.append(f"MAWB No '{fields['MAWB No']}' does not have the 11 digits of an air waybill — review")
            expl["MAWB No"]["confidence"] = 0.6
    if mode != "air":
        hb, mb = raw.get("house_bl_no"), raw.get("master_bl_no")
        header("Bill of Lading / HBL", hb or mb, "house_bl_no" if hb else "master_bl_no")
        if "Bill of Lading / HBL" in expl:
            expl["Bill of Lading / HBL"]["source_key"] = "house_bl_no" if hb else "master_bl_no"
    gw, cw = raw.get("gross_weight"), raw.get("chargeable_weight")
    wv, wkey = re.sub(r"\s+", " ", str(gw or cw or "")).strip(), "gross_weight" if gw else "chargeable_weight"
    if wv:
        whits, wbasis = cite(wv, ev.get(wkey), index)
        wnum = NUM_RE.search(wv)
        if not whits and wnum:      # "10669.000 kg" where "kg" is printed in the column header, not beside the number
            whits, wbasis = cite(wnum.group(0), ev.get(wkey), index)
        if whits:
            put("WEIGHT", wv, whits, wbasis, 1.0 if whits[0]["match"] == "exact" else whits[0]["score"] * 0.9)
        else:
            warnings.append(f"WEIGHT '{wv}' is not printed on the document; blanked")
            expl["WEIGHT"] = {"value": "", "rejected": wv, "basis": "not-found", "citations": []}
    # exchange rates
    rates, rhits = [], []
    for r in raw.get("exchange_rates") or []:
        cur, rate = str(r.get("currency", "")).upper().strip(), r.get("rate")
        if not cur or cur == "INR" or not isinstance(rate, (int, float)) or rate <= 0:
            continue
        hits = locate(rate, [index[x] for x in clean_lines(r.get("lines"), set(index))] or list(index.values()), numeric=True)
        shown = f"{rate:.6f}".rstrip("0").rstrip(".")      # keep every printed decimal (":g" would round 104.6538 to 104.654)
        if not hits and shown != f"{rate:.2f}":
            # more than two decimals: the numeric match above cannot see them. A two-decimal rate that
            # missed is not printed (a bare substring would match inside a longer number, e.g. 8.25 in 98.25)
            hits = locate(shown, list(index.values()))
        if not hits:
            warnings.append(f"Exchange rate {cur} {rate} not printed; dropped")
            continue
        entry = f"{cur} {shown}"
        if entry not in rates:
            rates.append(entry)
            rhits += hits
    if rates:
        put("Ex Rate", ", ".join(rates), rhits, "reader-cited", 1.0)

    # ---- line items: cite, then semantic categorization with the reader's vote ----
    items = []
    for it in raw.get("line_items") or []:
        desc = re.sub(r"\s+", " ", str(it.get("description") or "")).strip()
        amt = it.get("amount_inr")
        if not desc or not isinstance(amt, (int, float)):
            continue
        lids = clean_lines(it.get("lines"), set(index))
        ahits = locate(round(float(amt), 2), [index[x] for x in lids], numeric=True) if lids else []
        abasis = "reader-cited"
        if not ahits:
            ahits = locate(round(float(amt), 2), list(index.values()), numeric=True)
            abasis = "relocated" if ahits else "not-found"
        # the same number can be printed in several cells of a row (rate, amount, taxable value): cite the
        # amount cell, i.e. a two-decimal figure, right-most on the row
        ahits.sort(key=lambda h: (not re.search(r"\.\d{2}$", h["text"].strip()), -h["bbox"][0]))
        dhits = locate(desc, [index[x] for x in lids]) if lids else []
        if not dhits:
            dhits = locate(desc, list(index.values()))
        items.append({"description": desc, "amount_inr": round(float(amt), 2), "reader_category": it.get("category"),
                      "lines": lids, "amount_citations": ahits, "description_citations": dhits,
                      "amount_basis": abasis})
    region = region_of(raw.get("destination_country") or "")
    decisions = engine().classify([{"kind": "charge", "text": i["description"], "mode": mode, "region": region,
                                    "reader": i["reader_category"]} for i in items])
    totals: dict[str, float] = {}
    contrib: dict[str, list] = {}
    kept_items = []
    for i, d in zip(items, decisions):
        i["category"] = d.field
        i["category_confidence"] = round(d.confidence, 4)
        i["semantic"] = d.explain()
        if not i["amount_citations"]:
            warnings.append(f"Line item '{i['description']}' amount {i['amount_inr']:,.2f} is not printed; excluded")
            i["excluded"] = "amount-not-found"
            kept_items.append(i)
            continue
        if d.field == "NONE":
            warnings.append(f"Row '{i['description']}' is a total/tax row, not a charge; excluded")
            i["excluded"] = "not-a-charge"
            kept_items.append(i)
            continue
        rdr = i.get("reader_category")
        if d.review:
            warnings.append(f"'{i['description']}' booked under {d.field}: {d.review} — review")
        elif (rdr and rdr != d.field and rdr in OUTPUT_FIELDS and "alias-bank" not in d.basis
                and "business-rule" not in d.basis):
            warnings.append(f"Category of '{i['description']}': the reader read it as {rdr}, the semantic models "
                            f"as {d.field} — review")
        elif d.confidence < SEM_ACCEPT:
            warnings.append(f"Low-confidence category for '{i['description']}': {d.field} ({d.confidence:.2f}) — review")
        totals[d.field] = round(totals.get(d.field, 0.0) + i["amount_inr"], 2)
        contrib.setdefault(d.field, []).append(i)
        kept_items.append(i)
    for cat, total in totals.items():
        parts = contrib[cat]
        put(cat, _fmt(total), [h for p in parts for h in p["amount_citations"][:1]],
            "sum-of-cited-lines" if len(parts) > 1 else "cited-line", min(p["category_confidence"] for p in parts),
            {"contributions": [{"description": p["description"], "amount_inr": p["amount_inr"],
                                "citations": p["amount_citations"][:1] + p["description_citations"][:1],
                                "semantic": p["semantic"]} for p in parts]})

    # ---- header-label semantic linking (explanations, gap filling, disagreement checks) ----
    kvs = candidates(index)
    kv_dec = engine().classify([{"kind": "label", "text": k.label, "mode": mode, "region": region, "value": k.value[:28]}
                                for k in kvs]) if kvs else []
    linked = []
    for k, d in zip(kvs, kv_dec):
        if d.field in ("NONE",) or d.target not in OUTPUT_FIELDS:
            continue
        linked.append((k, d))
        f = d.target
        cur = fields.get(f, "")
        if cur and _an(cur) and _an(cur) in _an(k.value):
            expl.setdefault(f, {}).setdefault("label_links", []).append(
                {"label": k.label, "lines": k.line_ids, "layout": k.layout, **d.explain()})
    fill_ok = {"Invoice No", "Bill of Lading / HBL", "MAWB No", "HAWB No", "WEIGHT"}
    charge_rows = {lid for i in items for lid in i["lines"]}
    # A second reference number printed under a label of the same kind ("OUR B/L NO" next to "B/L / SWB NO."):
    # the reader may have taken the wrong one, so a person decides.
    for k, d in linked:
        f = d.target
        if f not in DOC_NO_FIELDS or not fields.get(f) or d.field != f or d.confidence < 0.95:
            continue
        if any(l in charge_rows for l in k.line_ids):
            continue
        # only on the page the value was taken from: attachments (packing lists, other lots) print their own numbers
        cited = (expl.get(f, {}).get("citations") or [{}])[0].get("page")
        if not k.line_ids or k.line_ids[-1] not in index or index[k.line_ids[-1]].page != cited:
            continue
        both = [v[0] for n, v in d.votes.items() if n in ("embedding", "laya")]
        other = COPY_MARK_RE.sub("", k.value.split("  ")[0].strip()).strip()
        if len(both) == 2 and len(set(both)) == 1 and _doc_no_like(other):
            a, b = _an(other), _an(fields[f])
            if a and b and a not in b and b not in a and not any(w.startswith(f"{f}: the document also") for w in warnings):
                warnings.append(f"{f}: the document also prints '{other}' under '{k.label}' "
                                f"(the value taken is '{fields[f]}') — review")
    for k, d in linked:
        f = d.target
        if f in fill_ok and not fields.get(f) and d.confidence >= 0.95:
            if (f in ("MAWB No", "HAWB No") and mode == "sea") or (f == "Bill of Lading / HBL" and mode == "air"):
                continue
            if d.field == "Master B/L" and any(dd.field == "Bill of Lading / HBL" for _, dd in linked):
                continue
            # Measured on held-out invoices, an unguarded fill was wrong 6 times in 7: a charge row ("B/L Fee
            # ... 996759") read as "label: value", or one model alone. A fill now needs a label that is not on
            # a charge row, both semantic models naming the same field, and a value shaped like the field.
            if any(l in charge_rows for l in k.line_ids):
                continue
            val = k.value.split("  ")[0].strip()
            if f in DOC_NO_FIELDS:
                val = COPY_MARK_RE.sub("", val).strip()
                if not _doc_no_like(val):
                    continue
            model_votes = [v[0] for n, v in d.votes.items() if n in ("embedding", "laya")]
            if "alias-bank" not in d.votes and (len(model_votes) < 2 or len(set(model_votes)) != 1):
                # one model only: not enough to fill the field, enough to stop the invoice being accepted unseen
                if f in DOC_NO_FIELDS and not any(w.startswith(f"{f} is empty") for w in warnings):
                    warnings.append(f"{f} is empty, but '{k.label}' ({val}) may be it — review")
                continue
            if f == "WEIGHT":       # a weight is one number (and maybe a unit), not a line of text
                wm = re.fullmatch(r"\s*([\d,]+(?:\.\d+)?)\s*(kgs?|kilograms?|lbs?|mt|t|tons?)?\.?\s*", val, re.IGNORECASE)
                if not wm or not _weight_like(k.label, val):
                    continue
            hits, basis = cite(val, k.line_ids, index)
            if hits and len(val) >= 3:
                put(f, val, hits, "semantic-label-link", d.confidence * 0.95,
                    {"label_links": [{"label": k.label, "lines": k.line_ids, "layout": k.layout, **d.explain()}]})
                warnings.append(f"{f} filled from label '{k.label}' (reader left it empty) — review")

    # ---- the document's own label decides ----
    # A number printed under a label that names MAWB / HAWB fills that key, also on a sea shipment: some forwarders
    # use one form for both modes ("H B/L No./HAWB No.", "M B/L No. / MAWB No.") and the reader, trained to leave
    # air waybill numbers empty on sea shipments, skips them.
    for key, label_re, reader_keys in (("MAWB No", MAWB_LABEL_RE, ("mawb_no", "master_bl_no")),
                                       ("HAWB No", HAWB_LABEL_RE, ("hawb_no", "house_bl_no"))):
        if fields.get(key):
            continue
        for k in kvs:
            if not label_re.search(k.label) or any(l in charge_rows for l in k.line_ids):
                continue
            val = COPY_MARK_RE.sub("", k.value.split("  ")[0].strip()).strip()
            if not _doc_no_like(val):
                continue
            hits, _basis = cite(val, k.line_ids, index)
            if not hits:
                continue
            confirmed = any(_an(str(raw.get(r) or "")) == _an(val) for r in reader_keys)   # the reader read the same number
            put(key, val, hits, "printed-under-label", 0.97 if confirmed else 0.85,
                {"label": k.label, "note": "the document prints this number under a label naming " + key.split()[0]})
            if not confirmed:
                warnings.append(f"{key} '{val}' taken from the label '{k.label}' (the reader did not return it) — review")
            break

    # ---- weights: a shipment total on the invoice and per-lot weights on attachments ----
    def _wnum(text: str) -> float | None:
        m = NUM_RE.search(text or "")
        try:
            return round(float(m.group(0).replace(",", "")), 3) if m else None
        except ValueError:
            return None

    cands = []     # (number, printed value, page, label, line ids) for every gross-weight label on the document
    for k, d in linked:
        if d.field == "WEIGHT" and k.line_ids and k.line_ids[-1] in index:
            v = _wnum(k.value)
            if v and 0 < v < 5_000_000 and _weight_like(k.label, k.value):
                cands.append((v, k.value.split("  ")[0].strip(), index[k.line_ids[-1]].page, k.label, k.line_ids))
    distinct = sorted({c[0] for c in cands})
    cur = _wnum(fields.get("WEIGHT", ""))
    if len(distinct) > 1 and cur is not None:
        item_pages = [h["page"] for i in kept_items for h in i["amount_citations"][:1]]
        inv_page = max(set(item_pages), key=item_pages.count) if item_pages else 1
        top = distinct[-1]
        is_total = len(distinct) > 2 and abs(sum(distinct[:-1]) - top) <= max(0.5, 0.002 * top)
        on_inv = sorted({c[0] for c in cands if c[2] == inv_page})
        pick, why = None, ""
        if is_total and cur != top:
            pick, why = top, "it equals the sum of the per-lot weights printed on the attachments"
        elif not is_total and len(on_inv) == 1 and cur != on_inv[0]:
            pick, why = on_inv[0], "it is the weight printed on the invoice page itself"
        listed = ", ".join(f"{v:g}" for v in distinct)
        if pick is not None:
            c = next(c for c in cands if c[0] == pick)
            hits, _ = cite(c[1], c[4], index)
            if hits:
                put("WEIGHT", c[1], hits, "shipment-weight", 0.95 if is_total else 0.85,
                    {"reader_value": fields.get("WEIGHT", ""), "candidates": listed, "note": f"chosen because {why}"})
                warnings.append(f"The document prints several gross weights ({listed}); used {c[1]} because {why} — review")
        elif not (is_total and cur == top):
            warnings.append(f"The document prints several gross weights ({listed}); kept {fields.get('WEIGHT')} — review")

    # ---- reconciliation ----
    charged = [i for i in kept_items if "excluded" not in i]
    items_sum = round(sum(i["amount_inr"] for i in charged), 2)
    sub = raw.get("subtotal_before_tax")
    reconciled = None
    if isinstance(sub, (int, float)) and locate(round(float(sub), 2), list(index.values()), numeric=True):
        reconciled = abs(items_sum - float(sub)) <= max(1.0, 0.0005 * float(sub))
        if not reconciled:
            warnings.append(f"Line items sum {items_sum:,.2f} != printed subtotal {float(sub):,.2f}")

    # ---- scans: a reference number the OCR engine itself was unsure about is not accepted unseen ----
    # (on held-out scans a score below 0.95 marks 2 of the 8 misread header values and 1% of the right ones; 0.97 marks
    #  5 of 8 but also 5% of the right ones, which cost a quarter of the automatic acceptances for no gain there)
    for f in ("Invoice No", "Invoice Date", "Vendor Name", "Container Number", "MAWB No", "HAWB No",
              "Bill of Lading / HBL", "WEIGHT", "Ex Rate"):
        e = expl.get(f) or {}
        if fields.get(f) and e.get("citations"):
            s_ocr = min(h.get("ocr_score", 1.0) for h in e["citations"])
            if s_ocr < OCR_TRUST:
                e["ocr_score"], e["confidence"] = s_ocr, round(min(e.get("confidence", 1.0), 0.89), 4)
                warnings.append(f"{f} '{fields[f]}' was read from the scan with low OCR confidence "
                                f"({s_ocr * 100:.0f}%) — review")

    # ---- routing ----
    uncited = [f for f, v in fields.items() if v and not expl.get(f, {}).get("citations")]
    low = [f for f, e in expl.items() if fields.get(f) and e.get("confidence", 1.0) < SEM_ACCEPT]
    # every reason an invoice goes to review is spelled out for the reviewer
    for f in low:
        if not any(f"'{f}'" in w or w.startswith(f) for w in warnings):
            e = expl[f]
            how = ("matched to the printed text only approximately" if (e.get("citations") or [{}])[0].get("match") == "fuzzy"
                   else "below the confidence needed for automatic acceptance")
            warnings.append(f"{f} '{fields[f]}' was {how} ({e.get('confidence', 0) * 100:.0f}%) — review")
    for f in uncited:
        warnings.append(f"{f} '{fields[f]}' could not be located on the document — review")
    if not charged:
        warnings.append("No charge line could be confirmed on the document — review")
    auto = (not warnings and not uncited and not low and reconciled is not False and bool(charged))
    route = "AUTO_ACCEPT" if (auto and os.environ.get("AUTO_ACCEPT", "1") == "1") else "REVIEW"
    citations = {f: e.get("citations", []) for f, e in expl.items() if fields.get(f)}
    audit = {
        "pipeline": "v2",
        "ingest_pages": [{"page": p.number, "source": p.source, "units": len(p.units), "notes": p.notes} for p in pages],
        "transport_mode": mode, "destination_country": raw.get("destination_country", ""),
        "master_bl_no": raw.get("master_bl_no", ""), "house_bl_no": raw.get("house_bl_no", ""),
        "fields_explained": expl, "line_items": kept_items, "line_items_sum_inr": items_sum,
        "subtotal_before_tax": sub, "total_invoice_amount": raw.get("total_invoice_amount"),
        "subtotal_reconciled": reconciled, "label_links": [{"label": k.label, "value": k.value, "lines": k.line_ids,
                                                             **d.explain()} for k, d in linked][:80],
        "citations": citations,
        "line_item_citations": [i["amount_citations"][:1] + i["description_citations"][:1] for i in kept_items],
        "citation_coverage": round(sum(1 for f, v in fields.items() if v and citations.get(f)) /
                                   max(1, sum(1 for v in fields.values() if v)), 4),
        "warnings": warnings, "needs_review": route != "AUTO_ACCEPT", "review_route": route,
        "reader_raw": raw, "evidence": text,
        "semantic_backends": engine().backends,
    }
    if not skip_facts:
        # extract_invoice sets skip_facts when the document parser's blocks replace these facts below
        try:        # full capture: everything printed, each item with its box (facts.py); never blocks the extraction
            from .facts import document_facts

            audit["facts"] = document_facts(index, citations, kept_items)
        except Exception as exc:
            audit["facts"], audit["facts_error"] = [], f"{type(exc).__name__}: {exc}"[:200]
    return fields, audit
