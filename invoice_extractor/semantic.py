"""Meaning-level mapping: header label synonyms and charge-description -> output category.

The charge lexicon is `schema.CHARGE_CATEGORIES` (regex aliases per category). On top of it:
  * generic normalisation: dotted acronyms (S.B. -> SB), freight abbreviations (Hdlg -> handling),
    naive singularisation, and OCR spelling repair against the lexicon vocabulary;
  * head-noun precedence: a match outside parentheses beats one inside them
    ("Road Transport (to CFS)" is transport, not CFS storage);
  * a mode-aware rule for bare "freight" (sea vs air from the shipment's transport mode);
  * everything without evidence falls to "Other Charges" with basis "default", which is the
    hook for the optional LLM fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from .schema import CHARGE_CATEGORIES

# ---------------------------------------------------------------- header labels
_NUM = r"(?:no|nos|number|num|#)"
HEADER_LABELS: dict[str, list[str]] = {
    "invoice_no": [rf"(?:tax\s+)?invoice\s*{_NUM}\.?", rf"inv\.?\s*{_NUM}\.?", rf"bill\s*{_NUM}\.?",
                   rf"(?:document|doc)\.?\s*{_NUM}\.?"],
    "invoice_date": [r"(?:tax\s+)?invoice\s*(?:date|dt)\.?", r"inv\.?\s*(?:date|dt)\.?", r"bill\s*(?:date|dt)\.?",
                     r"(?:document|doc)\.?\s*date"],
    "container": [rf"container\s*{_NUM}?\.?\s*(?:\(s\))?", rf"cntr\.?\s*{_NUM}?\.?", rf"equipment\s*{_NUM}?\.?"],
    "mawb": [rf"m\.?\s*awb\s*{_NUM}?\.?", rf"master\s*(?:awb|air\s*-?\s*way\s*-?\s*bill)\s*{_NUM}?\.?"],
    "hawb": [rf"h\.?\s*awb\s*{_NUM}?\.?", rf"house\s*(?:awb|air\s*-?\s*way\s*-?\s*bill)\s*{_NUM}?\.?"],
    "awb": [rf"awb\s*{_NUM}?\.?", rf"air\s*-?\s*way\s*-?\s*bill\s*{_NUM}?\.?"],
    "hbl": [rf"h\.?\s*b\s*/?\s*l\s*{_NUM}?\.?", rf"house\s*(?:b\s*/?\s*l|bill\s*of\s*lading)\s*{_NUM}?\.?"],
    "mbl": [rf"m\.?\s*b\s*/?\s*l\s*{_NUM}?\.?", rf"(?:master|ocean)\s*(?:b\s*/?\s*l|bill\s*of\s*lading)\s*{_NUM}?\.?"],
    "bl": [rf"b\s*/\s*l\s*{_NUM}\.?", rf"bl\s*{_NUM}\.?", rf"bill\s*of\s*lading\s*{_NUM}?\.?"],
    "gross_weight": [r"gross\s*(?:weight|wt)\.?", r"g\.\s*w\.?", r"gr\.?\s*wt\.?", r"total\s*weight"],
    "ex_rate": [r"ex(?:ch)?\.?\s*rate", r"exchange\s*rate", r"\broe\b", r"conversion\s*rate"],
}
# A bare "Date" / "Weight" label only counts when it starts the cell ("Due Date", "Chargeable Weight" do not).
STANDALONE_LABELS = {"invoice_date": r"date", "gross_weight": r"weight"}
NEGATIVE_PREFIX = {
    "invoice_no": r"(?:party|shipper'?s?|commercial|exporter'?s?|supplier|vendor|customer|buyer|proforma|your|"
                  r"shipping|s\s*/\s*b|\bs\s*/|original|debit|credit)\s*$",
    "invoice_date": r"(?:due|payment|pay|sob|sailing|etd|eta|flight|ack|bill\s*of\s*entry|shipping\s*bill|s\s*/?\s*b|"
                    r"arrival|departure|delivery|order|po|shipper'?s?|party|commercial|exporter'?s?|of)\s*$",
    "gross_weight": r"(?:chargeable|ch\.?|chg\.?|net|volumetric|vol\.?|tare)\s*$",
    "hbl": r"(?:master|ocean|carrier)\s*$",
    "bl": r"(?:house|master|ocean|h\.?|m\.?)\s*$",
    "awb": r"(?:house|master|h\.?|m\.?)\s*$",
}
_COMPILED = {f: [re.compile(rf"(?<![a-z0-9]){p}(?![a-z0-9])") for p in pats] for f, pats in HEADER_LABELS.items()}
_NEG = {f: re.compile(p) for f, p in NEGATIVE_PREFIX.items()}


@dataclass
class LabelHit:
    field: str
    start: int
    end: int


def find_labels(text: str) -> list[LabelHit]:
    """Label occurrences in a (lower-cased) text span, respecting negative prefixes like 'Party Invoice No'."""
    low = text.lower()
    hits = []
    for field, rxs in _COMPILED.items():
        for rx in rxs:
            for m in rx.finditer(low):
                neg = _NEG.get(field)
                if neg and neg.search(low[:m.start()]):
                    continue
                hits.append(LabelHit(field, m.start(), m.end()))
    for field, pat in STANDALONE_LABELS.items():
        m = re.match(rf"\s*({pat})(?![a-z0-9])", low)      # generic 'Date' / 'Weight' only as the cell's first word
        if m:
            hits.append(LabelHit(field, m.start(1), m.end(1)))
    # keep the longest label at each position (e.g. 'invoice date' over 'date')
    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    out: list[LabelHit] = []
    for h in hits:
        if out and h.start < out[-1].end and h.field != out[-1].field:
            continue
        if out and h.start == out[-1].start and h.field == out[-1].field:
            continue
        out.append(h)
    return out


# ---------------------------------------------------------------- charge categories
_ABBREV = [
    (r"\bhdlg\b\.?", "handling"), (r"\bchgs?\b\.?", "charges"), (r"\bchrgs?\b\.?", "charges"),
    (r"\bfrt\b\.?", "freight"), (r"\bwhse\b\.?", "warehouse"), (r"\bclrg\b\.?", "clearing"),
    (r"\btrpt\b\.?", "transport"), (r"\bsvc\b\.?", "service"), (r"\bamt\b\.?", "amount"),
]
_ROAD_WORDS = re.compile(r"\b(truck\w*|road|local|lorry|inland|rail|cartage|haulage|delivery|transport\w*)\b")
PRIORITY = [
    "Airway Bill Charges", "Terminal Handling Charges", "Shipping Bill Charges", "BL Charges", "ENS Charges",
    "AMS Charges", "EDI Fees", "DAP Charges", "Seal Charges", "VGM", "Pre Carriage Haulage", "Fuel Charges",
    "Cargo Screening", "Examination Charges", "Agency Charges", "Warehouse Charges Port", "Transportation",
    "Handling Charges", "Sea Freight", "Air Freight", "Misc", "Other Charges",
]
_CAT_RX = {c: [re.compile(p, re.I) for p in spec["patterns"]] for c, spec in CHARGE_CATEGORIES.items()}


@lru_cache(maxsize=1)
def _vocab() -> list[str]:
    words = set()
    for spec in CHARGE_CATEGORIES.values():
        words |= set(re.findall(r"[a-z]{4,}", spec["meaning"].lower()))
        for p in spec["patterns"]:
            words |= set(re.findall(r"[a-z]{4,}", re.sub(r"\\[a-z]", " ", p.lower())))
    words |= {"charges", "charge", "freight", "handling", "terminal", "examination", "warehouse", "transportation",
              "surcharge", "filing", "declaration", "documentation", "clearance", "customs", "screening", "issuance",
              "certificate", "destination", "origin", "export", "import", "security", "container", "cargo", "carrier",
              "haulage", "carriage", "manifest", "processing", "miscellaneous", "expenses", "agency", "shipping",
              "delivery", "transmission", "verified", "gross", "mass", "seal", "bunker", "sulphur", "emergency",
              "adjustment", "factor", "inland", "local", "trucking", "ocean", "port", "fees", "service", "bill"}
    return sorted(words)


@lru_cache(maxsize=1)
def _english() -> frozenset[str]:
    """System word list (macOS ships it; Debian/Ubuntu: wamerican). Real words are never 'repaired'."""
    for path in ("/usr/share/dict/words", "/usr/dict/words"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                return frozenset(w.strip().lower() for w in fh if w.strip().isalpha())
        except OSError:
            continue
    return frozenset()


def _segment(token: str, words: frozenset[str]) -> str | None:
    """Split a run-together token ('terminalhandlingcharges') into known words, fewest pieces first."""
    n = len(token)
    best: list = [None] * (n + 1)
    best[0] = []
    for i in range(1, n + 1):
        for j in range(max(0, i - 20), i):
            piece = token[j:i]
            if best[j] is not None and len(piece) >= 2 and piece in words:
                cand = best[j] + [piece]
                if best[i] is None or len(cand) < len(best[i]):
                    best[i] = cand
    return " ".join(best[n]) if best[n] and len(best[n]) > 1 else None


def normalize_description(desc: str) -> str:
    s = re.sub(r"\b([A-Za-z])\.\s?([A-Za-z])\.", r"\1\2", desc)          # S.B. -> SB
    s = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", s)                        # TerminalHandling -> Terminal Handling
    s = s.lower()
    known = frozenset(_vocab()) | _english()
    s = re.sub(r"[a-z]{10,}", lambda m: m.group(0) if m.group(0) in known else (_segment(m.group(0), known) or m.group(0)), s)
    for rx, rep in _ABBREV:
        s = re.sub(rx, rep, s)
    vocab, english = _vocab(), _english()
    toks = []
    for t in re.split(r"(\W+)", s):
        if len(t) >= 5 and t.isalpha() and t not in vocab and t not in english:
            best = process.extractOne(t, vocab, scorer=fuzz.ratio, score_cutoff=86)
            t = best[0] if best else t
        toks.append(t)
    s = "".join(toks)
    s = re.sub(r"\b(\w{3,})ies\b", r"\1y", s)
    return re.sub(r"\s+", " ", s).strip()


def _matches(text: str) -> list[tuple[int, str]]:
    found = []
    for cat, rxs in _CAT_RX.items():
        for rx in rxs:
            m = rx.search(text)
            if m:
                found.append((m.start(), cat))
                break
    return found


def _choose(found: list[tuple[int, str]]) -> str:
    cats = {c for _, c in found}
    for c in PRIORITY:
        if c in cats:
            return c
    return found[0][1]


def classify_charge(description: str, mode: str = "unknown") -> tuple[str, float, str]:
    """-> (category, confidence, basis). basis: lexicon | lexicon-paren | freight-mode | default."""
    text = normalize_description(description)
    main = re.sub(r"\([^)]*\)", " ", text)
    paren = " ".join(re.findall(r"\(([^)]*)\)", text))
    found = _matches(main)
    if found:
        return _choose(found), 0.95, "lexicon"
    found = _matches(paren)
    if found:
        return _choose(found), 0.85, "lexicon-paren"
    if re.search(r"\bfreight\b", text) and not _ROAD_WORDS.search(text):
        if mode == "air":
            return "Air Freight", 0.75, "freight-mode"
        if mode == "sea":
            return "Sea Freight", 0.75, "freight-mode"
    return "Other Charges", 0.5, "default"
