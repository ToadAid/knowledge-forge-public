from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile

from knowledge_forge.epub_extract import EpubExtractionError, extract_epub_spine


MIMETYPE = b"application/epub+zip"

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
      media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

PACKAGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c2"/>
    <itemref idref="c1" linear="no"/>
  </spine>
</package>
"""

CHAPTER_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter One</title><style>.x { color: red; }</style></head>
  <body>
    <h1>Alpha Title</h1>
    <p>Alpha <strong>body</strong></p>
    <script>DO NOT EXECUTE</script>
  </body>
</html>
"""

CHAPTER_TWO = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter Two</title></head>
  <body>
    <h2>Beta Heading</h2>
    <p>Beta body<br/>second line</p>
  </body>
</html>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body><p>DO NOT LOAD NAV OUTSIDE SPINE</p></body>
</html>
"""


def _entry(name: str, payload: bytes, *, stored: bool = True) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    return info, payload


def build_epub_bytes(
    *,
    container_xml: str = CONTAINER_XML,
    package_xml: str = PACKAGE_XML,
    chapter_one: str = CHAPTER_ONE,
    chapter_two: str = CHAPTER_TWO,
    mimetype: bytes = MIMETYPE,
    mimetype_first: bool = True,
    mimetype_stored: bool = True,
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        entries = [
            ("META-INF/container.xml", container_xml.encode("utf-8"), True),
            ("OEBPS/content.opf", package_xml.encode("utf-8"), True),
            ("OEBPS/nav.xhtml", NAV_XHTML.encode("utf-8"), True),
            ("OEBPS/text/ch1.xhtml", chapter_one.encode("utf-8"), True),
            ("OEBPS/text/ch2.xhtml", chapter_two.encode("utf-8"), True),
        ]
        mime_entry = ("mimetype", mimetype, mimetype_stored)
        ordered = [mime_entry, *entries] if mimetype_first else [entries[0], mime_entry, *entries[1:]]
        for name, payload, stored in ordered:
            info, payload = _entry(name, payload, stored=stored)
            archive.writestr(info, payload)
    return buffer.getvalue()


EPUB_BYTES = build_epub_bytes()


class EpubExtractionTests(unittest.TestCase):
    def test_spine_order_controls_extraction_not_manifest_or_zip_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            path.write_bytes(EPUB_BYTES)
            result = extract_epub_spine(path)

        self.assertEqual(
            result["extractor"],
            {"name": "knowledge-forge-epub-spine", "version": "0.1.0"},
        )
        self.assertEqual(result["package_path"], "OEBPS/content.opf")
        self.assertEqual(
            [document["item_id"] for document in result["documents"]],
            ["c2", "c1"],
        )
        self.assertEqual(
            [document["linear"] for document in result["documents"]],
            [True, False],
        )
        self.assertEqual(
            [block["text"] for block in result["documents"][0]["blocks"]],
            ["Beta Heading", "Beta body\nsecond line"],
        )
        self.assertNotIn("DO NOT LOAD NAV OUTSIDE SPINE", repr(result))
        self.assertNotIn("DO NOT EXECUTE", repr(result))

    def test_source_tags_are_descriptive_and_extraction_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            path.write_bytes(EPUB_BYTES)
            first = extract_epub_spine(path)
            second = extract_epub_spine(path)

        self.assertEqual(first, second)
        self.assertEqual(
            [block["source_tag"] for block in first["documents"][0]["blocks"]],
            ["h2", "p"],
        )

    def test_parent_traversal_manifest_href_refuses(self):
        package = PACKAGE_XML.replace(
            'href="text/ch1.xhtml"',
            'href="../outside.xhtml"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traversal.epub"
            path.write_bytes(build_epub_bytes(package_xml=package))
            with self.assertRaisesRegex(
                EpubExtractionError,
                "may not traverse parent directories",
            ):
                extract_epub_spine(path)

    def test_remote_manifest_href_refuses(self):
        package = PACKAGE_XML.replace(
            'href="text/ch2.xhtml"',
            'href="https://example.com/ch2.xhtml"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote.epub"
            path.write_bytes(build_epub_bytes(package_xml=package))
            with self.assertRaisesRegex(EpubExtractionError, "local archive path"):
                extract_epub_spine(path)

    def test_dtd_or_entity_declarations_refuse(self):
        chapter = """<?xml version="1.0"?>
<!DOCTYPE html [<!ENTITY x "forbidden">]>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>&x;</p></body></html>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entity.epub"
            path.write_bytes(build_epub_bytes(chapter_two=chapter))
            with self.assertRaisesRegex(
                EpubExtractionError,
                "forbidden DTD/entity",
            ):
                extract_epub_spine(path)

    def test_mimetype_requires_exact_bytes_not_trimmed_equivalence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whitespace-mime.epub"
            path.write_bytes(
                build_epub_bytes(mimetype=b"application/epub+zip\\n")
            )
            with self.assertRaisesRegex(
                EpubExtractionError,
                "exact bytes",
            ):
                extract_epub_spine(path)

    def test_container_rootfile_requires_opf_package_media_type(self):
        bad_container = CONTAINER_XML.replace(
            'media-type="application/oebps-package+xml"',
            'media-type="application/xml"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrong-rootfile-type.epub"
            path.write_bytes(build_epub_bytes(container_xml=bad_container))
            with self.assertRaisesRegex(
                EpubExtractionError,
                "rootfile media-type",
            ):
                extract_epub_spine(path)

    def test_invalid_mimetype_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-mime.epub"
            path.write_bytes(build_epub_bytes(mimetype=b"application/zip"))
            with self.assertRaisesRegex(EpubExtractionError, "mimetype"):
                extract_epub_spine(path)

    def test_mimetype_must_be_first_and_uncompressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "not-first.epub"
            first.write_bytes(build_epub_bytes(mimetype_first=False))
            with self.assertRaisesRegex(EpubExtractionError, "first archive member"):
                extract_epub_spine(first)

            compressed = Path(tmp) / "compressed.epub"
            compressed.write_bytes(build_epub_bytes(mimetype_stored=False))
            with self.assertRaisesRegex(EpubExtractionError, "uncompressed"):
                extract_epub_spine(compressed)

    def test_invalid_zip_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.epub"
            path.write_bytes(b"not-a-zip")
            with self.assertRaisesRegex(EpubExtractionError, "valid ZIP"):
                extract_epub_spine(path)


if __name__ == "__main__":
    unittest.main()
