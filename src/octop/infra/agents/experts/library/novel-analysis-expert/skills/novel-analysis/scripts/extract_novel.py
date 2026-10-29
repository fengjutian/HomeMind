"""Extract EPUB, PDF, or TXT fiction into source-marked UTF-8 text."""

from __future__ import annotations

import html
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def _plain_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    lines = (re.sub(r"\s+", " ", html.unescape(line)).strip() for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _extract_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as book:
        container = ElementTree.fromstring(book.read("META-INF/container.xml"))
        rootfile = next(node for node in container.iter() if node.tag.endswith("rootfile"))
        package_path = rootfile.attrib["full-path"]
        package_dir = Path(package_path).parent
        package = ElementTree.fromstring(book.read(package_path))
        manifest = {
            node.attrib["id"]: node.attrib["href"]
            for node in package.iter()
            if node.tag.endswith("item") and "id" in node.attrib and "href" in node.attrib
        }
        spine = [node.attrib["idref"] for node in package.iter() if node.tag.endswith("itemref")]
        sections: list[str] = []
        for index, item_id in enumerate(spine, 1):
            href = manifest.get(item_id)
            if not href:
                continue
            member = (package_dir / href.split("#", 1)[0]).as_posix()
            try:
                text = _plain_html(book.read(member).decode("utf-8", errors="replace"))
            except KeyError:
                continue
            if text:
                sections.append(f"[[EPUB_SECTION {index}: {href}]]\n{text}")
        return "\n\n".join(sections)


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    pages = []
    for index, page in enumerate(PdfReader(path).pages, 1):
        pages.append(f"[[PDF_PAGE {index}]]\n{page.extract_text() or ''}")
    return "\n\n".join(pages)


def _extract_txt(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return "\n".join(
        f"[[TXT_LINE {index}]] {line}" for index, line in enumerate(text.splitlines(), 1)
    )


def extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return _extract_epub(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".txt":
        return _extract_txt(path)
    raise ValueError(f"unsupported format: {suffix}; expected .epub, .pdf, or .txt")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: extract_novel.py INPUT OUTPUT")
    source, output = map(Path, sys.argv[1:])
    output.write_text(extract(source), encoding="utf-8")


if __name__ == "__main__":
    main()
