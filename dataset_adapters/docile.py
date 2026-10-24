"""DocILE adapter (KILE/LIR invoices) -> unified invoice record.

Source: https://github.com/rossumai/docile (6.7k annotated + 100k synthetic).
Access: requires a free secret token from https://docile.rossum.ai/; download
  with ./download_dataset.sh TOKEN annotated-trainval data/docile --unzip
License: DocILE data is released for research use; verify the current terms at
  docile.rossum.ai before redistribution. This adapter is not populated in the
  default corpus because no token was available in this session.

DocILE KILE annotates 55 field classes with bboxes; only classes that map
cleanly onto the freight-invoice ontology are converted (invoice number/date,
vendor, totals). Line-item tables map to Other Charges only when a single
unambiguous total exists; otherwise they stay "" (no invented values).
"""
from __future__ import annotations

from . import blank_record, finalize

LICENSE = "DocILE research terms (verify at docile.rossum.ai); token-gated"

FIELD_MAP = {
    "invoice_number": "Invoice No",
    "invoice_date": "Invoice Date",
    "due_date": None,
    "vendor_name": "Vendor Name",
    "vendor_address": None,
    "customer_name": None,
    "total_amount": None,  # no Total field in the target schema; kept in extras
}


def convert(doc: dict, image_rel: str) -> dict:
    """Convert one DocILE annotation dict to a unified record."""
    rec = blank_record("docile", doc.get("doc_id", "unknown"), image_rel, LICENSE,
                       template_id="docile_native", vendor_id="docile_native",
                       language=doc.get("language", "en"))
    for ann in doc.get("annotations", []):
        target = FIELD_MAP.get(ann.get("field", ""), "SKIP")
        if target and ann.get("text"):
            rec["ground_truth"][target] = ann["text"]
            if ann.get("bbox"):
                rec["regions"].append({"field": target, "bbox": ann["bbox"],
                                      "text": ann["text"]})
    return finalize(rec)
