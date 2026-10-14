from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile

from knowledge_forge.docx_extract import DocxExtractionError, extract_docx_paragraphs


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
</Types>
"""

DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Alpha Heading</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>First line</w:t></w:r>
      <w:r><w:tab/></w:r>
      <w:r><w:t>Tabbed</w:t></w:r>
      <w:r><w:br/></w:r>
      <w:r><w:t>Second line</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc>
          <w:p><w:r><w:t>Cell text</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""


def _zip_entry(name: str, payload: str) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_STORED
    return info, payload.encode("utf-8")


def build_docx_bytes(
    *,
    document_xml: str = DOCUMENT_XML,
    include_document: bool = True,
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info, payload = _zip_entry("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr(info, payload)
        if include_document:
            info, payload = _zip_entry("word/document.xml", document_xml)
            archive.writestr(info, payload)
    return buffer.getvalue()


DOCX_BYTES = build_docx_bytes()


class DocxExtractionTests(unittest.TestCase):
    def test_extracts_paragraphs_in_document_order_with_style_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.docx"
            path.write_bytes(DOCX_BYTES)
            result = extract_docx_paragraphs(path)

        self.assertEqual(
            result["extractor"],
            {
                "name": "knowledge-forge-docx-xml",
                "version": "0.1.0",
            },
        )
        self.assertEqual(result["paragraph_count"], 3)
        self.assertEqual(
            [item["text"] for item in result["paragraphs"]],
            [
                "Alpha Heading",
                "First line\tTabbed\nSecond line",
                "Cell text",
            ],
        )
        self.assertEqual(result["paragraphs"][0]["style_id"], "Heading1")
        self.assertEqual(
            [item["paragraph_number"] for item in result["paragraphs"]],
            [1, 2, 3],
        )

    def test_repeated_extraction_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "same.docx"
            path.write_bytes(DOCX_BYTES)
            first = extract_docx_paragraphs(path)
            second = extract_docx_paragraphs(path)
        self.assertEqual(first, second)

    def test_missing_main_document_part_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.docx"
            path.write_bytes(build_docx_bytes(include_document=False))
            with self.assertRaisesRegex(
                DocxExtractionError,
                "requires .*word/document.xml",
            ):
                extract_docx_paragraphs(path)

    def test_invalid_zip_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.docx"
            path.write_bytes(b"not-a-zip")
            with self.assertRaisesRegex(DocxExtractionError, "valid ZIP"):
                extract_docx_paragraphs(path)

    def test_empty_document_refuses(self):
        empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p/></w:body>
</w:document>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.docx"
            path.write_bytes(build_docx_bytes(document_xml=empty_xml))
            with self.assertRaisesRegex(
                DocxExtractionError,
                "no extractable paragraph text",
            ):
                extract_docx_paragraphs(path)


if __name__ == "__main__":
    unittest.main()
