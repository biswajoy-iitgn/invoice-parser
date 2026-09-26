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
import re
import time
from datetime import datetime

import ollama

from .ingest import load_pages, pages_to_prompt_text, pages_to_raw_text
from .schema import (
    CHARGE_CATEGORIES,
    CHARGE_FIELDS,
    LLM_JSON_SCHEMA,
    OUTPUT_FIELDS,
    glossary_text,
)

DEFAULT_MODEL = "qwen3-vl:8b-instruct"
RECONCILE_TOLERANCE_INR = 1.0
FALLBACK_BUCKETS = {"Other Charges", "Misc"}

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


DATE_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y",
    "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y", "%b %d, %Y", "%d %b %y",
]


def normalize_date(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip().replace(",", ", ")).replace(" ,", ",")
    s = re.sub(r",\s+", ", ", s)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw.strip()


def call_llm(document_text: str, model: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"INVOICE TEXT:\n\n{document_text}\n\nReturn the JSON now."},
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


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def extract_invoice(path: str, model: str = DEFAULT_MODEL, force_ocr: bool = False) -> tuple[dict, dict]:
    """Returns (fields, audit). `fields` has exactly OUTPUT_FIELDS; missing values are ""."""
    t0 = time.time()
    pages = load_pages(path, force_ocr=force_ocr)
    doc_text = pages_to_prompt_text(pages)
    evidence = pages_to_raw_text(pages) + "\n" + doc_text
    t_ingest = time.time() - t0

    raw = call_llm(doc_text, model)
    t_llm = time.time() - t0 - t_ingest

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
    fields["Bill of Lading / HBL"] = _clean(raw.get("house_bl_no")) or _clean(raw.get("master_bl_no"))
    fields["Weight"] = _clean(raw.get("gross_weight")) or _clean(raw.get("chargeable_weight"))

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
        llm_cat = item.get("category") if item.get("category") in CHARGE_FIELDS else "Other Charges"
        lex = lexicon_categories(desc)
        category, basis = llm_cat, "llm"
        if llm_cat in FALLBACK_BUCKETS and len(lex) == 1 and lex[0] not in FALLBACK_BUCKETS:
            category, basis = lex[0], "lexicon-override"
        elif lex and llm_cat in lex:
            basis = "llm+lexicon"
        verified = amount_in_text(amount, evidence)
        totals[category] = round(totals.get(category, 0.0) + amount, 2)
        audit_items.append({
            "description": desc,
            "amount_inr": amount,
            "category": category,
            "category_basis": basis,
            "llm_category": llm_cat,
            "lexicon_matches": lex,
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
    for key in ("Invoice No", "Vendor Name"):
        if fields[key] and fields[key].replace(" ", "").lower() not in re.sub(r"\s+", "", evidence).lower():
            warnings.append(f"{key} '{fields[key]}' not found verbatim in document")

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
        "needs_review": bool(warnings),
        "warnings": warnings,
        "timing_sec": {"ingest": round(t_ingest, 2), "llm": round(t_llm, 2)},
    }
    return fields, audit
