"""Invoice -> fixed-schema JSON.

Pipeline:
  1. ingest: Docling layout/tables (HunyuanOCR for scanned pages), see ingest.py
  2. LLM (Qwen3-VL 8B via Ollama, JSON-schema constrained): header fields + every charge
     line with a semantic category chosen from the glossary
  3. deterministic post-processing: synonym lexicon cross-check, ISO 6346
     container detection, amount-in-source verification, subtotal reconciliation
  4. aggregate line items per category into the requested output fields
"""

from __future__ import annotations

import json
import os
import re
import time

import ollama

from .grounding import cite_fields
from .ingest import load_pages, pages_to_prompt_text, pages_to_raw_text
from .schema import (
    CHARGE_CATEGORIES,
    CHARGE_FIELDS,
    LLM_JSON_SCHEMA,
    OUTPUT_FIELDS,
    glossary_text,
)
from .laya_client import semantic_field
from .validation import ground_fields, normalize_date


def citations_reading(citations: dict) -> list:
    """Grounded citations -> reading entries (field + printed text + box)."""
    reading = []
    for field, hits in (citations or {}).items():
        if field.startswith("__item_"):
            continue
        for h in hits or []:
            if not isinstance(h, dict):
                continue
            text = (h.get("source_text", "") or "").strip()
            if not text:
                continue
            box = h.get("bbox") or []
            reading.append({"page": h.get("page"), "label": field,
                            "content": text[:1500],
                            "bbox": [round(float(v), 1) for v in box] if len(box) == 4 else []})
    return reading

DEFAULT_MODEL = "qwen3-vl:8b-instruct"
RECONCILE_TOLERANCE_INR = 1.0
FALLBACK_BUCKETS = {"Other Charges", "Misc"}

READER_PROMPT = "INVOICE TEXT:\n\n{document_text}\n\nReturn the JSON now."

SYSTEM_PROMPT = f"""You are an expert freight-forwarding / logistics invoice analyst.
You read the raw text of one invoice (possibly several pages, columns may be misaligned)
and return a JSON object that follows the given schema exactly.

HEADER RULES
- vendor_name: the company that ISSUED the invoice (the service provider / seller), exactly as printed.
  Never the bill-to customer, shipper, consignee, buyer, or bank.
- invoice_no: the issuer's own invoice / document number (labels vary: Invoice No, INV NO, Document No,
  Tax Invoice No). NOT "Party Invoice No", shipper commercial invoice numbers, IRN, Ack No, job or customer numbers.
- invoice_date: the invoice/document date as printed (not due date, not ack date, not service date).
- transport_mode: sea, air or road, based on the shipment (vessel, container, B/L => sea; flight, AWB => air).
- destination_country: country of the port of discharge / final destination, "" if unknown.
- container_numbers: every shipping container number (4 letters + 7 digits, e.g. MSCU1234567). No seal numbers.
- mawb_no / hawb_no: ONLY for air shipments (master / house air waybill). "" for sea shipments.
- master_bl_no / house_bl_no: for sea shipments. Labels vary: MBL, M B/L, Ocean Bill of Lading, B/L / SWB No
  (master); HBL, H B/L, House Bill of Lading, BL No (house). A label like "H B/L No./HAWB No." holds the house
  document; decide B/L vs AWB from the transport mode. If only one BL number exists and it is not clearly
  master, put it in house_bl_no.
- gross_weight / chargeable_weight: total shipment weight with unit as printed (e.g. "10669 KG"), "" if absent.
- exchange_rates: every foreign-currency -> INR rate printed on the invoice (one entry per currency, skip INR).
- subtotal_before_tax: total of all charges before GST/tax. total_invoice_amount: final amount payable incl. tax.
  Use null if not printed.

LINE ITEM RULES
- One entry per chargeable service line in the charges table(s), across all pages.
- Do NOT include: sub-totals, GST/CGST/SGST/IGST/tax lines, round-off, grand totals, brought-forward/carry-forward
  rows, HSN/SAC tax summaries or tax-slab breakdown tables.
- If the same charges are listed twice (e.g. a summary table and a detailed breakdown or attachment page),
  include each charge only once.
- description: the full charge text including any sub-description printed under it
  (e.g. a row "OTHER CHARGES" with "THC" printed below it => "OTHER CHARGES THC").
- amount_inr: the charge amount in INR EXCLUDING GST (taxable value + any non-taxable value). For foreign-currency
  charges use the INR amount printed on the invoice (after exchange-rate conversion). Copy numbers exactly.
- category: pick the ONE category whose MEANING matches the charge, even when the wording differs.
  When a row has a generic title like "OTHER CHARGES" plus a specific sub-description, classify by the
  sub-description. Use "Other Charges" only when nothing else fits.

CATEGORIES (meaning and common aliases):
{glossary_text()}

Use "" for any text field that is not present on the invoice. Never invent values."""

CONTAINER_RE = re.compile(r"\b([A-Z]{3}[UJZ])\s?(\d{6})\s?(\d)\b")


def _iso6346_valid(owner_cat: str, serial: str, check: str) -> bool:
    values = {}
    v = 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if v % 11 == 0:
            v += 1
        values[ch] = v
        v += 1
    code = owner_cat + serial
    total = sum((values[c] if c.isalpha() else int(c)) * (2 ** i) for i, c in enumerate(code))
    return (total % 11) % 10 == int(check)


def find_containers(text: str) -> list[str]:
    found = []
    for m in CONTAINER_RE.finditer(text):
        if _iso6346_valid(m.group(1), m.group(2), m.group(3)):
            cn = "".join(m.groups())
            if cn not in found:
                found.append(cn)
    return found


def lexicon_categories(description: str) -> list[str]:
    hits = []
    for name, spec in CHARGE_CATEGORIES.items():
        if any(re.search(p, description, re.IGNORECASE) for p in spec["patterns"]):
            hits.append(name)
    return hits


def _amount_variants(amount: float) -> set[str]:
    neg = amount < 0
    amount = abs(round(amount, 2))
    whole, frac = f"{amount:.2f}".split(".")
    western = f"{int(whole):,}"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        indian = f"{head},{tail}"
    else:
        indian = whole
    variants = {f"{western}.{frac}", f"{indian}.{frac}", f"{whole}.{frac}"}
    if frac == "00":
        variants |= {western, indian, whole}
    if frac.endswith("0"):
        variants |= {f"{western}.{frac[0]}", f"{indian}.{frac[0]}", f"{whole}.{frac[0]}"}
    return {("-" + v if neg else v) for v in variants}


def amount_in_text(amount: float, text: str) -> bool:
    for v in _amount_variants(amount):
        if re.search(r"(?<![\d,.])" + re.escape(v) + r"(?![\d]|,\d)", text):
            return True
    return False


def call_llm(document_text: str, model: str) -> dict:
    prompt = READER_PROMPT.format(document_text=document_text)
    backend = os.environ.get("READER_BACKEND", "ollama").lower()
    if backend not in ("mlx", "llamacpp", "ollama"):
        raise RuntimeError(f"unknown READER_BACKEND {backend!r}")
    if backend == "mlx":
        from .mlx_reader import MLX_PROMPT, call_mlx

        return call_mlx(MLX_PROMPT.format(document_text=document_text))
    if backend == "llamacpp":
        return call_llamacpp(document_text)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    last_err = None
    for attempt in range(2):
        resp = ollama.chat(
            model=model,
            messages=messages,
            format=LLM_JSON_SCHEMA,
            think=False,
            options={"temperature": 0, "seed": 7 + attempt, "num_ctx": 32768, "num_predict": 8192},
            keep_alive="15m",
        )
        try:
            return json.loads(resp["message"]["content"])
        except json.JSONDecodeError as exc:
            last_err = exc
    raise RuntimeError(f"LLM returned invalid JSON twice: {last_err}")


def call_llamacpp(document_text: str) -> dict:
    """Reader via llama.cpp OpenAI-compatible server (Metal, multi-slot).

    Uses grammar-constrained JSON Schema decoding, so output always parses;
    retries only cover transport errors. Server: LLAMACPP_HOST (default
    http://127.0.0.1:8080), model alias from INVOICE_MODEL or DEFAULT_MODEL.
    """
    import httpx

    host = os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080").rstrip("/")
    model = os.environ.get("INVOICE_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": READER_PROMPT.format(document_text=document_text)},
        ],
        "temperature": 0,
        "seed": 7,
        "max_tokens": 8192,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "invoice", "strict": True, "schema": LLM_JSON_SCHEMA},
        },
    }
    last_err: Exception | None = None
    for _ in range(2):
        try:
            resp = httpx.post(f"{host}/v1/chat/completions", json=body, timeout=600.0)
            resp.raise_for_status()
            return json.loads(resp.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
            last_err = exc
    raise RuntimeError(f"llama.cpp reader failed twice: {last_err}")


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def resolve_category(llm_cat: str, desc: str) -> tuple[str, str, list[str], str, str, str]:
    """Resolve one line-item category; always returns a charge field.

    Returns (category, basis, lexicon_matches, semantic_category,
    semantic_backend, llm_category). The semantic vote is constrained to charge fields so
    it can never overwrite a header field with an amount.
    """
    llm_cat = llm_cat if llm_cat in CHARGE_FIELDS else "Other Charges"
    lex = lexicon_categories(desc)
    sem_cat, sem_backend = semantic_field(desc)
    if sem_cat not in CHARGE_FIELDS:
        sem_cat = "Other Charges"  # line items can only be charge fields
    category, basis = llm_cat, "llm"
    if llm_cat in FALLBACK_BUCKETS and len(lex) == 1 and lex[0] not in FALLBACK_BUCKETS:
        category, basis = lex[0], "lexicon-override"
    elif llm_cat in FALLBACK_BUCKETS and sem_cat not in FALLBACK_BUCKETS and (not lex or sem_cat in lex):
        category, basis = sem_cat, f"semantic-override:{sem_backend}"
    elif lex and llm_cat in lex:
        basis = "llm+lexicon"
    elif sem_cat == llm_cat:
        basis = f"llm+semantic:{sem_backend}"
    return category, basis, lex, sem_cat, sem_backend, llm_cat


def extract_invoice(path: str, model: str = DEFAULT_MODEL, force_ocr: bool = False) -> tuple[dict, dict]:
    """Returns (fields, audit). `fields` has exactly OUTPUT_FIELDS; missing values are "".

    INVOICE_PIPELINE=v2 (default) runs the cited pipeline (pipeline.py: text layer / PP-OCRv6,
    fine-tuned reader, semantic engine, bounding-box citations). INVOICE_PIPELINE=legacy runs the
    original Docling/Hunyuan + base-LLM flow below (kept for A/B comparison)."""
    if os.environ.get("INVOICE_PIPELINE", "v2").lower() != "legacy":
        from .pipeline import extract_invoice as _v2

        return _v2(path, model=model, force_ocr=force_ocr)
    t0 = time.time()
    pages = load_pages(path, force_ocr=force_ocr)
    t_ingest = time.time() - t0
    return extract_from_pages(pages, path, model=model, t_ingest=t_ingest)


def extract_from_pages(pages, path: str, model: str = DEFAULT_MODEL,
                       t_ingest: float = 0.0) -> tuple[dict, dict]:
    """Reader + validation stages over pre-ingested pages.

    `path` is used for audit/citations (the image must exist locally).
    Lets GPU-host transcriptions run through the identical Mac reader.
    """
    t0 = time.time()
    doc_text = pages_to_prompt_text(pages)
    transcription = pages_to_raw_text(pages)
    evidence = transcription + "\n" + doc_text

    raw = call_llm(doc_text, model)
    t_llm = time.time() - t0

    mode = raw.get("transport_mode", "unknown")
    fields = {k: "" for k in OUTPUT_FIELDS}
    fields["Invoice No"] = _clean(raw.get("invoice_no"))
    fields["Invoice Date"] = normalize_date(_clean(raw.get("invoice_date"))) if raw.get("invoice_date") else ""
    fields["Vendor Name"] = _clean(raw.get("vendor_name"))

    containers = [c.replace(" ", "").upper() for c in raw.get("container_numbers") or []]
    containers = [c for c in containers if CONTAINER_RE.fullmatch(c)]
    for c in find_containers(evidence):
        if c not in containers:
            containers.append(c)
    fields["Container Number"] = ", ".join(containers)

    if mode != "sea":
        fields["MAWB No"] = _clean(raw.get("mawb_no"))
        fields["HAWB No"] = _clean(raw.get("hawb_no"))
    house_bl, master_bl = _clean(raw.get("house_bl_no")), _clean(raw.get("master_bl_no"))
    if house_bl and master_bl:
        fields["Bill of Lading / HBL"] = f"HBL {house_bl}; MBL {master_bl}"
    else:
        fields["Bill of Lading / HBL"] = house_bl or master_bl
    fields["WEIGHT"] = _clean(raw.get("gross_weight")) or _clean(raw.get("chargeable_weight"))

    rates = []
    for r in raw.get("exchange_rates") or []:
        cur = _clean(r.get("currency")).upper()
        rate = r.get("rate")
        if cur and cur != "INR" and isinstance(rate, (int, float)) and rate > 0 and rate != 1:
            entry = f"{cur} {rate:g}"
            if entry not in rates:
                rates.append(entry)
    fields["Ex Rate"] = ", ".join(rates)

    totals: dict[str, float] = {}
    audit_items = []
    for item in raw.get("line_items") or []:
        desc = _clean(item.get("description"))
        amount = item.get("amount_inr")
        if not desc or not isinstance(amount, (int, float)):
            continue
        category, basis, lex, sem_cat, sem_backend, llm_cat = resolve_category(
            item.get("category"), desc)
        verified = amount_in_text(amount, evidence)
        totals[category] = round(totals.get(category, 0.0) + amount, 2)
        audit_items.append({
            "description": desc,
            "amount_inr": amount,
            "category": category,
            "category_basis": basis,
            "llm_category": llm_cat,
            "lexicon_matches": lex,
            "semantic_category": sem_cat,
            "semantic_backend": sem_backend,
            "amount_found_in_document": verified,
        })

    for cat, total in totals.items():
        fields[cat] = total

    items_sum = round(sum(i["amount_inr"] for i in audit_items), 2)
    subtotal = raw.get("subtotal_before_tax")
    reconciled = None
    if isinstance(subtotal, (int, float)):
        reconciled = abs(items_sum - subtotal) <= RECONCILE_TOLERANCE_INR

    warnings = []
    if reconciled is False:
        warnings.append(f"Line items sum {items_sum:,.2f} != invoice subtotal {subtotal:,.2f}")
    unverified = [i["description"] for i in audit_items if not i["amount_found_in_document"]]
    if unverified:
        warnings.append("Amounts not found verbatim in document: " + "; ".join(unverified))
    disagreements = [i["description"] for i in audit_items
                     if i["lexicon_matches"] and i["category"] not in i["lexicon_matches"]]
    if disagreements:
        warnings.append("LLM category differs from synonym lexicon (review): " + "; ".join(disagreements))
    if house_bl and master_bl:
        warnings.append(f"House BL {house_bl} and master BL {master_bl} both printed; stored both")
    evidence_flat = re.sub(r"\s+", "", evidence).lower()
    for key in ("Invoice No", "Vendor Name"):
        val = str(fields[key] or "")
        if val and val.replace(" ", "").lower() not in evidence_flat:
            warnings.append(f"{key} '{val}' not found verbatim in document")

    audit = {
        "file": path,
        "model": model,
        "pages": [{"page": p.number, "source": p.source, "chars": len(p.text), "notes": p.notes} for p in pages],
        "transport_mode": mode,
        "destination_country": _clean(raw.get("destination_country")),
        "master_bl_no": _clean(raw.get("master_bl_no")),
        "house_bl_no": _clean(raw.get("house_bl_no")),
        "line_items": audit_items,
        "line_items_sum_inr": items_sum,
        "subtotal_before_tax": subtotal,
        "total_invoice_amount": raw.get("total_invoice_amount"),
        "subtotal_reconciled": reconciled,
        # No calibrated field-level confidence model exists yet, so automatic
        # acceptance is disabled: every invoice routes to REVIEW.
        "review_route": "REVIEW",
        "needs_review": True,
        "warnings": warnings,
        "timing_sec": {"ingest": round(t_ingest, 2), "llm": round(t_llm, 2)},
        "evidence": evidence,
        "transcription": transcription,
    }
    verified_sums = {c for c in totals
                     if all(i["amount_found_in_document"] for i in audit_items if i["category"] == c)}
    fields, unsupported_fields = ground_fields(fields, evidence, verified_sums)
    if unsupported_fields:
        warnings.append("Unsupported values were blanked by evidence guard: " + ", ".join(unsupported_fields))
        audit["unsupported_fields"] = unsupported_fields
    # Citation boxes: measured OCR/text-layer hits per value (never guessed).
    t_cite = time.time()
    try:
        ext = os.path.splitext(path)[1].lower()
        # Ground fields + line items in one pass (shared OCR words / page scans).
        cite_values = dict(fields)
        item_keys = []
        for i, it in enumerate(audit_items):
            key = f"__item_{i}"
            item_keys.append(key)
            cite_values[key] = f"{it['description']} {it['amount_inr']}".strip()
        if ext == ".pdf":
            citations: dict[str, list] = {k: [] for k in cite_values}
            for p in pages:
                for k, hits in cite_fields(cite_values, path, is_pdf=True,
                                           page_no=p.number).items():
                    citations[k].extend(hits)
            for k in citations:
                citations[k] = sorted(citations[k], key=lambda h: -h.score)[:2]
        else:
            citations = cite_fields(cite_values, path, is_pdf=False)
        audit["citations"] = {k: [h.__dict__ for h in v]
                              for k, v in citations.items() if k in fields}
        audit["line_item_citations"] = [
            [h.__dict__ for h in citations.get(key, [])] for key in item_keys]
        cited = sum(1 for k, v in fields.items() if v and citations.get(k))
        audit["citation_coverage"] = round(cited / max(1, sum(1 for v in fields.values() if v)), 4)
        audit["reading"] = citations_reading(audit.get("citations") or {})
        audit["reading_note"] = "Grounded field citations (measured page hits)"
    except Exception as exc:
        audit["citations"] = {}
        audit["line_item_citations"] = []
        audit["reading"] = []
        audit["reading_note"] = "Citation pass failed — nothing was read"
        audit["citation_error"] = f"{type(exc).__name__}: {exc}"[:200]
    audit["timing_sec"]["cite"] = round(time.time() - t_cite, 2)
    return fields, audit
