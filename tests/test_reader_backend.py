"""Reader-backend switch contracts (MLX provider mocked)."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor import extract  # noqa: E402
from invoice_extractor.mlx_reader import _extract_json  # noqa: E402


class ReaderBackendContracts(unittest.TestCase):
    def test_mlx_backend_selected_by_env(self):
        with patch.dict(os.environ, {"READER_BACKEND": "mlx"}):
            with patch("invoice_extractor.mlx_reader.call_mlx",
                       return_value={"a": 1}) as m:
                self.assertEqual(extract.call_llm("doc", "ignored"), {"a": 1})
                m.assert_called_once()

    def test_ollama_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("READER_BACKEND", None)
            with patch("invoice_extractor.extract.ollama") as o:
                o.chat.return_value = {"message": {"content": '{"a": 1}'}}
                self.assertEqual(extract.call_llm("doc", "m"), {"a": 1})

    def test_json_repair(self):
        self.assertEqual(_extract_json('prefix {"a": 1} suffix'), {"a": 1})
        with self.assertRaises(Exception):
            _extract_json("no json here")

    def test_fence_and_think_strip(self):
        self.assertEqual(
            _extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(
            _extract_json('<think>reasoning {with} braces</think>\n{"a": 1}'),
            {"a": 1})

    def test_control_char_sanitize(self):
        self.assertEqual(
            _extract_json('{"a": "x\ny", "b": [1,\n2]}'),
            {"a": "x y", "b": [1, 2]})

    def test_trailing_garbage_ignored(self):
        self.assertEqual(
            _extract_json('{"a": 1} trailing {"b": 2}'), {"a": 1})


if __name__ == "__main__":
    unittest.main()
