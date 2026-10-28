"""Organizer: an LLM arranges the items that were already extracted from the invoice under a fixed set of named
details. It adds no information: a value is stored only when it is found inside an extracted item.

The reader fills the 31 business fields. Full capture (facts.py) keeps every printed item with its box, but as found
on the page: labels differ from vendor to vendor ("INV DT", "Dated", "Invoice Date"), layout rules mislabel some of
them, and the list is in page order. This step makes the rest of the invoice easy to query:

  1. the base language model (Qwen3-VL-8B, without the reader's adapter) is given the extracted items, grouped by
     the printed line they came from ("[L12] DUE DATE: Jul 20, 2025 | PO NO: PO/25/4165"), and returns
     {detail, value, line} for a fixed list of details (DETAILS below: parties, references, shipment, totals,
     payment, e-invoice);
  2. grounding: the value must be text inside an extracted item of the cited line (or a neighbouring line, or,
     failing that, any item). A value that is in no extracted item is dropped, never stored. The stored page and
     box are those of the extracted item(s);
  3. checks by type: a GSTIN must look like a GSTIN, a date must parse, an amount must be a number. A value that
     fails its check is kept but marked, and left out of the one-row-per-invoice view.

So the model decides only WHICH extracted text belongs to which detail. Each stored detail carries: the value as
printed, a normalized form (ISO date, plain number), page, box, the ids and the printed label of the extracted
item(s) it came from, how it was matched, and the model that arranged it.

(Asking the model for item ids instead of values was tried first and was much worse: an 8B model copies text
reliably but points to numbers badly - it filed the customer as the vendor and a date as the volume.)
"""
from __future__ import annotations

import json
import os
import re
import time

from .layout import Line
from .validation import normalize_date

# (group, detail, type, several values allowed, what it is)
DETAILS = [
    ("Vendor", "vendor_name", "text", False, "the company that issued the invoice"),
    ("Vendor", "vendor_address", "text", True, "the issuer's address, one item per printed line"),
    ("Vendor", "vendor_gstin", "gstin", False, "the issuer's GSTIN"),
    ("Vendor", "vendor_pan", "pan", False, "the issuer's PAN"),
    ("Vendor", "vendor_state", "text", False, "the issuer's state / state code"),
    ("Vendor", "vendor_phone", "text", False, "the issuer's phone number"),
    ("Vendor", "vendor_email", "email", False, "the issuer's e-mail address"),
    ("Vendor", "vendor_cin", "cin", False, "the issuer's CIN / registration number"),
    ("Customer", "customer_name", "text", False, "the company the invoice is billed to"),
    ("Customer", "customer_address", "text", True, "the billed party's address, one item per printed line"),
    ("Customer", "customer_gstin", "gstin", False, "the billed party's GSTIN"),
    ("Customer", "customer_pan", "pan", False, "the billed party's PAN"),
    ("Customer", "customer_code", "ref", False, "customer id / account number at the vendor"),
    ("Invoice", "invoice_type", "text", False, "TAX INVOICE, DEBIT NOTE, CREDIT NOTE ..."),
    ("Invoice", "due_date", "date", False, "payment due date"),
    ("Invoice", "payment_terms", "text", False, "credit terms, e.g. 'Net 30', '45 days from invoice date'"),
    ("Invoice", "place_of_supply", "text", False, "place of supply"),
    ("Invoice", "po_number", "ref", True, "the customer's purchase order number"),
    ("Invoice", "job_number", "ref", True, "the vendor's job / file / shipment reference"),
    ("Invoice", "commercial_invoice_number", "ref", True, "the shipper's commercial invoice number(s)"),
    ("Invoice", "shipping_bill_number", "ref", True, "shipping bill / bill of entry number(s)"),
    ("Invoice", "shipping_bill_date", "date", True, "shipping bill / bill of entry date"),
    ("Invoice", "currency", "text", False, "the currency the invoice total is in"),
    ("Shipment", "shipper", "text", False, "shipper / consignor"),
    ("Shipment", "consignee", "text", False, "consignee"),
    ("Shipment", "incoterm", "text", False, "FOB, CIF, DAP, EXW ..."),
    ("Shipment", "port_of_loading", "text", False, "port / airport of loading or origin"),
    ("Shipment", "port_of_discharge", "text", False, "port / airport of discharge or destination"),
    ("Shipment", "place_of_delivery", "text", False, "final place of delivery"),
    ("Shipment", "vessel_voyage", "text", False, "vessel and voyage, or flight"),
    ("Shipment", "etd", "date", False, "departure date"),
    ("Shipment", "eta", "date", False, "arrival date"),
    ("Shipment", "master_bl_number", "ref", False, "master / ocean bill of lading number"),
    ("Shipment", "packages", "text", False, "number and kind of packages"),
    ("Shipment", "net_weight", "text", False, "net weight with its unit"),
    ("Shipment", "chargeable_weight", "text", False, "chargeable weight with its unit"),
    ("Shipment", "volume", "text", False, "volume / measurement, e.g. '62.896 CBM'"),
    ("Shipment", "container_type", "text", True, "container size and type, e.g. '40HC'"),
    ("Shipment", "goods_description", "text", True, "description of the goods"),
    ("Shipment", "hs_code", "text", True, "HS code of the goods"),
    ("Totals", "taxable_total", "amount", False, "total before tax (sub total, net total, taxable value)"),
    ("Totals", "cgst_total", "amount", False, "total CGST"),
    ("Totals", "sgst_total", "amount", False, "total SGST"),
    ("Totals", "igst_total", "amount", False, "total IGST"),
    ("Totals", "invoice_total", "amount", False, "grand total payable including tax"),
    ("Totals", "amount_in_words", "text", False, "the total written in words"),
    ("Payment", "bank_name", "text", False, "the vendor's bank"),
    ("Payment", "bank_account_number", "ref", False, "the vendor's bank account number"),
    ("Payment", "bank_ifsc", "ifsc", False, "IFSC code"),
    ("Payment", "bank_swift", "text", False, "SWIFT / BIC code"),
    ("E-invoice", "irn", "irn", False, "invoice reference number (64 hex characters)"),
    ("E-invoice", "ack_number", "ref", False, "e-invoice acknowledgement number"),
    ("E-invoice", "ack_date", "date", False, "e-invoice acknowledgement date"),
]
SPEC = {d[1]: d for d in DETAILS}
CHECKS = {
    "gstin": re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$"),
    "pan": re.compile(r"^[A-Z]{5}\d{4}[A-Z]$"),
    "ifsc": re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$"),
    "irn": re.compile(r"^[0-9a-f]{64}$"),
    "email": re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"),
    "cin": re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$"),
}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["details"],
    "properties": {"details": {"type": "array", "maxItems": 120, "items": {
        "type": "object", "additionalProperties": False, "required": ["detail", "value", "line"],
        "properties": {"detail": {"type": "string", "enum": [d[1] for d in DETAILS]},
                       "value": {"type": "string"}, "line": {"type": "string"}}}}},
}
SYSTEM = (
    "You arrange information that was already extracted from a freight / logistics invoice. The extracted items are "
    "listed by the printed line they came from: [L12] item | item | item. An item is either 'label: value' or just "
    "a value.\n"
    "For every named detail that is among the items return {\"details\": [{\"detail\", \"value\", \"line\"}]}.\n"
    "Rules:\n"
    "- value: copy it exactly, character for character, from an item of that line. Do not reword, translate, "
    "complete or reformat. Only the value, not its label.\n"
    "- line: the id of the line the item is on, e.g. L12.\n"
    "- Only details that are among the items. Never guess; leave a detail out when no item carries it.\n"
    "- The vendor is the company that issued the invoice (letterhead, 'For <company>'); the customer is the party it "
    "is billed to ('Bill To', 'Customer', 'Party').\n"
    "- An address: one entry per line, in order, all with the same detail.\n"
    "- A detail that may have several values (shipping bills, commercial invoices, POs): one entry each.\n"
    "- The printed labels can be wrong or missing: judge by the value and where it sits.\n"
    "- Do not return the invoice number, invoice date, container numbers, house bill of lading, air waybill numbers, "
    "gross weight, exchange rates or the charge lines: they are handled elsewhere.\n"
    "Details:\n" + "\n".join(f"- {d[1]}: {d[4]}" for d in DETAILS)
)
MODEL_NAME = "Qwen3-VL-8B-Instruct (base, no adapter)"
MAX_LINES = int(os.environ.get("ORGANIZER_MAX_LINES", "260"))


def _line_no(lid: str) -> int:
    m = re.search(r"\d+", lid or "")
    return int(m.group(0)) if m else 0


def _listing(facts: list[dict]) -> tuple[str, dict]:
    """The extracted items as the model sees them, and {line id: [items]} for grounding."""
    by_line: dict[str, list[dict]] = {}
    for f in facts:
        if f.get("label") == "(column title)" or not str(f.get("value") or "").strip():
            continue
        by_line.setdefault(f["line"], []).append(f)
    rows = []
    for lid in sorted(by_line, key=_line_no)[:MAX_LINES]:
        items = sorted(by_line[lid], key=lambda f: f["bbox"][0])
        cells = [((f["label"] + ": ") if f.get("label") else "") +
                 re.sub(r"\s+", " ", f["value"]).strip()[:200] for f in items]
        rows.append(f"[{lid}] " + " | ".join(cells))
    return "\n".join(rows), by_line


def _call(listing: str) -> tuple[list[dict], dict]:
    from .pipeline import chat

    # the reader's own server (pool or llama.cpp); with llama.cpp the invoice adapter is switched off for this task
    lora = {} if os.environ.get("READER_BACKEND", "pool") == "pool" else {"lora": [{"id": 0, "scale": 0.0}]}
    text = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Extracted items:\n" + listing}],
                os.environ.get("ORGANIZER_MODEL"), SCHEMA, int(os.environ.get("ORGANIZER_MAX_TOKENS", "3000")),
                float(os.environ.get("ORGANIZER_TIMEOUT", "300")), extra=lora)
    out = {}
    try:
        items = json.loads(text).get("details", [])
    except json.JSONDecodeError:                       # cut off by max_tokens: keep the complete entries
        items = [json.loads(m) for m in re.findall(r"\{[^{}]*\}", text) if '"detail"' in m]
    return items, out.get("usage", {})


def _normalize(kind: str, value: str) -> tuple[str, bool]:
    """(normalized form, passed its check)."""
    v = value.strip().strip(".,;:")
    if kind == "date":                                 # "31-Aug-26 08:17": the date part
        v = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s?[AP]M)?$", "", v, flags=re.IGNORECASE)
    if kind in CHECKS:
        compact = re.sub(r"\s", "", v)
        compact = compact if kind in ("irn", "email") else compact.upper()
        return compact, bool(CHECKS[kind].match(compact))
    if kind == "date":
        iso = normalize_date(v)
        return iso, bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso))
    if kind == "ref":                                  # a reference number: has a digit, is not a sentence
        return v, bool(re.search(r"\d", v)) and len(v) >= 3 and len(v.split()) <= 6
    if kind == "amount":
        m = re.search(r"-?\d[\d,]*\.?\d*", v)
        if not m:
            return v, False
        return m.group(0).replace(",", ""), True
    return v, bool(v)


def _squash(t: str) -> str:
    return re.sub(r"\s+", " ", str(t)).strip()


def _key(t: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(t).lower())


def _find(value: str, items: list[dict]) -> list[dict]:
    """The extracted item(s) that contain `value`: one item, or a run of neighbours on a line that spell it out."""
    want = _key(value)
    if not want:
        return []
    for f in items:
        if want in _key(f["value"]):
            return [f]
    for i in range(len(items)):                         # a value printed across several items of one line
        acc = ""
        for j in range(i, min(len(items), i + 6)):
            if items[j]["line"] != items[i]["line"]:
                break
            acc += _key(items[j]["value"])
            if want == acc or (want in acc and j > i):
                return items[i:j + 1]
            if len(acc) > len(want) + 40:
                break
    return []


def organize(facts: list[dict], index: dict[str, Line] | None = None) -> dict:
    """Arrange the extracted items. Returns {"details": [...], "model", "seconds", "proposed", "dropped": [...]}."""
    t0 = time.time()
    listing, by_line = _listing(facts)
    if not listing:
        return {"details": [], "model": MODEL_NAME, "seconds": 0.0, "proposed": 0, "dropped": [], "tokens": {}}
    raw, usage = _call(listing)
    order = sorted(by_line, key=_line_no)
    everything = [f for lid in order for f in sorted(by_line[lid], key=lambda f: f["bbox"][0])]
    out, dropped, seen = [], [], set()
    for it in raw:
        key, value = it.get("detail"), _squash(it.get("value") or "")
        lid = str(it.get("line") or "").strip("[] ")
        if key not in SPEC or not value:
            continue
        group, _, kind, many, _ = SPEC[key]
        src, how = [], ""
        if lid in by_line:
            src, how = _find(value, sorted(by_line[lid], key=lambda f: f["bbox"][0])), "in an extracted item of the cited line"
            if not src:                                    # the model is often one line off
                k = order.index(lid)
                near = [f for x in order[max(0, k - 2):k + 3] for f in sorted(by_line[x], key=lambda f: f["bbox"][0])]
                src, how = _find(value, near), "in an extracted item next to the cited line"
        if not src:
            src, how = _find(value, everything), "in an extracted item elsewhere on the document"
        if not src:
            dropped.append({"detail": key, "value": value, "line": lid, "why": "not in any extracted item"})
            continue
        whole = " ".join(_squash(f["value"]) for f in src)
        at = whole.lower().find(value.lower())
        shown = whole[at:at + len(value)] if at >= 0 else (whole if len(src) > 1 else value)
        bbox = [min(f["bbox"][0] for f in src), min(f["bbox"][1] for f in src),
                max(f["bbox"][2] for f in src), max(f["bbox"][3] for f in src)]
        if len(src) == 1 and _key(shown) != _key(src[0]["value"]) and index and src[0].get("line") in index:
            from .pipeline import locate                   # the box of just that part of the item

            inside = [h for h in locate(shown, [index[src[0]["line"]]]) if h["match"] == "exact"
                      and h["bbox"][0] >= bbox[0] - 2 and h["bbox"][2] <= bbox[2] + 2]
            if inside:
                bbox = inside[0]["bbox"]
        sig = (key, _key(shown))
        if sig in seen or (not many and any(o["detail"] == key for o in out)):
            continue                                       # a repeat, or a second value for a single-valued detail
        seen.add(sig)
        normalized, ok = _normalize(kind, shown)
        out.append({"group": group, "detail": key, "value": shown, "normalized": normalized, "type": kind, "valid": ok,
                    "page": src[0]["page"], "bbox": [round(float(v), 2) for v in bbox], "line": src[0]["line"],
                    "items": [f["seq"] for f in src], "printed_label": src[0].get("label", ""),
                    "where": src[0].get("section", ""), "grounding": how})
    rank = {d[1]: n for n, d in enumerate(DETAILS)}
    out.sort(key=lambda o: rank[o["detail"]])              # stable: several values keep their printed order
    for n, o in enumerate(out, 1):
        o["seq"] = n
    return {"details": out, "model": MODEL_NAME, "seconds": round(time.time() - t0, 2), "proposed": len(raw),
            "dropped": dropped, "tokens": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens")}}
