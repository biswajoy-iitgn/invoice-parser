"""MIDD adapter (Multi-Layout Invoice Document Dataset) -> unified record.

Source: Zenodo http://doi.org/10.5281/zenodo.5113009 — 630 real invoice PDFs
  in 4 layouts from diverse suppliers, annotated for NER.
License: CC-BY-4.0 (per the dataset paper; attribute the authors).
Paper: "Multi-Layout Invoice Document Dataset (MIDD): A Dataset for Named
  Entity Recognition" (Data journal, 2021).

MIDD entities map onto the header subset of the freight ontology. Freight
charge fields do not exist in MIDD and stay "". Layout ids are preserved so
whole layouts can be held out of splits.
"""
from __future__ import annotations

from . import blank_record, finalize

LICENSE = "CC-BY-4.0"

ENTITY_MAP = {
    "invoice_number": "Invoice No",
    "invoice_no": "Invoice No",
    "invoice_date": "Invoice Date",
    "date": "Invoice Date",
    "seller": "Vendor Name",
    "vendor": "Vendor Name",
    "seller_name": "Vendor Name",
    "buyer": None,
    "total": None,
    "tax": None,
}


def convert(annotation: dict, image_rel: str, layout_id: str = "") -> dict:
    """Convert one MIDD annotation dict to a unified record."""
    src_id = annotation.get("doc_id", annotation.get("id", "unknown"))
    rec = blank_record("midd", src_id, image_rel, LICENSE,
                       template_id=f"midd_layout_{layout_id or 'native'}",
                       vendor_id="midd_native",
                       language=annotation.get("language", "en"))
    for ent in annotation.get("entities", annotation.get("annotations", [])):
        label = str(ent.get("label", ent.get("type", ""))).casefold()
        target = ENTITY_MAP.get(label)
        if target and ent.get("text"):
            rec["ground_truth"][target] = ent["text"]
            if ent.get("bbox"):
                rec["regions"].append({"field": target, "bbox": ent["bbox"],
                                      "text": ent["text"]})
    return finalize(rec)
