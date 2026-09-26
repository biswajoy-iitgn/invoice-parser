"""LLM paths (Ollama; default Qwen3-VL 8B as in research.md).

  full extraction   the model reads the page text and returns header fields + every charge line
                    with a category (JSON-schema constrained). Deterministic post-checks follow.
  line classifier   the model only picks a category for charge descriptions the lexicon could not
                    place. It never sees or produces amounts, so it cannot hallucinate a number.
"""

from __future__ import annotations

import json

from .schema import CHARGE_FIELDS, LLM_JSON_SCHEMA, glossary_text

DEFAULT_MODEL = "qwen3-vl:8b-instruct"

SYSTEM_PROMPT = f"""You are an expert freight-forwarding / logistics invoice analyst.
You read the raw text of one invoice (possibly several pages, columns may be misaligned)
and return a JSON object that follows the given schema exactly.

HEADER RULES
- vendor_name: the company that ISSUED the invoice (the service provider / seller), exactly as printed.
  Never the bill-to customer, shipper, consignee, buyer, or bank.
- invoice_no: the issuer's own invoice / document number (labels vary: Invoice No, INV NO, Document No,
  Tax Invoice No, Bill No). NOT "Party Invoice No", shipper commercial invoice numbers, IRN, Ack No, job or customer numbers.
- invoice_date: the invoice/document date as printed (not due date, not ack date, not sailing/flight/shipping-bill date).
- transport_mode: sea, air or road, based on the shipment (vessel, container, B/L => sea; flight, AWB => air).
- destination_country: country of the port of discharge / final destination, "" if unknown.
- container_numbers: every shipping container number (4 letters + 7 digits, e.g. MSCU1234567). No seal numbers.
- mawb_no / hawb_no: ONLY for air shipments (master / house air waybill). "" for sea shipments.
- master_bl_no / house_bl_no: for sea shipments. Labels vary: MBL, M B/L, Ocean Bill of Lading (master);
  HBL, H B/L, House Bill of Lading, B/L No (house). If only one BL number exists and it is not clearly
  master, put it in house_bl_no.
- gross_weight / chargeable_weight: total shipment weight with unit as printed (e.g. "10669 KG"), "" if absent.
- exchange_rates: every foreign-currency -> INR rate printed on the invoice (one entry per currency, skip INR).
- subtotal_before_tax: total of all charges before GST/tax. total_invoice_amount: final amount payable incl. tax.
  Use null if not printed.

LINE ITEM RULES
- One entry per chargeable service line in the charges table(s), across all pages.
- Do NOT include: sub-totals, section sub-totals, GST/CGST/SGST/IGST/tax lines, round-off, grand totals,
  HSN/SAC tax summaries or tax-slab breakdown tables.
- If the same charges are listed twice, include each charge only once.
- description: the full charge text.
- amount_inr: the charge amount in INR EXCLUDING GST (taxable value + any non-taxable value). For foreign-currency
  charges use the INR amount printed on the invoice (after exchange-rate conversion). Copy numbers exactly.
- category: pick the ONE category whose MEANING matches the charge, even when the wording differs.
  Use "Other Charges" only when nothing else fits.

CATEGORIES (meaning and common aliases):
{glossary_text()}

Use "" for any text field that is not present on the invoice. Never invent values."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"categories": {"type": "array", "items": {"type": "string", "enum": CHARGE_FIELDS}}},
    "required": ["categories"],
}


def _client():
    import ollama  # optional dependency, only needed for LLM paths
    return ollama


def call_llm(document_text: str, model: str = DEFAULT_MODEL) -> tuple[dict, bool]:
    """-> (parsed JSON, raw_valid). raw_valid is False when the first answer was not valid JSON."""
    ollama = _client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"INVOICE TEXT:\n\n{document_text}\n\nReturn the JSON now."},
    ]
    last_err = None
    for attempt in range(2):
        resp = ollama.chat(model=model, messages=messages, format=LLM_JSON_SCHEMA, think=False,
                           options={"temperature": 0, "seed": 7 + attempt, "num_ctx": 32768, "num_predict": 8192},
                           keep_alive="15m")
        try:
            return json.loads(resp["message"]["content"]), attempt == 0
        except json.JSONDecodeError as exc:
            last_err = exc
    raise RuntimeError(f"LLM returned invalid JSON twice: {last_err}")


def classify_lines(descriptions: list[str], mode: str, model: str = DEFAULT_MODEL) -> list[str] | None:
    if not descriptions:
        return []
    ollama = _client()
    listing = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(descriptions))
    prompt = (f"Transport mode of the shipment: {mode}.\nClassify each freight-invoice charge description into exactly "
              f"one category by MEANING (wording may differ from the aliases).\n\nCATEGORIES:\n{glossary_text()}\n\n"
              f"DESCRIPTIONS:\n{listing}\n\nReturn {{\"categories\": [...]}} with one category per description, in order.")
    resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}], format=CLASSIFY_SCHEMA,
                       think=False, options={"temperature": 0, "seed": 7}, keep_alive="15m")
    try:
        cats = json.loads(resp["message"]["content"])["categories"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if len(cats) != len(descriptions) or any(c not in CHARGE_FIELDS for c in cats):
        return None
    return cats
