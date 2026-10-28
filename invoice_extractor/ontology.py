"""Canonical internal field ontology.

External JSON keys (schema.OUTPUT_FIELDS) are the frozen contract and never
change. Internally the pipeline, database, and reporting view use these
stable snake_case codes.
"""

from .schema import OUTPUT_FIELDS

EXTERNAL_TO_CANONICAL = {
    "Invoice No": "invoice_number",
    "Invoice Date": "invoice_date",
    "Container Number": "container_number",
    "MAWB No": "mawb_number",
    "HAWB No": "hawb_number",
    "Vendor Name": "vendor_name",
    "Bill of Lading / HBL": "bill_of_lading_hbl",
    "WEIGHT": "weight",
    "Sea Freight": "sea_freight",
    "Air Freight": "air_freight",
    "Terminal Handling Charges": "terminal_handling_charges",
    "Air Waybill Charges": "airway_waybill_charges",
    "BL Charges": "bl_charges",
    "ENS Charges": "ens_charges",
    "Seal Charges": "seal_charges",
    "VGM": "vgm",
    "EDI Fees": "edi_fees",
    "DAP Charges": "dap_charges",
    "Other Charges": "other_charges",
    "AMS CHARGES": "ams_charges",
    "Handling Charges": "handling_charges",
    "Pre Carriage Haulage": "pre_carriage_haulage",
    "Ex Rate": "exchange_rate",
    "Misc": "misc",
    "Transportation": "transportation",
    "Warehouse charges Port": "warehouse_charges_port",
    "Examination charges": "examination_charges",
    "Fuel Charges": "fuel_charges",
    "Cargo Screening": "cargo_screening",
    "Agency Charges": "agency_charges",
    "Shipping bill charges": "shipping_bill_charges",
}

assert set(EXTERNAL_TO_CANONICAL) == set(OUTPUT_FIELDS), "ontology must cover every output field"
assert len(set(EXTERNAL_TO_CANONICAL.values())) == len(OUTPUT_FIELDS), "canonical codes must be unique"

CANONICAL_TO_EXTERNAL = {v: k for k, v in EXTERNAL_TO_CANONICAL.items()}

# Header (identity) fields vs charge (money) fields; "Ex Rate" is its own kind.
HEADER_CODES = {
    "invoice_number", "invoice_date", "container_number", "mawb_number",
    "hawb_number", "vendor_name", "bill_of_lading_hbl", "weight",
}
CHARGE_CODES = {
    code for code in EXTERNAL_TO_CANONICAL.values()
    if code not in HEADER_CODES and code != "exchange_rate"
}

CRITICAL_CODES = {
    "invoice_number", "invoice_date", "vendor_name", "container_number",
    "mawb_number", "hawb_number", "bill_of_lading_hbl", "weight",
}


def canonical(external: str) -> str:
    return EXTERNAL_TO_CANONICAL[external]


def external(canonical_code: str) -> str:
    return CANONICAL_TO_EXTERNAL[canonical_code]


def to_canonical(fields: dict) -> dict:
    """Map an external contract dict to canonical codes (unknown keys dropped)."""
    return {EXTERNAL_TO_CANONICAL[k]: v for k, v in fields.items() if k in EXTERNAL_TO_CANONICAL}


def to_external(canonical_fields: dict) -> dict:
    """Map canonical codes back to the external contract (unknown keys dropped)."""
    return {CANONICAL_TO_EXTERNAL[k]: v for k, v in canonical_fields.items() if k in CANONICAL_TO_EXTERNAL}
