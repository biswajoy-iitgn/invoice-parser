"""Arithmetic checks: the expression evaluator is safe, and each check type passes, fails, or says it could not run."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor import checks  # noqa: E402


def table(columns, rows):
    return {"columns": columns, "rows": [dict(zip(columns, r)) for r in rows]}


class ExpressionTests(unittest.TestCase):
    def test_only_arithmetic_is_allowed(self):
        for bad in ("__import__('os')", "a.b", "a ** 2", "f(1)", "'x'", "a if b else c", "[1]", "lambda: 1"):
            with self.assertRaises((ValueError, SyntaxError), msg=bad):
                checks.parse(bad)
        self.assertEqual({"c", "mn", "cr"}, checks.names_in("C + MN/6 + (CR)/5"))

    def test_evaluate(self):
        env = {"a": 2.0, "b": 3.0}
        self.assertEqual(7.0, checks.evaluate("a*b + 1", env))
        self.assertEqual(-1.0, checks.evaluate("-(b - a)", env))
        self.assertIsNone(checks.evaluate("a / 0", env))
        self.assertIsNone(checks.evaluate("a + z", env))           # a missing value: no answer, not a wrong one

    def test_printed_numbers(self):
        n = checks.number
        self.assertEqual(1800.0, n("$1,800.00"))
        self.assertEqual(0.047, n("0,047"))
        self.assertEqual(1234.5, n("1.234,5"))
        self.assertEqual(1800.0, n("1,800"))
        self.assertEqual(-5.0, n("-5"))
        self.assertIsNone(n("-"))
        self.assertIsNone(n(""))


class CheckTests(unittest.TestCase):
    def test_row_formula(self):
        t = {"pay": table(["Hourly Rate", "Total Hours", "Total Pay"], [["$45.00", "40", "$1,800.00"], ["$50.00", "20", "$1,000.00"], ["$10.00", "5", "$99.00"]])}
        c = {"type": "row_formula", "table": "pay", "result": "Total Pay", "expr": "HourlyRate * TotalHours", "tolerance": 0.05}
        checks.validate(c, {"pay": t["pay"]["columns"]}, set())
        r = checks.run([c], {}, t)[0]
        self.assertFalse(r["ok"])
        self.assertIn("row 3: Total Pay printed 99, the formula gives 50", r["detail"])
        t["pay"]["rows"].pop()
        self.assertTrue(checks.run([c], {}, t)[0]["ok"])

    def test_a_row_with_a_missing_number_is_skipped_not_failed(self):
        t = {"pay": table(["Rate", "Hours", "Pay"], [["10", "", "100"], ["10", "2", "20"]])}
        r = checks.run([{"type": "row_formula", "table": "pay", "result": "Pay", "expr": "Rate*Hours"}], {}, t)[0]
        self.assertTrue(r["ok"])
        t = {"pay": table(["Rate", "Hours", "Pay"], [["10", "", "100"]])}
        self.assertIsNone(checks.run([{"type": "row_formula", "table": "pay", "result": "Pay", "expr": "Rate*Hours"}], {}, t)[0]["ok"])

    def test_text_in_the_result_column_fails_instead_of_skipping(self):
        t = {"heat": table(["C", "MN", "CE"], [["0,047", "0,160", "EN10130:2006"]])}       # a code read into the CE column
        r = checks.run([{"type": "row_formula", "table": "heat", "result": "CE", "expr": "C + MN/6"}], {}, t)[0]
        self.assertFalse(r["ok"])
        self.assertIn("not a number", r["detail"])

    def test_total_row(self):
        t = {"t": table(["Item", "Qty"], [["a", "23.990"], ["b", "23.950"], ["Total", "47.940"]])}
        c = {"type": "total_row", "table": "t"}
        self.assertTrue(checks.run([c], {}, t)[0]["ok"])
        t["t"]["rows"][2]["Qty"] = "47.490"                          # two digits swapped in the total
        r = checks.run([c], {}, t)[0]
        self.assertFalse(r["ok"])
        self.assertIn("rows add up to 47.94, the total row says 47.49", r["detail"])
        self.assertIsNone(checks.run([c], {}, {"t": table(["Item", "Qty"], [["a", "1"]])})[0]["ok"])   # no total row: nothing to check

    def test_an_unlabelled_total_row_is_found(self):
        cols = ["Item No", "Batch ID", "Qty Net", "YS"]
        rows = [["90", "72568U", "23.990", "250"], ["90", "72570U", "23.950", "250"], ["", "", "47.940", ""]]
        c = {"type": "total_row", "table": "t", "columns": ["Qty Net"]}
        self.assertTrue(checks.run([c], {}, {"t": table(cols, rows)})[0]["ok"])
        rows[2][2] = "47.490"
        self.assertFalse(checks.run([c], {}, {"t": table(cols, rows)})[0]["ok"])
        auto = checks.run_automatic([{"columns": cols, "rows": rows}])
        self.assertEqual([False], [r["ok"] for r in auto])
        self.assertEqual([], checks.run_automatic([{"columns": cols, "rows": rows[:2]}]))     # two rows: nothing to call a total
        rows[2][2] = "47.940"
        self.assertTrue(checks.run_automatic([{"columns": cols, "rows": rows}])[0]["ok"])

    def test_sum_field_formula_and_range(self):
        t = {"t": table(["Amount"], [["100"], ["50.50"]])}
        fields = {"subtotal": "150.50", "tax": "27.09", "total": "177.59", "weight": "-3"}
        cs = [{"type": "sum", "table": "t", "column": "Amount", "field": "subtotal"},
              {"type": "field_formula", "result": "total", "expr": "subtotal + tax"},
              {"type": "range", "field": "weight", "min": 0}]
        r = checks.run(cs, fields, t)
        self.assertEqual([True, True, False], [x["ok"] for x in r])
        self.assertIn("-3 is outside 0..", r[2]["detail"])
        fields["total"] = "177.95"
        self.assertFalse(checks.run(cs[1:2], fields, t)[0]["ok"])
        self.assertTrue(checks.run([{"type": "range", "table": "t", "column": "Amount", "min": 0, "max": 100}], {}, t)[0]["ok"])

    def test_compare_on_rows_fields_and_dates(self):
        t = {"m": table(["YS", "UTS", "EL"], [["250", "349", "38"], ["410", "349", "30"]])}
        r = checks.run([{"type": "compare", "table": "m", "left": "YS", "op": "<=", "right": "UTS"}], {}, t)[0]
        self.assertFalse(r["ok"])
        self.assertIn("row 2: YS is 410, UTS is 349", r["detail"])
        cs = [{"type": "compare", "left": "issued", "op": "<=", "right": "received", "as": "date"},
              {"type": "compare", "left": "gross", "op": ">=", "right": "net + tare"}]
        f = {"issued": "06.04.2026", "received": "7-4-26", "gross": "100", "net": "90", "tare": "10"}
        self.assertEqual([True, True], [x["ok"] for x in checks.run(cs, f, {})])
        f.update(received="5-4-26", gross="99")                       # received before issued, gross below net + tare
        res = checks.run(cs, f, {})
        self.assertEqual([False, False], [x["ok"] for x in res])
        self.assertIn("2026-04-06", res[0]["detail"])
        self.assertIsNone(checks.run(cs[:1], {"issued": "06.04.2026", "received": ""}, {})[0]["ok"])
        r = checks.run([{"type": "compare", "table": "m", "left": "YS+UTS+EL", "op": "<", "right": 700}], {}, t)
        self.assertFalse(r[0]["ok"])

    def test_same_unique_complete(self):
        f = {"cert": "AMNS/TCH/20260406194006306390", "footer": "AMNS/TCH/20260406194006306399", "pan": "AAACK2676H", "gstin": "24AAACK2676H1ZM"}
        r = checks.run([{"type": "same", "a": "cert", "b": "footer"}, {"type": "same", "a": "pan", "b": "gstin", "b_slice": [2, 12]}], f, {})
        self.assertEqual([False, True], [x["ok"] for x in r])
        t = {"b": table(["Batch ID", "Qty"], [["72568U", "1"], ["72570U", ""], ["72568u", "3"]])}
        u = checks.run([{"type": "unique", "table": "b", "column": "Batch ID"}, {"type": "complete", "table": "b", "columns": ["Qty"]}], {}, t)
        self.assertEqual([False, False], [x["ok"] for x in u])
        self.assertIn("in rows 1 and 3", u[0]["detail"])
        self.assertIn("row 2: Qty", u[1]["detail"])
        cols = ["Item", "Batch ID", "Qty"]
        rows = [["90", "72568U", "23.990"], ["90", "72570U", "23.950"], ["", "", "47.940"]]       # the last row is the total: no batch ID by design
        self.assertTrue(checks.run([{"type": "complete", "table": "t", "columns": ["Batch ID", "Qty"]}], {}, {"t": table(cols, rows)})[0]["ok"])
        rows[1][1] = ""
        self.assertFalse(checks.run([{"type": "complete", "table": "t", "columns": ["Batch ID", "Qty"]}], {}, {"t": table(cols, rows)})[0]["ok"])

    def test_pattern_identifier_and_date_window(self):
        f = {"gstin": "24AAACK2676H1ZN", "vehicle": "GJ32T 3525", "d": "01.01.2099", "old": "01.01.2001", "ok": "01.01.2026"}
        cs = [{"type": "identifier", "field": "gstin", "kind": "gstin"}, {"type": "identifier", "field": "vehicle", "kind": "vehicle"},
              {"type": "pattern", "field": "vehicle", "regex": "[A-Z]{2}\\d{2}[A-Z] \\d{4}"},
              {"type": "date_window", "field": "d"}, {"type": "date_window", "field": "old", "max_age_days": 365}, {"type": "date_window", "field": "ok", "max_age_days": 3650}]
        res = checks.run(cs, f, {})
        self.assertEqual([False, True, True, False, False, True], [x["ok"] for x in res])
        self.assertIn("is in the future", res[3]["detail"])
        t = {"t": table(["Container"], [["CSQU3054383"], ["CSQU3054384"]])}
        r = checks.run([{"type": "identifier", "table": "t", "column": "Container", "kind": "container"}], {}, t)[0]
        self.assertFalse(r["ok"])
        self.assertIn("CSQU3054384", r["detail"])

    def test_validation_of_the_new_types(self):
        tabs, fields = {"t": ["A", "B"]}, {"x", "y"}
        for check, msg in [({"type": "compare", "left": "A", "op": "~", "right": "B", "table": "t"}, "needs left, right and op"),
                           ({"type": "compare", "left": "A", "op": "<", "right": "Z", "table": "t"}, "unknown names"),
                           ({"type": "same", "a": "x", "b": "nope"}, "must be fields"), ({"type": "unique", "table": "t", "column": "Q"}, "unknown column"),
                           ({"type": "complete", "table": "t", "columns": ["Q"]}, "needs a table"), ({"type": "identifier", "field": "x", "kind": "passport"}, "kind must be"),
                           ({"type": "pattern", "field": "x", "regex": "("}, "bad regex"), ({"type": "date_window", "field": "nope"}, "known field")]:
            with self.assertRaisesRegex(ValueError, msg):
                checks.validate(check, tabs, fields)
        checks.validate({"type": "compare", "left": "x", "op": "<=", "right": "y"}, tabs, fields)

    def test_validation_of_the_names_check(self):
        tabs, fields = {"t": ["A", "B"]}, {"x", "y"}
        with self.assertRaisesRegex(ValueError, "unknown names"):
            checks.validate({"type": "compare", "left": "x", "op": "<", "right": "q"}, tabs, fields)

    def test_known_formulas_run_on_any_table(self):
        tabs, fields = {"t": ["Qty", "Rate", "Amount"]}, {"total", "tax"}
        for check, msg in [({"type": "nope"}, "check type"), ({"type": "row_formula", "table": "x", "result": "Amount", "expr": "1"}, "unknown table"),
                           ({"type": "row_formula", "table": "t", "result": "Amount", "expr": "Qty*Price"}, "unknown column names"),
                           ({"type": "row_formula", "table": "t", "result": "Nope", "expr": "Qty"}, "'result' must be"),
                           ({"type": "field_formula", "result": "total", "expr": "tax + vat"}, "unknown fields"),
                           ({"type": "range", "field": "tax"}, "needs min or max"), ({"type": "row_formula", "table": "t", "result": "Amount", "expr": "Qty.real"}, "only numbers")]:
            with self.assertRaisesRegex(ValueError, msg):
                checks.validate(check, tabs, fields)

    def test_known_formulas_run_on_any_table(self):
        cols = ["Heat No", "C", "MN", "CR", "CU", "NI", "V", "MO", "NB", "TI", "MAE", "CE"]
        good = [["P36819", "0.106", "0.400", "0.007", "0.002", "0.003", "0.001", "0.001", "0.002", "0.000", "0.003", "0.174"]]   # a real certificate
        res = checks.run_automatic([{"columns": cols, "rows": good}])
        self.assertEqual({"table 1: carbon equivalent (CE)", "table 1: micro-alloy sum (MAE)"}, {r["name"] for r in res})
        self.assertTrue(all(r["ok"] for r in res))
        wrong = [row[:-1] + ["0.714"] for row in good]               # 0.174 misread as 0.714
        res = checks.run_automatic([{"columns": cols, "rows": wrong}])
        self.assertFalse(next(r for r in res if "CE" in r["name"])["ok"])
        self.assertEqual([], checks.run_automatic([{"columns": ["A", "B"], "rows": [["1", "2"]]}]))
        total = checks.run_automatic([{"columns": ["Item", "Qty"], "rows": [["a", "1"], ["b", "2"], ["Total", "3"]]}])
        self.assertEqual("table 1: total row", total[0]["name"])
        self.assertTrue(total[0]["ok"])


if __name__ == "__main__":
    unittest.main()
