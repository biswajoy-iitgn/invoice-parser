#!/usr/bin/env python3
"""Check the ODBC chain Excel for Mac uses: iODBC driver manager -> Actual Technologies driver -> database.

pyodbc is built against unixODBC (+ psqlODBC), which Excel does not use, so this talks to
/usr/local/lib/libiodbc.dylib directly through ctypes and runs the lookup from docs/EXCEL_LOOKUP.md.

  .venv/bin/python scripts/check_excel_odbc.py "DSN=NEON_INVOICE_DB" [invoice_number]

The account comes from the DSN, or from ODBC_UID / ODBC_PWD in the environment (never printed).
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

IODBC = os.environ.get("IODBC_LIB", "/usr/local/lib/libiodbc.dylib")
ENV, DBC, STMT = 1, 2, 3
NTS, NO_DATA, C_CHAR = -3, 100, 1


def main() -> int:
    conn_str = sys.argv[1] if len(sys.argv) > 1 else "DSN=NEON_INVOICE_DB"
    invoice = sys.argv[2] if len(sys.argv) > 2 else ""
    shown = conn_str
    if os.environ.get("ODBC_UID"):
        conn_str += f";UID={os.environ['ODBC_UID']}"
        shown += ";UID=<from env>"
    if os.environ.get("ODBC_PWD"):
        conn_str += f";PWD={os.environ['ODBC_PWD']}"
        shown += ";PWD=<hidden>"
    lib = ctypes.CDLL(IODBC)
    H = ctypes.c_void_p
    lib.SQLAllocHandle.argtypes = [ctypes.c_short, H, ctypes.POINTER(H)]
    lib.SQLSetEnvAttr.argtypes = [H, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.SQLDriverConnect.argtypes = [H, H, ctypes.c_char_p, ctypes.c_short, ctypes.c_char_p, ctypes.c_short,
                                     ctypes.POINTER(ctypes.c_short), ctypes.c_ushort]
    lib.SQLGetDiagRec.argtypes = [ctypes.c_short, H, ctypes.c_short, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
                                  ctypes.c_char_p, ctypes.c_short, ctypes.POINTER(ctypes.c_short)]
    lib.SQLExecDirect.argtypes = [H, ctypes.c_char_p, ctypes.c_int]
    lib.SQLNumResultCols.argtypes = [H, ctypes.POINTER(ctypes.c_short)]
    lib.SQLDescribeCol.argtypes = [H, ctypes.c_ushort, ctypes.c_char_p, ctypes.c_short, ctypes.POINTER(ctypes.c_short),
                                   ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_ulong),
                                   ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_short)]
    lib.SQLFetch.argtypes = [H]
    lib.SQLGetData.argtypes = [H, ctypes.c_ushort, ctypes.c_short, ctypes.c_char_p, ctypes.c_long,
                               ctypes.POINTER(ctypes.c_long)]
    lib.SQLFreeHandle.argtypes = [ctypes.c_short, H]
    lib.SQLDisconnect.argtypes = [H]
    for f in ("SQLAllocHandle", "SQLSetEnvAttr", "SQLDriverConnect", "SQLGetDiagRec", "SQLExecDirect",
              "SQLNumResultCols", "SQLDescribeCol", "SQLFetch", "SQLGetData", "SQLFreeHandle", "SQLDisconnect"):
        getattr(lib, f).restype = ctypes.c_short

    def diag(kind: int, handle) -> str:
        msgs = []
        for i in range(1, 6):
            state, native = ctypes.create_string_buffer(8), ctypes.c_int()
            msg, n = ctypes.create_string_buffer(1024), ctypes.c_short()
            if lib.SQLGetDiagRec(kind, handle, i, state, ctypes.byref(native), msg, 1024, ctypes.byref(n)) not in (0, 1):
                break
            msgs.append(f"[{state.value.decode()}] {msg.value.decode(errors='replace')}")
        return " | ".join(msgs) or "(no diagnostic)"

    env, dbc, stmt = H(), H(), H()
    lib.SQLAllocHandle(ENV, None, ctypes.byref(env))
    lib.SQLSetEnvAttr(env, 200, ctypes.c_void_p(3), 0)          # ODBC 3
    lib.SQLAllocHandle(DBC, env, ctypes.byref(dbc))
    out, n = ctypes.create_string_buffer(2048), ctypes.c_short()
    print("iODBC connect:", shown)
    t = time.time()
    rc = lib.SQLDriverConnect(dbc, None, conn_str.encode(), NTS, out, 2048, ctypes.byref(n), 0)   # no prompt
    if rc not in (0, 1):
        print(f"CONNECT FAILED (rc={rc}): {diag(DBC, dbc)}")
        return 1
    print(f"connected in {time.time() - t:.2f}s")
    lib.SQLAllocHandle(STMT, dbc, ctypes.byref(stmt))
    where = " WHERE invoice_number = '" + invoice.replace("'", "''") + "'" if invoice else " ORDER BY updated_at DESC"
    sql = "SELECT * FROM reporting.invoice_export" + where
    t = time.time()
    rc = lib.SQLExecDirect(stmt, sql.encode(), NTS)
    if rc not in (0, 1):
        print(f"QUERY FAILED (rc={rc}): {diag(STMT, stmt)}")
        return 1
    ncol = ctypes.c_short()
    lib.SQLNumResultCols(stmt, ctypes.byref(ncol))
    cols = []
    for i in range(1, ncol.value + 1):
        name, nl, typ, size, dec, nul = (ctypes.create_string_buffer(256), ctypes.c_short(), ctypes.c_short(),
                                         ctypes.c_ulong(), ctypes.c_short(), ctypes.c_short())
        lib.SQLDescribeCol(stmt, i, name, 256, ctypes.byref(nl), ctypes.byref(typ), ctypes.byref(size),
                           ctypes.byref(dec), ctypes.byref(nul))
        cols.append(name.value.decode())
    rows = []
    while lib.SQLFetch(stmt) in (0, 1):
        row = {}
        for i, c in enumerate(cols, 1):
            buf, ind = ctypes.create_string_buffer(4096), ctypes.c_long()
            if lib.SQLGetData(stmt, i, C_CHAR, buf, 4096, ctypes.byref(ind)) in (0, 1) and ind.value != -1:
                row[c] = buf.value.decode(errors="replace")
        rows.append(row)
    print(f"{len(rows)} row(s), {len(cols)} columns in {time.time() - t:.2f}s")
    for r in rows[:5]:
        print("  ", {k: v for k, v in r.items() if k not in ("invoice_id", "organization_id")})
    lib.SQLFreeHandle(STMT, stmt)
    lib.SQLDisconnect(dbc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
