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

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

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


# ------------------------------------------------------------- dispatch

#: Extension -> parser key. The extension is what a household actually
#: has; the stored mime type can be wrong or absent.
PARSERS_BY_SUFFIX: dict[str, str] = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".pdf": "pdf",
    ".docx": "docx",
}

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(PARSERS_BY_SUFFIX)

#: Bumped when a parser changes in a way that invalidates stored chunks.
PARSER_VERSION = "1"


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
        raise UnsupportedDocument(
            "PDF has no text layer; OCR is not available in this build",
        )
    return pages


def parse_pdf(path: Path) -> ParsedDocument:
    _guard_size(path)
    pages = _pdf_text(path)
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


def parse(path: Path) -> ParsedDocument:
    """Parse ``path`` with whichever parser its extension selects."""
    kind = parser_for(path)
    if kind is None:
        raise UnsupportedDocument(
            f"{path.suffix or 'unknown'} is not a supported document format",
        )
    if not path.is_file():
        raise ParseFailure(f"file does not exist: {path.name}")
    if kind == "text":
        return parse_text(path)
    if kind == "pdf":
        return parse_pdf(path)
    return parse_docx(path)


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
    "parse_pdf",
    "parse_text",
    "parser_for",
]
