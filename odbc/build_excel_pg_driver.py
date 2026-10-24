#!/usr/bin/env python3
"""Make the free PostgreSQL ODBC driver (psqlODBC, LGPL) loadable by Excel for Mac. No licence, no row limit.

Why this is needed: Excel for Mac is sandboxed and talks to databases through iODBC. Homebrew's psqlODBC lives
under /opt/homebrew, which the sandbox cannot read, and it is linked against unixODBC's installer library. This
script copies the driver and the libraries it needs into Excel's own data folder, re-points them at each other
and at the iODBC installer library, re-signs them (ad hoc), and registers the driver and a DSN.

  brew install psqlodbc                       # once
  python3 odbc/build_excel_pg_driver.py       # builds the copy + registers driver "PostgreSQL Free" and DSN NEON_INVOICE_PG
  python3 odbc/build_excel_pg_driver.py --save-login prompt      # also store a login, so Excel refreshes without asking
  python3 odbc/build_excel_pg_driver.py --server <host> --database <db> --user <role> --dsn <name>

Then open excel/Invoice_Live_Query.xlsx (or, for your own query: Data > Get Data > From Database (Microsoft Query) and
pick the data source). Without --save-login no password is written anywhere; with it, the password is stored in
~/Library/ODBC/odbc.ini, readable by your user only.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CONTAINER = Path.home() / "Library/Containers/com.microsoft.Excel/Data"
IODBC_INST = "/Library/Frameworks/iODBCinst.framework/Versions/3.52/iODBCinst"
DRIVER_NAME = "PostgreSQL Free"


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def deps(path: Path) -> list[str]:
    return [l.split()[0] for l in sh("otool", "-L", str(path)).splitlines()[1:]]


def resolve(dep: str, owner: Path) -> Path:
    if dep.startswith("@loader_path/") or dep.startswith("@rpath/"):
        return (owner.resolve().parent / dep.split("/", 1)[1]).resolve()
    return Path(dep).resolve()


def build(src: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    closure: dict[str, Path] = {}
    todo = [src]
    while todo:
        p = todo.pop()
        if p.name in closure:
            continue
        closure[p.name] = p.resolve()
        for d in deps(p):
            if "libodbcinst" in d or "libltdl" in d:        # replaced by the iODBC installer library
                continue
            if d.startswith(("/opt/homebrew", "/usr/local/Cellar", "/usr/local/opt", "@loader_path", "@rpath")):
                r = resolve(d, p)
                if r.exists() and r.name != p.resolve().name:
                    todo.append(Path(d) if not d.startswith("@") else r)
    # Build in a scratch folder, then move each file into place. A move gives the file a new identity, so an
    # Excel that already has the old driver loaded keeps working (overwriting a loaded library crashes it).
    stage = dest / ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    names = {}
    for name, real in closure.items():
        out = stage / name
        shutil.copy2(real, out)
        out.chmod(0o755)
        names[name] = out
    for name, out in names.items():
        if name != src.name:
            sh("install_name_tool", "-id", f"@loader_path/{name}", str(out))
        for d in deps(out):
            base = Path(d).name
            if "libodbcinst" in d:
                sh("install_name_tool", "-change", d, IODBC_INST, str(out))
            elif base in names and base != name and not d == f"@loader_path/{base}":
                sh("install_name_tool", "-change", d, f"@loader_path/{base}", str(out))
    for out in names.values():
        sh("codesign", "--force", "--sign", "-", str(out))
    left = [d for out in names.values() for d in deps(out) if "/opt/homebrew" in d or "/usr/local/Cellar" in d]
    if left:
        raise SystemExit(f"still linked outside the copy: {sorted(set(left))}")
    for name, out in names.items():
        os.replace(out, dest / name)
    stage.rmdir()
    return dest / src.name


def write_ini(path: Path, section: str, values: dict, index_section: str, index_value: str) -> None:
    """Add or replace one section and list it in the index section; everything else in the file is kept.

    The file is rewritten in place, never replaced: Excel sees ~/Library/ODBC/*.ini through a hard link.
    """
    text = path.read_text() if path.exists() else ""

    def block(name: str) -> re.Pattern:
        return re.compile(rf"(?ms)^\[{re.escape(name)}\][^\n]*\n(.*?)(?=^\[|\Z)")

    text = block(section).sub("", text).rstrip("\n")
    body = "".join(f"{k} = {v}\n" for k, v in values.items())
    text = (text + "\n\n" if text else "") + f"[{section}]\n{body}"
    m = block(index_section).search(text)
    entry = f"{section} = {index_value}\n"
    if m:
        kept = "".join(l + "\n" for l in m.group(1).splitlines()
                       if l.strip() and not re.match(rf"\s*{re.escape(section)}\s*=", l))
        text = text[:m.start(1)] + kept + entry + "\n" + text[m.end(1):]
    else:
        text = f"[{index_section}]\n{entry}\n" + text
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "r+" if path.exists() else "w") as f:
        f.seek(0)
        f.truncate()
        f.write(text if text.endswith("\n") else text + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=os.environ.get("PSQLODBC", "/opt/homebrew/lib/psqlodbca.so"),
                    help="the ANSI psqlODBC driver (iODBC needs the ANSI build, not psqlodbcw)")
    ap.add_argument("--dest", default=str(CONTAINER / "Library/ODBC/psqlodbc-free"))
    ap.add_argument("--dsn", default="NEON_INVOICE_PG")
    ap.add_argument("--server")
    ap.add_argument("--port", default="5432")
    ap.add_argument("--database")
    ap.add_argument("--user", help="database user for the data source (default: keep the saved one, else invoice_reporting_ro)")
    ap.add_argument("--save-login", choices=["prompt", "readonly", "app"],
                    help="store user and password in the data source so Excel refreshes without asking: 'prompt' asks "
                         "here, 'readonly' takes DATABASE_URL_READONLY, 'app' takes DATABASE_URL (the app's own login; "
                         "use a read-only login instead when you have one)")
    ap.add_argument("--forget-login", action="store_true", help="remove a stored password from the data source")
    ap.add_argument("--ini-dir", default=str(CONTAINER / "Library/ODBC"), help=argparse.SUPPRESS)
    ap.add_argument("--no-register", action="store_true")
    a = ap.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("macOS only. On Windows install psqlODBC x64 from https://odbc.postgresql.org/ - Excel loads it as is.")
    src = Path(a.source)
    if not src.exists():
        raise SystemExit(f"{src} not found: run `brew install psqlodbc` first")
    if not Path(IODBC_INST).exists():
        raise SystemExit("iODBC is not installed (https://www.iodbc.org/ - it also comes with the Actual drivers)")
    if not CONTAINER.exists():
        raise SystemExit("Excel has not been started on this account yet: open Excel once, then run this again")
    driver = build(src, Path(a.dest))
    print(f"driver copy: {driver}")
    if a.no_register:
        return 0
    urls = {k: os.environ.get(k, "") for k in ("DATABASE_URL_READONLY", "DATABASE_URL")}
    env = Path(__file__).resolve().parents[1] / ".env.local"
    for line in env.read_text().splitlines() if env.exists() else []:
        m = re.match(r"\s*(DATABASE_URL(?:_READONLY)?)\s*=\s*['\"]?([^'\"\n]+)", line)
        if m and not urls[m.group(1)]:
            urls[m.group(1)] = m.group(2)
    server, database = a.server, a.database
    if not (server and database):           # default to the app's database, without the secret part
        m = re.match(r"\w+://[^@]*@([^/:?]+)(?::(\d+))?/([^?]+)", urls["DATABASE_URL_READONLY"] or urls["DATABASE_URL"])
        if not m:
            raise SystemExit("pass --server and --database (no DATABASE_URL found)")
        server, database = server or m.group(1), database or m.group(3)
    odbc_dir = Path(a.ini_dir)
    ini = odbc_dir / "odbc.ini"
    old = dict(re.findall(r"(?m)^(Username|Password)\s*=\s*(.*)$", (re.search(
        rf"(?ms)^\[{re.escape(a.dsn)}\]\n(.*?)(?=^\[|\Z)", ini.read_text()) or [""])[0])) if ini.exists() else {}
    user, password = a.user or old.get("Username") or "invoice_reporting_ro", old.get("Password", "")
    if a.user and a.user != old.get("Username"):
        password = ""                       # a stored password belongs to the user it was stored for
    if a.save_login in ("readonly", "app"):
        from urllib.parse import unquote, urlparse

        key = "DATABASE_URL_READONLY" if a.save_login == "readonly" else "DATABASE_URL"
        if not urls[key]:
            raise SystemExit(f"{key} is not set (environment or .env.local)")
        u = urlparse(urls[key])
        user, password = unquote(u.username or ""), unquote(u.password or "")
    elif a.save_login == "prompt":
        import getpass

        user = input(f"Database user [{user}]: ").strip() or user
        password = getpass.getpass(f"Password for {user} (not shown): ")
    if a.forget_login:
        password = ""
    write_ini(odbc_dir / "odbcinst.ini", DRIVER_NAME,
              {"Description": "psqlODBC (free, LGPL) built for Excel for Mac", "Driver": str(driver)},
              "ODBC Drivers", "Installed")
    values = {"Description": "Invoice reporting view (free PostgreSQL driver, no row limit)", "Driver": str(driver),
              "Servername": server, "Port": a.port, "Database": database, "Username": user, "SSLmode": "require"}
    if password:
        values["Password"] = password
    write_ini(ini, a.dsn, values, "ODBC Data Sources", DRIVER_NAME)
    ini.chmod(0o600)                        # the file may hold a password: readable by this user only
    print(f"registered driver '{DRIVER_NAME}' and data source '{a.dsn}' (user {user}, "
          f"{'password stored' if password else 'no password stored'}) in {odbc_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
