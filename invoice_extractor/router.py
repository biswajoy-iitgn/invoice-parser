"""Which profile reads a document, and which models that takes. Decided before any model is loaded, from what is already
known for free: whether the PDF has a text layer, the text itself (text layer or the PP-OCRv6 pass every scan gets anyway),
and the profiles' own configuration.

Profile: the one the user chose; otherwise each profile scores the page text on its `detect:` phrases (2 points each) and
its own field labels (1 point each). The best profile wins when it scores at least MIN_SCORE and leads the next by MARGIN;
anything less goes to `general`, which reads every value and table, so a doubtful guess never loses content.

Models (reports/parser_bakeoff.md): no OCR model for digital pages (the text layer is exact); the reader the profile names,
falling back to the catalog default when its files are not on this machine; the table reader (PaddleOCR-VL-1.6) only for
the general profile, and only when the page shows a table.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

MIN_SCORE = 4
MARGIN = 2
_NUM = re.compile(r"\d[\d,.]*")


@dataclass
class Route:
    profile: str
    detected: bool                    # chosen by the router (True) or by the user (False)
    reader: str                       # catalog id of the reader model
    tables: str                       # document-parser | reader | off
    scores: dict = field(default_factory=dict)
    why: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def score_profiles(text: str, profiles: dict) -> dict[str, int]:
    """Points per profile for this text (profiles that cannot be auto-detected score nothing)."""
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    out = {}
    for pid, p in profiles.items():
        if pid == "general" or not p.detect:
            continue
        pts = sum(2 for ph in p.detect if _has(low, ph))
        pts += sum(1 for f in p.fields if len(f.label) > 3 and _has(low, f.label))
        if pts:
            out[pid] = pts
    return out


def _has(low: str, phrase: str) -> bool:
    phrase = phrase.lower().strip()
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", low))


def detect(text: str, profiles: dict) -> tuple[str, dict, str]:
    """(profile id, scores, reason)."""
    scores = score_profiles(text, profiles)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked:
        return "general", scores, "no profile's words are on the page"
    best, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if top < MIN_SCORE:
        return "general", scores, f"{best} scored {top}, below {MIN_SCORE}"
    if top - runner_up < MARGIN:
        return "general", scores, f"{best} ({top}) and {ranked[1][0]} ({runner_up}) are too close to call"
    return best, scores, f"{best} scored {top}" + (f", next {ranked[1][0]} {runner_up}" if len(ranked) > 1 else "")


def looks_tabular(index: dict) -> bool:
    """At least three printed lines carrying three or more numbers each: a table worth the table reader."""
    return sum(1 for ln in index.values() if len(_NUM.findall(ln.text)) >= 3) >= 3


def reader_id(requested: str | None = None) -> str:
    """A reader the pool can run on this machine: the requested catalog id (or served model name) when its files are here,
    else the catalog default."""
    from .model_pool import pool
    from .profiles import catalog

    readers = catalog()["readers"]
    default = next(e["id"] for e in readers if e.get("default"))
    want = next((e["id"] for e in readers if requested in (e["id"], e.get("model"))), None) if requested else None
    for cand in (want, default):
        if cand and pool().available(cand):
            return cand
    return default


def plan(profile, text: str, index: dict, chosen: bool) -> Route:
    """The route for one document under `profile` (already detected or chosen)."""
    from .profiles import model_choice

    why = []
    want_reader = model_choice(profile, "readers")["id"]
    rid = reader_id(want_reader)
    if rid != want_reader:
        why.append(f"reader {want_reader} is not on this machine; {rid} reads instead")
    # Tables: a field profile's tables come from the reader, which also maps them onto the profile's columns. Adding the
    # table reader's grids to its input gave no gain on the real invoices (45 of 45 charge lines either way, twice the time)
    # and lost rows on a mill certificate, so the table reader runs only for the general profile, whose tables it reads
    # directly, and only when the page shows a table.
    tables = "reader" if profile.tables else "off"
    if profile.engine == "general":
        tables = model_choice(profile, "tables")["id"]
        if tables == "document-parser" and not looks_tabular(index):
            tables = "reader"
            why.append("no table on the page, so the table reader is not loaded")
    return Route(profile=profile.id, detected=not chosen, reader=rid, tables=tables, why=why)
