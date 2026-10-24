"""Shared field mapping for the MinerU-4 / PaddleOCR-VL sheet engines.

Both engines reduce a parsed document to the same intermediate shape::

    kv:    printed label -> value, e.g. {"Document No.": "7044012289"}
    items: charge lines, e.g. [{"description": "Seal Fee FCL P.O.L", "amount_inr": 1120.0}]
    text:  full plain text, for regex fallbacks (container number, exchange rates)

and this module maps that onto exactly :data:`schema.OUTPUT_FIELDS`
(missing values are ``""``, charge totals are ``"1234.56"`` like the v2 pipeline).
Pure functions only: no servers, no models, safe to unit test.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List

from .schema import CHARGE_CATEGORIES, OUTPUT_FIELDS

# Checked after the schema patterns: wording the glossary meaning covers but has
# no regex for. (Schema-owned wording such as "carrier charges origin" must win.)
_LOCAL_ALIASES = [
    (re.compile(r"\bhaulage\b", re.I), "Transportation"),  # delivery / on-carriage haulage
    (re.compile(r"\bhamali\b", re.I), "Handling Charges"),  # manual labour / handling
]

# Rows that are totals or tax, never a charge line of their own.
_NON_CHARGE = re.compile(
    r"net total|gross total|total amount|subtotal|amount in words|"
    r"\b[sc]gst\b|\butgst\b|\bigst\b|tax\s+inr|grand total|"
    r"\btotal\b",
    re.I,
)


def is_total_row(description: str) -> bool:
    """True when a parsed row is a total/tax footer, not a charge line."""
    return bool(_NON_CHARGE.search(description or ""))

_CONTAINER_RE = re.compile(r"\b([A-Z]{4}\d{7})(?![0-9])")
_KG_RE = re.compile(r"([\d,]+\.\d+)\s*kg", re.I)
_SEA_RE = re.compile(r"\b(vessel|ocean|fcl|lcl|container|port of loading|bill of lading)\b", re.I)
_AIR_RE = re.compile(r"\b(airway|air waybill|air freight|flight|airport)\b", re.I)


def transport_mode(text: str) -> str:
    """sea | air | "" from document wording (sea cues win: ocean BLs print MAWB labels too)."""
    if _SEA_RE.search(text or ""):
        return "sea"
    if _AIR_RE.search(text or ""):
        return "air"
    return ""


def _bare_freight(description: str) -> bool:
    """True when the line means just 'freight' plus currency/amount noise."""
    t = re.sub(r"[@(),]", " ", description or "")
    t = re.sub(r"\b(usd|eur|inr)\b", " ", t, flags=re.I)
    t = re.sub(r"[\d.]+", " ", t)
    return [w.lower() for w in t.split()] == ["freight"]


def empty_fields() -> Dict[str, str]:
    return {f: "" for f in OUTPUT_FIELDS}


def classify_charge(description: str, mode: str = "") -> str:
    """Map one printed charge line onto a schema category (default Other Charges)."""
    desc = description or ""
    # Some houses glue their group header onto the charge ("OTHER CHARGESTHC",
    # "OTHER CHARGESENS"): classify the real charge behind the prefix.
    m = re.match(r"(?i)other\s+charges?\s*(.+)$", desc)
    if m and m.group(1).strip():
        desc = m.group(1).strip()
    for name, spec in CHARGE_CATEGORIES.items():
        for pat in spec["patterns"]:
            if re.search(pat, desc, re.I):
                return name
    if _bare_freight(desc):
        return "Sea Freight" if mode == "sea" else "Air Freight" if mode == "air" else "Other Charges"
    for rx, target in _LOCAL_ALIASES:
        if rx.search(desc):
            return target
    return "Other Charges"


def charges_from_items(items: List[dict], mode: str = "") -> Dict[str, str]:
    """Sum charge lines per category. Returns {category: "1234.56"} (non-empty only)."""
    totals: Dict[str, float] = {}
    for it in items or []:
        desc = str(it.get("description") or "")
        try:
            amt = float(str(it.get("amount_inr") or "").replace(",", ""))
        except (TypeError, ValueError):
            continue
        if not amt or is_total_row(desc):
            continue
        cat = classify_charge(desc, mode)
        totals[cat] = round(totals.get(cat, 0.0) + amt, 2)
    return {cat: f"{total:.2f}" for cat, total in totals.items()}


def line_items_for(items: List[dict], mode: str = "") -> List[dict]:
    """Detail-view charge lines: total/tax footers dropped, categories attached."""
    out: List[dict] = []
    for it in items or []:
        desc = str(it.get("description") or "")
        if is_total_row(desc):
            continue
        try:
            amt = round(float(str(it.get("amount_inr") or "").replace(",", "")), 2)
        except (TypeError, ValueError):
            continue
        if not amt:
            continue
        out.append({"description": desc, "amount_inr": amt,
                    "category": classify_charge(desc, mode),
                    "category_confidence": 1.0 if desc else 0.0,
                    "tax_code": it.get("tax_code", "")})
    return out


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9/ ]+", "", (label or "").lower()).strip()


def container_from_text(text: str) -> str:
    """First container-like token, preferring a line that says container."""
    m = re.search(r"CONTAINERS?\s*:?\s*([A-Z]{4}\d{6,7})", text or "")
    if m:  # glued label: "CONTAINERSFFAU4143091 (40HC ...)"
        return m.group(1)
    cands = []
    for ln in (text or "").splitlines():
        for mm in _CONTAINER_RE.finditer(ln):
            start = mm.start()
            if "ifsc" in ln[max(0, start - 15):start].lower():
                continue  # bank IFSC, e.g. "IFSC: HSBC0400002"
            cands.append((mm.group(1), ln))
    for num, ln in cands:
        if "container" in ln.lower():
            return num
    return cands[0][0] if cands else ""


_SPLIT_LABELS = sorted([
    "Document No.", "Invoice No.", "Customer No.", "Service Date",
    "M B/L No. / MAWB No.", "H B/L No./HAWB No.", "MAWB No.", "HAWB No.",
    "M B/L No.", "H B/L No.", "Place of Supply", "Freight Terms",
    "Vessel/ Flight", "Our Reference", "Your Reference", "Date",
], key=len, reverse=True)


def split_colon(cell: str) -> tuple[str, str]:
    """Split a glued "Label: value" cell ("Document No: AMD1142005")."""
    m = re.match(r"([^:<>|]{1,45}?):\s*(.+)$", (cell or "").strip())
    if m and re.search(r"[A-Za-z]", m.group(1)) and m.group(2).strip():
        return m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip()
    return "", ""


def first_segment(cell: str) -> str:
    """First line of a multi-line cell ("B/L Fee Export\\n00996719 Y" -> "B/L Fee Export")."""
    parts = [p.strip() for p in re.split(r"\\n|\n", cell or "")]
    return next((p for p in parts if p), "")


def split_known_label(cell: str) -> tuple[str, str]:
    """Split a glued "Label value" cell ("H B/L No./HAWB No. 70430050554").

    Returns (label, value) or ("", "") when the cell does not start with a
    looked-up label followed by a value.
    """
    t = (cell or "").strip()
    for lab in _SPLIT_LABELS:
        if t.startswith(lab) and len(t) > len(lab) + 1:
            return lab, t[len(lab):].strip()
    return "", ""


# Labels that parsers glue onto one line/cell without newlines
# ("Invoice No : ERIA261452Invoice Date : 08 Sep 2026Terms : Net 30").
_GLUED_LABELS = sorted([
    "Party Invoice No", "Place Of Supply", "Place of Supply", "Shipment Type",
    "Clearance at", "Container No", "Shipper", "Consignee", "Buyer",
    "SB/BE No", "SB/BE Date", "Shipment No.", "Shipment No",
    "Document No.", "Invoice No.", "Invoice No", "Invoice Date", "Customer No.",
    "Service Date", "M B/L No.", "H B/L No.", "MBL No.", "MBL No", "HBL No.",
    "HBL No", "MAWB No.", "MAWB No", "HAWB No.", "HAWB No",
    "Freight Terms", "Due Date", "Job No", "Terms", "Bill To", "Ship To",
    "Date",
], key=len, reverse=True)
_GLUED_RE = re.compile("|".join(re.escape(l) for l in _GLUED_LABELS), re.I)

# Labels whose values are reference numbers: a glued address tail is cut off.
_DOC_LABELS = {"Document No.", "Invoice No.", "Invoice No", "MBL No.", "MBL No",
               "HBL No.", "HBL No", "MAWB No.", "MAWB No", "HAWB No.", "HAWB No",
               "M B/L No.", "H B/L No.", "Job No", "Shipment No.", "Shipment No",
               "SB/BE No", "Container No", "Container No."}

# Labels whose values are dates: keep the first date-shaped token.
_DATE_LABELS = {"Invoice Date", "Date", "Document Date", "Service Date",
                "Due Date", "SB/BE Date"}

# Lowercased mirrors: glued matching is case-insensitive ("HBL NO:").
_DOC_NORM = {l.lower() for l in _DOC_LABELS}
_DATE_NORM = {l.lower() for l in _DATE_LABELS}


def glued_kv(text: str) -> Dict[str, str]:
    """kv pairs from glued "Label value Label value" runs (no newlines).

    Finds every known label, slices the value between consecutive labels.
    Values over 120 chars are prose, not values, and are dropped.
    """
    kv: Dict[str, str] = {}
    # A glued label often starts right after the previous value ("ERIA261452Invoice
    # Date", "CHANGODARInvoice Date"); labels are Title Case, so only a preceding
    # *lowercase* letter means a mid-word false hit ("Update").
    matches = [(m.group(0), m.start(), m.end()) for m in _GLUED_RE.finditer(text or "")
               if m.start() == 0 or not (text[m.start() - 1].isalpha()
                                         and text[m.start() - 1].islower())]
    for i, (lab, _, end) in enumerate(matches):
        raw = text[end:matches[i + 1][1] if i + 1 < len(matches) else len(text)]
        val = re.sub(r"^[\s:|\-–—]+", "", raw).strip()
        val = re.sub(r"\s+", " ", val)
        lab_norm = lab.lower()
        if lab_norm in _DOC_NORM:
            # Glued value + address prose ("AMDTI26-27-0289SARKHEJ-BAVLA ROAD, ..."):
            # cut after the number when the tail looks like prose.
            m = re.search(r"(?<=\d)(?=[A-Z]{3,})", val)
            if m and re.search(r"[ ,]", val[m.start():]):
                val = val[:m.start()].strip()
        if lab_norm in _DATE_NORM:
            # Glued value + place prose ("22-Aug-2026AHMEDABAD - 382213"):
            # keep the first date-shaped token.
            m = re.search(r"\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", val)
            if m:
                val = m.group(0)
        if val and len(val) <= 120:
            kv.setdefault(lab, val)
    return kv


def clean_vendor(line: str) -> str:
    """Vendor line as printed, cut before trailing tax-id noise."""
    t = re.split(r"\s+(?:GST\b|GSTIN|GST Identification|PAN\b|PAN:|CIN\b|ORIGINAL\b)",
                 line or "", maxsplit=1)[0].strip()
    return re.sub(r"^([A-Z])(?=[A-Z][a-z])", "", t)  # stray cap glued to the name: SJeena


def weight_from_text(text: str, container: str = "", segments: list | None = None) -> str:
    """Gross weight in kg: an explicit WEIGHT number first, then the largest
    quantity in the container's own segment (parsers merge "10669.000 kg /
    40.000 cbm", and whole tables flatten to one line), then any kg figure."""
    if not text:
        return ""
    units = segments if segments else text.splitlines()
    for seg in units:
        m = re.search(r"WEIGHT\s*:?\s*([\d,]+\.\d+)", seg, re.I)
        if m:
            # "Actual Weight Actual Volume ..." is a header, not a value
            before = seg[:m.start()].strip().lower()
            if not before.endswith("actual"):
                return f"{float(m.group(1).replace(',', '')):.3f} kg"
    if container:
        # Numbers must sit next to the container mention: parsers sometimes
        # flatten whole tables into one segment, so only look ±120 chars around it
        # (a totals figure pages away is not the weight).
        for s in units:
            i = s.find(container)
            if i < 0:
                continue
            window = s[max(0, i - 120):i + 120]
            nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+\.\d+", window)]
            if nums:
                return f"{max(nums):.3f} kg"
        seg = next((s for s in units if "high cube" in s.lower() or "40hc" in s.lower()), "")
        nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+\.\d+", seg)]
        if nums:
            return f"{max(nums):.3f} kg"
    w = _KG_RE.search(text)
    return f"{w.group(1).replace(',', '')} kg" if w else ""


def headers_from_kv(kv: Dict[str, str], text: str = "", vendor: str = "",
                    segments: list | None = None) -> Dict[str, str]:
    """Map printed label/value pairs onto the 8 header fields (missing values are "")."""
    norm = {_norm(k): (k, v) for k, v in (kv or {}).items()}

    def get(*labels: str) -> str:
        for lab in labels:
            hit = norm.get(_norm(lab))
            if hit and str(hit[1]).strip():
                return str(hit[1]).strip()
        for lab in labels:  # suffix: "Document Date" satisfies "Date"
            nl = _norm(lab)
            for key, (orig, val) in norm.items():
                if key != nl and key.endswith(" " + nl) and str(val).strip():
                    return str(val).strip()
        return ""

    def find(*needles: str) -> str:
        for lab, (orig, val) in norm.items():
            if str(val).strip() and all(n in lab for n in needles):
                return str(val).strip()
        return ""

    inv = (get("Document No.", "Document No", "Invoice No.", "Invoice No", "Invoice Number")
           or find("invoice", "no"))
    if not inv:  # heading-embedded number: "## TAX INVOICE IN1AMD261031252"
        # (a banner followed by other caps, e.g. "TAX INVOICE KN TRACKING",
        # is not a number: the token must contain a digit)
        m = re.search(r"TAX INVOICE[ \t]+([A-Z0-9][A-Z0-9-]*)", text or "")
        if m and re.search(r"\d", m.group(1)) and len(m.group(1)) >= 5:
            inv = m.group(1)
    if not inv:  # "## INV NO : IVJCAMD270007254"
        m = re.search(r"INV\s*NO\.?\s*:?\s*([A-Z0-9][A-Z0-9-]*)", text or "")
        inv = m.group(1) if m else ""
    mawb = (get("M B/L No. / MAWB No.", "MAWB No.", "MAWB No", "M B/L No.", "M B/L No",
                 "MBL No.", "MBL No")
            or find("mawb"))
    if not mawb:
        m = re.search(r"OCEAN BILL OF LADING\s*([A-Z0-9]+)", text or "")
        mawb = m.group(1) if m else ""
    hawb = (get("H B/L No./HAWB No.", "HAWB No.", "HAWB No", "H B/L No.", "H B/L No",
                 "HBL No.", "HBL No")
            or find("hawb"))
    if not hawb:
        m = re.search(r"HOUSE BILL(?: OF LADING)?\s*([A-Z0-9]+)", text or "")
        hawb = m.group(1) if m else ""
    if not hawb:  # "HBL NO: BANQ1075870985"
        m = re.search(r"HBL\s*NO\.?\s*:?\s*([A-Z0-9][A-Z0-9-]*)", text or "")
        hawb = m.group(1) if m else ""
    if not hawb:  # forwarder ref: "BL No : 5836209" (never inside "MBL No")
        m = re.search(r"(?<!M)BL\s*No\.?\s*:?\s*([A-Z0-9][A-Z0-9-]*)", text or "")
        hawb = m.group(1) if m else ""
    # "Document No/Date" is this invoice's own number/date; a bare "Invoice
    # Date" can belong to a referenced preceding document, so it comes second.
    dt = get("Document Date", "Invoice Date", "Date") or find("invoice", "date")
    if not dt:
        m = re.search(r"DATE\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text or "")
        dt = m.group(1) if m else ""
    if inv and not dt:  # glued number+date: "AMD1142005 13.08.2026"
        m = re.match(r"(.+?)\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})$", inv)
        if m:
            inv, dt = m.group(1).strip(), m.group(2)
    out = {
        "Invoice No": inv,
        "Invoice Date": dt,
        "MAWB No": mawb,
        "HAWB No": hawb,
        "Vendor Name": clean_vendor(vendor),
    }
    if mawb and hawb:
        out["Bill of Lading / HBL"] = f"HBL {hawb}; MBL {mawb}"
    else:
        out["Bill of Lading / HBL"] = hawb or mawb
    out["Container Number"] = (container_from_text(text or "")
                                 or get("Container No", "Container No.", "Container"))
    out["WEIGHT"] = weight_from_text(text or "", out["Container Number"], segments)
    for k in out:
        out[k] = out[k].strip().rstrip(",;")
    return out


# Foreign currencies printed with INR rates on freight invoices.
_RATE_CURRENCIES = ("USD", "EUR", "GBP", "AED", "SGD", "CNY", "JPY", "CHF", "CAD", "AUD")


def exchange_rate(text: str) -> str:
    """e.g. "USD 96.12000; EUR 112.17000" ("" when nothing is printed)."""
    found = []
    for cur in _RATE_CURRENCIES:
        m = (re.search(rf"{cur}[^@\n]{{0,40}}@\s*([\d.]+)", text or "", re.I)          # USD 7,225.00 @ 98.25
             or re.search(rf"{cur}\s+exchange\s+rate\s*:\s*([\d.]+)", text or "", re.I)
             or re.search(rf"exchange\s+rate\s*:?\s*{cur}\s*[-–]?\s*INR\s*:?\s*([\d.]+)", text or "", re.I)  # EXCHANGE RATE:USD - INR : 98.6034
             or re.search(rf"{cur}\s*[-–]\s*INR\s*:?\s*([\d.]+)", text or "", re.I)
             # bare "CUR 96.1200": 4+ decimals means a rate, never an amount
             or re.search(rf"{cur}\s+(?![\d,]*,)(\d+\.\d{{4,}})", text or "", re.I))
        if m:
            found.append(f"{cur} {m.group(1)}")
    return "; ".join(found)


def kv_from_lines(lines: List[str]) -> Dict[str, str]:
    """kv pairs from plain "Label: value" lines (docx-style parses)."""
    kv: Dict[str, str] = {}
    for ln in lines or []:
        t = re.sub(r"^#+\s*", "", (ln or "").strip())
        if not t or "|" in t or "<" in t or t.startswith("<!--") or len(t) > 160:
            continue
        m = re.match(r"([^:<>|]{1,60}?):\s*(.+)$", t)
        if m and re.search(r"[A-Za-z]", m.group(1)) and m.group(2).strip():
            kv.setdefault(m.group(1).strip(), m.group(2).strip())
    return kv


def generic_html_kv_items(rows: List[list]) -> tuple[Dict[str, str], List[dict]]:
    """kv + charge items from plain HTML rows shared by both reducers.

    Two-cell rows are label/value pairs; ``[SAC: ...]`` rows are charge lines
    whose last amount cell is the ex-tax charge in INR; SR ledgers
    (serial, description, SAC, amounts...) and S.No grids (serial,
    description, assessable amount, tax split...) book their first non-zero
    amount.
    """
    kv: Dict[str, str] = {}
    items: List[dict] = []
    for cells in rows or []:
        if not cells:
            continue
        if (len(cells) >= 8 and re.fullmatch(r"\d{1,3}", (cells[0] or "").strip())
                and re.search(r"[A-Za-z]{3,}", first_segment(cells[1] or ""))):
            # GST-style charge grid (# | description | units | HSN | qty | rate |
            # CGST% | CGST | SGST% | SGST | amount/total): book the ex-tax charge.
            # When the last amount equals an earlier amount plus its two taxes
            # (Taxable + CGST + SGST = Total), the earlier one is the charge and
            # the last cell is the tax-inclusive total.
            desc = first_segment(cells[1])
            amts = []
            for c in cells[2:]:
                # Glued tax+rate cells ("1,377.009.00%") contribute their amounts
                # too: the tax part is what the base+2*tax check below needs.
                for m in re.finditer(r"[\d,]+\.\d{2}", c or ""):
                    try:
                        v = float(m.group(0).replace(",", ""))
                    except ValueError:
                        continue
                    if v:
                        amts.append(v)
            if desc and amts:
                # The last cell may be the tax-inclusive total while an earlier
                # cell is the ex-tax charge: last == base + 2*tax (CGST == SGST).
                # Rate cells ("9.00") pollute fixed positions, so search by value:
                # the largest amount B with (last - B) / 2 present in the row.
                # Otherwise the first sizable amount wins (SR ledgers print the
                # taxable value before its tax split, ending on 0.00/empty).
                amt = amts[-1]
                for b in sorted(set(amts[:-1]), reverse=True):
                    t = (amts[-1] - b) / 2
                    if t > 0 and any(abs(v - t) < 0.05 for v in amts):
                        amt = b
                        break
                else:
                    big = [v for v in amts if v > 100]
                    amt = big[0] if big else amts[0]
                items.append({"description": desc, "amount_inr": amt})
            continue
        if (len(cells) >= 3 and re.fullmatch(r"\d{1,3}", (cells[0] or "").strip())
                and re.search(r"[A-Za-z]{3,}", first_segment(cells[1] or ""))
                and not re.fullmatch(r"\d{6}", first_segment(cells[2] or "").strip())
                and re.search(r"[\d,]+\.\d{2}", " ".join(cells[2:]))):
            desc = first_segment(cells[1])
            amt = 0.0
            for c in cells[2:]:
                if "%" in (c or ""):
                    continue
                m = re.search(r"([\d,]+\.\d{2})", c or "")
                if m:
                    try:
                        v = float(m.group(1).replace(",", ""))
                    except ValueError:
                        continue
                    if v:
                        amt = v
                        break
            if desc and amt:
                items.append({"description": desc, "amount_inr": amt})
            continue
        if (len(cells) >= 4 and re.fullmatch(r"\d{1,3}", (cells[0] or "").strip())
                and re.search(r"[A-Za-z]{3,}", cells[1] or "")
                and re.fullmatch(r"\d{6}", (cells[2] or "").strip())):
            desc = re.sub(r"\s+", " ", cells[1]).strip()
            amt = 0.0
            for c in cells[3:]:
                if "%" in (c or ""):
                    continue
                m = re.fullmatch(r"\s*([\d,]+\.\d{2})\s*", c or "")
                if m:
                    try:
                        v = float(m.group(1).replace(",", ""))
                    except ValueError:
                        continue
                    if v:
                        amt = v
                        break
            if desc and amt:
                items.append({"description": desc, "amount_inr": amt})
            continue
        if re.match(r"\[SAC:", (cells[0] or "").strip()) and len(cells) >= 3:
            amts = re.findall(r"[\d,]+\.\d{2}", " ".join(cells[2:]))
            if not amts:
                continue
            try:
                amt = float(amts[-1].replace(",", ""))
            except ValueError:
                continue
            desc = re.sub(r"\s+", " ", cells[1]).strip()
            tax = next((re.sub(r"\s+", "", c or "").strip(",")
                        for c in cells[2:] if "%" in (c or "")), "")
            if desc:
                items.append({"description": desc, "amount_inr": amt, "tax_code": tax})
        elif len(cells) == 2 and cells[0].strip() and cells[1].strip() and len(cells[0]) < 60:
            kv.setdefault(cells[0].strip(), re.sub(r"\s+", " ", cells[1]).strip())
            # Glued "Label: value" inside either cell ("Supply Type: TAX" beside
            # "Document No: AMD1142005"): register the inner pairs too.
            for cell in cells:
                lab, val = split_colon(cell)
                if not lab:
                    lab, val = split_known_label(cell)
                if lab and val:
                    kv.setdefault(lab, val)
    return kv, items


def _amount(cell: str) -> float | None:
    try:
        v = float((cell or "").replace(",", "").strip() or 0)
    except ValueError:
        return None
    return v or None


def tax_info_from_grid(rows: List[list]) -> tuple[Dict[str, dict], Dict[str, str]]:
    """Page-2 tax table -> ({code: breakdown}, totals). Empty when no tax table."""
    codes: Dict[str, dict] = {}
    totals: Dict[str, str] = {}
    if not rows or len(rows) < 2:
        return codes, totals
    hdr = [(c or "").strip().lower() for c in rows[0]]
    if not hdr or hdr[0] != "tax" or not any("gross total" in h for h in hdr):
        return codes, totals
    col: Dict[str, int] = {}
    for i, h in enumerate(hdr):
        if h == "net inr":
            col.setdefault("net", i)
        elif h == "gross total inr":
            col["gross"] = i
        elif h == "texts":
            col["texts"] = i
    pairs: list = []
    cur: str = ""
    pct: int | None = None
    for i, h in enumerate(hdr):
        hh = h.replace("/", " ")
        if "utgst" in hh or "sgst" in hh or hh in ("s%",):
            cur = "sgst"
        elif "cgst" in hh:
            cur = "cgst"
        elif "igst" in hh:
            cur = "igst"
        if hh in ("%", "s%") or hh.endswith("%"):
            pct = i
        elif "tax inr" in hh and cur:
            pairs.append((cur, pct, i))
            pct = None

    def cell(row: list, i: int | None) -> str:
        return row[i].strip() if i is not None and i < len(row) and row[i] else ""

    for row in rows[1:]:
        c0 = (row[0] or "").strip()
        if re.fullmatch(r"[A-Z]", c0):
            entry: Dict[str, object] = {"cells": [c for c in row]}
            if "net" in col:
                entry["net_inr"] = _amount(cell(row, col["net"]))
            if "gross" in col:
                entry["gross_inr"] = _amount(cell(row, col["gross"]))
            if "texts" in col and cell(row, col["texts"]):
                entry["texts"] = cell(row, col["texts"])
            for name, pi, ai in pairs:
                entry[f"{name}_pct"] = cell(row, pi) or None
                entry[f"{name}_inr"] = _amount(cell(row, ai))
            codes[c0] = entry
        elif "gross total" in c0.lower():
            amts = [_amount(c) for c in row]
            amts = [a for a in amts if a]
            if amts:
                totals["gross_total_inr"] = f"{max(amts):.2f}"
    return codes, totals


_TOTAL_ROW_KEY = [
    (re.compile(r"total invoice amount|invoice total|grand total|amount payable|balance due", re.I),
     "invoice_total_inr"),
    (re.compile(r"^total(\s+inr)?\.?$", re.I), "invoice_total_inr"),
    (re.compile(r"^sub\s?total", re.I), "subtotal_inr"),
    (re.compile(r"(?:add|total).*\bcgst\b|\bcgst\b.*(?:add|total)", re.I), "cgst_inr"),
    (re.compile(r"(?:add|total).*\b(?:sgst|utgst)\b|\b(?:sgst|utgst)\b.*(?:add|total)", re.I),
     "sgst_inr"),
    (re.compile(r"(?:add|total).*\bigst\b|\bigst\b.*(?:add|total)", re.I), "igst_inr"),
]


def invoice_total_from_rows(rows: List[list]) -> Dict[str, str]:
    """Total/footer rows -> invoice_total/subtotal/cgst/sgst/igst (last amount wins).

    Only rows whose first cell is a short total/tax label qualify, so charge
    lines that merely mention tax never leak in.
    """
    out: Dict[str, str] = {}
    for cells in rows or []:
        if not cells:
            continue
        label = (cells[0] or "").strip()
        if len(label) > 40:
            continue
        for rx, key in _TOTAL_ROW_KEY:
            if rx.search(label) and key not in out:
                amts = re.findall(r"[\d,]+\.\d{2}", " ".join(c for c in cells[1:] if c))
                if amts:
                    out[key] = f"{float(amts[-1].replace(',', '')):.2f}"
                break
    return out


def totals_from_text(text: str) -> Dict[str, str]:
    """Net total + amount-in-words from anywhere in the parsed text."""
    t: Dict[str, str] = {}
    m = re.search(r"Net Total\s*([\d,]+\.\d{2})", text or "")
    if m:
        t["net_total_inr"] = f"{float(m.group(1).replace(',', '')):.2f}"
    m = re.search(r"AMOUNT IN WORDS?\s*(?:INR\s*)?(.+)", text or "", re.I)
    if m:
        t["amount_in_words"] = re.sub(r"\s+", " ", m.group(1)).strip()[:220]
    return t


def build_fields(kv: Dict[str, str], items: List[dict], text: str = "", vendor: str = "",
                 segments: list | None = None) -> Dict[str, str]:
    """Assemble the full OUTPUT_FIELDS dict from one parsed document."""
    fields = empty_fields()
    fields.update(headers_from_kv(kv, text, vendor, segments))
    fields.update(charges_from_items(items, transport_mode(text)))
    fields["Ex Rate"] = exchange_rate(text)
    return fields


def make_extra(text: str, tax_codes: Dict[str, dict], tax_totals: Dict[str, str]) -> Dict[str, dict]:
    """Shared tax/totals extra block for the three sheet reducers."""
    return {"tax_codes": tax_codes,
            "totals": {**totals_from_text(text), **tax_totals}}


def finalize_extract(engine: str, fields: Dict[str, str], items: List[dict], text: str = "",
                     extra: Dict[str, dict] | None = None, started: float = 0.0,
                     note: str = "") -> Dict[str, object]:
    """Shared audit tail for the three sheet engines' extract().

    Counts filled OUTPUT_FIELDS, routes to REVIEW below 5, and stamps timing.
    """
    extra = extra or {}
    mode = transport_mode(text or "")
    line_items = line_items_for(items, mode)
    filled = sum(1 for v in fields.values() if v)
    warnings = [note] if note else []
    if filled < 5:
        warnings.append(f"only {filled} of {len(OUTPUT_FIELDS)} fields found — review")
    return {"engine": engine, "review_route": "AUTO_ACCEPT" if filled >= 5 else "REVIEW",
            "warnings": warnings, "line_items": line_items, "needs_review": filled < 5,
            "tax_codes": extra.get("tax_codes", {}),
            "totals": {**totals_from_text(text or ""), **extra.get("totals", {})},
            "timing_sec": {"engine": round(time.time() - started, 2)}}


def docx_blocks(path: str) -> tuple[Dict[str, str], List[dict], str, str]:
    """Best-effort kv/items/text from a .docx (no layout engine involved).

    Paragraphs shaped "Label: value" become kv pairs; table rows whose last
    cell parses as a number become charge lines.
    """
    import docx

    doc = docx.Document(path)
    kv: Dict[str, str] = {}
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if ":" in t:
            lab, _, val = t.partition(":")
            if lab.strip() and val.strip() and len(lab) < 60:
                kv.setdefault(lab.strip(), val.strip())
    items: List[dict] = []
    for table in doc.tables:
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            if len(cells) < 2:
                continue
            try:
                amt = float(cells[-1].replace(",", "").replace("INR", "").strip())
            except ValueError:
                continue
            desc = " ".join(c for c in cells[:-1] if c)
            if desc:
                items.append({"description": desc, "amount_inr": amt})
    text = "\n".join([p.text or "" for p in doc.paragraphs] +
                     [" | ".join((c.text or "") for c in row.cells)
                      for table in doc.tables for row in table.rows])
    vendor = ""
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if len(t) > 4 and t == t.upper() and any(c.isalpha() for c in t):
            vendor = t.title() if t.isupper() else t
            break
    return kv, items, text, vendor
