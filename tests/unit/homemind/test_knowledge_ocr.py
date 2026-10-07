"""OCR and the extended knowledge pipeline (Stage 5).

What these tests protect is not "OCR runs" but the decisions around it:
a scan with no engine must not index as an empty success, a text-layer
PDF must not pay for OCR, and a family's documents must not leave the
household without an explicit opt-in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.family.knowledge_parsers import (
    PARSER_VERSION,
    ParseFailure,
    UnsupportedDocument,
    parse,
    parse_html,
    parse_pptx,
    parse_xlsx,
    parser_for,
)
from homemind.infra.family.ocr import (
    LocalTesseractOcrProvider,
    OcrError,
    OcrPage,
    UnavailableOcrProvider,
    build_default_ocr_provider,
)


class _StubOcr:
    """A provider that returns canned pages without touching a binary."""

    name = "stub"
    is_external = False

    def __init__(self, pages: list[OcrPage] | None = None, *, error: Exception | None = None):
        self._pages = pages or []
        self._error = error
        self.calls = 0

    def recognize_pdf(self, path: Path) -> list[OcrPage]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._pages


# ------------------------------------------------------------------ formats


def test_new_suffixes_select_a_parser(tmp_path: Path) -> None:
    for suffix, kind in (
        (".html", "html"),
        (".htm", "html"),
        (".xlsx", "xlsx"),
        (".pptx", "pptx"),
        (".pdf", "pdf"),
        (".docx", "docx"),
        (".txt", "text"),
    ):
        probe = tmp_path / f"sample{suffix}"
        probe.write_bytes(b"x")
        assert parser_for(probe) == kind, suffix


def test_parser_version_bumped_so_stale_chunks_are_rebuilt() -> None:
    """Adding formats and OCR changes what a chunk means.

    A stored document keeps its chunks only when its parser version
    matches, so the version has to move or every family would silently
    keep v1 chunks with no OCR and no spreadsheet support.
    """
    assert PARSER_VERSION == "2"


# --------------------------------------------------------------------- html


def test_html_keeps_prose_and_drops_scripts(tmp_path: Path) -> None:
    page = tmp_path / "receipt.html"
    page.write_text(
        "<html><head><style>body{color:red}</style></head><body>"
        "<script>var secret='do-not-index';</script>"
        "<h1>Warranty</h1><p>Covers 24 months.</p>"
        "<div>Serial 12345</div></body></html>",
        encoding="utf-8",
    )
    parsed = parse_html(page)
    assert "Warranty" in parsed.text
    assert "24 months" in parsed.text
    assert "Serial 12345" in parsed.text
    # The whole point: nothing a reader cannot see ends up searchable.
    assert "do-not-index" not in parsed.text
    assert "color:red" not in parsed.text


def test_html_entities_are_decoded(tmp_path: Path) -> None:
    page = tmp_path / "a.html"
    page.write_text("<p>Tom &amp; Jerry &lt;3</p>", encoding="utf-8")
    assert "Tom & Jerry <3" in parse_html(page).text


def test_html_chunks_carry_a_locator(tmp_path: Path) -> None:
    page = tmp_path / "long.html"
    page.write_text("<p>" + ("word " * 600) + "</p>", encoding="utf-8")
    parsed = parse_html(page)
    assert len(parsed.chunks) >= 2
    assert all(chunk.locator == "paragraph" for chunk in parsed.chunks)


def test_empty_html_is_still_parseable(tmp_path: Path) -> None:
    page = tmp_path / "blank.html"
    page.write_text("<html></html>", encoding="utf-8")
    assert parse_html(page).text == ""


# -------------------------------------------------------------------- xlsx


def test_xlsx_rows_carry_a_sheet_locator(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Medical"
    sheet.append(["Item", "Cost"])
    sheet.append(["Vaccination", "120"])
    sheet2 = book.create_sheet("School")
    sheet2.append(["Term"])
    sheet2.append(["Autumn"])
    target = tmp_path / "records.xlsx"
    book.save(target)

    parsed = parse_xlsx(target)
    assert "Vaccination" in parsed.text
    assert "Autumn" in parsed.text
    locators = {chunk.locator for chunk in parsed.chunks}
    assert locators & {"sheet Medical", "sheet School"}


def test_empty_spreadsheet_is_a_parse_failure(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    target = tmp_path / "empty.xlsx"
    book.save(target)
    with pytest.raises(ParseFailure):
        parse_xlsx(target)


# -------------------------------------------------------------------- pptx


def test_pptx_slides_carry_a_slide_locator(tmp_path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "House rules"
    target = tmp_path / "deck.pptx"
    deck.save(target)

    parsed = parse_pptx(target)
    assert "House rules" in parsed.text
    assert all(chunk.locator == "slide 1" for chunk in parsed.chunks)


# --------------------------------------------------------------------- pdf


def _minimal_pdf_with_text(tmp_path: Path, body: str = "Hello warranty") -> Path:
    """A one-page PDF carrying a real text layer.

    Written by hand rather than through a writer library: pypdf cannot
    add text, and this keeps the test to the one property that matters
    — that ``extract_text`` finds something and OCR is never needed.
    """
    stream = f"BT /F1 12 Tf 72 720 Td ({body}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    target = tmp_path / "text.pdf"
    target.write_bytes(bytes(out))
    return target


def test_a_pdf_text_layer_is_preferred_over_ocr(tmp_path: Path) -> None:
    """OCR costs minutes; a PDF that already has text pays none of it."""
    pytest.importorskip("pypdf")
    target = _minimal_pdf_with_text(tmp_path)

    provider = _StubOcr()
    parsed = parse(target, ocr_provider=provider)
    assert provider.calls == 0
    assert parsed.ocr_used is False
    assert "warranty" in parsed.text.lower()


def test_a_scanned_pdf_without_an_engine_is_unsupported(tmp_path: Path) -> None:
    """An empty index entry is worse than an honest failure.

    A family who believes their warranty is searchable, and cannot find
    it, has been told a lie. Recording UNSUPPORTED is visible.
    """
    pypdf = pytest.importorskip("pypdf")
    target = tmp_path / "scan.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(UnsupportedDocument):
        parse(target, ocr_provider=None)


def test_ocr_pages_become_citable_chunks(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    target = tmp_path / "scan.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)

    provider = _StubOcr(
        [
            OcrPage(page_number=1, text="Warranty expires 2027-03-01"),
            OcrPage(page_number=2, text="Serial number A-99120"),
        ]
    )
    parsed = parse(target, ocr_provider=provider)
    assert "Warranty expires" in parsed.text
    assert parsed.ocr_used is True
    pages = {chunk.page_number for chunk in parsed.chunks}
    assert pages == {1, 2}
    assert all(chunk.locator and chunk.locator.startswith("page ") for chunk in parsed.chunks)


def test_recognition_that_returns_nothing_is_a_failure(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    target = tmp_path / "scan.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)

    # Blank recognition must not be recorded as a successful index.
    with pytest.raises(ParseFailure):
        parse(target, ocr_provider=_StubOcr([OcrPage(page_number=1, text="   ")]))


def test_an_ocr_engine_failure_is_a_parse_failure(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    target = tmp_path / "scan.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ParseFailure) as excinfo:
        parse(target, ocr_provider=_StubOcr(error=OcrError("tesseract is not installed")))
    assert "tesseract" in str(excinfo.value)


def test_a_provider_bug_does_not_leak_its_type_as_a_crash(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    target = tmp_path / "scan.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ParseFailure):
        parse(target, ocr_provider=_StubOcr(error=ValueError("internal detail")))


def test_an_oversized_file_is_refused_before_it_is_read(tmp_path: Path) -> None:
    from homemind.infra.family.knowledge_parsers import MAX_PARSE_BYTES

    target = tmp_path / "huge.pdf"
    with target.open("wb") as handle:
        handle.truncate(MAX_PARSE_BYTES + 1)
    with pytest.raises(ParseFailure):
        parse(target, ocr_provider=_StubOcr())


# ---------------------------------------------------------------- ocr provider


def test_local_provider_is_not_external() -> None:
    """The privacy default: recognition stays inside the household."""
    assert LocalTesseractOcrProvider.is_external is False
    assert UnavailableOcrProvider().is_external is False


def test_unavailable_provider_fails_loudly(tmp_path: Path) -> None:
    target = tmp_path / "x.pdf"
    target.write_bytes(b"%PDF")
    with pytest.raises(OcrError):
        UnavailableOcrProvider().recognize_pdf(target)


def test_local_provider_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OcrError):
        LocalTesseractOcrProvider().recognize_pdf(tmp_path / "absent.pdf")


def test_local_provider_refuses_an_oversized_file(tmp_path: Path) -> None:
    from homemind.infra.family.ocr import MAX_OCR_BYTES

    target = tmp_path / "big.pdf"
    with target.open("wb") as handle:
        handle.truncate(MAX_OCR_BYTES + 1)
    with pytest.raises(OcrError) as excinfo:
        LocalTesseractOcrProvider().recognize_pdf(target)
    assert "limit" in str(excinfo.value)


def test_default_provider_selection_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    provider = build_default_ocr_provider()
    assert isinstance(provider, UnavailableOcrProvider)


def test_default_provider_prefers_a_present_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    provider = build_default_ocr_provider()
    assert isinstance(provider, LocalTesseractOcrProvider)
    assert provider.is_external is False


def test_page_numbers_are_one_based(tmp_path: Path) -> None:
    from homemind.infra.family.ocr import _page_number_from_filename

    assert _page_number_from_filename(Path("/tmp/doc-00.png")) == 1
    assert _page_number_from_filename(Path("/tmp/doc-07.png")) == 8
    # A name without the numeric suffix still yields a usable page.
    assert _page_number_from_filename(Path("/tmp/doc.png")) == 1
