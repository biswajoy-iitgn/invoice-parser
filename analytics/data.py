"""Read the invoices to analyse from PostgreSQL. Read-only: nothing here writes."""
from __future__ import annotations

import hashlib
import json
import os
import re
from urllib.parse import urlparse

import pandas as pd

CHARGES = {
    "sea_freight": "Sea Freight", "air_freight": "Air Freight", "terminal_handling_charges": "Terminal Handling Charges",
    "airway_waybill_charges": "Air Waybill Charges", "bl_charges": "BL Charges", "ens_charges": "ENS Charges",
    "seal_charges": "Seal Charges", "vgm": "VGM", "edi_fees": "EDI Fees", "dap_charges": "DAP Charges",
    "other_charges": "Other Charges", "ams_charges": "AMS CHARGES", "handling_charges": "Handling Charges",
    "pre_carriage_haulage": "Pre Carriage Haulage", "misc": "Misc", "transportation": "Transportation",
    "warehouse_charges_port": "Warehouse charges Port", "examination_charges": "Examination charges",
    "fuel_charges": "Fuel Charges", "cargo_screening": "Cargo Screening", "agency_charges": "Agency Charges",
    "shipping_bill_charges": "Shipping bill charges",
}
HEADER = {"invoice_number": "Invoice No", "invoice_date": "Invoice Date", "vendor_name": "Vendor Name",
          "container_number": "Container Number", "bill_of_lading_hbl": "Bill of Lading / HBL",
          "mawb_no": "MAWB No", "hawb_no": "HAWB No", "weight": "WEIGHT", "ex_rate": "Ex Rate"}
CONTAINER_RE = re.compile(r"\b[A-Z]{3}[UJZ]\s?\d{6}\s?\d\b")


def _connect(url: str):
    import psycopg

    if "sslmode=" not in url and (urlparse(url).hostname or "") not in ("localhost", "127.0.0.1", "::1", ""):
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return psycopg.connect(url, connect_timeout=30)


def database_url() -> str:
    url = os.environ.get("ANALYTICS_DATABASE_URL") or os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("no database address: set DATABASE_URL_READONLY or DATABASE_URL")
    return url


def read_frames(url: str | None = None, org: str | None = None, date_from=None, date_to=None) -> dict:
    """{'invoices': DataFrame (one row per invoice), 'runs': DataFrame | None, 'source': {...}}."""
    url = url or database_url()
    org = org or os.environ.get("INVOICE_ORG_ID")
    cond, args = [], []
    if org:
        cond.append("organization_id = %s")
        args.append(org)
    if date_from:
        cond.append("invoice_date >= %s")
        args.append(date_from)
    if date_to:
        cond.append("invoice_date <= %s")
        args.append(date_to)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    with _connect(url) as cn:
        cur = cn.cursor()
        cur.execute(f"SELECT * FROM reporting.invoice_export{where} ORDER BY invoice_date NULLS LAST, invoice_number", args)
        cols = [c.name for c in cur.description]
        inv = pd.DataFrame(cur.fetchall(), columns=cols)
        runs = None
        try:      # the read-only reporting role only sees the view; run history is optional
            cur.execute(
                """SELECT i.invoice_number, r.pipeline_version, r.status, r.started_at, r.processing_time_ms,
                          r.final_output_json->>'review_route' AS route,
                          jsonb_array_length(coalesce(r.final_output_json->'warnings', '[]'::jsonb)) AS warnings
                     FROM invoices i JOIN extraction_runs r ON r.id = i.current_extraction_run_id"""
                + (" WHERE i.organization_id = %s" if org else ""), [org] if org else [])
            runs = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
        except Exception:
            cn.rollback()
    return {"invoices": prepare(inv), "runs": runs,
            "source": {"host": urlparse(url).hostname, "view": "reporting.invoice_export", "organization": org}}


def weight_kg(text) -> float | None:
    """'2,905 KG' / '10669.000 kg' / '6750.500' -> kilograms (tonnes and pounds converted)."""
    m = re.search(r"(\d[\d,]*\.?\d*)\s*([a-zA-Z]*)", str(text or ""))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = m.group(2).lower()
    if unit in ("t", "mt", "ton", "tons", "tonne", "tonnes"):
        v *= 1000.0
    elif unit in ("lb", "lbs"):
        v *= 0.45359237
    return v if v > 0 else None


def prepare(inv: pd.DataFrame) -> pd.DataFrame:
    """Typed, analysis-ready invoices: numeric charges, total, mode, month, weight in kg, containers."""
    inv = inv.copy()
    for c in CHARGES:
        if c not in inv.columns:
            inv[c] = None
        inv[c] = pd.to_numeric(inv[c], errors="coerce")
    inv["total"] = inv[list(CHARGES)].sum(axis=1, min_count=1).fillna(0.0)
    inv["invoice_date"] = pd.to_datetime(inv.get("invoice_date"), errors="coerce")
    inv["month"] = inv["invoice_date"].dt.strftime("%Y-%m")
    inv["vendor_name"] = inv.get("vendor_name").fillna("").astype(str).str.strip().replace("", "(no vendor)")
    has = lambda c: inv[c].fillna("").astype(str).str.strip() != ""      # noqa: E731
    air = inv["air_freight"].notna() | has("mawb_no") | has("hawb_no")
    sea = inv["sea_freight"].notna() | has("container_number") | has("bill_of_lading_hbl")
    inv["mode"] = ["air" if a and not s else "sea" if s and not a else "air" if a else "other" for a, s in zip(air, sea)]
    inv["weight_kg"] = inv["weight"].map(weight_kg)
    inv["containers"] = inv["container_number"].fillna("").astype(str).map(lambda t: len(set(CONTAINER_RE.findall(t.upper()))))
    return inv


def fingerprint(inv: pd.DataFrame) -> str:
    """SHA-256 of exactly the rows analysed, so a report can be tied to its input."""
    cols = [c for c in ["invoice_id", "invoice_number", "invoice_date", "vendor_name", *CHARGES, "weight", "ex_rate",
                        "container_number", "bill_of_lading_hbl", "review_status", "updated_at"] if c in inv.columns]
    payload = json.dumps(inv[cols].astype(str).values.tolist(), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
