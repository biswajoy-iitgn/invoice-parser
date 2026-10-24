"""Evidence-grounding, strict fixed-schema serialization, and conservative confidence routing.

Grounding is format-aware: normalized values (ISO dates, plain-decimal amounts)
are compared against the evidence in a normalized space instead of by naive
substring, which systematically blanked every reformatted value. Numeric
support requires FULL token equality against a number printed in the source,
so the guard stays strict against hallucinated amounts.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from .schema import CHARGE_FIELDS, OUTPUT_FIELDS

DATE_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y",
    "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y", "%b %d, %Y", "%d %b %y",
]

NUMBER_RE = re.compile(r"-?[\d,]+\.\d+|-?\d[\d,]*")
DATE_TOKEN_RE = re.compile(
    r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}\s+[A-Za-z]{3,9}\s*,?\s*\d{2,4}\b"
    r"|\b\d{1,2}[-/.][A-Za-z]{3,9}[-/.]\d{2,4}\b"      # 31-Aug-26, 22-Aug-2026
    r"|\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b"          # Aug 22, 2026
)
CURRENCY_WORDS = {"inr", "usd", "eur", "gbp", "aed", "rs", "rs.", "inr.", "$"}


def normalize_date(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip().replace(",", ", ")).replace(" ,", ",")
    s = re.sub(r",\s+", ", ", s)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw.strip()


def fixed_json(values: Mapping) -> dict[str, str]:
    """Drop unknown keys and guarantee every public field is a string."""
    return {key: str(values.get(key) or "") for key in OUTPUT_FIELDS}


def _norm_num(token: str) -> str:
    t = token.replace(",", "").strip()
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


def evidence_numbers(evidence: str) -> set[str]:
    return {_norm_num(m.group(0)) for m in NUMBER_RE.finditer(evidence or "")}


def supported_number(value: str, evidence: str) -> bool:
    """True iff the value's number equals a number printed in the evidence."""
    m = NUMBER_RE.search(value or "")
    if not m:
        return False
    return _norm_num(m.group(0)) in evidence_numbers(evidence)


def supported_date(value: str, evidence: str) -> bool:
    target = normalize_date(value)
    if target == value.strip() and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        return supported(value, evidence)  # unparseable: fall back to substring
    for m in DATE_TOKEN_RE.finditer(evidence or ""):
        if normalize_date(m.group(0)) == target:
            return True
    return False


def supported_rate(value: str, evidence: str) -> bool:
    """'USD 98.25, EUR 112.17': every (code, number) pair needs its rate in evidence."""
    if not (value or "").strip():
        return True
    nums = evidence_numbers(evidence)
    src = re.sub(r"\s+", " ", evidence or "").casefold()
    found = False
    for m in NUMBER_RE.finditer(value):
        found = True
        if _norm_num(m.group(0)) not in nums:
            return False
        wm = re.search(r"([A-Za-z$]+)\.?\s*$", value[:m.start()])
        code = wm.group(1).casefold() if wm else ""
        if code and code in CURRENCY_WORDS and code not in src:
            return False
    return found


def supported(value: str, evidence: str) -> bool:
    value = re.sub(r"\s+", " ", value or "").strip()
    if not value:
        return True
    source = re.sub(r"\s+", " ", evidence or "").casefold()
    return re.sub(r"\s+", " ", value).casefold() in source


_BL_PREFIX_RE = re.compile(r"^(?:H\s*/?\s*B\s*/?\s*L|M\s*/?\s*B\s*/?\s*L|house|master)\b[\s:.]*", re.I)


def supported_bl(value: str, evidence: str) -> bool:
    """Joined 'HBL <house>; MBL <master>': every bare number must be printed."""
    parts = [p.strip() for p in re.split(r"[;,]", value or "") if p.strip()]
    if not parts:
        return True
    return all(supported(_BL_PREFIX_RE.sub("", p).strip() or p, evidence) for p in parts)


def ground_fields(values: Mapping, evidence: str,
                  verified_sums: set[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Blank values that are not printed in the evidence.

    `verified_sums`: charge fields whose value is the sum of several line amounts that were each
    found printed; the total itself is not printed anywhere, so it is supported by construction."""
    out = fixed_json(values)
    unsupported = []
    for key, value in out.items():
        if not value:
            continue
        if key == "Invoice Date":
            ok = supported_date(value, evidence)
        elif key in CHARGE_FIELDS:
            ok = supported_number(value, evidence) or key in (verified_sums or set())
        elif key == "Ex Rate":
            ok = supported_rate(value, evidence)
        elif key == "Bill of Lading / HBL":
            ok = supported_bl(value, evidence)
        else:
            ok = supported(value, evidence)
        if not ok:
            unsupported.append(key)
            out[key] = ""
    return out, unsupported
