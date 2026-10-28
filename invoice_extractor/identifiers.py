"""Validators for identifiers that carry their own proof of correctness: a check digit, a fixed shape, a known code set.

A misread character usually breaks the shape or the check digit, so a value that passes is far more likely to be right than one
that was only "found on the page". `validate(kind, value)` returns "" when the value is fine, else what is wrong.

Kinds: gstin, pan, ifsc, mobile, pin, email, vehicle, irn, hsn, container, mawb, date.
Dates parse the layouts printed on Indian paperwork (31.03.2026, 7-4-26, 2026-03-31, 31 Mar 2026) and must be plausible.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_CS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_STATES = set("AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL OD OR PB PY RJ SK TN TR TS UK UP WB BH".split())
_GST_STATES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        p = _CS.index(ch) * (1 if i % 2 == 0 else 2)
        total += p // 36 + p % 36
    return _CS[(36 - total % 36) % 36]


def parse_date(s: str) -> date | None:
    """A printed date -> date, or None. Two-digit years are 20xx. Day first, as on these documents."""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    parts = (int(m[1]), int(m[2]), int(m[3])) if m else None
    if not parts:
        m = re.fullmatch(r"(\d{1,2})[-./\s]+(\d{1,2})[-./\s]+(\d{2}|\d{4})", s)
        if m:
            y = int(m[3])
            parts = (y + 2000 if y < 100 else y, int(m[2]), int(m[1]))
    if not parts:
        m = re.fullmatch(r"(\d{1,2})[-./\s]+([A-Za-z]{3})[a-z]*[-./,\s]+(\d{2}|\d{4})", s)
        if m and m[2].lower() in _MONTHS:
            y = int(m[3])
            parts = (y + 2000 if y < 100 else y, _MONTHS[m[2].lower()], int(m[1]))
    try:
        return date(*parts) if parts else None
    except ValueError:
        return None


def validate(kind: str, value: str) -> str:
    v = (value or "").strip()
    if kind == "gstin":
        g = re.sub(r"\s", "", v).upper()
        if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g):
            return "not shaped like a GSTIN (2 digits, 10-character PAN, entity digit, Z, check character)"
        if g[:2] not in _GST_STATES:
            return f"state code {g[:2]} does not exist"
        if gstin_check_char(g[:14]) != g[14]:
            return f"GSTIN check character should be {gstin_check_char(g[:14])}, printed {g[14]}"
    elif kind == "pan":
        p = v.upper()
        if not re.fullmatch(r"[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]", p):
            return "not shaped like a PAN (5 letters, 4 digits, a letter; the 4th letter is the holder type)"
    elif kind == "ifsc":
        if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v.upper()):
            return "not shaped like an IFSC (4 letters, 0, 6 characters)"
    elif kind == "mobile":
        d = re.sub(r"[\s-]", "", v)
        if not re.fullmatch(r"(\+?91)?[6-9]\d{9}", d):
            return "an Indian mobile number is 10 digits starting with 6 to 9"
    elif kind == "pin":
        if not re.fullmatch(r"[1-9]\d{5}", re.sub(r"\s", "", v)):
            return "a PIN code is 6 digits and does not start with 0"
    elif kind == "email":
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", v):
            return "not an email address"
    elif kind == "vehicle":
        plate = re.sub(r"[\s-]", "", v.upper())
        if re.fullmatch(r"\d{2}BH\d{4}[A-Z]{1,2}", plate):                    # Bharat series
            return ""
        m = re.fullmatch(r"([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})", plate)
        if not m:
            return "not shaped like an Indian registration (state, district, series, 4 digits)"
        if m[1] not in _STATES:
            return f"{m[1]} is not a state code"
    elif kind == "irn":
        if not re.fullmatch(r"[0-9a-fA-F]{64}", re.sub(r"\s", "", v)):
            return "an IRN is 64 hexadecimal characters"
    elif kind == "hsn":
        if not re.fullmatch(r"\d{4}|\d{6}|\d{8}", re.sub(r"\s", "", v)):
            return "an HSN or SAC code is 4, 6 or 8 digits"
    elif kind == "container":
        from .pipeline import iso6346_ok

        if not iso6346_ok(v):
            return "not a valid container number (ISO 6346 check digit)"
    elif kind == "mawb":
        from .pipeline import mawb_ok

        ok = mawb_ok(v)
        if ok is None:
            return "an air waybill number is 11 digits"
        if not ok:
            return "air waybill check digit (mod 7) does not match"
    elif kind == "date":
        d = parse_date(v)
        if d is None:
            return "not a recognised date"
        if d.year < 2000 or d > date.today() + timedelta(days=366):
            return f"{d.isoformat()} is not a plausible date"
    else:
        raise ValueError(f"unknown identifier kind {kind!r}")
    return ""


KINDS = {"gstin", "pan", "ifsc", "mobile", "pin", "email", "vehicle", "irn", "hsn", "container", "mawb", "date"}
