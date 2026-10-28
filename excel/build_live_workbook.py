#!/usr/bin/env python3
"""Build excel/Invoice_Live_Query.xlsx: a macro-free workbook that pulls invoices from the database into Excel.

Sheets:
  Query     filter cells (invoice number, vendor, dates, review status); typing a filter refreshes the tables
  Lookup    type invoice numbers in column A; the other columns fill in from the database by themselves
  Invoices  one row per invoice that matches the filters
  Charges   one row per charge of those invoices (ready for a pivot table)
  Details   the other details of the invoices typed on Lookup, arranged under named details (vendor GSTIN, due
            date, ports, totals, bank ...), one row per detail
  More fields  the same details as columns, one row per invoice that matches the filters
All of it reads the data source NEON_INVOICE_PG through refreshable ODBC queries bound to cells. The file contains
no data, no login and no macros: Excel for Windows and Excel for Mac open the same file.

Building needs Excel for Mac (the query tables are created by Excel itself, then the file is patched to add the
filter parameters). Using the workbook needs nothing but Excel and the data source:

  macOS    python3 odbc/build_excel_pg_driver.py      (free psqlODBC, copied where Excel can load it)
  Windows  powershell -File odbc\\setup_windows.ps1    (free psqlODBC x64)

  .venv/bin/python excel/build_live_workbook.py        # rebuild the workbook (developer step)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from export_invoices import CHARGES, HEADERS, STATUS, TITLE  # noqa: E402

DSN = "NEON_INVOICE_PG"
# every column the queries return: the 31 fields, the status columns, and the address of the evidence page
COLUMNS = HEADERS + STATUS + [("evidence_url", "Evidence link")]
LINK_ROWS = 5000                  # rows of the sheet "Invoices" that carry a clickable "Open" link in column A
REFRESH_MINUTES = 1               # Excel re-reads the database this often while the workbook is open
OUT = ROOT / "excel" / "Invoice_Live_Query.xlsx"
CONTAINER_DOCS = Path.home() / "Library/Containers/com.microsoft.Excel/Data/Documents"
# filter cells on the sheet "Query" (what the user types) and the helper cells the queries read
INPUT = {"invoice": "B5", "vendor": "B6", "date_from": "B7", "date_to": "B8", "status": "B9"}
PARAM = {"invoice": "H5", "vendor": "H6", "date_from": "H7", "date_to": "H8", "status": "H9"}
FILTER = ("COALESCE(e.invoice_number, '') ILIKE {invoice} AND COALESCE(e.vendor_name, '') ILIKE {vendor} "
          "AND COALESCE(e.invoice_date, DATE '1900-01-01') BETWEEN CAST({date_from} AS date) AND CAST({date_to} AS date) "
          "AND COALESCE(e.review_status, '') LIKE {status}")
ORDER = ["invoice", "vendor", "date_from", "date_to", "status"]


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_invoices(marks: dict) -> str:
    cols = ", ".join(f"e.{c} AS {q(t)}" for c, t in COLUMNS)
    return (f"SELECT {cols} FROM reporting.invoice_export e WHERE {FILTER.format(**marks)} "
            "ORDER BY e.invoice_date DESC NULLS LAST, e.invoice_number")


def sql_charges(marks: dict) -> str:
    values = ", ".join(f"('{TITLE[c]}', e.{c})" for c in CHARGES)
    return (f'SELECT e.invoice_number AS "Invoice No", e.invoice_date AS "Invoice Date", e.vendor_name AS "Vendor Name", '
            f'c.charge AS "Charge", c.amount AS "Amount (INR)", e.review_status AS "Review status" '
            f"FROM reporting.invoice_export e CROSS JOIN LATERAL (VALUES {values}) AS c(charge, amount) "
            f"WHERE c.amount IS NOT NULL AND {FILTER.format(**marks)} "
            "ORDER BY e.invoice_date DESC NULLS LAST, e.invoice_number, c.charge")


LOOKUP_ROWS = 200                 # invoice numbers that can be typed on the sheet "Lookup"
# Excel refuses a text parameter longer than 255 characters ("Bad parameter type"), so the typed numbers, joined
# with "|", are handed to the database in pieces: LOOKUP_CHUNKS hidden cells of CHUNK characters each.
LOOKUP_CHUNKS, CHUNK = 24, 240
LOOKUP_PARAM_COL = "AO"           # hidden column on "Lookup": rows 1..LOOKUP_CHUNKS hold the pieces


def sql_lookup(mark: str) -> str:
    cols = ", ".join(f"e.{c} AS {q(t)}" for c, t in COLUMNS)
    return (f"SELECT {cols} FROM reporting.invoice_export e "
            f"WHERE UPPER(e.invoice_number) = ANY(string_to_array({mark}, '|')) ORDER BY e.invoice_number, e.updated_at DESC")


def sql_details(mark: str) -> str:
    """The details of the invoices typed on "Lookup", arranged by the organizer (reporting.invoice_attributes)."""
    return ('SELECT d.invoice_number AS "Invoice No", d.detail_group AS "Group", '
            "replace(d.detail, '_', ' ') AS \"Detail\", d.value AS \"Value\", d.normalized AS \"Normalized\", "
            "CASE WHEN d.valid THEN 'yes' ELSE 'no' END AS \"Check passed\", d.page_number AS \"Page\", "
            'd.printed_label AS "Printed label" '
            f"FROM reporting.invoice_attributes d WHERE UPPER(d.invoice_number) = ANY(string_to_array({mark}, '|')) "
            "ORDER BY d.invoice_number, d.seq")


def sql_more(marks: dict) -> str:
    """One row per invoice that matches the filters, one column per arranged detail (reporting.invoice_attributes_wide)."""
    from invoice_extractor.organizer import DETAILS

    cols = ", ".join(f'w.{key} AS {q(key.replace("_", " ").capitalize())}' for _, key, _, _, _ in DETAILS)
    return (f'SELECT e.invoice_number AS "Invoice No", e.invoice_date AS "Invoice Date", {cols} '
            "FROM reporting.invoice_export e JOIN reporting.invoice_attributes_wide w ON w.invoice_id = e.invoice_id "
            f"WHERE {FILTER.format(**marks)} ORDER BY e.invoice_date DESC NULLS LAST, e.invoice_number")


# the list parameter as the database sees it: the pieces put together again
LIST_MARK = " || ".join(["CAST(? AS text)"] * LOOKUP_CHUNKS)
PLACEHOLDERS = {k: "?" for k in ORDER}
# used once, when Excel creates the queries: a filter that matches nothing, so the template stores no invoice data
EMPTY = {"invoice": "'%'", "vendor": "'%'", "date_from": "'2999-01-01'", "date_to": "'2999-01-02'", "status": "'%'"}


def base_workbook(path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Query"
    ws["A1"] = "Invoice database - live query"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = ("Type a filter (or leave everything empty for all invoices): the sheets Invoices and Charges are filled "
                f"from the database. They also refresh by themselves every {REFRESH_MINUTES} minute(s); Data > Refresh All "
                "does it at once.")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:F2")
    ws.row_dimensions[2].height = 34
    ws["A4"], ws["B4"] = "Filter", "Value"
    for c in ("A4", "B4"):
        ws[c].font = Font(bold=True, color="FFFFFF")
        ws[c].fill = PatternFill("solid", fgColor="1F4E78")
    rows = [("invoice", "Invoice number contains", "part of the number, e.g. AMD"),
            ("vendor", "Vendor name contains", "part of the name, e.g. dachser"),
            ("date_from", "Invoice date from", "a date; empty = no lower limit"),
            ("date_to", "Invoice date to", "a date; empty = no upper limit"),
            ("status", "Review status", "All, PENDING, IN_REVIEW, APPROVED, REJECTED, REPROCESSED")]
    edge = Side(style="thin", color="8EA9C1")
    for key, label, hint in rows:
        cell = ws[INPUT[key]]
        ws[f"A{cell.row}"], ws[f"C{cell.row}"] = label, hint
        ws[f"C{cell.row}"].font = Font(color="7F7F7F", italic=True)
        cell.fill = PatternFill("solid", fgColor="FFF2CC")
        cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
    for key in ("date_from", "date_to"):
        ws[INPUT[key]].number_format = "yyyy-mm-dd"
    ws[INPUT["status"]] = "All"
    dv = DataValidation(type="list", formula1='"All,PENDING,IN_REVIEW,APPROVED,REJECTED,REPROCESSED"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(ws[INPUT["status"]])
    for key in ("date_from", "date_to"):
        dd = DataValidation(type="date", operator="greaterThan", formula1="1", allow_blank=True,
                            showErrorMessage=True, errorTitle="Not a date", error="Enter a date, for example 2026-08-31.")
        ws.add_data_validation(dd)
        dd.add(ws[INPUT[key]])
    ws["A11"], ws["B11"] = "Invoices returned", "=MAX(0,COUNTA(Invoices!B:B)-1)"
    ws["A12"], ws["B12"] = "Charge lines returned", "=MAX(0,COUNTA(Charges!A:A)-1)"
    ws["A13"], ws["B13"] = "Charges before GST (INR)", "=SUM(Charges!E:E)"
    ws["B13"].number_format = "#,##0.00"
    for r in (11, 12, 13):
        ws[f"A{r}"].font = Font(bold=True)
    ws["A15"] = ("First use: the data source NEON_INVOICE_PG must exist on this computer (see docs/EXCEL_LIVE_QUERY.md). "
                 "Excel asks for the database password unless it is saved in the data source.")
    ws["A15"].alignment = Alignment(wrap_text=True, vertical="top")
    ws["A15"].font = Font(color="7F7F7F")
    ws.merge_cells("A15:F15")
    ws.row_dimensions[15].height = 34
    # helper cells the queries read: always text, so both Excel versions send the same thing to the database
    ws["H4"] = "sent to the database"
    b = INPUT
    ws[PARAM["invoice"]] = f'="%"&TRIM({b["invoice"]})&"%"'
    ws[PARAM["vendor"]] = f'="%"&TRIM({b["vendor"]})&"%"'
    iso = 'YEAR({c})&"-"&RIGHT("0"&MONTH({c}),2)&"-"&RIGHT("0"&DAY({c}),2)'
    ws[PARAM["date_from"]] = f'=IF(ISNUMBER({b["date_from"]}),{iso.format(c=b["date_from"])},"1900-01-01")'
    ws[PARAM["date_to"]] = f'=IF(ISNUMBER({b["date_to"]}),{iso.format(c=b["date_to"])},"2999-12-31")'
    ws[PARAM["status"]] = f'=IF(OR(TRIM({b["status"]})="",UPPER(TRIM({b["status"]}))="ALL"),"%",UPPER(TRIM({b["status"]})))'
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 28, 26, 58
    ws.column_dimensions["H"].hidden = True
    ws["A16"] = ("Details for particular invoices: type the invoice numbers in column A of the sheet Lookup; the other "
                 "columns fill in by themselves, and the sheet Details lists their other details (due date, PO number, "
                 "parties, ports, totals, bank ...). The sheet More fields has those details as columns for every invoice "
                 "that matches the filters.")
    ws["A16"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A16:F16")
    ws.row_dimensions[16].height = 34

    # ---- Lookup: type invoice numbers in column A, the row fills from the database ----
    from openpyxl.utils import get_column_letter as L

    lk = wb.create_sheet("Lookup")
    titles = [t for _, t in COLUMNS]
    n = len(titles)
    last = LOOKUP_ROWS + 1
    kcol, jcol, mcol = L(n + 3), L(n + 4), L(n + 2)          # clean key, running list, matched row
    for j, t in enumerate(titles, 1):
        shown = "Invoice No (type here)" if j == 1 else "Evidence" if t == "Evidence link" else t
        c = lk.cell(row=1, column=j, value=shown)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        lk.column_dimensions[L(j)].width = 24 if j == 1 else max(13, min(30, len(t) + 4))
    lk.row_dimensions[1].height = 32
    money = {t for c, t in HEADERS if c in CHARGES}
    for r in range(2, last + 1):
        a = lk.cell(row=r, column=1)
        a.number_format = "@"                                # keep what is typed (leading zeros, long numbers)
        a.fill = PatternFill("solid", fgColor="FFF2CC")
        lk[f"{kcol}{r}"] = f'=IF(TRIM($A{r})="","",UPPER(TRIM($A{r})))'
        lk[f"{jcol}{r}"] = (f"={kcol}{r}" if r == 2 else
                            f'=IF({kcol}{r}="",{jcol}{r - 1},IF({jcol}{r - 1}="",{kcol}{r},{jcol}{r - 1}&"|"&{kcol}{r}))')
        lk[f"{mcol}{r}"] = f'=IF({kcol}{r}="","",IFERROR(MATCH({kcol}{r},LookupData!$A:$A,0),0))'
        for j, t in enumerate(titles[1:], 2):
            src = f"INDEX(LookupData!{L(j)}:{L(j)},${mcol}{r})"
            missing = '"not in the database"' if j == 2 else '""'
            shown = f'HYPERLINK({src},"Open")' if t == "Evidence link" else src
            c = lk.cell(row=r, column=j, value=f'=IF(${mcol}{r}="","",IF(${mcol}{r}=0,{missing},IF({src}="","",{shown})))')
            if t == "Evidence link":
                c.font = Font(color="0563C1", underline="single")
            if t == "Invoice Date":
                c.number_format = "yyyy-mm-dd"
            elif t == "Last updated":
                c.number_format = "yyyy-mm-dd hh:mm"
            elif t in money:
                c.number_format = "#,##0.00"
    whole = L(n + 5)                                         # the whole list in one cell (may be thousands of characters)
    lk[f"{whole}1"] = f'="~none~|"&{jcol}{last}'
    for k in range(1, LOOKUP_CHUNKS + 1):                    # ... and cut into pieces Excel accepts as parameters
        piece = f"MID(${whole}$1,{1 + CHUNK * (k - 1)},{CHUNK})"
        lk[f"{LOOKUP_PARAM_COL}{k}"] = f'=IF({piece}="","|",{piece})'      # never empty: every Excel sends a value
    lk["A1"] = (f'=IF(LEN(${whole}$1)>{LOOKUP_CHUNKS * CHUNK},"TOO MANY NUMBERS: the last ones are not looked up",'
                '"Invoice No (type here)")')
    assert L(n + 5) < LOOKUP_PARAM_COL or len(L(n + 5)) < len(LOOKUP_PARAM_COL), "parameter column overlaps the helpers"
    for col in (mcol, kcol, jcol, whole, LOOKUP_PARAM_COL):
        lk.column_dimensions[col].hidden = True
    lk.freeze_panes = "B2"
    inv = wb.create_sheet("Invoices")
    # column A: a link to the evidence page of the invoice on that row (the query fills B onwards)
    link = L(1 + len(COLUMNS))
    inv["A1"] = "Evidence"
    inv["A1"].font = Font(bold=True)
    for r in range(2, LINK_ROWS + 2):
        c = inv.cell(row=r, column=1, value=f'=IF(INDEX(${link}:${link},ROW())="","",HYPERLINK(INDEX(${link}:${link},ROW()),"Open"))')
        c.font = Font(color="0563C1", underline="single")
    inv.column_dimensions["A"].width = 10
    wb.create_sheet("Charges")
    wb.create_sheet("Details")
    wb.create_sheet("More fields")
    wb.create_sheet("LookupData")
    wb.move_sheet("Lookup", offset=0)                        # order: Query, Lookup, Invoices, Charges, LookupData
    wb.save(path)


APPLESCRIPT = '''
set cs to system attribute "XL_CONN"
with timeout of 150 seconds
	tell application "Microsoft Excel"
		activate
		open workbook workbook file name (system attribute "XL_FILE")
		set wb to active workbook
		set ws to worksheet "Invoices" of wb
		set qt to make new query table at ws with properties {connection:cs, destination:(range "B1" of ws), sql:(system attribute "XL_SQL1")}
		refresh query table qt without background query
		set bold of font object of row 1 of ws to true
		set ws2 to worksheet "Charges" of wb
		set qt2 to make new query table at ws2 with properties {connection:cs, destination:(range "A1" of ws2), sql:(system attribute "XL_SQL2")}
		refresh query table qt2 without background query
		set bold of font object of row 1 of ws2 to true
		set ws3 to worksheet "LookupData" of wb
		set qt3 to make new query table at ws3 with properties {connection:cs, destination:(range "A1" of ws3), sql:(system attribute "XL_SQL3")}
		refresh query table qt3 without background query
		set ws4 to worksheet "Details" of wb
		set qt4 to make new query table at ws4 with properties {connection:cs, destination:(range "A1" of ws4), sql:(system attribute "XL_SQL4")}
		refresh query table qt4 without background query
		set bold of font object of row 1 of ws4 to true
		set ws5 to worksheet "More fields" of wb
		set qt5 to make new query table at ws5 with properties {connection:cs, destination:(range "A1" of ws5), sql:(system attribute "XL_SQL5")}
		refresh query table qt5 without background query
		set bold of font object of row 1 of ws5 to true
		activate object worksheet "Query" of wb
		select range "B5" of worksheet "Query" of wb
		save wb
		set n1 to count of columns of used range of ws
		set n2 to count of columns of used range of ws2
		close active workbook saving no
		return {n1, n2}
	end tell
end timeout
'''


def excel_running() -> bool:
    return subprocess.run(["pgrep", "-f", "MacOS/Microsoft Excel"], capture_output=True).returncode == 0


def excel_with_password(password: str | None) -> None:
    """(Re)start Excel; with a password it is handed to the database library through the environment only."""
    for attempt in range(60):
        if not excel_running():
            break
        if attempt % 10 == 0:
            subprocess.run(["osascript", "-e", 'tell application "Microsoft Excel" to quit saving no'],
                           capture_output=True, timeout=30)
        time.sleep(1)
    else:
        raise SystemExit("Excel did not quit; close it and run this again")
    time.sleep(1)
    cmd = ["open", "-a", "Microsoft Excel"]
    if password:
        cmd += ["--env", f"PGPASSWORD={password}"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if "could not be set" in out.stderr:
        raise SystemExit("Excel was still running; the login could not be handed over")
    for _ in range(40):                       # wait until Excel answers
        r = subprocess.run(["osascript", "-e", 'tell application "Microsoft Excel" to count workbooks'],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            break
        time.sleep(1)
    time.sleep(2)


def patch(src: Path, dst: Path) -> dict:
    """Replace the build-time connection by the neutral one and bind the filters to the cells on "Query"."""
    info = {}
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/connections.xml":
                xml = data.decode("utf-8")
                info["connections_before"] = re.sub(r"UID=[^;\"]*", "UID=<removed>", xml)[:1200]
                def cell_param(name: str, sheet: str, addr: str) -> str:
                    col, row = re.match(r"([A-Z]+)(\d+)", addr).groups()
                    return (f'<parameter name={quoteattr(name)} sqlType="12" parameterType="cell" refreshOnChange="1" '
                            f'cell={quoteattr(f"{sheet}!${col}${row}")}/>')

                filters = "".join(cell_param(k, "Query", PARAM[k]) for k in ORDER)
                blocks = {"Invoices": (sql_invoices(PLACEHOLDERS), f'<parameters count="{len(ORDER)}">{filters}</parameters>'),
                          "Charges": (sql_charges(PLACEHOLDERS), f'<parameters count="{len(ORDER)}">{filters}</parameters>'),
                          }
                pieces = (f'<parameters count="{LOOKUP_CHUNKS}">' + "".join(
                    cell_param(f"invoice numbers {k}", "Lookup", f"{LOOKUP_PARAM_COL}{k}")
                    for k in range(1, LOOKUP_CHUNKS + 1)) + "</parameters>")
                blocks["Lookup"] = (sql_lookup(LIST_MARK), pieces)
                blocks["Details"] = (sql_details(LIST_MARK), pieces)
                blocks["More fields"] = (sql_more(PLACEHOLDERS), f'<parameters count="{len(ORDER)}">{filters}</parameters>')

                def fix(m: re.Match) -> str:
                    conn = m.group(0)
                    name = ("Charges" if "LATERAL" in conn else "More fields" if "invoice_attributes_wide" in conn
                            else "Details" if "reporting.invoice_attributes" in conn
                            else "Lookup" if "string_to_array" in conn else "Invoices")
                    command, block = blocks[name]
                    conn = re.sub(r'connection="[^"]*"', f'connection="DSN={DSN};"', conn)
                    conn = re.sub(r'command="[^"]*"', "command=" + quoteattr(command), conn)
                    conn = re.sub(r"<parameters.*?</parameters>", "", conn, flags=re.DOTALL)
                    conn = conn.replace("</connection>", block + "</connection>")
                    head = re.match(r"<connection [^>]*>", conn).group(0)
                    new_head = re.sub(r'\s(refreshOnLoad|savePassword|background|saveData|name|interval)="[^"]*"', "", head)
                    new_head = new_head[:-1] + (f' name="{name}" interval="{REFRESH_MINUTES}" refreshOnLoad="1" '
                                                'background="1" saveData="1">')
                    return conn.replace(head, new_head)

                xml, n = re.subn(r"<connection .*?</connection>", fix, xml, flags=re.DOTALL)
                info["connections"] = n
                if "PWD=" in xml or "UID=" in xml:
                    raise SystemExit("a login is still inside the workbook; not writing it")
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return info


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("building needs Excel for Mac; the finished workbook runs on Windows and Mac")
    env = ROOT / ".env.local"
    for line in env.read_text().splitlines() if env.exists() else []:
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip().strip("'\""))
    url = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set (needed once, to let Excel read the column list)")
    u = urllib.parse.urlparse(url)
    work = CONTAINER_DOCS / "invoice_live_build.xlsx"          # Excel may only write inside its own folder
    base_workbook(work)
    excel_with_password(urllib.parse.unquote(u.password or ""))
    script = Path(os.environ.get("TMPDIR", "/tmp")) / "invoice_live_build.applescript"
    script.write_text(APPLESCRIPT)
    run_env = dict(os.environ, XL_CONN=f"ODBC;DSN={DSN};UID={u.username};", XL_FILE=str(work),
                   XL_SQL1=sql_invoices(EMPTY), XL_SQL2=sql_charges(EMPTY), XL_SQL3=sql_lookup("'~none~'"), XL_SQL4=sql_details("'~none~'"),      # no parameters yet: see patch()
                   XL_SQL5=sql_more(EMPTY))
    try:
        r = subprocess.run(["osascript", str(script)], env=run_env, capture_output=True, text=True, timeout=200)
    finally:
        script.unlink(missing_ok=True)
    print("excel:", r.returncode, (r.stdout + r.stderr).strip()[:400])
    if r.returncode:
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    info = patch(work, OUT)
    work.unlink(missing_ok=True)
    excel_with_password(None)                                  # leave Excel running without the password
    print(f"{OUT} written ({OUT.stat().st_size} bytes, {info['connections']} queries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
