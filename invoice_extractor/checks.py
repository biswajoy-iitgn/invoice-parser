"""Arithmetic checks for numeric values: does what was read add up?

Finding a number on the page proves it was read as printed (or misread the same way twice). It does not prove the number is
right. Documents that print totals, rates, taxes or derived values give a second, independent test: the arithmetic.

Checks are config (the `checks:` list of a profile, plus configs/checks.yaml for formulas every table is tried against):

  sum            a table column adds up to a field                       {type: sum, table, column, field, tolerance}
  total_row      a printed "Total" row (or an unlabelled last row of      {type: total_row, table, columns?, label_column?, tolerance}
                 numbers) equals the rows above it
  row_formula    every row: result column = expression over its columns  {type: row_formula, table, result, expr, tolerance}
  field_formula  one field = expression over other fields                {type: field_formula, result, expr, tolerance}
  range          a value (or a whole column) lies within min..max        {type: range, field | table+column, min, max}
  compare        left op right, per row (table) or on fields; numbers    {type: compare, left, op, right, table?, as?: number|date}
                 or dates ("as: date"): YS <= UTS, received >= issued
  same           two fields agree (optionally one slice of a longer one) {type: same, a, b, a_slice?, b_slice?}
  unique         no value repeats in a column (batch IDs, employee IDs)  {type: unique, table, column}
  complete       the named columns are filled in every row               {type: complete, table, columns}
  pattern        a field or column matches a regular expression          {type: pattern, field | table+column, regex}
  identifier     GSTIN, PAN, IFSC, mobile, PIN, vehicle, container ...   {type: identifier, kind, field | table+column}
  date_window    a date is not in the future and not older than N days   {type: date_window, field, max_age_days?}

Expressions use + - * / ( ), numbers and names. Names are column titles or field keys with everything but letters and digits removed, case ignored
("Hourly Rate" -> hourlyrate, total_pay -> totalpay). They are parsed with `ast`, never evaluated as code.

Each check yields {"name", "ok": True | False | None, "detail"}; None means it could not run (a value missing or not a number).
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import yaml

CHECK_TYPES = {"sum", "total_row", "row_formula", "field_formula", "range", "compare", "same", "unique", "complete", "pattern", "identifier", "date_window"}
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
TOTAL_LABEL = re.compile(r"^\s*(grand\s+|net\s+|sub\s*-?\s*)?total\b", re.IGNORECASE)
_OK_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Load)


def norm(name: str) -> str:
    """Column title -> identifier used in expressions."""
    return re.sub(r"[^0-9a-z]", "", str(name).casefold())


def number(s) -> float | None:
    """A printed number: currency signs and thousands separators dropped, a decimal comma understood ("0,047", "1.234,5").
    Digits broken by anything else ("EN10130:2006", "2T222", "12/03/2024") are a code or a date, not a number: None."""
    s = re.sub(r"(?<=[A-Za-z])\.", " ", str(s if s is not None else ""))     # an abbreviation's dot ("Rs. 1,800") is not a decimal
    if re.search(r"\d[^\d.,\s'’-]+\d", s):
        return None
    s = re.sub(r"[^\d.,-]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}|-?0,\d+", s) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse(expr: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        if not (re.fullmatch(r"[A-Za-z0-9_ ]+", expr) and re.search(r"[A-Za-z]", expr)):
            raise
        tree = ast.parse(norm(expr), mode="eval")   # a lone column title such as "Yield Strength" is one name
    for node in ast.walk(tree):
        if not isinstance(node, _OK_NODES) or (isinstance(node, ast.Constant) and not isinstance(node.value, (int, float))):
            raise ValueError(f"expression {expr!r}: only numbers, names and + - * / ( ) are allowed")
    return tree


def names_in(expr: str) -> set[str]:
    return {norm(n.id) for n in ast.walk(parse(expr)) if isinstance(n, ast.Name)}


def evaluate(expr: str, env: dict[str, float | None]) -> float | None:
    """The value of `expr`, or None when a name has no number (or a division by zero)."""
    def go(n):
        if isinstance(n, ast.Expression):
            return go(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            v = env.get(norm(n.id))
            if v is None:
                raise KeyError(n.id)
            return v
        if isinstance(n, ast.UnaryOp):
            return -go(n.operand) if isinstance(n.op, ast.USub) else go(n.operand)
        a, b = go(n.left), go(n.right)
        return a + b if isinstance(n.op, ast.Add) else a - b if isinstance(n.op, ast.Sub) else a * b if isinstance(n.op, ast.Mult) else a / b

    try:
        return go(parse(expr))
    except (KeyError, ZeroDivisionError):
        return None


def validate(check: dict, tables: dict[str, list[str]], fields: set[str]) -> None:
    """Raises ValueError for a check that cannot work: unknown type, table, column, field or name."""
    t = check.get("type")
    if t not in CHECK_TYPES:
        raise ValueError(f"check type must be one of {sorted(CHECK_TYPES)}")
    cols = {norm(c): c for c in tables.get(check.get("table"), [])}
    if t in ("sum", "total_row", "row_formula", "unique", "complete") and check.get("table") not in tables:
        raise ValueError(f"check {check}: unknown table")
    if t == "sum" and (norm(check.get("column", "")) not in cols or check.get("field") not in fields):
        raise ValueError(f"check {check}: needs one of the table's columns and a known field")
    if t == "total_row":
        for c in ([check["column"]] if "column" in check else check.get("columns") or []) + ([check["label_column"]] if "label_column" in check else []):
            if norm(c) not in cols:
                raise ValueError(f"check {check}: unknown column {c!r}")
    if t == "row_formula":
        if norm(check.get("result", "")) not in cols:
            raise ValueError(f"check {check}: 'result' must be one of the table's columns")
        unknown = names_in(check.get("expr", "")) - set(cols)
        if unknown:
            raise ValueError(f"check {check}: unknown column names {sorted(unknown)}; write titles as letters and digits only, lower case")
    if t == "field_formula":
        if check.get("result") not in fields:
            raise ValueError(f"check {check}: 'result' must be a field")
        unknown = names_in(check.get("expr", "")) - {norm(f) for f in fields}
        if unknown:
            raise ValueError(f"check {check}: unknown fields {sorted(unknown)}")
    if t in ("range", "pattern", "identifier"):
        if not (check.get("field") in fields or (check.get("table") in tables and norm(check.get("column", "")) in cols)):
            raise ValueError(f"check {check}: needs a known field, or a table and one of its columns")
    if t == "range" and "min" not in check and "max" not in check:
        raise ValueError(f"check {check}: needs min or max")
    if t == "pattern":
        try:
            re.compile(check.get("regex", ""))
        except re.error as e:
            raise ValueError(f"check {check}: bad regex ({e})") from e
    if t == "identifier":
        from .identifiers import KINDS

        if check.get("kind") not in KINDS:
            raise ValueError(f"check {check}: kind must be one of {sorted(KINDS)}")
    if t == "compare":
        if check.get("op") not in OPS or "left" not in check or "right" not in check:
            raise ValueError(f"check {check}: needs left, right and op (one of {sorted(OPS)})")
        known = set(cols) if check.get("table") in tables else {norm(f) for f in fields}
        if check.get("table") is not None and check["table"] not in tables:
            raise ValueError(f"check {check}: unknown table")
        names = names_in(str(check["left"])) | names_in(str(check["right"]))
        if names - known:
            raise ValueError(f"check {check}: unknown names {sorted(names - known)}")
    if t == "same" and (check.get("a") not in fields or check.get("b") not in fields):
        raise ValueError(f"check {check}: 'a' and 'b' must be fields")
    if t in ("unique",) and norm(check.get("column", "")) not in cols:
        raise ValueError(f"check {check}: unknown column")
    if t == "complete" and (check.get("table") not in tables or not check.get("columns") or any(norm(x) not in cols for x in check["columns"])):
        raise ValueError(f"check {check}: needs a table and its columns")
    if t == "date_window" and check.get("field") not in fields:
        raise ValueError(f"check {check}: needs a known field")


def _g(x: float) -> str:
    return f"{x:.6g}"


def _part(v, sl) -> str:
    v = str(v or "").strip()
    return v[sl[0]:sl[1]] if sl and v else v


def _side(expr, env: dict, as_date: bool):
    if as_date:
        from .identifiers import parse_date

        return parse_date(str(env.get(norm(str(expr))) or ""))
    return evaluate(str(expr), {k: number(v) for k, v in env.items()})


def _compare(c: dict, fields: dict, tab: dict, col: dict, tol: float):
    as_date = c.get("as") == "date"
    envs = [({norm(k): r.get(v) for k, v in col.items()}, f"row {i}: ") for i, r in enumerate(tab["rows"], 1)] if "table" in c else [({norm(k): v for k, v in fields.items()}, "")]
    bad, ran = [], 0
    for env, where in envs:
        a, b = _side(c["left"], env, as_date), _side(c["right"], env, as_date)
        if a is None or b is None:
            continue
        ran += 1
        ok = abs(a - b) <= tol if c["op"] == "==" and not as_date else OPS[c["op"]](a, b)
        if not ok:
            show = (lambda x: x.isoformat()) if as_date else _g
            bad.append(f"{where}{c['left']} is {show(a)}, {c['right']} is {show(b)}: expected {c['left']} {c['op']} {c['right']}")
    if not ran:
        return None, "not run: a value is missing or not a number"
    return not bad, "; ".join(bad[:3]) or f"{ran} comparison(s) hold"


def run(checks: list[dict], fields: dict[str, str], tables: dict[str, dict]) -> list[dict]:
    """fields: key -> printed value. tables: key -> {"columns": [...], "rows": [{column: printed value}]}."""
    out = []
    for c in checks:
        t, tol = c["type"], float(c.get("tolerance", 0.01))
        name = c.get("name") or {"sum": f"{c.get('column')} adds up to {c.get('field')}", "total_row": f"{c.get('table')}: total row",
                                 "row_formula": f"{c.get('result')} = {c.get('expr')}", "field_formula": f"{c.get('result')} = {c.get('expr')}",
                                 "range": f"{c.get('field') or c.get('column')} within range",
                                 "compare": f"{c.get('left')} {c.get('op')} {c.get('right')}", "same": f"{c.get('a')} agrees with {c.get('b')}",
                                 "unique": f"{c.get('column')} has no repeats", "complete": f"{c.get('table')}: no empty cells",
                                 "pattern": f"{c.get('field') or c.get('column')} matches {c.get('regex')}",
                                 "identifier": f"{c.get('field') or c.get('column')} is a valid {c.get('kind')}",
                                 "date_window": f"{c.get('field')} is a plausible date"}[t]
        ok, detail = None, "not run: a value is missing or not a number"
        tab = tables.get(c.get("table"), {"columns": [], "rows": []})
        col = {norm(x): x for x in tab["columns"]}
        if t == "sum":
            have = number(fields.get(c["field"]))
            vals = [number(r.get(col.get(norm(c["column"]), ""))) for r in tab["rows"]]
            if have is not None and vals and None not in vals:
                ok, detail = abs(sum(vals) - have) <= tol, f"rows add up to {_g(sum(vals))}; {c['field']} is {_g(have)}"
        elif t == "total_row":
            label = col.get(norm(c.get("label_column", "")), tab["columns"][0] if tab["columns"] else "")
            wanted = [col[norm(x)] for x in ([c["column"]] if "column" in c else c.get("columns") or [])] or [x for x in tab["columns"] if x != label]
            tot = [r for r in tab["rows"] if TOTAL_LABEL.match(r.get(label, "") or "")]
            rows = tab["rows"]
            # a merged "Total" cell often comes out empty: a last row with only numbers (the label cell and every column that is
            # not being added up empty) is the total; three rows are needed unless the columns to add up are named
            if not tot and len(rows) >= (2 if ("column" in c or "columns" in c) else 3) and not (rows[-1].get(label) or "").strip() and any(number(rows[-1].get(x)) is not None for x in wanted) \
                    and all(not (rows[-1].get(x) or "").strip() for x in tab["columns"] if x not in wanted):
                tot = [rows[-1]]
            body = [r for r in rows if r not in tot]
            bad, ran = [], 0
            for x in wanted:
                nums = [number(r.get(x)) for r in body]
                for tr in tot:
                    have = number(tr.get(x))
                    if have is None or not nums or None in nums:
                        continue
                    ran += 1
                    if abs(sum(nums) - have) > tol:
                        bad.append(f"{x}: rows add up to {_g(sum(nums))}, the total row says {_g(have)}")
            if ran:
                ok, detail = not bad, "; ".join(bad[:3]) or f"{ran} total(s) match the rows above"
            else:
                detail = "no total row to check"
        elif t == "row_formula":
            bad, ran = [], 0
            for i, r in enumerate(tab["rows"], 1):
                env = {k: number(r.get(v)) for k, v in col.items()}
                want, have = evaluate(c["expr"], env), env.get(norm(c["result"]))
                printed = str(r.get(col.get(norm(c["result"]), "")) or "").strip()
                if want is not None and have is None and printed:     # text where the result should be: a misread cell
                    ran += 1
                    bad.append(f"row {i}: {c['result']} reads {printed[:24]!r}, not a number")
                    continue
                if want is None or have is None:
                    continue
                ran += 1
                if abs(want - have) > tol + float(c.get("relative", 0)) * abs(want):
                    bad.append(f"row {i}: {c['result']} printed {_g(have)}, the formula gives {_g(want)}")
            if ran:
                ok, detail = not bad, "; ".join(bad[:3]) + (f" (and {len(bad) - 3} more)" if len(bad) > 3 else "") or f"{ran} row(s) match"
        elif t == "field_formula":
            env = {norm(k): number(v) for k, v in fields.items()}
            want, have = evaluate(c["expr"], env), env.get(norm(c["result"]))
            if want is not None and have is not None:
                ok, detail = abs(want - have) <= tol, f"{c['result']} is {_g(have)}; the formula gives {_g(want)}"
        elif t == "compare":
            ok, detail = _compare(c, fields, tab, col, tol)
        elif t == "same":
            a, b = _part(fields.get(c["a"]), c.get("a_slice")), _part(fields.get(c["b"]), c.get("b_slice"))
            if a and b:
                ok, detail = norm(a) == norm(b), f"{c['a']} is {a}; {c['b']} is {b}"
        elif t == "unique":
            seen, dup = {}, []
            for i, r in enumerate(tab["rows"], 1):
                v = norm(r.get(col.get(norm(c["column"]), ""), "") or "")
                if v:
                    if v in seen:
                        dup.append(f"{r.get(col[norm(c['column'])])} in rows {seen[v]} and {i}")
                    seen.setdefault(v, i)
            if seen:
                ok, detail = not dup, "; ".join(dup[:3]) or f"{len(seen)} distinct values"
        elif t == "complete":
            first = tab["columns"][0] if tab["columns"] else ""
            body = [(i, r) for i, r in enumerate(tab["rows"], 1) if not TOTAL_LABEL.match(r.get(first, "") or "")]
            if len(body) >= 3 and not (body[-1][1].get(first) or "").strip() and any(number(v) is not None for v in body[-1][1].values()):
                body = body[:-1]                                 # an unlabelled last row of numbers is a total, not a batch
            miss = [f"row {i}: {x}" for i, r in body if any((v or "").strip() for v in r.values())
                    for x in c["columns"] if not (r.get(col[norm(x)]) or "").strip()]
            if tab["rows"]:
                ok, detail = not miss, "; ".join(miss[:4]) + (f" (and {len(miss) - 4} more)" if len(miss) > 4 else "") or "every row is filled in"
        elif t in ("pattern", "identifier"):
            vals = [fields.get(c["field"])] if "field" in c else [r.get(col.get(norm(c["column"]), "")) for r in tab["rows"]]
            vals = [str(v).strip() for v in vals if v is not None and str(v).strip()]
            if vals:
                if t == "pattern":
                    why = [f"{v!r} does not match {c['regex']}" for v in vals if not re.fullmatch(c["regex"], v)]
                else:
                    from .identifiers import validate as ident

                    why = [f"{v}: {m}" for v in vals if (m := ident(c["kind"], v))]
                ok, detail = not why, "; ".join(why[:3]) or f"{len(vals)} value(s) valid"
        elif t == "date_window":
            from datetime import date, timedelta

            from .identifiers import parse_date

            d = parse_date(fields.get(c["field"]) or "")
            if d is not None:
                today = date.today()
                old = c.get("max_age_days")
                bad = "is in the future" if d > today + timedelta(days=1) else f"is more than {old} days old" if old and (today - d).days > old else ""
                ok, detail = not bad, f"{d.isoformat()} {bad}".strip() if bad else f"{d.isoformat()} is within the window"
        else:
            vals = [number(fields.get(c["field"]))] if "field" in c else [number(r.get(col.get(norm(c["column"]), ""))) for r in tab["rows"]]
            vals = [v for v in vals if v is not None]
            if vals:
                lo, hi = c.get("min", float("-inf")), c.get("max", float("inf"))
                out_of = [v for v in vals if not lo <= v <= hi]
                ok, detail = not out_of, f"{_g(out_of[0])} is outside {c.get('min', '')}..{c.get('max', '')}" if out_of else "within range"
        out.append({"name": name, "ok": ok, "detail": detail})
    return out


# ------------------------------------------------------- formulas tried on every table --
def checks_file() -> Path:
    return Path(os.environ.get("CHECKS_FILE") or Path(__file__).resolve().parents[1] / "configs" / "checks.yaml")


def known_formulas() -> list[dict]:
    p = checks_file()
    return (yaml.safe_load(p.read_text()) or {}).get("formulas", []) if p.exists() else []


def automatic(tables: list[dict]) -> list[tuple[int, dict]]:
    """For a table with no profile: [(table index, check)]: a total row against the rows above it, and each known formula whose
    columns are all present. `tables`: [{"columns": [...], "rows": [[...]]}] as the general profile holds them."""
    found = []
    for i, t in enumerate(tables):
        if not t["columns"]:             # no column titles: nothing to name a check by
            continue
        cols = {norm(c) for c in t["columns"] if c}
        labels = [r[0] for r in t["rows"] if r]
        unlabelled = len(t["rows"]) >= 3 and not (t["rows"][-1][0] or "").strip() and any(number(v) is not None for v in t["rows"][-1][1:])
        if any(TOTAL_LABEL.match(x or "") for x in labels) or unlabelled:
            found.append((i, {"type": "total_row", "table": "t", "label_column": t["columns"][0], "tolerance": 0.01,
                              "name": f"table {i + 1}: total row"}))
        for f in known_formulas():
            if norm(f["result"]) in cols and names_in(f["expr"]) <= cols:
                found.append((i, {"type": "row_formula", "table": "t", "result": f["result"], "expr": f["expr"],
                                  "tolerance": f.get("tolerance", 0.01), "name": f"table {i + 1}: {f.get('name', f['result'])}"}))
    return found


def run_automatic(tables: list[dict]) -> list[dict]:
    out = []
    for i, c in automatic(tables):
        t = tables[i]
        rows = [{(t["columns"][j] if j < len(t["columns"]) else f"c{j}"): v for j, v in enumerate(r)} for r in t["rows"]]
        res = run([c], {}, {"t": {"columns": t["columns"], "rows": rows}})[0]
        res["name"] = c["name"]
        out.append(res)
    return out


# ------------------------------------------------ identifiers recognised by their label --
def identifier_rules() -> list[tuple[re.Pattern, str]]:
    p = checks_file()
    rules = (yaml.safe_load(p.read_text()) or {}).get("identifiers", []) if p.exists() else []
    return [(re.compile(r["label"], re.IGNORECASE), r["kind"]) for r in rules]


def run_identifiers(fields: list[dict]) -> list[dict]:
    """The general profile has no field types: a labelled value is validated by what its label says it is (configs/checks.yaml).
    fields: [{"label", "value"}] -> [{"name", "ok", "detail", "label", "field"}] for the ones a rule recognised ("field": its index)."""
    from .identifiers import validate

    rules, out = identifier_rules(), []
    for i, f in enumerate(fields):
        for rx, kind in rules:
            v = str(f["value"]).strip()
            if not rx.search(f["label"]):
                continue
            if kind == "date" and not (len(v) <= 14 and sum(c.isdigit() for c in v) >= 4):
                continue                                  # a "date" label on a sentence or a time stamp is not a date to check
            why = validate(kind, v)
            out.append({"name": f"{f['label']} is a valid {kind}", "ok": not why, "detail": why or f"{v} is valid", "label": f["label"], "field": i})
            break
    return out
