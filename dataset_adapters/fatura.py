"""FATURA adapter (10k synthetic invoices, 50 templates) -> unified record.

Source: https://zenodo.org/records/8261508 (v1, 2023; also FATURA2 mirror
  https://huggingface.co/datasets/mathieu1256/FATURA2-invoices with
  white/coloured backgrounds + token/bbox/ner_tags parquet).
License: listed as Open on Zenodo; verify the record's license field before
  redistribution (FATURA2 HF mirror states its own terms).
Download: fetch the Zenodo archive, or stream the HF mirror:
  huggingface-cli download mathieu1256/FATURA2-invoices --repo-type dataset

FATURA annotates 24 text regions per invoice. Region labels that map cleanly
onto the freight ontology (invoice number/date, seller) are converted; the
rest stay "". Provenance (template id) is preserved for leakage control:
FATURA templates must not cross corpus splits.
"""
from __future__ import annotations

from . import blank_record, finalize

LICENSE = "Zenodo Open (verify record license before redistribution)"

REGION_MAP = {
    "invoice_number": "Invoice No",
    "invoice_no": "Invoice No",
    "invoice_date": "Invoice Date",
    "date": "Invoice Date",
    "seller": "Vendor Name",
    "seller_name": "Vendor Name",
    "company": "Vendor Name",
}


def convert(annotation: dict, image_rel: str, template_id: str = "") -> dict:
    """Convert one FATURA annotation dict to a unified record."""
    src_id = annotation.get("image_id", annotation.get("id", "unknown"))
    rec = blank_record("fatura", src_id, image_rel, LICENSE,
                       template_id=f"fatura_{template_id or 'native'}",
                       vendor_id=f"fatura_{template_id or 'native'}",
                       language=annotation.get("language", "en"))
    regions = annotation.get("regions", annotation.get("annotations", []))
    for r in regions:
        label = str(r.get("label", r.get("class", ""))).casefold()
        target = REGION_MAP.get(label)
        if target and r.get("text"):
            rec["ground_truth"][target] = r["text"]
            if r.get("bbox"):
                rec["regions"].append({"field": target, "bbox": r["bbox"],
                                      "text": r["text"]})
    return finalize(rec)
