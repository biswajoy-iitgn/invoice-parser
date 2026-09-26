"""Invoice -> the 31-field output JSON.

Backends
  layout      OCR/PDF word boxes -> label/value geometry + charges-table parser -> semantic charge
              mapper -> validators. Every value is copied from the page, so it cannot invent one.
  layout+llm  as layout; charge lines the lexicon cannot place are classified by the LLM (category only).
  llm         the research.md LLM path: page text -> Qwen3-VL (JSON-schema constrained) -> post-checks.

Common post-processing: ISO 6346 / MAWB check digits, date normalisation, amount grounding,
subtotal reconciliation, per-field confidence, strict output contract (schema.finalize).
"""

from __future__ import annotations

import re
import time
from collections import Counter

from . import header as hdr
from . import llm as llm_mod
from . import tables
from .ingest import Page, attach_docling_markdown, is_digital, load_document_readings, pages_to_prompt_text
from .ocr import ENSEMBLE, ENSEMBLE_FALLBACK
from .layout import build_rows, reading_order_text
from .schema import CHARGE_FIELDS, OUTPUT_FIELDS, finalize
from .semantic import classify_charge
from .validators import find_containers, normalize_mawb, parse_date, parse_number

RECONCILE_TOLERANCE_INR = 1.0
BACKENDS = ("layout", "layout+llm", "llm")


def _exrate_from_text(all_text: str) -> float | None:
    for m in re.finditer(r"1\s*(USD|EUR|GBP)\s*=\s*(?:INR|Rs\.?)?\s*(\d{2,3}\.\d{1,4})", all_text, re.I):
        v = parse_number(m.group(2))
        if v and 30 < v < 200:
            return v
    for m in re.finditer(r"@\s*(\d{2,3}\.\d{2,4})\b", all_text):
        v = parse_number(m.group(1))
        if v and 30 < v < 200:
            return v
    return None


def extract_layout(pages: list[Page], llm_model: str | None = None) -> tuple[dict, dict]:
    pages_rows = [(p.number, build_rows(p.words)) for p in pages if p.words]
    if not pages_rows:
        raise RuntimeError("no word boxes on any page (the layout backend needs an OCR engine with boxes)")
    all_text = "\n".join(reading_order_text(rows) for _, rows in pages_rows)
    cands = hdr.collect_candidates(pages_rows)
    mode = hdr.transport_mode(all_text, cands)
    fields = {k: "" for k in OUTPUT_FIELDS}
    conf: dict[str, float] = {}
    basis: dict[str, str] = {}

    def put(field: str, cand):
        if cand is not None and cand.value not in (None, ""):
            fields[field], conf[field], basis[field] = cand.value, round(cand.conf, 3), cand.basis

    put("Invoice No", hdr.best(cands, "invoice_no"))
    put("Invoice Date", hdr.best(cands, "invoice_date") or hdr.fallback_date(pages_rows))
    put("Vendor Name", hdr.vendor_name(pages_rows))
    put("Container Number", hdr.containers(all_text))
    put("MAWB No", hdr.best(cands, "mawb") or hdr.best(cands, "awb"))
    put("HAWB No", hdr.best(cands, "hawb"))
    put("Bill of Lading / HBL", hdr.best(cands, "hbl") or hdr.best(cands, "bl") or hdr.best(cands, "mbl"))
    put("Weight", hdr.best(cands, "gross_weight"))

    lines, tinfo = tables.extract_lines(pages_rows)
    fx_votes = Counter(round(l.exrate, 4) for l in lines if l.exrate and 30 < l.exrate < 200)
    if fx_votes:
        fields["Ex Rate"], conf["Ex Rate"], basis["Ex Rate"] = fx_votes.most_common(1)[0][0], 0.9, "table"
    else:
        put("Ex Rate", hdr.best(cands, "ex_rate"))
        if not fields["Ex Rate"]:
            v = _exrate_from_text(all_text)
            if v:
                fields["Ex Rate"], conf["Ex Rate"], basis["Ex Rate"] = v, 0.7, "inline-pattern"

    items = []
    for l in lines:
        if l.amount_inr is None:
            continue
        cat, c_conf, c_basis = classify_charge(l.description, mode)
        items.append({"description": l.description, "amount_inr": l.amount_inr, "category": cat,
                      "category_basis": c_basis, "category_conf": c_conf, "amount_conf": l.conf,
                      "arithmetic_checks": l.checks, "currency": l.currency, "page": l.page})
    if llm_model:
        todo = [i for i, it in enumerate(items) if it["category_basis"] == "default"]
        try:
            cats = llm_mod.classify_lines([items[i]["description"] for i in todo], mode, llm_model)
        except Exception as exc:  # LLM unavailable: keep lexicon result, say so
            cats = None
            tinfo["llm_error"] = str(exc)[:200]
        for i, c in zip(todo, cats or []):
            items[i]["category"], items[i]["category_basis"], items[i]["category_conf"] = c, "llm", 0.8

    items_sum = round(sum(i["amount_inr"] for i in items), 2)
    subtotal = tables.printed_subtotal(pages_rows)
    reconciled = None if subtotal is None else abs(items_sum - subtotal) <= RECONCILE_TOLERANCE_INR
    rec_factor = 1.0 if reconciled else (0.85 if reconciled is None else 0.6)
    totals: dict[str, float] = {}
    for it in items:
        c = it["category"]
        totals[c] = round(totals.get(c, 0.0) + it["amount_inr"], 2)
        line_conf = it["amount_conf"] * it["category_conf"] * rec_factor
        conf[c] = round(min(conf.get(c, 1.0), line_conf), 3)
        basis[c] = "table"
    fields.update(totals)

    warnings = []
    if reconciled is False:
        warnings.append(f"Line items sum {items_sum:,.2f} != printed subtotal {subtotal:,.2f}")
    if tinfo["unparsed_rows"]:
        warnings.append(f"{tinfo['unparsed_rows']} table row(s) could not be parsed")
    low = [f for f, c in conf.items() if c < 0.6]
    if low:
        warnings.append("Low confidence: " + ", ".join(low))
    audit = {"transport_mode": mode, "field_confidence": conf, "field_basis": basis, "line_items": items,
             "line_items_sum_inr": items_sum, "subtotal_before_tax": subtotal, "subtotal_reconciled": reconciled,
             "tables": tinfo, "warnings": warnings, "needs_review": bool(warnings)}
    return fields, audit


def extract_llm(pages: list[Page], model: str) -> tuple[dict, dict]:
    """The research.md path: Docling/OCR text -> LLM JSON -> deterministic checks."""
    doc_text = pages_to_prompt_text(pages)
    raw, raw_valid = llm_mod.call_llm(doc_text, model)
    mode = raw.get("transport_mode", "unknown")
    clean = lambda s: re.sub(r"\s+", " ", str(s or "")).strip()
    fields = {k: "" for k in OUTPUT_FIELDS}
    fields["Invoice No"] = clean(raw.get("invoice_no"))
    fields["Invoice Date"] = parse_date(clean(raw.get("invoice_date"))) or "" if raw.get("invoice_date") else ""
    fields["Vendor Name"] = clean(raw.get("vendor_name"))
    conts = [c for c in (clean(x).replace(" ", "").upper() for x in raw.get("container_numbers") or []) if find_containers(c)]
    for c in find_containers(doc_text):
        if c not in conts:
            conts.append(c)
    fields["Container Number"] = ", ".join(conts)
    if mode != "sea":
        fields["MAWB No"] = normalize_mawb(clean(raw.get("mawb_no"))) or clean(raw.get("mawb_no"))
        fields["HAWB No"] = clean(raw.get("hawb_no"))
    fields["Bill of Lading / HBL"] = clean(raw.get("house_bl_no")) or clean(raw.get("master_bl_no"))
    w = parse_number(re.sub(r"[^\d.,]", "", clean(raw.get("gross_weight")) or clean(raw.get("chargeable_weight"))) or "")
    fields["Weight"] = w if w else ""
    rates = [r.get("rate") for r in raw.get("exchange_rates") or [] if isinstance(r.get("rate"), (int, float)) and r.get("rate") > 1]
    fields["Ex Rate"] = rates[0] if rates else ""
    items = []
    for it in raw.get("line_items") or []:
        if isinstance(it.get("amount_inr"), (int, float)) and clean(it.get("description")):
            cat = it.get("category") if it.get("category") in CHARGE_FIELDS else "Other Charges"
            items.append({"description": clean(it.get("description")), "amount_inr": it["amount_inr"], "category": cat})
            fields[cat] = round((fields[cat] or 0) + it["amount_inr"], 2)
    audit = {"transport_mode": mode, "llm_raw_valid_json": raw_valid, "line_items": items,
             "field_confidence": {}, "warnings": [], "needs_review": False}
    return fields, audit


HEADER_OUT = ["Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No", "Vendor Name",
              "Bill of Lading / HBL", "Weight"]


def _vote_key(field: str, v) -> str:
    if field == "Vendor Name":
        return re.sub(r"[^a-z0-9]", "", str(v).lower())
    if field == "Weight":
        return f"{float(v):.3f}" if isinstance(v, (int, float)) else str(v)
    return re.sub(r"[^A-Z0-9]", "", str(v).upper())


def table_score(audit: dict) -> float:
    """Verification evidence for a reading's charges table: reconciliation with the printed subtotal and
    line items whose amount is confirmed by >= 2 independent arithmetic paths."""
    items = audit.get("line_items") or []
    verified = sum(len(it.get("arithmetic_checks") or []) >= 2 for it in items)
    return 10.0 * bool(audit.get("subtotal_reconciled")) + verified + 0.1 * len(items)


def merge_readings(results: dict[str, tuple[dict, dict]]) -> tuple[dict, dict]:
    """Combine OCR readings: charges from the best-verified table, header fields by confidence-weighted vote,
    containers as the union of check-digit-valid numbers."""
    names = list(results)
    best = max(names, key=lambda n: (table_score(results[n][1]), -names.index(n)))
    fields = {k: "" for k in OUTPUT_FIELDS}
    bf, ba = results[best]
    fields["Ex Rate"] = bf.get("Ex Rate", "")
    conf = {k: v for k, v in ba["field_confidence"].items() if k == "Ex Rate"}
    basis = {k: f"{best}:{v}" for k, v in ba["field_basis"].items() if k in conf}
    # classify each chosen line with the best-read description of the same amount in any reading
    # (PP-OCRv4 drops inter-word spaces, which defeats the lexicon)
    rank = {"lexicon": 3, "lexicon-paren": 2, "freight-mode": 1, "default": 0}
    items = []
    for it in ba.get("line_items") or []:
        alts = [it] + [o for n in names if n != best for o in results[n][1].get("line_items") or []
                       if abs(o["amount_inr"] - it["amount_inr"]) <= 0.01]
        pick = max(alts, key=lambda o: (rank.get(o["category_basis"], 0), o["description"].count(" ")))
        items.append({**it, "description": pick["description"], "category": pick["category"],
                      "category_basis": pick["category_basis"], "category_conf": pick["category_conf"]})
    rec = ba.get("subtotal_reconciled")
    rec_factor = 1.0 if rec else (0.85 if rec is None else 0.6)
    for it in items:
        c = it["category"]
        fields[c] = round((fields[c] or 0.0) + it["amount_inr"], 2)
        conf[c] = round(min(conf.get(c, 1.0), it["amount_conf"] * it["category_conf"] * rec_factor), 3)
        basis[c] = f"{best}:table"
    votes_log = {}
    for f in HEADER_OUT:
        groups: dict[str, list] = {}
        for n in names:
            v = results[n][0].get(f, "")
            if v in ("", None):
                continue
            c = results[n][1]["field_confidence"].get(f, 0.5)
            groups.setdefault(_vote_key(f, v), []).append((c, n, v))
        if f == "Container Number":
            seen = []
            for n in names:
                for c in str(results[n][0].get(f, "") or "").split(","):
                    c = c.strip()
                    if c and c not in seen:
                        seen.append(c)
            if seen:
                fields[f], conf[f], basis[f] = ", ".join(seen), 0.95, "union-iso6346"
            continue
        if not groups:
            continue
        key, members = max(groups.items(), key=lambda kv: (sum(c for c, _, _ in kv[1]), max(c for c, _, _ in kv[1])))
        c, n, v = max(members)
        fields[f] = v
        conf[f] = round(min(0.99, c + 0.1 * (len(members) - 1)), 3)
        basis[f] = f"vote {len(members)}/{len(names)}"
        votes_log[f] = {k: [m[1] for m in ms] for k, ms in groups.items()}
    audit = dict(ba)
    audit["line_items"] = items
    audit.update({"field_confidence": conf, "field_basis": basis, "ensemble": {
        "readings": names, "table_from": best,
        "table_scores": {n: table_score(results[n][1]) for n in names}, "header_votes": votes_log}})
    return fields, audit


def extract_invoice(path: str, backend: str = "layout", ocr: str = "tesseract", model: str = llm_mod.DEFAULT_MODEL,
                    force_ocr: bool = False) -> tuple[dict, dict]:
    """Returns (fields, audit). `fields` has exactly the 31 output keys; absent values are "".
    ocr: tesseract | tesseract-raw | rapidocr | hunyuan | ensemble (tesseract + tesseract-raw + rapidocr)."""
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    t0 = time.time()
    readings = ENSEMBLE if ocr == "ensemble" else [ocr]
    per = load_document_readings(path, readings, force_ocr=force_ocr)
    if len(readings) > 1 and is_digital(per[readings[0]]):
        per = {readings[0]: per[readings[0]]}          # exact text layer: one reading is enough
    t_ingest = time.time() - t0
    llm_model = model if backend == "layout+llm" else None
    if backend == "llm":
        pages = per[readings[0]]
        attach_docling_markdown(path, pages)
        fields, audit = extract_llm(pages, model)
    elif len(per) == 1:
        pages = next(iter(per.values()))
        fields, audit = extract_layout(pages, llm_model=llm_model)
    else:
        results = {}
        for name, pages in per.items():
            try:
                results[name] = extract_layout(pages, llm_model=None)
            except RuntimeError:
                continue
        if ocr == "ensemble" and not any(r[1].get("subtotal_reconciled") for r in results.values()):
            # cascade: the slower PP-OCRv4 reading only when no Tesseract reading reconciles its table
            t1 = time.time()
            more = load_document_readings(path, ENSEMBLE_FALLBACK, force_ocr=force_ocr)
            t_ingest += time.time() - t1
            for name, pages in more.items():
                per[name] = pages
                try:
                    results[name] = extract_layout(pages, llm_model=None)
                except RuntimeError:
                    continue
        if not results:
            raise RuntimeError("no OCR reading produced word boxes")
        fields, audit = merge_readings(results)
        if llm_model:   # classify unplaced lines once, on the chosen table
            pages = per[audit["ensemble"]["table_from"]]
            fields2, audit2 = extract_layout(pages, llm_model=llm_model)
            for k in CHARGE_FIELDS:
                fields[k] = fields2.get(k, "")
            audit["line_items"] = audit2["line_items"]
        pages = per[audit["ensemble"]["table_from"]]
    audit.update({
        "file": path, "backend": backend, "ocr": ocr,
        "pages": [{"page": p.number, "source": p.source, "words": len(p.words), **p.notes} for p in pages],
        "timing_sec": {"ingest": round(t_ingest, 2), "extract": round(time.time() - t0 - t_ingest, 2)},
    })
    return finalize(fields), audit
