"""Deterministic parsers and check-digit validators for invoice values."""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
                "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})

_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "i": "1",
                            "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "g": "9"})
_ALPHA_FIX = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A", "7": "T"})

AMOUNT_TOKEN = re.compile(r"^[-(]?(?:\d{1,3}(?:[,.]\d{2,3})+|\d+)(?:[.,]\d{1,4})?\)?%?$")


def parse_number(text: str) -> float | None:
    """'1,66,069.75' / '166,069.75' / '166069.75' / '(1,200.00)' -> float. Returns None for non-numbers."""
    if text is None:
        return None
    s = str(text).strip()
    s = re.sub(r"^(?:rs\.?|inr|usd|eur|₹|\$)\s*", "", s, flags=re.I)
    s = re.sub(r"\s*(?:inr|usd|eur|/-)$", "", s, flags=re.I).strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()% ")
    if not s or not re.fullmatch(r"[\d,.]+", s) or not re.search(r"\d", s):
        return None
    dots = s.count(".")
    if dots == 1:
        s = s.replace(",", "")
    elif dots > 1:
        # OCR read thousands commas as dots: the last dot is decimal only when followed by 1-2 digits
        head, _, tail = s.rpartition(".")
        s = re.sub(r"[.,]", "", head) + ("." + tail if len(tail) <= 2 else tail)
    elif re.search(r",\d{2}$", s):
        # amounts carry 2 decimals: a trailing ",dd" is a misread decimal point ("3,800,00")
        head, _, tail = s.rpartition(",")
        s = head.replace(",", "") + "." + tail
    else:
        s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def is_amount_text(text: str) -> bool:
    return bool(AMOUNT_TOKEN.match(text.strip())) and parse_number(text) is not None


_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?!\d)"), "dmy"),
    (re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]*([A-Za-z]{3,9})\.?[\s\-/.,]*(\d{2,4})(?!\d)"), "dMy"),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)"), "Mdy"),
]


def _month(word: str) -> int | None:
    w = word.lower().rstrip(".")
    return _MONTHS.get(w) if (len(w) == 3 or w in _MONTHS) else None


def _mk_date(y: int, m: int, d: int) -> str | None:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d).isoformat() if 1990 <= y <= 2100 else None
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[int, str]]:
    """All dates in text as (start offset, ISO string). Numeric dates are read day-first (Indian convention)."""
    out = []
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(text):
            a, b, c = m.groups()
            iso = None
            if kind == "ymd":
                iso = _mk_date(int(a), int(b), int(c))
            elif kind == "dmy":
                iso = _mk_date(int(c), int(b), int(a))
            elif kind == "dMy":
                mon = _month(b)
                if mon:
                    iso = _mk_date(int(c), mon, int(a))
            elif kind == "Mdy":
                mon = _month(a)
                if mon:
                    iso = _mk_date(int(c), mon, int(b))
            if iso:
                out.append((m.start(), iso))
    out.sort()
    return out


def parse_date(text: str) -> str | None:
    found = find_dates(text)
    return found[0][1] if found else None


_ISO_VALUES = {}
_v = 10
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    if _v % 11 == 0:
        _v += 1
    _ISO_VALUES[_ch] = _v
    _v += 1


def iso6346_valid(code: str) -> bool:
    if not re.fullmatch(r"[A-Z]{3}[UJZ]\d{7}", code):
        return False
    total = sum((_ISO_VALUES[c] if c.isalpha() else int(c)) * (2 ** i) for i, c in enumerate(code[:10]))
    return (total % 11) % 10 == int(code[10])


CONTAINER_LOOSE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{4})\s?([A-Za-z0-9]{3})\s?([A-Za-z0-9]{3})\s?([A-Za-z0-9])(?![A-Za-z0-9])")


def find_containers(text: str) -> list[str]:
    """ISO 6346 container numbers, repairing common OCR letter/digit confusions; check digit must hold."""
    found = []
    for m in CONTAINER_LOOSE.finditer(text):
        raw = "".join(m.groups())
        owner = raw[:4].upper().translate(_ALPHA_FIX)
        digits = raw[4:].translate(_DIGIT_FIX)
        if not re.fullmatch(r"[A-Z]{4}", owner) or not digits.isdigit():
            continue
        code = owner + digits
        if iso6346_valid(code) and code not in found:
            found.append(code)
    return found


MAWB_RE = re.compile(r"(?<![\dA-Za-z])(\d{3})\s*[-–]?\s*(\d{4})\s?(\d{3})\s?(\d)(?![\dA-Za-z])")


def normalize_mawb(text: str, require_hyphen: bool = False) -> str | None:
    """MAWB = 3-digit airline prefix + 7-digit serial + check digit (serial mod 7)."""
    t = text.translate(_DIGIT_FIX) if re.search(r"\d{3}", text) else text
    for m in MAWB_RE.finditer(t):
        if require_hyphen and "-" not in m.group(0) and "–" not in m.group(0):
            continue
        serial = m.group(2) + m.group(3)
        if int(serial) % 7 == int(m.group(4)):
            return f"{m.group(1)}-{serial}{m.group(4)}"
    return None


ID_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{3,}[A-Z0-9]$")


def clean_id(text: str) -> str | None:
    """Document identifiers: letters/digits with / or -, at least 3 digits, never an amount or a date."""
    t = text.strip().strip(":;,.#|()[]").strip()
    t = re.sub(r"\s*([/-])\s*", r"\1", t)
    if " " in t:
        t = t.split(" ")[0]
    up = t.upper()
    if not ID_TOKEN.match(up) or sum(c.isdigit() for c in up) < 3:
        return None
    if find_dates(up) and re.fullmatch(r"[\d/.\-]+", up):
        return None
    return t


def parse_weight(text: str) -> float | None:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:kgs?|kilo|kg\.)?", text, flags=re.I)
    if not m:
        return None
    return parse_number(m.group(1))
