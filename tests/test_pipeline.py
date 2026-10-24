import unittest
from PIL import Image
from invoice_extractor.schema import OUTPUT_FIELDS
from invoice_extractor.validation import fixed_json, ground_fields, normalize_date
from invoice_extractor.quality import assess

class PipelineContracts(unittest.TestCase):
 def test_requested_fixed_schema(self):
  values=fixed_json({"Invoice No":"A","unexpected":"x"})
  self.assertEqual(list(values),OUTPUT_FIELDS);self.assertEqual(values['Invoice No'],'A');self.assertNotIn('unexpected',values)
  self.assertIn('WEIGHT',values);self.assertIn('Handling Charges',values)
 def test_unsupported_fields_are_blanked(self):
  result,unsupported=ground_fields({"Invoice No":"INV-4","Vendor Name":"Fiction Corp"},"Invoice No: INV-4")
  self.assertEqual(result['Invoice No'],'INV-4');self.assertEqual(result['Vendor Name'],'');self.assertEqual(unsupported,['Vendor Name'])
 def test_quality_gate_flags_small_low_contrast_input(self):
  q=assess(Image.new('RGB',(500,500),(255,255,255)))
  self.assertTrue(q.low_resolution);self.assertEqual(q.min_dimension,500)
 def test_normalized_amounts_survive_grounding(self):
  ev="Sea freight FCL INR 13,081.13 | THC 4,856.78"
  result,unsupported=ground_fields({"Sea Freight":"13081.13","Air Freight":"999.99"},ev)
  self.assertEqual(result['Sea Freight'],'13081.13');self.assertEqual(result['Air Freight'],'')
  self.assertEqual(unsupported,['Air Freight'])
 def test_hallucinated_amounts_are_blanked(self):
  result,unsupported=ground_fields({"Sea Freight":"12345.67"},"Total INR 9,999.00")
  self.assertEqual(result['Sea Freight'],'');self.assertEqual(unsupported,['Sea Freight'])
 def test_normalized_dates_survive_grounding(self):
  self.assertEqual(normalize_date("06/10/2024"),"2024-10-06")
  result,unsupported=ground_fields({"Invoice Date":"2024-10-06"},"Invoice Date: 06/10/2024")
  self.assertEqual(result['Invoice Date'],'2024-10-06');self.assertEqual(unsupported,[])
  result,unsupported=ground_fields({"Invoice Date":"2025-01-01"},"Invoice Date: 06/10/2024")
  self.assertEqual(result['Invoice Date'],'');self.assertEqual(unsupported,['Invoice Date'])
 def test_semantic_vote_never_overwrites_headers(self):
  from invoice_extractor.extract import resolve_category
  from invoice_extractor.schema import CHARGE_FIELDS
  for desc in ["Invoice No details", "Vendor Name plate", "THC port of loading", "xyzzy 123"]:
    cat, basis, lex, sem, backend, llm = resolve_category("Other Charges", desc)
    self.assertIn(cat, CHARGE_FIELDS, desc)
    self.assertIn(sem, CHARGE_FIELDS, desc)
 def test_exchange_rates_grounded_per_entry(self):
  ev="USD/INR 98.25; EUR/INR 112.17"
  result,unsupported=ground_fields({"Ex Rate":"USD 98.25, EUR 112.17"},ev)
  self.assertEqual(unsupported,[])
  result,unsupported=ground_fields({"Ex Rate":"USD 98.25, GBP 105.00"},ev)
  self.assertEqual(result['Ex Rate'],'')

if __name__=='__main__':unittest.main()
