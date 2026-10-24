"""Ontology and evidence-candidate contracts."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor import ontology  # noqa: E402
from invoice_extractor.evidence import build_candidates  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402
from invoice_extractor.semantic import AliasBank  # noqa: E402


class OntologyContracts(unittest.TestCase):
    def test_covers_all_output_fields(self):
        self.assertEqual(set(ontology.EXTERNAL_TO_CANONICAL), set(OUTPUT_FIELDS))
        self.assertEqual(len(ontology.EXTERNAL_TO_CANONICAL), 31)

    def test_handlign_typo_mapping(self):
        self.assertEqual(ontology.canonical("Handling Charges"), "handling_charges")
        self.assertEqual(ontology.external("handling_charges"), "Handling Charges")

    def test_roundtrip(self):
        fields = {k: "v" for k in OUTPUT_FIELDS}
        self.assertEqual(ontology.to_external(ontology.to_canonical(fields)), fields)

    def test_critical_is_header_subset(self):
        self.assertTrue(ontology.CRITICAL_CODES <= ontology.HEADER_CODES)
        self.assertEqual(len(ontology.CRITICAL_CODES), 8)


class EvidenceContracts(unittest.TestCase):
    def test_build_candidates_marks_support(self):
        fields = {"Invoice No": "INV-1", "Sea Freight": "100.00", "Misc": ""}
        audit = {"evidence": "Invoice No INV-1 Sea Freight 100.00",
                 "line_items": [{"description": "Sea Freight", "amount_inr": 100.0,
                                 "category": "Sea Freight", "category_basis": "llm+lexicon",
                                 "amount_found_in_document": True}]}
        cands = {c.canonical_field: c for c in build_candidates(fields, audit)}
        self.assertEqual(set(cands), {"invoice_number", "sea_freight"})
        self.assertEqual(cands["sea_freight"].evidence_score, 1.0)
        self.assertEqual(cands["sea_freight"].source_type, "VLM")
        self.assertEqual(cands["invoice_number"].value, "INV-1")

    def test_laya_source_type(self):
        fields = {"Agency Charges": "50"}
        audit = {"evidence": "Agency Charges 50",
                 "line_items": [{"description": "Agency Charges SB", "amount_inr": 50.0,
                                 "category": "Agency Charges",
                                 "category_basis": "semantic-override:laya-finetuned",
                                 "amount_found_in_document": True}]}
        (c,) = build_candidates(fields, audit)
        self.assertEqual(c.source_type, "LAYA")
        self.assertEqual(c.semantic_score, 1.0)


class AliasBankUndo(unittest.TestCase):
    def test_learn_then_unlearn_restores_lookup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            bank = AliasBank(learned_path=Path(d) / "learned.json", include_ontology=False)
            bank.learn("charge", "Wharfage", "Terminal Handling Charges")
            self.assertEqual(bank.lookup("charge", "wharfage")[0], "Terminal Handling Charges")
            self.assertTrue(bank.unlearn("charge", "Wharfage"))
            self.assertIsNone(bank.lookup("charge", "wharfage"))
            self.assertFalse(bank.unlearn("charge", "Wharfage"))


if __name__ == "__main__":
    unittest.main()
