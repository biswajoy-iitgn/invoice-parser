"""Charges table: header-role detection, column geometry, row parsing, arithmetic cross-checks."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .layout import Row, Word
from .validators import parse_number

ROLE_RX = [
    ("non_gst", r"non[\s-]*(?:gst|tax)|exempt|non[\s-]*taxable"),
    ("fc_amount", r"amount\s*\(?\s*(?:fc|cur|usd|eur|foreign)|fc\s*amount"),
    ("taxable", r"taxable|amount\s*\(?\s*(?:inr|rs)|(?:inr|rs\.?)\s*amount|value\s*\(?\s*inr"),
    ("total", r"total|gross\s*amount"),
    ("gst_amt", r"(?:gst|tax)\s*amt|\bcgst|\bsgst|igst\s*amt|^igst$"),
    ("gst_pct", r"(?:gst|tax)\s*%|igst\s*%|^%$|^gst$|^tax$|gst\s*rate"),
    ("exrate", r"ex\.?\s*rate|\broe\b|exch|conversion|^fx$"),
    ("rate", r"rate|unit\s*price|price"),
    ("qty", r"qty|quantity|^units?$|no\.?\s*of"),
    ("cur", r"^cur|currency"),
    ("sac", r"sac|hsn"),
    ("sr", r"^#$|^sr|^s\.?\s*no|^sl\b"),
    ("basis", r"basis|^uom$"),
    ("amount", r"amount|value|^inr$|^rs\.?$"),
    ("desc", r"desc|particular|charge|nature|service|detail|item"),
]
_ROLE_C = [(r, re.compile(p, re.I)) for r, p in ROLE_RX]
NUMERIC_ROLES = {"non_gst", "fc_amount", "taxable", "total", "gst_amt", "gst_pct", "exrate", "rate", "qty", "amount"}
STOP_RX = re.compile(r"(grand\s*total|invoice\s*total|total\s*amount|amount\s*payable|net\s*payable|total\s*invoice|"
                     r"round\s*off|in\s*words|\bbank\b|a/c\s*no|authori[sz]ed|^\W*(?:add\s*:?\s*)?[csi]gst\b|"
                     r"^\W*add\b|e\s*&\s*o\.?\s*e|^\W*(?:inr|rupees)\s+[a-z]+\s+(?:lakh|thousand|hundred|crore))", re.I)
SKIP_RX = re.compile(r"(sub\s*-?\s*total|\btotal\b|net\s*amount|taxable\s*amount|before\s*tax)", re.I)
CURRENCY = {"INR", "USD", "EUR", "GBP", "AED", "SGD", "JPY", "CNY"}
UNITS = {"KG", "KGS", "CNTR", "SHPT", "W/M", "CBM", "UNIT", "PCS"}


def role_of(text: str) -> str | None:
    t = text.strip().lower()
    for role, rx in _ROLE_C:
        if rx.search(t):
            return role
    return None


@dataclass
class Column:
    role: str
    x0: float
    x1: float
    text: str


@dataclass
class Line:
    description: str
    amount_inr: float | None
    conf: float
    currency: str = "INR"
    exrate: float | None = None
    values: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    page: int = 1


def _ink(w: Word) -> bool:
    """False for rule lines and OCR'd cell borders ('-----', '|', '—'), which must not shape columns."""
    return bool(re.search(r"[A-Za-z0-9#%]", w.text))


def _is_num(t: str) -> bool:
    return parse_number(t) is not None and bool(re.search(r"\d", t))


def _cluster_header(words: list[Word]) -> list[Column]:
    h = statistics.median(w.h for w in words)
    ws = sorted(words, key=lambda w: w.x0)
    groups: list[list[Word]] = []
    for w in ws:
        if groups and w.x0 <= max(x.x1 for x in groups[-1]) + 0.55 * h:
            groups[-1].append(w)
        else:
            groups.append([w])
    cols = []
    for g in groups:
        g.sort(key=lambda w: (round(w.cy / h), w.x0))
        text = " ".join(w.text for w in g)
        cols.append(Column(role_of(text) or "unknown", min(w.x0 for w in g), max(w.x1 for w in g), text))
    return cols


def find_header(rows: list[Row]) -> tuple[int, list[Column], list[Word]] | None:
    best = None
    for i, row in enumerate(rows):
        roles = [role_of(s.text) for s in row.segments]
        if "desc" not in roles and not any(role_of(w.text) == "desc" for w in row.words):
            continue
        if any(_is_num(w.text) and len(w.text) >= 4 for w in row.words):
            continue
        band = [w for w in row.words if _ink(w)]
        for j in (i - 1, i + 1):
            if 0 <= j < len(rows):
                o = rows[j]
                gap = (row.y0 - o.y1) if j < i else (o.y0 - row.y1)
                if gap < 0.6 * row.h and not any(_is_num(w.text) and len(w.text) >= 3 for w in o.words):
                    band += [w for w in o.words if _ink(w)]
        cols = _cluster_header(band)
        score = sum(c.role in NUMERIC_ROLES for c in cols)
        has_desc = any(c.role == "desc" for c in cols)
        # a header whose "Description" cell OCR lost still qualifies with >= 3 numeric roles
        if (score >= 1 and has_desc or score >= 3) and (best is None or score + has_desc > best[0]):
            best = (score + has_desc, i, cols, band)
    if not best:
        return None
    return best[1], best[2], best[3]


def _data_rows(rows: list[Row], start: int) -> list[Row]:
    out = []
    for row in rows[start + 1:]:
        if STOP_RX.search(row.text):
            break
        if SKIP_RX.search(row.text):
            continue
        if sum(_is_num(w.text) for w in row.words) >= 2 and re.search(r"[A-Za-z]{2}", row.text):
            out.append(row)
    return out


def _infer_desc(cols: list[Column], data: list[Row]) -> None:
    """No 'Description' header was read: the description column is the one holding the most letters."""
    if any(c.role == "desc" for c in cols) or not data:
        return
    letters = [0] * len(cols)
    for row in data:
        for w in row.words:
            letters[_assign(cols, w.cx)] += sum(ch.isalpha() for ch in w.text) * (w.text.upper() not in CURRENCY | UNITS)
    k = max(range(len(cols)), key=lambda i: letters[i])
    if letters[k] and cols[k].role in ("unknown", "sr", "sac"):
        cols[k].role = "desc"


def partition_columns(rows: list[Row], start: int, header_words: list[Word]) -> list[Column]:
    """Column boundaries from vertical gutters in the data rows (headers are often centred while
    data is left/right aligned); header words are then attached to the partition holding their centre."""
    data = _data_rows(rows, start)
    if not data:
        return _cluster_header(header_words)
    h = statistics.median(w.h for r in data for w in r.words)
    spans = []
    for row in data:
        ws = sorted((w for w in row.words if _ink(w)), key=lambda w: w.x0)
        if not ws:
            continue
        cur = [ws[0].x0, ws[0].x1]
        for w in ws[1:]:
            if w.x0 - cur[1] < 0.5 * h:
                cur[1] = max(cur[1], w.x1)
            else:
                spans.append(cur)
                cur = [w.x0, w.x1]
        spans.append(cur)
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1] + 0.5 * h:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    headers = sorted((c for c in (_cluster_header(header_words) if header_words else []) if c.role != "unknown"),
                     key=lambda c: c.x0)
    kinds = _span_kinds(merged, data)
    roles = _align(headers, merged, kinds, h)
    bounds = [(p[1] + q[0]) / 2 for p, q in zip(merged, merged[1:])]
    edges = [-1e6] + bounds + [1e6]
    cols: list[Column] = []
    for k in range(len(merged)):
        assigned = roles.get(k, [])
        if len(assigned) == 2:      # one data column carried two headers (missed gutter): split at the header gap
            a, b = assigned
            cut = (a.x1 + b.x0) / 2
            cols += [Column(a.role, edges[k], cut, a.text), Column(b.role, cut, edges[k + 1], b.text)]
        else:
            c = assigned[0] if assigned else None
            cols.append(Column(c.role if c else "unknown", edges[k], edges[k + 1], c.text if c else ""))
    _infer_desc(cols, data)
    return cols


_TEXT_ROLES = {"desc", "cur", "basis"}


def _span_kinds(spans: list[list[float]], data: list[Row]) -> list[str]:
    counts = [[0, 0] for _ in spans]
    for row in data:
        for w in row.words:
            if not _ink(w):
                continue
            k = min(range(len(spans)), key=lambda i: 0 if spans[i][0] <= w.cx <= spans[i][1] else
                    min(abs(w.cx - spans[i][0]), abs(w.cx - spans[i][1])))
            counts[k][0 if _is_num(w.text) else 1] += 1
    return ["num" if n >= t else "text" for n, t in counts]


def _align(headers: list[Column], spans: list[list[float]], kinds: list[str], h: float) -> dict[int, list[Column]]:
    """Order-preserving alignment of header cells to data columns (dynamic programming).
    Headers are often centred or left-aligned over right-aligned numbers, so nearest-centre fails."""
    m, n = len(headers), len(spans)
    INF = float("inf")
    SKIP_H, SKIP_S, SPLIT = 2.5, 2.0, 1.0

    def cell(k: int) -> tuple[float, float]:
        # numbers are right-aligned (their cell starts where the previous column ends); text is left-aligned
        if kinds[k] == "num":
            return (spans[k - 1][1] if k else spans[k][0] - 10 * h), spans[k][1]
        return spans[k][0], (spans[k + 1][0] if k + 1 < n else spans[k][1] + 10 * h)

    def cost(c: Column, k: int) -> float:
        a, b = cell(k)
        cx = (c.x0 + c.x1) / 2
        dist = max(0.0, a - cx, cx - b) / h
        want = "text" if c.role in _TEXT_ROLES else "num"
        return dist + (4.0 if want != kinds[k] and not (c.role == "cur" and kinds[k] == "num") else 0.0)

    dp = [[INF] * (n + 1) for _ in range(m + 1)]
    back: dict[tuple[int, int], tuple] = {}
    dp[0][0] = 0.0
    for i in range(m + 1):
        for j in range(n + 1):
            v = dp[i][j]
            if v == INF:
                continue
            steps = []
            if i < m:
                steps.append((i + 1, j, SKIP_H, None))
            if j < n:
                steps.append((i, j + 1, SKIP_S, None))
            if i < m and j < n:
                steps.append((i + 1, j + 1, cost(headers[i], j), ("match", i, j)))
            if i + 1 < m and j < n:
                steps.append((i + 2, j + 1, cost(headers[i], j) + cost(headers[i + 1], j) + SPLIT, ("split", i, j)))
            for ni, nj, c, op in steps:
                if v + c < dp[ni][nj]:
                    dp[ni][nj] = v + c
                    back[(ni, nj)] = (i, j, op)
    out: dict[int, list[Column]] = {}
    i, j = m, n
    while (i, j) != (0, 0):
        pi, pj, op = back[(i, j)]
        if op and op[0] == "match":
            out[op[2]] = [headers[op[1]]]
        elif op and op[0] == "split":
            out[op[2]] = [headers[op[1]], headers[op[1] + 1]]
        i, j = pi, pj
    return out


def _assign(cols: list[Column], x: float) -> int:
    for k, c in enumerate(cols):
        if c.x0 <= x < c.x1:
            return k
    return len(cols) - 1


def _num_from(words: list[Word]) -> float | None:
    txt = "".join(w.text for w in words)
    nums = re.findall(r"-?[\d][\d,.]*", txt)
    for n in reversed(nums):
        v = parse_number(n)
        if v is not None:
            return v
    return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, 0.0005 * abs(b))


def parse_rows(rows: list[Row], start: int, cols: list[Column], page_no: int) -> tuple[list[Line], int]:
    desc_k = next((k for k, c in enumerate(cols) if c.role == "desc"), None)
    if desc_k is None:
        return [], 0
    lines: list[Line] = []
    last_kind = "header"
    last_y1 = rows[start].y1
    unparsed = 0
    for row in rows[start + 1:]:
        text = row.text
        if STOP_RX.search(text):
            break
        by_col: dict[int, list[Word]] = {}
        for w in row.words:
            by_col.setdefault(_assign(cols, w.cx), []).append(w)
        desc_words = sorted(by_col.get(desc_k, []), key=lambda w: w.x0)
        desc = " ".join(w.text for w in desc_words).strip(" |")
        vals: dict[str, float] = {}
        cur = None
        for k, ws in by_col.items():
            role = cols[k].role
            ws = [w for w in ws if w not in desc_words]
            if not ws:
                continue
            if role == "cur" or any(w.text.upper() in CURRENCY for w in ws):
                c = next((w.text.upper() for w in ws if w.text.upper() in CURRENCY), None)
                cur = cur or c
                if role == "cur":
                    continue
            if role in NUMERIC_ROLES:
                v = _num_from(ws)
                if v is not None:
                    vals[role] = vals.get(role, 0.0) + v if role == "gst_amt" else v
        if SKIP_RX.search(desc) or (not desc and SKIP_RX.search(text)):
            last_kind = "skip"
            continue
        has_amount = any(r in vals for r in ("taxable", "non_gst", "amount", "total"))
        if not has_amount:
            if desc and last_kind == "data" and row.y0 - last_y1 < 0.6 * row.h and lines:
                lines[-1].description += " " + desc
                last_y1 = row.y1
            elif desc and re.search(r"\d", text) and not re.search(r"[A-Za-z]{3,}.*charges?$|filing$", desc, re.I):
                unparsed += 1
            last_kind = "section" if desc else last_kind
            continue
        if not desc:
            unparsed += 1
            continue
        line = _finalize(desc, vals, cur, row, page_no)
        lines.append(line)
        last_kind, last_y1 = "data", row.y1
    return lines, unparsed


_PAREN_FX = re.compile(r"\(\s*([A-Z]{3})\s*([\d,]+(?:\.\d+)?)\s*(?:@\s*([\d.]+))?\s*\)")


def _finalize(desc: str, vals: dict, cur: str | None, row: Row, page_no: int) -> Line:
    exrate = vals.get("exrate")
    m = _PAREN_FX.search(desc)
    if m:
        cur = cur or m.group(1)
        if m.group(3):
            exrate = parse_number(m.group(3))
        vals.setdefault("fc_amount", parse_number(m.group(2)))
        desc = (desc[:m.start()] + desc[m.end():]).strip()
    cands: list[tuple[str, float]] = []
    if "taxable" in vals or "non_gst" in vals:
        cands.append(("column", round(vals.get("taxable", 0.0) + vals.get("non_gst", 0.0), 2)))
    elif "amount" in vals:
        cands.append(("column", vals["amount"]))
    if vals.get("fc_amount") and exrate and exrate > 1.0:
        cands.append(("fc*roe", round(vals["fc_amount"] * exrate, 2)))
    if vals.get("rate") and vals.get("qty"):
        cands.append(("qty*rate*roe", round(vals["rate"] * vals["qty"] * (exrate if exrate and exrate > 1 else 1.0), 2)))
    if "total" in vals:
        if "gst_pct" in vals:
            cands.append(("total/(1+gst)", round(vals["total"] / (1 + vals["gst_pct"] / 100), 2)))
        if "gst_amt" in vals:
            cands.append(("total-gst", round(vals["total"] - vals["gst_amt"], 2)))
        if not cands:
            cands.append(("total", vals["total"]))
    if not cands:
        return Line(desc, None, 0.0, cur or "INR", exrate, vals, [], page_no)
    support = [(sum(_close(v, o) for _, o in cands), -i, name, v) for i, (name, v) in enumerate(cands)]
    agree, _, name, value = max(support)
    checks = [n for n, v in cands if _close(v, value)]
    n_checks = len(cands)
    conf = min(w.conf for w in row.words)
    conf = conf * (1.0 if agree >= 2 else (0.85 if n_checks == 1 else 0.6))
    if agree < 2 and n_checks > 1 and cands[0][0] == "column":
        value, name = cands[0][1], "column"
    return Line(desc, value, round(conf, 3), cur or "INR", exrate, vals, checks, page_no)


def extract_lines(pages_rows: list[tuple[int, list[Row]]]) -> tuple[list[Line], dict]:
    lines, info = [], {"tables": [], "unparsed_rows": 0}
    for page_no, rows in pages_rows:
        hdr = find_header(rows)
        if not hdr:
            continue
        i, _, band = hdr
        cols = partition_columns(rows, i, band)
        ls, unparsed = parse_rows(rows, i, cols, page_no)
        lines += ls
        info["unparsed_rows"] += unparsed
        info["tables"].append({"page": page_no, "columns": [f"{c.role}:{c.text}" for c in cols], "lines": len(ls)})
    return lines, info


_SUBTOTAL = re.compile(r"(sub\s*-?\s*total(?!\s*-)|total\s*taxable\s*value|taxable\s*amount|net\s*amount|"
                       r"total\s*\(?before\s*tax|total\s*non[\s-]*taxable)", re.I)


def printed_subtotal(pages_rows: list[tuple[int, list[Row]]]) -> float | None:
    """Pre-tax total printed on the invoice (sum of non-taxable + taxable when printed separately)."""
    found: dict[str, float] = {}
    for _, rows in pages_rows:
        for row in rows:
            m = _SUBTOTAL.search(row.text)
            if not m or re.search(r"sub\s*total\s*-", row.text, re.I):
                continue
            nums = [parse_number(w.text) for w in row.words if _is_num(w.text)]
            nums = [n for n in nums if n is not None]
            if nums:
                key = "non" if "non" in m.group(0).lower() else "tax"
                found.setdefault(key, nums[-1])
    if not found:
        return None
    return round(sum(found.values()), 2)
