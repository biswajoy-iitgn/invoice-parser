"""Format-aware field comparison and line-item scoring shared by every evaluator.

Charge fields compare by normalized number, dates by ISO date, weights by
number (unit spelling ignored), exchange rates as a set of (code, rate)
pairs; everything else case-insensitively after whitespace normalization.
"""

from __future__ import annotations

import re

from .schema import CHARGE_FIELDS, HEADER_FIELDS, OUTPUT_FIELDS
from .validation import _norm_num, normalize_date

NUMBER_RE = re.compile(r"-?[\d,]+\.\d+|-?\d[\d,]*")
CRITICAL = set(HEADER_FIELDS)
CURRENCY_FIELDS = set(CHARGE_FIELDS)


def norm(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip().casefold()


def _first_number(s: str) -> str | None:
    m = NUMBER_RE.search(s)
    return _norm_num(m.group(0)) if m else None


def rate_set(s: str) -> set:
    out = set()
    for entry in str(s or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        m = NUMBER_RE.search(entry)
        code = entry.split()[0].upper() if entry.split() else ""
        out.add((code, _norm_num(m.group(0)) if m else ""))
    return out


def _containers(s: str) -> set:
    return {c for c in re.findall(r"[A-Z]{4}\d{7}", str(s or "").upper())}


def field_equal(field: str, gold, pred) -> bool:
    g, p = norm(gold), norm(pred)
    if not g and not p:
        return True
    if not g or not p:
        return False
    if field == "Invoice Date":
        return normalize_date(g) == normalize_date(p)
    if field in CURRENCY_FIELDS or field == "WEIGHT":
        gn, pn = _first_number(g), _first_number(p)
        if gn is None or pn is None:
            return g == p
        return gn == pn
    if field == "Ex Rate":
        return rate_set(g) == rate_set(p)
    if field == "Container Number":
        return _containers(g) == _containers(p) and bool(_containers(g))
    return re.sub(r"[\s.,]", "", g) == re.sub(r"[\s.,]", "", p)


def field_match(field: str, gold, pred, alternatives: list | None = None) -> bool:
    if field_equal(field, gold, pred):
        return True
    return any(field_equal(field, alt, pred) for alt in alternatives or [])


def _amount_close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, 0.0005 * abs(a))


def score_line_items(gold_items: list[dict], pred_items: list[dict]) -> dict:
    """Best-match alignment on amount, then category (deterministic).

    Returns counts: gold, pred, matched (amount found), category_strict,
    category_lenient (alt_categories accepted), spurious (unmatched preds).
    """
    used = set()
    matched = strict = lenient = 0
    misses = []
    for g in gold_items:
        try:
            g_amt = float(g["amount_inr"])
        except (TypeError, ValueError):
            misses.append({"gold": g.get("description"), "reason": "gold amount not numeric"})
            continue
        best = None  # (category_rank, index): strict match beats lenient beats amount-only, ties go to doc order
        for j, p in enumerate(pred_items):
            if j in used or not isinstance(p.get("amount_inr"), (int, float)):
                continue
            if not _amount_close(g_amt, float(p["amount_inr"])):
                continue
            cat = p.get("category")
            rank = 0 if cat == g["category"] else (1 if cat in g.get("alt_categories", []) else 2)
            if best is None or (rank, j) < best:
                best = (rank, j)
        if best is None:
            misses.append({"gold": g["description"], "reason": "amount not extracted"})
            continue
        hit = best[1]
        used.add(hit)
        matched += 1
        cat = pred_items[hit].get("category")
        ok_strict = cat == g["category"]
        ok_len = ok_strict or cat in g.get("alt_categories", [])
        strict += ok_strict
        lenient += ok_len
        if not ok_len:
            misses.append({"gold": g["description"], "want": g["category"], "got": cat})
    return {"gold": len(gold_items), "pred": len(pred_items), "matched": matched,
            "category_strict": strict, "category_lenient": lenient,
            "spurious": len(pred_items) - len(used), "misses": misses}


def score_document(gold_fields: dict, pred_fields: dict, alternatives: dict | None = None) -> dict:
    alternatives = alternatives or {}
    per = {}
    for k in OUTPUT_FIELDS:
        per[k] = {
            "strict": field_equal(k, gold_fields.get(k), pred_fields.get(k)),
            "lenient": field_match(k, gold_fields.get(k), pred_fields.get(k), alternatives.get(k)),
            "gold": gold_fields.get(k, ""),
            "pred": pred_fields.get(k, ""),
        }
    return per


def aggregate(docs: list[dict]) -> dict:
    """docs: [{"fields": score_document(...), "items": score_line_items(...)}]."""
    out: dict = {"documents": len(docs)}
    for mode in ("strict", "lenient"):
        f_ok = {k: sum(d["fields"][k][mode] for d in docs) for k in OUTPUT_FIELDS}
        n = max(1, len(docs))
        out[mode] = {
            "field_accuracy": sum(f_ok.values()) / (n * len(OUTPUT_FIELDS)),
            "critical_field_accuracy": sum(f_ok[k] for k in CRITICAL) / (n * len(CRITICAL)),
            "charge_field_accuracy": sum(f_ok[k] for k in CURRENCY_FIELDS) / (n * len(CURRENCY_FIELDS)),
            "invoice_exact_match": sum(all(d["fields"][k][mode] for k in OUTPUT_FIELDS) for d in docs) / n,
            "per_field": {k: f_ok[k] / n for k in OUTPUT_FIELDS},
        }
    gold = sum(d["items"]["gold"] for d in docs if d.get("items"))
    if gold:
        m = sum(d["items"]["matched"] for d in docs)
        out["line_items"] = {
            "gold": gold,
            "amount_recall": m / gold,
            "category_accuracy_strict": sum(d["items"]["category_strict"] for d in docs) / gold,
            "category_accuracy_lenient": sum(d["items"]["category_lenient"] for d in docs) / gold,
            "spurious": sum(d["items"]["spurious"] for d in docs),
        }
    return out
