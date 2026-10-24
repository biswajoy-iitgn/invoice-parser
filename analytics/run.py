#!/usr/bin/env python3
"""Run the analytics once: read the database, compute, write the reports, record the run.

  .venv/bin/python -m analytics.run                      # everything in the database
  .venv/bin/python -m analytics.run --from 2026-04-01 --to 2026-06-30
  .venv/bin/python -m analytics.run --if-changed         # skip when no invoice changed since the last run

Outputs (also copied to reports/analytics/latest/):
  dashboard.html   interactive charts and findings      (the app serves it at /analytics)
  mis_report.xlsx  MIS workbook with Excel charts
  insights.json    the same numbers, machine-readable

Each run is noted in the audit log (input fingerprint, outputs, findings), so any report
can be tied to the exact data it was built from. Reports live under reports/analytics/.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = Path(os.environ.get("ANALYTICS_DIR", ROOT / "reports" / "analytics"))


def load_env_file() -> None:
    f = ROOT / ".env.local"
    if f.exists():
        for line in f.read_text().splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip("'\"")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def last_fingerprint() -> str | None:
    f = OUT_ROOT / "latest" / "insights.json"
    try:
        return json.loads(f.read_text())["meta"]["fingerprint"]
    except Exception:
        return None


def _record(snap: dict, outputs: list[dict], trigger: str, params: dict, started: float) -> dict:
    """Note the run in the audit log (needs a role that may write)."""
    import uuid

    url, org = os.environ.get("DATABASE_URL"), os.environ.get("INVOICE_ORG_ID")
    if not url or not org:
        return {"recorded": False, "reason": "DATABASE_URL / INVOICE_ORG_ID not set"}
    try:
        from .data import _connect

        m = snap["meta"]
        run_id = str(uuid.uuid4())
        with _connect(url) as cn:
            cn.execute("SELECT set_config('app.current_org', %s, TRUE)", (org,))
            cn.execute("INSERT INTO audit_logs (organization_id, entity, entity_id, action, new_json) "
                       "VALUES (%s, 'analytics_run', %s, 'CREATE', %s)",
                       (org, run_id, json.dumps({"trigger": trigger, "invoices": m["invoices"], "by": getpass.getuser(),
                                                 "input_fingerprint": m["fingerprint"], "outputs": outputs,
                                                 "findings": len(snap["findings"])})))
        return {"recorded": True, "run_id": run_id}
    except Exception as exc:
        return {"recorded": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def run(trigger: str = "manual", date_from: str | None = None, date_to: str | None = None,
        if_changed: bool = False, frames: dict | None = None, record: bool = True) -> dict:
    """One analytics pass. Returns {"skipped": bool, "dir", "invoices", "findings", "record": {...}}."""
    from . import report_html, report_xlsx
    from .data import read_frames
    from .insights import analyse

    started = time.time()
    frames = frames or read_frames(date_from=date_from, date_to=date_to)
    snap = analyse(frames)
    if if_changed and snap["meta"]["fingerprint"] and snap["meta"]["fingerprint"] == last_fingerprint():
        return {"skipped": True, "reason": "no invoice changed since the last run", "invoices": snap["meta"]["invoices"]}
    out = OUT_ROOT / time.strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "insights.json").write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    report_html.build(snap, out / "dashboard.html")
    report_xlsx.build(snap, out / "mis_report.xlsx")
    outputs = [{"file": f.name, "sha256": _sha(f), "bytes": f.stat().st_size} for f in sorted(out.iterdir())]
    params = {k: v for k, v in (("from", date_from), ("to", date_to)) if v}
    rec = _record(snap, outputs, trigger, params, started) if record else {"recorded": False, "reason": "recording off"}
    (out / "run.json").write_text(json.dumps({"trigger": trigger, "parameters": params, "outputs": outputs, "record": rec,
                                              "seconds": round(time.time() - started, 2)}, indent=1))
    latest = OUT_ROOT / "latest"
    tmp = OUT_ROOT / f".latest.{os.getpid()}.{time.time_ns()}"
    aside = OUT_ROOT / f".latest.prev.{os.getpid()}.{time.time_ns()}"
    shutil.copytree(out, tmp)
    try:
        # rename(2) cannot replace a non-empty dir: move the old latest/ aside
        # first so readers never see a half-published tree.
        if latest.exists():
            os.rename(latest, aside)
        try:
            os.rename(tmp, latest)
        except Exception:
            if aside.exists():
                os.rename(aside, latest)  # restore the previous report
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(aside, ignore_errors=True)
    return {"skipped": False, "dir": str(out), "invoices": snap["meta"]["invoices"], "findings": len(snap["findings"]),
            "record": rec, "seconds": round(time.time() - started, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyse the invoices in the database and write the reports.")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--if-changed", action="store_true", help="do nothing when the data is unchanged since the last run")
    ap.add_argument("--trigger", default="manual")
    a = ap.parse_args()
    load_env_file()
    res = run(a.trigger, a.date_from, a.date_to, a.if_changed)
    if res["skipped"]:
        print(f"skipped: {res['reason']} ({res['invoices']} invoices)")
    else:
        print(f"{res['invoices']} invoices analysed, {res['findings']} findings, {res['seconds']}s -> {res['dir']}")
        print("  recorded in the database:", res["record"].get("run_id") or res["record"].get("reason"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
