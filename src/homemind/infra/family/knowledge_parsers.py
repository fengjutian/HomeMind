"""Parsers for the household documents HomeMind indexes.

Four formats, chosen because that is what a family actually keeps: plain
text, Markdown, text PDFs, and Word documents. OCR for scanned PDFs is
explicitly *not* here -- a first useful version beats a perfect one that
never lands, and a scanned PDF fails loudly as ``UNSUPPORTED`` rather
than indexing as an empty document.

Every parser returns :class:`ParsedDocument`: normalised text plus the
locator that puts each chunk back into the original. Locators are the
whole point of this module. A search hit that says "page 3" is
verifiable; one that says "somewhere in the file" is not.

Parsers are defensive about size: a 900 MB PDF must not be read into
memory just to be rejected, so the byte budget is checked before the
content is pulled in.
"""

from __future__ import annotations

import contextlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Refuse anything larger than this before reading. A family's photo
#: library contains video; parsing it as text would exhaust memory.
MAX_PARSE_BYTES = 32 * 1024 * 1024

#: Chunk targets. Large enough that a sentence survives, small enough that
#: a search hit is specific.
DEFAULT_CHUNK_CHARS = 900
DEFAULT_CHUNK_OVERLAP = 120


class UnsupportedDocument(ValueError):
    """The file is a format this build cannot read."""


class ParseFailure(ValueError):
    """The file is the right format but could not be read."""


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit, with enough context to cite it."""

    ordinal: int
    text: str
    locator: str | None = None
    page_number: int | None = None


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    chunks: list[Chunk] = field(default_factory=list)
    parser_version: str = "1"
    #: Set when the text came out of OCR rather than a text layer.
    ocr_used: bool = False


# ------------------------------------------------------------- dispatch

#: Extension -> parser key. The extension is what a household actually
#: has; the stored mime type can be wrong or absent.
PARSERS_BY_SUFFIX: dict[str, str] = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".html": "html",
    ".htm": "html",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".pptx": "pptx",
}

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(PARSERS_BY_SUFFIX)

#: Bumped when a parser changes in a way that invalidates stored chunks.
PARSER_VERSION = "2"


def parser_for(path: Path) -> str | None:
    """The parser that would read ``path``, or ``None``."""
    return PARSERS_BY_SUFFIX.get(path.suffix.lower())


def _guard_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_PARSE_BYTES:
        raise ParseFailure(f"file is too large to parse ({size} bytes)")


def normalize_text(raw: str) -> str:
    """Collapse whitespace without destroying paragraph structure.

    Paragraph breaks are kept because they are the cheapest locator a text
    file can offer, and they keep a chunk from fusing two unrelated
    sentences.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ")
    paragraphs = re.split(r"\n\s*\n", text)
    cleaned = [" ".join(part.split()) for part in paragraphs]
    return "\n\n".join(part for part in cleaned if part)


def chunk_text(
    text: str,
    *,
    size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks on paragraph boundaries.

    Overlap exists so a fact that straddles a boundary is still
    retrievable from at least one chunk. Splitting prefers a paragraph
    break within the window and falls back to a hard cut, because a hard
    cut mid-word is worse than slightly uneven chunks.
    """
    if not text.strip():
        return []
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single oversized paragraph is cut on the size boundary.
        while len(paragraph) > size:
            head = paragraph[:size]
            cut = head.rfind(" ")
            if cut < size // 2:
                cut = size
            chunks.append(head[:cut].strip())
            remainder = paragraph[cut:].strip()
            if overlap and remainder:
                paragraph = f"{head[max(0, cut - overlap) : cut]}{remainder}"
            else:
                paragraph = remainder
        current = paragraph
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c.strip()]


# --------------------------------------------------------------- text


def parse_text(path: Path) -> ParsedDocument:
    _guard_size(path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ParseFailure(f"cannot read {path.name}: {exc}") from exc
    text = normalize_text(raw)
    chunks = [
        Chunk(
            ordinal=index,
            text=body,
            locator=f"paragraph {index + 1}",
        )
        for index, body in enumerate(chunk_text(text))
    ]
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION)


# ---------------------------------------------------------------- pdf


def _pdf_text(path: Path) -> list[tuple[int, str]]:
    """Per-page text, or raise if the page is an image with no text layer."""
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ParseFailure("pypdf is not installed on this host") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ParseFailure(f"cannot read PDF {path.name}: {exc}") from exc

    pages: list[tuple[int, str]] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            content = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the doc
            raise ParseFailure(f"cannot read page {number}: {exc}") from exc
        normalized = normalize_text(content)
        if normalized:
            pages.append((number, normalized))
    if not pages:
        # Almost always a scan. Saying so is more useful than indexing an
        # empty document that will never match a query.
        raise _NoTextLayer("PDF has no text layer")
    return pages


class _NoTextLayer(ParseFailure):
    """A PDF whose pages carry images rather than text.

    Distinguished from an ordinary parse failure because it is the one
    case OCR can fix: :func:`parse_pdf` catches exactly this and hands
    the file to an engine instead of recording the document unreadable.
    """


def parse_pdf(path: Path, *, ocr_provider: Any | None = None) -> ParsedDocument:
    """Parse a PDF, falling back to OCR when it is a scan.

    The text layer is tried first because it is free, exact, and already
    carries character positions. OCR only runs when there is no usable
    layer, so a family's ordinary PDFs pay nothing for the capability.
    """
    _guard_size(path)
    try:
        pages = _pdf_text(path)
    except _NoTextLayer:
        if ocr_provider is None:
            raise UnsupportedDocument(
                "PDF has no text layer and no OCR provider is configured",
            ) from None
        return _parse_pdf_with_ocr(path, ocr_provider)

    text = "\n\n".join(body for _number, body in pages)
    chunks: list[Chunk] = []
    for number, body in pages:
        for piece in chunk_text(body):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=piece,
                    locator=f"page {number}",
                    page_number=number,
                )
            )
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION)


def _parse_pdf_with_ocr(path: Path, ocr_provider: Any) -> ParsedDocument:
    """Run recognition over a scanned PDF and chunk what came back.

    The provider is called from here rather than from the knowledge
    manager so the privacy gate has already run by the time any page
    text exists in memory.
    """
    from homemind.infra.family.ocr import OcrError  # noqa: PLC0415 — avoid a cycle

    try:
        recognized = ocr_provider.recognize_pdf(path)
    except OcrError as exc:
        raise ParseFailure(f"OCR failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — a provider bug is a parse failure
        raise ParseFailure(f"OCR provider error: {type(exc).__name__}") from exc

    pages = [
        (page.page_number, normalize_text(page.text)) for page in recognized if page.text.strip()
    ]
    if not pages:
        # Recognised nothing: recording this as an empty success would
        # make the family believe a blank page is searchable.
        raise ParseFailure("OCR produced no readable text")

    text = "\n\n".join(body for _number, body in pages)
    chunks: list[Chunk] = []
    for number, body in pages:
        for piece in chunk_text(body):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=piece,
                    locator=f"page {number}",
                    page_number=number,
                )
            )
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION, ocr_used=True)


# --------------------------------------------------------------- docx


def parse_docx(path: Path) -> ParsedDocument:
    """Read a Word document without needing python-docx.

    A .docx is a zip of XML, and the paragraphs live in one part. Reading
    it directly avoids a dependency whose absence would otherwise make the
    format silently unavailable on a NAS.
    """
    _guard_size(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            document_part = "word/document.xml"
            if document_part not in names:
                raise ParseFailure("docx has no word/document.xml part")
            xml = archive.read(document_part).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise ParseFailure(f"{path.name} is not a valid docx archive") from exc
    except OSError as exc:
        raise ParseFailure(f"cannot read {path.name}: {exc}") from exc

    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.DOTALL)
    lines: list[str] = []
    for paragraph in paragraphs:
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, flags=re.DOTALL)
        line = "".join(_unescape(run) for run in runs).strip()
        if line:
            lines.append(line)

    if not lines:
        raise UnsupportedDocument("docx contains no readable text")

    text = normalize_text("\n\n".join(lines))
    chunks = [
        Chunk(ordinal=index, text=body, locator=f"paragraph {index + 1}")
        for index, body in enumerate(chunk_text(text))
    ]
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION)


def _unescape(value: str) -> str:
    """Undo the XML entities Word writes inside ``<w:t>``."""
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


# ------------------------------------------------------------ dispatch


def parse(path: Path, *, ocr_provider: Any | None = None) -> ParsedDocument:
    """Parse ``path`` with whichever parser its extension selects.

    ``ocr_provider`` is only consulted for a PDF with no usable text
    layer — that is the signature of a scan. ``None`` means "no engine",
    in which case such a PDF is recorded UNSUPPORTED rather than
    indexed as an empty document that would never match a query.
    """
    kind = parser_for(path)
    if kind is None:
        raise UnsupportedDocument(
            f"{path.suffix or 'unknown'} is not a supported document format",
        )
    if not path.is_file():
        raise ParseFailure(f"file does not exist: {path.name}")
    dispatch = {
        "text": parse_text,
        "html": parse_html,
        "xlsx": parse_xlsx,
        "pptx": parse_pptx,
        "docx": parse_docx,
    }
    handler = dispatch.get(kind)
    if handler is not None:
        return handler(path)
    return parse_pdf(path, ocr_provider=ocr_provider)


# --------------------------------------------------------------- html


#: Tags whose contents are never prose a reader would cite.
_HTML_DROP_TAGS = ("script", "style", "noscript", "template", "head")
#: Tags that imply a line break in the rendered page.
_HTML_BLOCK_TAGS = (
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "blockquote",
    "pre",
    "table",
)


def parse_html(path: Path) -> ParsedDocument:
    """Read an HTML page as text.

    Hand-rolled rather than pulling in a parser: the goal is a
    searchable copy of what a reader sees, not a faithful DOM. Script
    and style contents are dropped, because indexing a page's
    JavaScript would let a query "match" a bundle it has nothing to do
    with.
    """
    _guard_size(path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ParseFailure(f"cannot read HTML: {exc}") from exc
    body = _strip_html(raw)
    text = normalize_text(body)
    chunks: list[Chunk] = []
    for ordinal, piece in enumerate(chunk_text(text)):
        # HTML has no pages; the heading that introduced the piece is
        # the locator a reader can actually follow.
        chunks.append(Chunk(ordinal=ordinal, text=piece, locator="paragraph", page_number=None))
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION)


def _strip_html(raw: str) -> str:
    import re  # noqa: PLC0415

    text = raw
    for tag in _HTML_DROP_TAGS:
        text = re.sub(rf"<{tag}\b.*?</{tag}>", " ", text, flags=re.S | re.I)
    block_pattern = "|".join(_HTML_BLOCK_TAGS)
    # Turn block boundaries into newlines *before* dropping tags, so two
    # adjacent <div>s do not run together into one meaningless line.
    text = re.sub(rf"</?(?:{block_pattern})\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, replacement in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, replacement)
    return text


# --------------------------------------------------------------- xlsx


def parse_xlsx(path: Path) -> ParsedDocument:
    """Read a spreadsheet, keeping a sheet-and-cell locator.

    A cell reference is the locator a person can follow back to the
    file, so every chunk records the sheet it came from and the range it
    covers. Rows are joined into prose because that is what a search
    index can match; the sheet header is repeated per row so a hit
    still names its sheet after chunking splits it.
    """
    _guard_size(path)
    try:
        from openpyxl import load_workbook  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ParseFailure("openpyxl is not installed on this host") from exc

    try:
        # ``read_only`` keeps a 200 MB workbook from being materialised;
        # formulas are read as their cached value, which is what a reader
        # would see on screen.
        workbook = load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseFailure(f"cannot read spreadsheet: {type(exc).__name__}") from exc

    sections: list[tuple[str, str]] = []
    try:
        for sheet in workbook.worksheets:
            rows = []
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = ["" if cell is None else str(cell) for cell in row]
                if not any(cells):
                    continue
                rows.append((number, "\t".join(cells)))
            if rows:
                body = "\n".join(body for _number, body in rows)
                sections.append((sheet.title, body))
    except Exception as exc:
        raise ParseFailure(f"cannot read worksheet: {type(exc).__name__}") from exc
    finally:
        with contextlib.suppress(Exception):
            workbook.close()

    if not sections:
        raise ParseFailure("spreadsheet has no readable rows")

    text = "\n\n".join(f"{title}\n{body}" for title, body in sections)
    chunks: list[Chunk] = []
    for title, body in sections:
        for piece in chunk_text(body):
            chunks.append(Chunk(ordinal=len(chunks), text=piece, locator=f"sheet {title}"))
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION)


# --------------------------------------------------------------- pptx


def parse_pptx(path: Path) -> ParsedDocument:
    """Read a slide deck, one locator per slide.

    Slides are the natural unit of citation: "slide 4" is something a
    reader can go and look at.
    """
    _guard_size(path)
    try:
        from pptx import Presentation  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ParseFailure("python-pptx is not installed on this host") from exc

    try:
        presentation = Presentation(str(path))
    except Exception as exc:
        raise ParseFailure(f"cannot read presentation: {type(exc).__name__}") from exc

    sections: list[tuple[int, str]] = []
    for number, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            frame = getattr(shape, "text_frame", None)
            if frame is None:
                continue
            body = normalize_text(frame.text or "")
            if body:
                parts.append(body)
        if parts:
            sections.append((number, "\n".join(parts)))

    if not sections:
        raise ParseFailure("presentation has no readable text")

    text = "\n\n".join(body for _number, body in sections)
    chunks: list[Chunk] = []
    for number, body in sections:
        for piece in chunk_text(body):
            chunks.append(
                Chunk(
                    ordinal=len(chunks), text=piece, locator=f"slide {number}", page_number=number
                )
            )
    return ParsedDocument(text=text, chunks=chunks, parser_version=PARSER_VERSION)


__all__ = [
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_CHUNK_OVERLAP",
    "MAX_PARSE_BYTES",
    "PARSERS_BY_SUFFIX",
    "PARSER_VERSION",
    "SUPPORTED_SUFFIXES",
    "Chunk",
    "ParseFailure",
    "ParsedDocument",
    "UnsupportedDocument",
    "chunk_text",
    "normalize_text",
    "parse",
    "parse_docx",
    "parse_html",
    "parse_pdf",
    "parse_pptx",
    "parse_text",
    "parse_xlsx",
    "parser_for",
]
