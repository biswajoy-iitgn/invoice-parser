"""Full capture: everything printed on the document, each item with its own box.

The 31 output fields are what the business asked for; an invoice prints much more (PO number, due date, both
parties' tax ids, vessel, ports, package count, every column of the charge table, tax summary, bank details,
e-invoice numbers ...). This module turns the measured layout (layout.Line / Unit) into "facts":

  pair    a printed label and its value          "DUE DATE" -> "Jul 20, 2025"      (inline "a: b", or a label/value grid)
  table   one cell of a table, with its column   "TAX IN INR" -> "CGST 9%=105.45," (section = which row)
  block   a heading and the lines under it       "PARTY NAME" -> "Om Steel Pvt. Ltd., 139, MIDC, ..."
  text    anything else that is printed          the vendor's address line, a declaration, a stamp

No model is involved: the rules look only at where text sits (columns that line up, gaps, colons), so a fact is
always text that is really on the page, with the box it was read from. Nothing printed is dropped: what the rules
cannot label is kept as `text`. Labels are best effort; values and boxes are exact.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field as dc_field

from .layout import Line, Unit

LABEL_WORDS = ("a/c no", "a/c no.", "account no", "account number", "acc no", "ifsc", "ifsc code", "swift", "swift code",
               "branch", "bank", "email", "e-mail", "tel", "telephone", "phone", "mobile", "fax", "gstin", "gst no",
               "gstin no", "pan", "pan no", "cin", "state", "state code", "irn", "ack no", "ack no.", "ack date",
               "place of supply", "website", "web", "msme", "lut no", "iec", "tan")
TYPES = (("gstin", re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$")),
         ("pan", re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")),
         ("irn", re.compile(r"^[0-9a-f]{64}$")),
         ("email", re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")),
         ("ifsc", re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")),
         ("container", re.compile(r"^[A-Z]{3}[UJZ]\s?\d{6}\s?\d$")),
         ("amount", re.compile(r"^-?(?:[A-Z]{3}\s)?\d{1,3}(?:,\d{2,3})*\.\d{2}$|^-?\d+\.\d{2}$")),
         ("percent", re.compile(r"^\d+(\.\d+)?\s?%$")),
         ("date", re.compile(r"^(\d{1,2}[-/. ]\w{2,9}[-/. ,]+\d{2,4}|\w{3,9}\.? \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2})(\s+\d{1,2}:\d{2}(:\d{2})?)?$")),
         ("phone", re.compile(r"^(?=.{9,18}$)(\+\d[\d\s()-]+\d|0?\d{2,5}[\s-]\d[\d\s-]+\d|[6-9]\d{9})$")),
         ("number", re.compile(r"^-?[\d,]+(\.\d+)?$")))
_DATE_TAIL = r"\d{1,2}[-/. ][A-Za-z]{3,9}[-/. ,]+\d{2,4}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2}"
_TAIL_AMOUNT = re.compile(r"^(?P<label>[A-Za-z][A-Za-z .&/()%'-]{1,40}?)\s*[:#]?\s+(?P<value>-?(?:[A-Z]{3} )?[\d,]+(?:\.\d+)?)$")
_TAIL_TYPED = re.compile(r"^(?P<label>[A-Za-z][A-Za-z .&/()%'-]{1,40}?)\s*[:#]?\s+(?P<value>" + _DATE_TAIL +
                         r"|(?=\S*\d)[A-Z0-9][A-Z0-9/-]{4,31})$")


def tail_pair(text: str):
    """"DUE DATE 15-Oct-26" -> ("DUE DATE", "15-Oct-26"): a label and a typed value printed with one space between."""
    m = _TAIL_AMOUNT.match(text) or _TAIL_TYPED.match(text)
    if not m or not looks_label(m.group("label")) or len(m.group("label").split()) > 5:
        return None
    return m.group("label").rstrip(": #"), m.group("value")


_COLON = re.compile(r"\s*(?::-|:|#)\s+|\s*(?::-|:)$|(?<=[A-Za-z.)])\s*:(?=\S)")


@dataclass
class Cell:
    text: str
    box: tuple
    units: list
    line: str
    page: int

    @property
    def x0(self):
        return self.box[0]

    @property
    def x1(self):
        return self.box[2]


@dataclass
class Fact:
    kind: str                      # pair | table | block | text
    label: str
    value: str
    page: int
    bbox: list
    line: str
    section: str = ""              # the heading above it / which table row
    value_type: str = ""           # gstin, pan, irn, email, ifsc, phone, amount, percent, date, number, container, text
    field: str = ""                # the output field this value feeds, when it is one of the 31
    label_bbox: list = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = [round(float(v), 2) for v in self.bbox]
        d["label_bbox"] = [round(float(v), 2) for v in self.label_bbox]
        return d


def value_type(v: str) -> str:
    s = v.strip().rstrip(".,;")
    for name, rx in TYPES:
        if rx.match(s):
            return name
    return "text"


def _union(boxes) -> tuple:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _char_w(units: list[Unit]) -> float:
    n = sum(len(u.text) for u in units)
    return (sum(u.box[2] - u.box[0] for u in units) / n) if n else 5.0


def line_cells(line: Line) -> list[Cell]:
    """The cells of a line: the same segmentation as the layout text (runs separated by two or more spaces)."""
    units = list(line.units)
    cells, k, carry = [], 0, 0
    for seg in re.findall(r"\S+(?: \S+)*", line.text):
        want, got, grp = len(seg.replace(" ", "")) - carry, 0, []
        while k < len(units) and got < want:
            grp.append(units[k])
            got += len(re.sub(r"\s", "", units[k].text))
            k += 1
        carry = max(0, got - want)
        if grp:
            cells.append(Cell(seg, _union(u.box for u in grp), grp, line.id, line.page))
        elif cells:                      # one OCR segment holds text the layout shows as two runs
            cells[-1].text += "  " + seg
    if k < len(units) and cells:
        cells[-1].units += units[k:]
        cells[-1].box = _union(u.box for u in cells[-1].units)
    return cells


def looks_label(t: str) -> bool:
    t = t.strip().rstrip(":#-").strip()
    if not (2 <= len(t) <= 42):
        return False
    letters, digits = sum(c.isalpha() for c in t), sum(c.isdigit() for c in t)
    return (letters >= 2 and digits <= 1 and letters >= 0.5 * len(t.replace(" ", "")) and "@" not in t
            and t[0].isalpha())


def _has_digit(t: str) -> bool:
    return bool(re.search(r"\d", t))


def _sub(cell: Cell, start: int, end: int) -> tuple:
    """Box of cell.text[start:end]: whole words where the cell is made of words, a proportional slice of an OCR segment."""
    pos, boxes = 0, []
    for u in cell.units:
        a, b = pos, pos + len(u.text)
        if b > start and a < end:
            if a >= start and b <= end or len(cell.units) > 1 and not (" " in u.text.strip()):
                boxes.append(u.box)
            else:                       # part of one OCR segment
                w = (u.box[2] - u.box[0]) / max(1, len(u.text))
                boxes.append((u.box[0] + w * max(0, start - a), u.box[1], u.box[0] + w * min(len(u.text), end - a), u.box[3]))
        pos = b + 1
    return _union(boxes) if boxes else cell.box


def inline_pairs(cell: Cell) -> list[tuple]:
    """"Tel: +91 70 5421 Email: a@b.c" -> [(label, value, label box, value box)]. [] when the cell has no "label:"."""
    text = cell.text
    marks = [m for m in _COLON.finditer(text) if m.start() > 0]
    # a colon inside a time ("15:02"), a ratio or a URL is not a label mark
    marks = [m for m in marks if not re.search(r"\d$", text[:m.start()]) or not re.match(r"\d", text[m.end():m.end() + 1] or "x")]
    if not marks:
        return []
    out, label_start = [], 0
    for k, m in enumerate(marks):
        label = text[label_start:m.start()].strip()
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        chunk = text[m.end():end]
        nxt_label = ""
        if k + 1 < len(marks):                       # the tail of this chunk is the next label
            words = chunk.rstrip().split(" ")
            low = chunk.rstrip().lower()
            known = max((w for w in LABEL_WORDS if low.endswith(" " + w) or low == w), key=len, default="")
            if known:
                nxt_label = chunk.rstrip()[len(chunk.rstrip()) - len(known):]
            else:
                tail = []
                for w in reversed(words):
                    if _has_digit(w) or "@" in w or len(tail) >= 3:
                        break
                    tail.insert(0, w)
                if len(tail) == len(words) and len(words) > 1:
                    tail = tail[-1:]
                nxt_label = " ".join(tail)
        value = chunk[:len(chunk.rstrip()) - len(nxt_label)].strip() if nxt_label else chunk.strip()
        v_at = text.find(value, m.end()) if value else -1
        l_at = text.find(label, label_start) if label else -1
        if label and looks_label(label) or label.lower().rstrip(".") in LABEL_WORDS:
            out.append((label.rstrip(" .") if label.lower() in LABEL_WORDS else label, value,
                        _sub(cell, l_at, l_at + len(label)) if l_at >= 0 else cell.box,
                        _sub(cell, v_at, v_at + len(value)) if v_at >= 0 else cell.box))
        elif not out:
            return []                               # the text before the first colon is not a label: leave the cell whole
        label_start = end - len(nxt_label) if nxt_label else end
    return out


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _is_header(cells: list[Cell]) -> bool:
    return len(cells) >= 3 and all(looks_label(c.text) for c in cells)


def _column(cell: Cell, header: list[Cell], tol: float) -> int:
    """Which column a cell sits under: an aligned left or right edge, else the column whose title starts at or
    before the cell (a title sits at the start of its column, numbers are pushed to its end), else the nearest."""
    for k, h in enumerate(header):
        if _near(cell.x0, h.x0, tol) or _near(cell.x1, h.x1, tol):
            return k
    for k, h in enumerate(header):
        end = header[k + 1].x0 - tol if k + 1 < len(header) else float("inf")
        if h.x0 - tol <= cell.x0 < end:
            return k
    centre = (cell.x0 + cell.x1) / 2
    return min(range(len(header)), key=lambda k: abs(centre - (header[k].x0 + header[k].x1) / 2))


def _split_by_columns(cell: Cell, header: list[Cell], tol: float) -> list[tuple[int, Cell]]:
    """A cell that runs across two columns ("1,382.58 CGST 9%=105.45") is cut where the next column begins."""
    first = _column(Cell(cell.units[0].text, cell.units[0].box, [cell.units[0]], cell.line, cell.page), header, tol)
    parts, cur, col = [], [], first
    for u in cell.units:
        nxt = next((k for k in range(col + 1, len(header)) if u.box[0] >= header[k].x0 - tol), None)
        if nxt is not None and cur and cell.x0 < header[nxt].x0 - tol:
            parts.append((col, cur))
            cur, col = [], max(k for k in range(col + 1, len(header)) if u.box[0] >= header[k].x0 - tol)
        cur.append(u)
    parts.append((col, cur))
    if len(parts) == 1:
        return [(_column(cell, header, tol), cell)]
    return [(k, Cell(" ".join(u.text for u in us), _union(u.box for u in us), us, cell.line, cell.page)) for k, us in parts]


def document_facts(index: dict[str, Line], citations: dict | None = None, line_items: list | None = None) -> list[dict]:
    """All facts of the document in reading order (see the module text)."""
    facts: list[Fact] = []
    pages = sorted({l.page for l in index.values()})
    for page in pages:
        lines = [l for l in index.values() if l.page == page]
        rows = [(l, line_cells(l)) for l in lines]
        rows = [(l, cs) for l, cs in rows if cs]
        if not rows:
            continue
        cw = _char_w([u for l, _ in rows for u in l.units])
        tol = max(2.5, 1.2 * cw)
        used: set = set()
        section = ""
        heights = sorted(l.box[3] - l.box[1] for l, _ in rows)
        pitch = heights[len(heights) // 2] or 10.0
        gaps = sorted(max(0.0, b[0].box[1] - a[0].box[3]) for a, b in zip(rows, rows[1:]))
        usual_gap = gaps[len(gaps) // 2] if gaps else 0.0
        i = 0
        while i < len(rows):
            line, cs = rows[i]
            nxt = rows[i + 1][1] if i + 1 < len(rows) else []
            if i and line.box[1] - rows[i - 1][0].box[3] > 1.6 * pitch:
                section = ""                     # a clear gap above: the heading no longer applies
            # ---- a table: a row of column titles, then rows whose numbers sit under them
            third = rows[i + 2][1] if i + 2 < len(rows) else []
            grid = (len(cs) % 2 == 0 and len(nxt) == len(cs) == len(third)
                    and all(looks_label(c.text) for c in nxt[0::2] + third[0::2])
                    and all(_near(a.x0, b.x0, tol) and _near(a.x0, c.x0, tol) for a, b, c in zip(cs[0::2], nxt[0::2], third[0::2])))
            if _is_header(cs) and nxt and any(_has_digit(c.text) and _column(c, cs, tol) > 0 for c in nxt) and not grid:
                header, name = cs, " / ".join(c.text for c in cs[:2])
                used.update((line.id, k) for k in range(len(cs)))
                for c in cs:
                    facts.append(Fact("table", "(column title)", c.text, page, list(c.box), line.id, f"table: {name}"))
                j, row_no, row_name = i + 1, 0, ""
                while j < len(rows):
                    rline, rcs = rows[j]
                    placed = [p for c in rcs for p in _split_by_columns(c, header, tol)]
                    in_first = any(k == 0 for k, _ in placed)
                    numeric_under = any(k > 0 and _has_digit(c.text) for k, c in placed)
                    total_like = not in_first and (
                        len(rcs) >= 2 and looks_label(rcs[0].text) and _has_digit(rcs[-1].text)
                        or len(rcs) == 1 and bool(_TAIL_AMOUNT.match(rcs[0].text)))      # "Net Total 1,88,151.93"
                    wrapped = (row_no and len(rcs) == 1 and len(header) > 1 and rcs[0].x1 < header[1].x0)   # a description's 2nd line
                    sentence = all(inline_pairs(c) for c in rcs) or (len(rcs) == 1 and len(placed) > 2)
                    if (not numeric_under and not wrapped) or total_like or sentence or _is_header(rcs):
                        break
                    if in_first and numeric_under or not row_no:
                        row_no += 1
                        row_name = next((c.text for k, c in placed if k == 0), "")[:60]
                    for k, c in placed:
                        facts.append(Fact("table", header[k].text, c.text, page, list(c.box), rline.id,
                                          f"table: {name} · row {row_no}" + (f": {row_name}" if row_name else ""),
                                          value_type(c.text)))
                    used.update((rline.id, k) for k in range(len(rcs)))
                    j += 1
                i, section = j, ""
                continue
            # ---- a heading on its own line: it names what follows ("PARTY NAME", "Bill To", "Consignment Details")
            prev_single = i > 0 and len(rows[i - 1][1]) == 1 and _near(rows[i - 1][1][0].x0, cs[0].x0, 3 * tol) \
                and line.box[1] - rows[i - 1][0].box[3] <= 1.6 * pitch
            set_apart = i == 0 or line.box[1] - rows[i - 1][0].box[3] >= max(1.5 * usual_gap, 0.35 * pitch)
            if len(cs) == 1 and looks_label(cs[0].text) and len(cs[0].text) <= 32 and not prev_single and set_apart and (
                    cs[0].text.isupper() or cs[0].text.rstrip().endswith(":") or cs[0].text.istitle()) and nxt and \
                    _near(nxt[0].x0, cs[0].x0, 3 * tol):
                body, j = [], i + 1
                while j < len(rows) and len(rows[j][1]) == 1 and _near(rows[j][1][0].x0, cs[0].x0, 3 * tol) \
                        and not inline_pairs(rows[j][1][0]) and not looks_heading(rows[j][1][0].text) and len(body) < 6:
                    body.append(rows[j])
                    j += 1
                section = cs[0].text.rstrip(": ")
                used.add((line.id, 0))
                if body:
                    facts.append(Fact("block", section, ", ".join(b[1][0].text for b in body), page,
                                      list(_union(b[1][0].box for b in body)), body[0][0].id, section, "text",
                                      label_bbox=list(cs[0].box)))
                    used.update((b[0].id, 0) for b in body)
                else:
                    facts.append(Fact("text", "", cs[0].text, page, list(cs[0].box), line.id, section, "heading"))
                i = j
                continue
            # ---- label / value pairs on this line
            pairs_done = False
            plain = lambda c: not inline_pairs(c) and not tail_pair(c.text)

            def same_columns(a, b):
                return len(a) == len(b) and all(_near(x.x0, y.x0, tol) for x, y in zip(a, b))

            def grid_row(r):
                return len(r) % 2 == 0 and same_columns(r[0::2], cs[0::2]) and all(looks_label(c.text) for c in r[0::2])

            window = [rows[k][1] for k in range(max(0, i - 2), min(len(rows), i + 3)) if k != i]
            peers = [r for r in window if len(r) == len(cs) and grid_row(r)]
            # a grid: the same label columns on two more lines, and at least one value that is plainly a value
            in_grid = len(peers) >= 2 and any(_has_digit(c.text) or not looks_label(c.text)
                                              for r in peers + [cs] for c in r[1::2])
            # a row of short labels with one row of values right under them, column by column
            if (len(cs) >= 2 and not in_grid and all(looks_label(c.text) and plain(c) and len(c.text.split()) <= 4 for c in cs)
                    and nxt and same_columns(nxt, cs) and not all(looks_label(c.text) and c.text.isupper() and
                                                                 len(c.text.split()) <= 2 for c in nxt)):
                for a, b in zip(cs, nxt):
                    facts.append(Fact("pair", a.text.rstrip(": "), b.text, page, list(b.box), rows[i + 1][0].id, section,
                                      value_type(b.text), label_bbox=list(a.box)))
                used.update((line.id, k) for k in range(len(cs)))
                used.update((rows[i + 1][0].id, k) for k in range(len(nxt)))
                i += 2
                continue
            even = (len(cs) >= 2 and len(cs) % 2 == 0 and all(looks_label(c.text) and plain(c) for c in cs[0::2])
                    and all(plain(c) and not c.text.startswith(":") for c in cs[1::2]))
            if even:
                # the same label columns on the line above or below: a label / value grid
                aligned = in_grid
                # or a short label next to a value that is clearly a value (a number, a date, an id)
                typed = all(len(a.text.split()) <= 4 and value_type(b.text) != "text" for a, b in zip(cs[0::2], cs[1::2]))
                if aligned or typed:
                    for a, b in zip(cs[0::2], cs[1::2]):
                        facts.append(Fact("pair", a.text.rstrip(": "), b.text, page, list(b.box), line.id, section,
                                          value_type(b.text), label_bbox=list(a.box)))
                    used.update((line.id, k) for k in range(len(cs)))
                    pairs_done = True
            if not pairs_done:
                for k, c in enumerate(cs):
                    if (line.id, k) in used:
                        continue
                    nxt_cell = cs[k + 1] if k + 1 < len(cs) and (line.id, k + 1) not in used else None
                    # "BL No" | ": 5836209"  and  "INV NO:" | "IVJ..."   (the colon sits in its own column)
                    if nxt_cell is not None and looks_label(c.text) and (
                            re.match(r"^[:#]\s*\S", nxt_cell.text) or c.text.rstrip().endswith(":")):
                        value = re.sub(r"^[:#]\s*", "", nxt_cell.text)
                        at = nxt_cell.text.find(value)
                        facts.append(Fact("pair", c.text.rstrip(": "), value, page, list(_sub(nxt_cell, at, len(nxt_cell.text))),
                                          line.id, section, value_type(value), label_bbox=list(c.box)))
                        used.update({(line.id, k), (line.id, k + 1)})
                        continue
                    got = inline_pairs(c)
                    for label, value, lbox, vbox in got:
                        if value:
                            facts.append(Fact("pair", label, value, page, list(vbox), line.id, section, value_type(value),
                                              label_bbox=list(lbox)))
                    if got:
                        used.add((line.id, k))
            # ---- whatever is left on the line is kept as plain text ("Net Total 1,88,151.93" still splits into a pair)
            for k, c in enumerate(cs):
                if (line.id, k) not in used:
                    tp = tail_pair(c.text)
                    if tp:
                        at = c.text.rfind(tp[1])
                        facts.append(Fact("pair", tp[0], tp[1], page, list(_sub(c, at, len(c.text))), line.id, section,
                                          value_type(tp[1]), label_bbox=list(_sub(c, 0, len(tp[0])))))
                    else:
                        facts.append(Fact("text", "", c.text, page, list(c.box), line.id, section, value_type(c.text)))
                    used.add((line.id, k))
            i += 1
    out = [f.to_dict() for f in facts]
    _link_fields(out, citations or {}, line_items or [])
    for n, f in enumerate(out, 1):
        f["seq"] = n
    return out


def looks_heading(t: str) -> bool:
    t = t.strip()
    return looks_label(t) and len(t) <= 32 and t.isupper() and len(t.split()) <= 4


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def _link_fields(facts: list[dict], citations: dict, line_items: list) -> None:
    """Mark the facts that are the source of one of the 31 output fields (same place on the page)."""
    marks = [(name, h) for name, hits in citations.items() for h in hits[:8]]
    for name, h in marks:
        best = max((f for f in facts if f["page"] == h.get("page", 1)), key=lambda f: _overlap(f["bbox"], h["bbox"]), default=None)
        if best is not None and _overlap(best["bbox"], h["bbox"]) >= 0.5 and not best["field"]:
            best["field"] = name


# ---------------------------------------------------------------- from a document parser (PaddleOCR-VL) --
BLOCK_SECTION = {"header": "page header", "footer": "page footer", "paragraph_title": "title", "doc_title": "title",
                 "vision_footnote": "note", "footnote": "note", "seal": "stamp", "image": "picture", "figure": "picture",
                 "figure_title": "caption", "table_title": "caption", "number": "page number", "aside_text": "margin text"}


def _html_rows(html_text: str) -> list[list[str]]:
    """<table> ... </table> -> rows of cell texts (a cell's own line breaks are kept as "\\n")."""
    import html as _html
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.rows, self.row, self.cell = [], None, None

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = []
            elif tag == "br" and self.cell is not None:
                self.cell.append("\n")

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append("".join(self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

    p = P()
    p.feed(_html.unescape(html_text.replace("\\n", "\n")))
    return [r for r in p.rows if any(c.strip() for c in r)]


def _one_line(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("\\n", " ")).strip()


def _split_text_pairs(text: str) -> list[tuple[str, str]]:
    """"PAN No.: AACCA7791D" / "IFSC: X  MICR: Y" inside a parsed block -> [(label, value)] ([] when it is plain text)."""
    probe = Cell(text, (0.0, 0.0, float(max(1, len(text))), 1.0), [Unit(text, (0.0, 0.0, float(max(1, len(text))), 1.0))], "", 1)
    return [(label, value) for label, value, _lb, _vb in inline_pairs(probe) if value]


def facts_from_blocks(parsed: list[dict], index: dict[str, Line] | None = None, citations: dict | None = None,
                      line_items: list | None = None) -> list[dict]:
    """Facts built from a document parser's blocks (vl_client.parse_document) instead of the line rules above.

    A paragraph stays one item; a table becomes cells with their column titles (or label / value pairs for a form
    drawn as a table); text inside a picture or stamp is an item too. The box of an item is measured on the text
    layer / OCR words when its text can be found there inside the block, otherwise it is the block's box."""
    from .pipeline import locate

    facts: list[Fact] = []
    counter = [0]

    def group() -> str:
        counter[0] += 1
        return f"K{counter[0]}"

    def box_of(value: str, page: int, block: list[float]) -> list[float]:
        if index and value and len(value) <= 120:
            inside = [l for l in index.values() if l.page == page
                      and block[1] - 3 <= (l.box[1] + l.box[3]) / 2 <= block[3] + 3
                      and l.box[2] >= block[0] - 3 and l.box[0] <= block[2] + 3]
            for h in locate(value, inside):
                if h["match"] == "exact" and h["bbox"][0] >= block[0] - 6 and h["bbox"][2] <= block[2] + 6:
                    return [float(v) for v in h["bbox"]]
        return [float(v) for v in block]

    def add(kind, label, value, page, block, line, section, lbox=None):
        value = _one_line(value)
        if value:
            facts.append(Fact(kind, _one_line(label), value, page, box_of(value, page, block), line, section,
                              value_type(value), label_bbox=lbox or []))

    table_no = 0
    for pg in parsed:
        page = pg["page"]
        for b in sorted(pg.get("blocks", []), key=lambda b: (round(b["bbox"][1] / 6), b["bbox"][0])):
            label, content, bbox = (b.get("label") or "text"), str(b.get("content") or ""), b["bbox"]
            if not content.strip() or len(bbox) != 4:
                continue
            if label == "table" and "<t" in content:
                rows = _html_rows(content)
                if not rows:
                    continue
                table_no += 1
                width = max(len(r) for r in rows)
                titled = (len(rows) >= 2 and len(rows[0]) == width >= 3 and all("\n" not in c for c in rows[0])
                          and all(looks_label(c) or not c for c in rows[0])
                          and sum(1 for r in rows[1:] if len(r) == width) >= max(1, (len(rows) - 1) // 2))
                if titled:
                    name = " / ".join(c for c in rows[0][:2] if c)
                    head = group()
                    for c in rows[0]:
                        if c:
                            add("table", "(column title)", c, page, bbox, head, f"table: {name}")
                    for r_no, row in enumerate(rows[1:], 1):
                        line, first = group(), next((c for c in row if c), "")
                        filled = [c for c in row if c]
                        if len(row) != width and len(filled) == 2 and looks_label(filled[0]):     # "Net Total | 875,852.30"
                            add("pair", filled[0].rstrip(": "), filled[1], page, bbox, line, f"table: {name}")
                            continue
                        for k, c in enumerate(row):
                            if c:
                                title = rows[0][k] if k < len(rows[0]) and len(row) == width else ""
                                add("table", title, c, page, bbox, line,
                                    f"table: {name} · row {r_no}" + (f": {_one_line(first)[:60]}" if first else ""))
                    continue
                for row in rows:                               # a form drawn as a table: cells are label / value
                    line, k = group(), 0
                    while k < len(row):
                        c = row[k]
                        parts = [p.strip() for p in c.split("\n") if p.strip()]
                        nxt = row[k + 1] if k + 1 < len(row) else ""
                        if len(parts) >= 2 and looks_label(parts[0]):
                            add("pair", parts[0].rstrip(": "), " ".join(parts[1:]), page, bbox, line, f"table {table_no}")
                        elif len(parts) == 1 and len(row) == 2 and k == 0 and looks_label(c) and nxt and not looks_label(nxt.split("\n")[0]):
                            add("pair", c.rstrip(": "), nxt, page, bbox, line, f"table {table_no}")
                            k += 1
                        elif c:
                            tp = tail_pair(_one_line(c))
                            pairs = _split_text_pairs(_one_line(c))
                            if pairs:
                                for lab, val in pairs:
                                    add("pair", lab, val, page, bbox, line, f"table {table_no}")
                            elif tp:
                                add("pair", tp[0], tp[1], page, bbox, line, f"table {table_no}")
                            else:
                                add("text", "", c, page, bbox, line, f"table {table_no}")
                        k += 1
                continue
            text = _one_line(content.lstrip("# "))
            section = BLOCK_SECTION.get(label, "")
            pairs = _split_text_pairs(text) if len(text) <= 400 else []
            line = group()
            if pairs:
                for lab, val in pairs:
                    add("pair", lab, val, page, bbox, line, section)
            else:
                add("text", "", text, page, bbox, line, section)
    out = [f.to_dict() for f in facts]
    _link_fields(out, citations or {}, line_items or [])
    for n, f in enumerate(out, 1):
        f["seq"] = n
    return out
