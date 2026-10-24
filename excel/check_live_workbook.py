#!/usr/bin/env python3
"""Drive excel/Invoice_Live_Query.xlsx in Excel for Mac the way a person would, and compare with the database.

  .venv/bin/python excel/check_live_workbook.py                 # login handed to Excel for the check only
  .venv/bin/python excel/check_live_workbook.py --saved-login   # use the login stored in the data source
  .venv/bin/python excel/check_live_workbook.py --live          # also: process an invoice in the app and watch
                                                                #   Excel pick the change up by itself

What is checked
  1. filters   type a filter on "Query" -> "Invoices" and "Charges" refresh by themselves and match the database
  2. lookup    paste invoice numbers into "Lookup" column A -> the row fills in (all columns compared), with an
               "Open" link to the evidence page
  3. details   "Details" lists every captured item of those invoices (row count and first rows compared)
  4. live      an invoice processed by the app shows its new "Last updated" in Excel without touching Excel

The login comes from DATABASE_URL (.env.local): the user name is put in the data source for the duration of the
check and the password is handed to Excel through its environment. Neither is written to the workbook.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "excel"))
from build_live_workbook import (COLUMNS, CONTAINER_DOCS, DSN, FILTER, OUT, REFRESH_MINUTES,  # noqa: E402
                                 excel_with_password)
from openpyxl.utils import get_column_letter  # noqa: E402

ODBC_INI = Path.home() / "Library/ODBC/odbc.ini"
TITLES = [t for _, t in COLUMNS]
LASTCOL = get_column_letter(len(TITLES))                            # on "Lookup" (starts in column A)
UPDATED_COL = get_column_letter(2 + TITLES.index("Last updated"))   # on "Invoices" (the query starts in column B)
APP = os.environ.get("INVOICE_APP", "http://127.0.0.1:8000")
# the columns compared on the sheet "Lookup" (one request to Excel per cell, so not all 35)
CHECK_TITLES = ["Invoice No", "Invoice Date", "Vendor Name", "Container Number", "MAWB No", "Bill of Lading / HBL",
                "Sea Freight", "Review status", "Last updated", "Evidence link"]
CHECK_COLS = [get_column_letter(1 + TITLES.index(t)) for t in CHECK_TITLES]
CASES = [
    ("vendor contains 'megalo'", {"vendor": "megalo"}),
    ("dates 2026-08-01 .. 2026-08-31, status APPROVED", {"date_from": "2026-08-01", "date_to": "2026-08-31", "status": "APPROVED"}),
    ("no filter", {}),
]

HANDLERS = '''
on splitText(t, d)
	set AppleScript's text item delimiters to d
	set parts to text items of t
	set AppleScript's text item delimiters to ""
	return parts
end splitText

on joinText(l, d)
	set AppleScript's text item delimiters to d
	set t to l as text
	set AppleScript's text item delimiters to ""
	return t
end joinText

on settle(sheetNames, limit)
	-- give the change time to start a refresh, then wait until no query has been refreshing for 2 seconds
	delay 4
	set quiet to 0
	set waited to 0
	repeat while quiet < 2 and waited < limit
		delay 1
		set waited to waited + 1
		set busy to false
		repeat with nm in sheetNames
			try
				tell application "Microsoft Excel" to set busy to busy or (refreshing of (query table 1 of worksheet (nm as text) of active workbook))
			on error
				set busy to true
			end try
		end repeat
		if busy then
			set quiet to 0
		else
			set quiet to quiet + 1
		end if
	end repeat
	return waited
end settle
'''

STAGE1 = HANDLERS + '''
set out to ""
with timeout of 1500 seconds
	tell application "Microsoft Excel"
		activate
		open workbook workbook file name (system attribute "XL_FILE")
		set wb to active workbook
		set wq to worksheet "Query" of wb
		set wi to worksheet "Invoices" of wb
	end tell
	repeat with spec in paragraphs of (system attribute "XL_CASES")
		set f to my splitText(spec as text, "~")
		tell application "Microsoft Excel" to set value of range "B5:B9" of wq to {{item 2 of f}, {item 3 of f}, {item 4 of f}, {item 5 of f}, {item 6 of f}}
		my settle({"Invoices", "Charges", "More fields"}, 60)
		tell application "Microsoft Excel"
			calculate
			set n to (value of range "B11" of wq) as integer
			set inv to ""
			if n > 0 then set inv to my joinText(value of range ("B2:B" & (n + 1)) of wi, ",")
			set out to out & (item 1 of f) & "|" & n & "|" & ((value of range "B12" of wq) as integer) & "|" & (value of range "B13" of wq) & "|" & inv & linefeed
		end tell
	end repeat
	-- Lookup: paste the invoice numbers in one go, like a person pasting a list
	set typed to my splitText(system attribute "XL_LOOKUP", "~")
	set col to {}
	repeat with t in typed
		set end of col to {t as text}
	end repeat
	tell application "Microsoft Excel"
		set wl to worksheet "Lookup" of wb
		set value of range ("A2:A" & ((count of typed) + 1)) of wl to col
	end tell
	my settle({"LookupData", "Details"}, 90)
	tell application "Microsoft Excel"
		calculate
		set colLetters to my splitText(system attribute "XL_COLS", "~")
		repeat with rr in my splitText(system attribute "XL_ROWS", "~")
			set vals to {}
			repeat with cl in colLetters
				set end of vals to (string value of range ((cl as text) & rr) of wl)
			end repeat
			set out to out & "LOOKUP|" & rr & "|" & my joinText(vals, "~") & linefeed
		end repeat
		set wd to worksheet "Details" of wb
		set detailCount to (count of rows of used range of wd) - 1
		set out to out & "DETAILS|" & detailCount
		repeat with rr in {2, 3, 4}
			set vals to {}
			repeat with c in (cells of range ("A" & rr & ":E" & rr) of wd)
				set end of vals to (string value of c)
			end repeat
			set out to out & "|" & my joinText(vals, "~")
		end repeat
		set out to out & linefeed
		set wm to worksheet "More fields" of wb
		set out to out & "MORE|" & ((count of rows of used range of wm) - 1) & "|" & (count of columns of used range of wm) & linefeed
	end tell
end timeout
return out
'''

STAGE3 = HANDLERS + '''
-- the workbook is still open and nobody touches it: does the new "Last updated" arrive by itself?
set target to system attribute "XL_TARGET"
set want to system attribute "XL_WANT"
set col to system attribute "XL_UPDATED_COL"
set seen to ""
set waited to 0
with timeout of 400 seconds
	repeat while waited < ((system attribute "XL_LIMIT") as integer)
		tell application "Microsoft Excel"
			set wi to worksheet "Invoices" of active workbook
			set n to (count of rows of used range of wi)
			repeat with r from 2 to n
				if (string value of range ("B" & r) of wi) is target then set seen to (string value of range (col & r) of wi)
			end repeat
		end tell
		if seen is want then exit repeat
		delay 5
		set waited to waited + 5
	end repeat
	tell application "Microsoft Excel" to close active workbook saving no
end timeout
return seen & "|" & waited
'''


def expected(url: str, f: dict) -> tuple[list[str], int, float]:
    import psycopg

    marks = {k: "%s" for k in ("invoice", "vendor", "date_from", "date_to", "status")}
    args = [f"%{f.get('invoice', '')}%", f"%{f.get('vendor', '')}%", f.get("date_from") or "1900-01-01",
            f.get("date_to") or "2999-12-31", f.get("status") or "%"]
    where = FILTER.format(**marks)
    with psycopg.connect(url) as cn:
        inv = [r[0] for r in cn.execute(f"SELECT e.invoice_number FROM reporting.invoice_export e WHERE {where}", args)]
        cols = [r[0] for r in cn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='reporting' AND "
            "table_name='invoice_export' AND data_type='numeric'")]
        tot = cn.execute("SELECT " + ", ".join(f"COALESCE(SUM(e.{c}),0), COUNT(e.{c})" for c in cols) +
                         f" FROM reporting.invoice_export e WHERE {where}", args).fetchone()
    return sorted(inv), int(sum(tot[1::2])), float(sum(tot[0::2]))


def lookup_matches(url: str, key: str, cells: list[str]) -> tuple[bool, str]:
    """Compare one row of the sheet Lookup (as displayed) with the database row for that invoice number."""
    import psycopg

    with psycopg.connect(url) as cn:
        cn.execute("SET TIME ZONE 'UTC'")
        row = cn.execute("SELECT " + ", ".join(c for c, _ in COLUMNS) + " FROM reporting.invoice_export "
                         "WHERE UPPER(invoice_number) = UPPER(%s) ORDER BY updated_at DESC", (key,)).fetchone()
    if row is None:
        blank = all(not c for c in cells[2:]) and cells[1] == "not in the database"      # cells[1] is the Invoice Date column
        return blank, "not in the database (Excel says so too)" if blank else f"database has no such invoice, Excel shows {cells[1:4]}"
    bad = []
    wanted = dict(zip(TITLES, row))
    for title, got in zip(CHECK_TITLES[1:], cells[1:]):
        want = wanted[title]
        if title == "Evidence link":
            same = got == ("Open" if want else "")
        elif want is None:
            same = got == ""
        elif isinstance(want, (int, float)) or type(want).__name__ == "Decimal":
            try:
                same = abs(float(got.replace(",", "")) - float(want)) < 0.005
            except ValueError:
                same = False
        elif hasattr(want, "hour"):
            same = got == want.strftime("%Y-%m-%d %H:%M")
        elif hasattr(want, "year"):
            same = got == want.strftime("%Y-%m-%d")
        else:
            same = got.strip() == str(want).strip()
        if not same:
            bad.append(f"{title}: Excel {got!r} / database {want!r}")
    return not bad, f"{len(CHECK_TITLES) - 1} checked columns identical" if not bad else "; ".join(bad[:3])


def details_expected(url: str, typed: list[str]) -> tuple[int, list[list[str]]]:
    import psycopg

    keys = [t.strip().upper() for t in typed if t.strip()]
    with psycopg.connect(url) as cn:
        rows = cn.execute("SELECT d.invoice_number, d.detail_group, replace(d.detail, '_', ' '), d.value, d.normalized "
                          "FROM reporting.invoice_attributes d WHERE UPPER(d.invoice_number) = ANY(%s) "
                          "ORDER BY d.invoice_number, d.seq", (keys,)).fetchall()
    return len(rows), [[str(c or "") for c in r] for r in rows[:3]]


def osa(script_text: str, env: dict, timeout: int) -> subprocess.CompletedProcess:
    f = Path(os.environ.get("TMPDIR", "/tmp")) / "invoice_live_check.applescript"
    f.write_text(script_text)
    try:
        return subprocess.run(["osascript", str(f)], env=env, capture_output=True, text=True, timeout=timeout)
    finally:
        f.unlink(missing_ok=True)


def main() -> int:
    for line in (ROOT / ".env.local").read_text().splitlines():
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip().strip("'\""))
    url = os.environ["DATABASE_URL"]
    u = urllib.parse.urlparse(url)
    work = CONTAINER_DOCS / "invoice_live_check.xlsx"
    shutil.copy2(OUT, work)
    ini = ODBC_INI.read_text()
    sect = re.search(rf"(?ms)^\[{DSN}\]\n.*?(?=^\[|\Z)", ini)
    swapped = ini.replace(sect.group(0), re.sub(r"(?m)^Username = .*$", f"Username = {u.username}", sect.group(0)))
    saved, live, ok = "--saved-login" in sys.argv, "--live" in sys.argv, True
    try:
        if saved:
            excel_with_password(None)
        else:
            with open(ODBC_INI, "r+") as fh:
                fh.seek(0), fh.truncate(), fh.write(swapped)
            excel_with_password(urllib.parse.unquote(u.password or ""))
        lines = "\n".join("~".join([name, f.get("invoice", ""), f.get("vendor", ""), f.get("date_from", ""),
                                    f.get("date_to", ""), f.get("status", "All")]) for name, f in CASES)
        typed = ["7044012289", "amd1142005", "NO-SUCH-1", "  GRIA260934 "] + [f"FAKE-{i:06d}" for i in range(120)] + ["ERIA261452"]
        check_rows = [2, 3, 4, 5, len(typed) + 1]
        env = dict(os.environ, XL_FILE=str(work), XL_CASES=lines, XL_LOOKUP="~".join(typed),
                   XL_ROWS="~".join(map(str, check_rows)), XL_COLS="~".join(CHECK_COLS))
        try:
            r = osa(STAGE1, env, 1600)
        except subprocess.TimeoutExpired:
            print("TIMEOUT: Excel did not answer (a dialog may be open)")
            return 1
        if r.returncode:
            print("Excel error:", r.stderr.strip()[:400])
            return 1
        got = {l.split("|")[0]: l.split("|") for l in r.stdout.splitlines() if "|" in l}
        print("1. filters (typed on the sheet Query; the tables refresh by themselves)")
        for name, f in CASES:
            inv, lines_n, total = expected(url, f)
            g = got.get(name)
            same = bool(g) and (sorted(x for x in g[4].split(",") if x) == inv and int(g[2]) == lines_n
                                and abs(float(g[3] or 0) - total) < 0.01)
            ok = ok and same
            print(f"   {name}: Excel {g[1] if g else '?'} invoices / {g[2] if g else '?'} charge lines / "
                  f"{float(g[3] or 0) if g else 0:,.2f} | database {len(inv)} / {lines_n} / {total:,.2f} -> "
                  f"{'MATCH' if same else 'DIFFERENT'}")
        look = [l.split("|", 2) for l in r.stdout.splitlines() if l.startswith("LOOKUP|")]
        print(f"2. lookup ({len(typed)} invoice numbers pasted into column A; the list sent to the database is "
              f"{sum(len(t.strip()) + 1 for t in typed)} characters long)")
        for _, rr, vals in look:
            cells = vals.split("~")
            same, detail = lookup_matches(url, cells[0].strip(), cells)
            ok = ok and same
            print(f"   row {rr}: '{cells[0].strip()}' -> {detail} -> {'MATCH' if same else 'DIFFERENT'}")
        ok = ok and len(look) == len(check_rows)
        det = next((l.split("|") for l in r.stdout.splitlines() if l.startswith("DETAILS|")), None)
        want_n, want_rows = details_expected(url, typed)
        got_rows = [x.split("~") for x in det[2:]] if det else []
        same = bool(det) and int(det[1]) == want_n and got_rows == want_rows[:len(got_rows)]
        ok = ok and same
        print(f"3. details: Excel lists {det[1] if det else '?'} arranged details for those invoices | database {want_n}; "
              f"first rows {'identical' if got_rows == want_rows[:len(got_rows)] else 'DIFFERENT'} -> "
              f"{'MATCH' if same else 'DIFFERENT'}")
        if not same and det:
            print("     Excel:", got_rows[:1], "| database:", want_rows[:1])
        more = next((l.split("|") for l in r.stdout.splitlines() if l.startswith("MORE|")), None)
        import psycopg

        with psycopg.connect(url) as cn:
            n_inv = cn.execute("SELECT count(*) FROM reporting.invoice_export").fetchone()[0]
            n_cols = cn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='reporting' "
                                "AND table_name='invoice_attributes_wide'").fetchone()[0] - 3
        same = bool(more) and int(more[1]) == n_inv and int(more[2]) == n_cols
        ok = ok and same
        print(f"   more fields (no filter): Excel {more[1] if more else '?'} invoices x {more[2] if more else '?'} columns | "
              f"database {n_inv} x {n_cols} -> {'MATCH' if same else 'DIFFERENT'}")
        if live:
            import httpx
            import psycopg

            target, sample = "7044012289", "DACHSER.pdf"
            sql = "SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') FROM reporting.invoice_export " \
                  "WHERE invoice_number = %s ORDER BY updated_at DESC LIMIT 1"
            with psycopg.connect(url) as cn:
                before = cn.execute(sql, (target,)).fetchone()[0]
            while time.strftime("%Y-%m-%d %H:%M", time.gmtime()) == before:     # the cell shows minutes
                time.sleep(5)
            t0 = time.time()
            with open(ROOT / "sample invoices" / sample, "rb") as fh:
                resp = httpx.post(f"{APP}/extract", files={"file": (sample, fh, "application/pdf")}, timeout=900)
            persisted = (resp.json().get("persistence") or {}).get("persisted")
            with psycopg.connect(url) as cn:
                after = cn.execute(sql, (target,)).fetchone()[0]
            print(f"4. live: the app processed {target} in {time.time() - t0:.0f} s (saved to the database: {persisted}); "
                  f"'Last updated' in the database went {before} -> {after}")
            r3 = osa(STAGE3, dict(os.environ, XL_TARGET=target, XL_WANT=after, XL_UPDATED_COL=UPDATED_COL,
                                  XL_LIMIT=str(REFRESH_MINUTES * 60 + 120)), 500)
            seen, waited = (r3.stdout.strip().split("|") + ["?"])[:2]
            same = seen == after and after != before
            ok = ok and same
            print(f"   Excel, untouched, showed {seen!r} after {waited} s -> {'MATCH' if same else 'NOT UPDATED'} "
                  f"{r3.stderr.strip()[:200]}")
        else:
            subprocess.run(["osascript", "-e", 'tell application "Microsoft Excel" to close active workbook saving no'],
                           capture_output=True)
    finally:
        if not saved:
            with open(ODBC_INI, "r+") as fh:
                fh.seek(0), fh.truncate(), fh.write(ini)
        work.unlink(missing_ok=True)
        excel_with_password(None)
    print("RESULT:", "everything matches the database" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
