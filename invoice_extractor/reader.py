"""Reader contract (v2): line-numbered layout text in, cited JSON out.

Shared verbatim by training (scripts/build_reader_v2.py) and inference (extract.py), so the
fine-tuned Qwen3-VL-8B-Instruct sees exactly the prompt shape it was trained on.

Every scalar value and every line item carries the ids of the lines ([L#]) it was read from;
the pipeline resolves those ids to measured boxes (citations) and verifies the value is
actually printed there.
"""
from __future__ import annotations

import json
import re

from .schema import CHARGE_FIELDS

SCALAR_KEYS = ["invoice_no", "invoice_date", "vendor_name", "container_numbers", "mawb_no", "hawb_no",
               "master_bl_no", "house_bl_no", "gross_weight", "chargeable_weight", "subtotal_before_tax",
               "total_invoice_amount"]

SYSTEM_PROMPT = (
    "You extract freight / logistics invoices. The input is the invoice text with one numbered line per "
    "printed line ([L#] ...), columns kept in place. Return ONLY one JSON object with keys: invoice_no, "
    "invoice_date, vendor_name, transport_mode (sea|air|road|unknown), destination_country, container_numbers, "
    "mawb_no, hawb_no, master_bl_no, house_bl_no, gross_weight, chargeable_weight, exchange_rates, line_items, "
    "subtotal_before_tax, total_invoice_amount, evidence.\n"
    "Rules: copy values exactly as printed. vendor_name is the company that issued the invoice, never the "
    "customer, shipper or consignee. invoice_no is the issuer's own number, never a party/shipper invoice, "
    "IRN, ack, job or customer number. mawb_no/hawb_no only for air shipments; master_bl_no/house_bl_no only "
    "for sea. Labels differ between invoices; decide by meaning. Use \"\" for absent text, [] for absent lists, "
    "null for absent totals. Never invent a value that is not printed.\n"
    "exchange_rates: one {currency, rate, lines} per foreign currency (skip INR).\n"
    "line_items: one {description, amount_inr, category, lines} per charge row. Skip subtotals, taxes, "
    "round-off, brought/carried-forward rows and HSN/SAC tax summaries, and count a charge repeated on another "
    "page only once. description = the charge text as printed incl. a sub-description printed under it. "
    "amount_inr = taxable INR value excluding GST. category = one of: " + "; ".join(CHARGE_FIELDS) + ".\n"
    "evidence: for every non-empty scalar key, the list of line ids where its value is printed."
)


def user_message(layout_text: str) -> str:
    return layout_text


def target_json(t: dict) -> str:
    """Canonical compact serialization (fixed key order) used as the training target."""
    order = ["invoice_no", "invoice_date", "vendor_name", "transport_mode", "destination_country",
             "container_numbers", "mawb_no", "hawb_no", "master_bl_no", "house_bl_no", "gross_weight",
             "chargeable_weight", "exchange_rates", "line_items", "subtotal_before_tax", "total_invoice_amount",
             "evidence"]
    return json.dumps({k: t[k] for k in order}, ensure_ascii=False, separators=(",", ":"))


JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_no": {"type": "string"}, "invoice_date": {"type": "string"}, "vendor_name": {"type": "string"},
        "transport_mode": {"type": "string", "enum": ["sea", "air", "road", "unknown"]},
        "destination_country": {"type": "string"},
        "container_numbers": {"type": "array", "items": {"type": "string"}},
        "mawb_no": {"type": "string"}, "hawb_no": {"type": "string"},
        "master_bl_no": {"type": "string"}, "house_bl_no": {"type": "string"},
        "gross_weight": {"type": "string"}, "chargeable_weight": {"type": "string"},
        "exchange_rates": {"type": "array", "items": {"type": "object", "properties": {
            "currency": {"type": "string"}, "rate": {"type": "number"},
            "lines": {"type": "array", "items": {"type": "string"}}},
            "required": ["currency", "rate", "lines"]}},
        "line_items": {"type": "array", "items": {"type": "object", "properties": {
            "description": {"type": "string"}, "amount_inr": {"type": "number"},
            "category": {"type": "string", "enum": list(CHARGE_FIELDS)},
            "lines": {"type": "array", "items": {"type": "string"}}},
            "required": ["description", "amount_inr", "category", "lines"]}},
        "subtotal_before_tax": {"type": ["number", "null"]},
        "total_invoice_amount": {"type": ["number", "null"]},
        "evidence": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
    },
    "required": ["invoice_no", "invoice_date", "vendor_name", "transport_mode", "destination_country",
                 "container_numbers", "mawb_no", "hawb_no", "master_bl_no", "house_bl_no", "gross_weight",
                 "chargeable_weight", "exchange_rates", "line_items", "subtotal_before_tax",
                 "total_invoice_amount", "evidence"],
}

_LINE_ID = re.compile(r"^L\d+$")


def clean_lines(ids, valid: set[str] | None = None) -> list[str]:
    out = []
    for x in ids or []:
        x = str(x).strip().strip("[]")
        if _LINE_ID.match(x) and (valid is None or x in valid) and x not in out:
            out.append(x)
    return out


def parse_output(text: str) -> dict:
    """Tolerant JSON extraction (strips think tags / fences); raises ValueError when no object."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    raise ValueError(f"reader returned no JSON object: {text[:200]!r}")
