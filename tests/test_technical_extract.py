from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from knowledge_forge.technical_extract import TechnicalExtractionError, extract_technical_blocks


class TechnicalExtractionTests(unittest.TestCase):
    def test_blocks_preserve_indentation_and_line_ranges(self):
        source = "import os\n\ndef alpha(value):\n    if value:\n        return value\n\nVALUE = 3\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(source, encoding="utf-8")
            result = extract_technical_blocks(path)
        self.assertEqual(result["language"], "python")
        self.assertEqual(
            [(b["start_line"], b["end_line"], b["text"]) for b in result["blocks"]],
            [
                (1, 1, "import os"),
                (3, 5, "def alpha(value):\n    if value:\n        return value"),
                (7, 7, "VALUE = 3"),
            ],
        )

    def test_empty_file_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "__init__.py"
            path.write_bytes(b"")
            self.assertEqual(extract_technical_blocks(path)["blocks"], [])

    def test_invalid_utf8_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.py"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(TechnicalExtractionError, "UTF-8"):
                extract_technical_blocks(path)

    def test_unknown_suffix_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.bin"
            path.write_bytes(b"x")
            with self.assertRaisesRegex(TechnicalExtractionError, "unsupported technical source suffix"):
                extract_technical_blocks(path)


if __name__ == "__main__":
    unittest.main()
