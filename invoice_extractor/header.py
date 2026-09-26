"""Header fields from label/value geometry: value inline after the label, in the cell to the right,
or in the cell below (stacked layouts). Every value must pass a field-specific validator."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

from .layout import Row, Segment
from .semantic import find_labels
from .validators import clean_id, find_containers, find_dates, normalize_mawb, parse_number, parse_weight


@dataclass
class Candidate:
    value: object
    conf: float
    basis: str
    page: int = 1
    y: float = 0.0


_LEAD = re.compile(r"^[\s:;.\-#=|]+")
_POS_W = {"inline": 1.0, "right": 0.95, "below": 0.85}
_LABEL_W = {"invoice_no": 1.0, "invoice_date": 1.0, "mawb": 1.0, "hawb": 1.0, "awb": 0.9, "hbl": 1.0,
            "mbl": 1.0, "bl": 0.95, "gross_weight": 1.0, "ex_rate": 1.0, "container": 1.0}
_DATE_NEG = re.compile(r"(?:due|payment|pay\s*by|sob|sailing|etd|eta|flight|ack|shipping\s*bill|s/?\s*bill|\bsb\b|"
                       r"arrival|departure|delivery|order|\bpo\b|shipper|party|commercial|exporter|b/?e|dt)\W*$", re.I)


def _parse(field: str, text: str):
    text = _LEAD.sub("", text).strip()
    if not text:
        return None
    first = text.split()[0] if text.split() else ""
    if field == "invoice_no":
        v = clean_id(first)
        return v if v and not re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", v) else None
    if field == "invoice_date":
        dates = find_dates(text[:40])
        return dates[0][1] if dates and dates[0][0] <= 3 else None
    if field in ("mawb", "awb"):
        return normalize_mawb(text[:24])
    if field in ("hawb", "hbl", "mbl", "bl"):
        return clean_id(first)
    if field == "gross_weight":
        m = re.match(r"(\d[\d,]*(?:\.\d+)?)\s*(kgs?|kilos?|kg\.)?", text, re.I)
        if not m:
            return None
        v = parse_weight(m.group(1))
        return v if v and v > 0 else None
    if field == "ex_rate":
        m = (re.search(r"1\s*[A-Z]{3}\s*=\s*(?:inr|rs\.?)?\s*(\d+(?:\.\d+)?)", text, re.I)
             or re.match(r"(?:[A-Z]{3}\s*)?(\d+(?:\.\d+)?)", text))
        if m:
            v = parse_number(m.group(1))
            return v if v and 30 < v < 200 else None
    return None


def _below(rows: list[Row], ri: int, seg: Segment, max_rows: int = 3) -> list[Segment]:
    out = []
    for r in rows[ri + 1: ri + 1 + max_rows]:
        if r.y0 - seg.y1 > 2.2 * max(seg.y1 - seg.y0, 1):
            break
        for s in r.segments:
            if min(s.x1, seg.x1) - max(s.x0, seg.x0) > 0.3 * min(s.x1 - s.x0, seg.x1 - seg.x0):
                out.append(s)
                break
        if out:
            break
    return out


def collect_candidates(pages_rows: list[tuple[int, list[Row]]]) -> dict[str, list[Candidate]]:
    cands: dict[str, list[Candidate]] = {}
    for page_no, rows in pages_rows:
        for ri, row in enumerate(rows):
            segs = row.segments
            for si, seg in enumerate(segs):
                for hit in find_labels(seg.text):
                    field = hit.field
                    options = []
                    rest = seg.text[hit.end:]
                    tail = " ".join(s.text for s in segs[si + 1:si + 3])
                    if _LEAD.sub("", rest).strip():
                        options.append(("inline", rest + " " + tail, seg.conf))
                    elif si + 1 < len(segs):
                        options.append(("right", tail, segs[si + 1].conf))
                    only_label = not _LEAD.sub("", rest).strip() and hit.start <= 2
                    if only_label:
                        for b in _below(rows, ri, seg):
                            options.append(("below", b.text, b.conf))
                    for pos, text, conf in options:
                        val = _parse(field, text)
                        if val is None:
                            continue
                        lead = 1.0 if hit.start <= 2 else 0.9   # label preceded by other words in its cell
                        score = _POS_W[pos] * _LABEL_W.get(field, 1.0) * lead * (0.6 + 0.4 * conf)
                        cands.setdefault(field, []).append(Candidate(val, score, f"label:{pos}", page_no, row.cy))
                        break
    return cands


def best(cands: dict[str, list[Candidate]], field: str) -> Candidate | None:
    lst = cands.get(field) or []
    if not lst:
        return None
    votes = Counter(str(c.value) for c in lst)
    top = max(lst, key=lambda c: (c.conf + 0.05 * (votes[str(c.value)] - 1), -c.page, -c.y))
    return top


def fallback_date(pages_rows: list[tuple[int, list[Row]]]) -> Candidate | None:
    """Topmost date on page 1 not introduced by a due/ETD/SB/flight context."""
    for page_no, rows in pages_rows[:1]:
        for row in rows[: max(8, len(rows) // 2)]:
            prev = ""
            for seg in row.segments:
                for off, iso in find_dates(seg.text):
                    ctx = prev + " " + seg.text[:off]
                    if not _DATE_NEG.search(ctx.strip()):
                        return Candidate(iso, 0.45, "fallback:first-date", page_no, row.cy)
                prev = seg.text
    return None


_COMPANY = re.compile(r"\b(pvt|private|ltd|limited|llp|inc|gmbh|co\.?|corp|logistics|shipping|freight|forwarders?|"
                      r"cargo|maritime|agencies|express|movers|lines|solutions|systems)\b", re.I)
_NOT_VENDOR = re.compile(r"\b(invoice|tax|original|duplicate|recipient|bill\s*to|billed|gstin|pan|consignee|customer|"
                         r"to,|statement|receipt|signatory|bank|ifsc|a/c)\b", re.I)
_FOR = re.compile(r"^\s*for\s*[:,]?\s+(.{3,90}?)\s*[.:]?\s*$", re.I)


def _row_phrases(row: Row) -> list[tuple[str, float, float]]:
    """Adjacent same-size segments of a row joined (large or letter-spaced type splits into cells)."""
    out, cur = [], []
    for seg in row.segments:
        h = sorted(w.h for w in seg.words)[len(seg.words) // 2]
        if cur:
            ph = cur[-1][1]
            if seg.x0 - cur[-1][0].x1 < 3 * max(h, ph) and 0.75 < h / ph < 1.33:
                cur.append((seg, h))
                continue
            out.append(cur)
        cur = [(seg, h)]
    if cur:
        out.append(cur)
    return [(" ".join(s.text for s, _ in grp), sorted(h for _, h in grp)[len(grp) // 2], min(s.conf for s, _ in grp))
            for grp in out]


def vendor_name(pages_rows: list[tuple[int, list[Row]]]) -> Candidate | None:
    page_no, rows = pages_rows[0]
    if not rows:
        return None
    top_limit = rows[0].y0 + 0.3 * (rows[-1].y1 - rows[0].y0)
    top = []
    for row in rows:
        if row.y0 > top_limit:
            break
        for t, h, conf in _row_phrases(row):
            t = t.strip(" |:")
            alpha = sum(ch.isalpha() for ch in t)
            spaced = sum(len(x) == 1 for x in t.split()) > len(t.split()) / 2   # letter-spaced title "I N V O I C E"
            if alpha < 5 or len(t.split()) < 2 or spaced or _NOT_VENDOR.search(t) or find_labels(t):
                continue
            score = h * (2.0 if _COMPANY.search(t) else 1.0) * (0.7 + 0.3 * conf)
            top.append((score, t, conf))
    fors = []
    for pn, rs in pages_rows:
        for row in rs:
            for t, _, conf in _row_phrases(row):
                m = _FOR.match(t)
                if m and _COMPANY.search(m.group(1)) and not _NOT_VENDOR.search(m.group(1)):
                    fors.append((m.group(1).strip(), conf))
    head = max(top)[1:] if top else None
    if head and fors:
        f = max(fors, key=lambda x: x[1])
        if fuzz.token_set_ratio(head[0].lower(), f[0].lower()) >= 80:
            # same company: keep the fuller rendering (a split or truncated heading loses words)
            text = max((head[0], f[0]), key=lambda x: (len(x.split()), x == head[0]))
            return Candidate(text, 0.95, "top+for-line")
        return Candidate(head[0], 0.6, "top-largest (for-line disagrees)")
    if head:
        return Candidate(head[0], 0.7, "top-largest")
    if fors:
        return Candidate(fors[0][0], 0.7, "for-line")
    return None


def containers(all_text: str) -> Candidate | None:
    found = find_containers(all_text)
    return Candidate(", ".join(found), 0.95, "iso6346-scan") if found else None


def transport_mode(all_text: str, cands: dict[str, list[Candidate]]) -> str:
    t = all_text.lower()
    air = 2 * bool(cands.get("mawb") or cands.get("hawb")) + len(re.findall(
        r"\b(flight|airport|awb|air\s*way\s*bill|chargeable\s*w|air\s*freight|airfreight)\b", t))
    sea = 2 * bool(find_containers(all_text)) + 2 * bool(cands.get("hbl") or cands.get("mbl") or cands.get("bl")) + \
        len(re.findall(r"\b(vessel|voyage|b/l|bill\s*of\s*lading|port\s*of\s*loading|pol|cbm|ocean|sea\s*freight|sob)\b", t))
    if air > sea:
        return "air"
    if sea > air:
        return "sea"
    return "unknown"
