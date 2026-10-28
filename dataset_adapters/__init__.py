"""Shared record schema for public-dataset adapters.

Every adapter maps its native annotations into the unified invoice record
(see scripts/generate_dataset.py) while preserving provenance
(source_dataset, source_id, license). Fields that do not exist in the source
stay "" — adapters never invent values.
"""
from __future__ import annotations

FIELDS = ["Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No",
          "Vendor Name", "Bill of Lading / HBL", "WEIGHT", "Sea Freight",
          "Air Freight", "Terminal Handling Charges", "Air Waybill Charges",
          "BL Charges", "ENS Charges", "Seal Charges", "VGM", "EDI Fees",
          "DAP Charges", "Other Charges", "AMS CHARGES", "Handling Charges",
          "Pre Carriage Haulage", "Ex Rate", "Misc", "Transportation",
          "Warehouse charges Port", "Examination charges", "Fuel Charges",
          "Cargo Screening", "Agency Charges", "Shipping bill charges"]


def blank_record(source_dataset: str, source_id: str, image: str,
                 license: str, **extra) -> dict:
    gt = {k: "" for k in FIELDS}
    return {
        "id": f"{source_dataset.upper()}_{source_id}",
        "image": image,
        "source": f"public_{source_dataset}",
        "source_dataset": source_dataset,
        "source_id": str(source_id),
        "license": license,
        "ground_truth": gt,
        "field_presence": {},
        "regions": [],
        "degradation": {},
        **extra,
    }


def finalize(rec: dict) -> dict:
    rec["field_presence"] = {k: bool(v) for k, v in rec["ground_truth"].items()}
    return rec
