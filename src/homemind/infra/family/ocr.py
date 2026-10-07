"""Optical character recognition for scanned family documents (Stage 5).

The protocol is deliberately tiny — a provider takes a PDF and returns
per-page text. Everything else (which pages to skip, how to merge OCR
with a text layer, what to record when recognition fails) is *this*
module's job, so adding a second engine later cannot produce two
different behaviours for the same family setting.

**The privacy boundary is the point.** A scanned warranty, a school
notice, a medical form: those are exactly the documents a family does
not want sent to a third party. So local OCR is the default and an
external provider requires an explicit, family-level opt-in plus an
audit row. Recognition text is never logged — a whole page of OCR would
leak the document's content into the log file.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Refuse to OCR anything larger than this. A "scanned document" that
#: is 300 MB is a mistake or an attack, not a warranty.
MAX_OCR_BYTES = 64 * 1024 * 1024

#: Give the OCR engine a hard wall-clock budget so one pathological page
#: cannot hold a worker slot open indefinitely.
DEFAULT_OCR_TIMEOUT_SECONDS = 120


class OcrError(RuntimeError):
    """Recognition failed for a reason worth recording."""


@dataclass(frozen=True)
class OcrPage:
    """One recognised page.

    ``page_number`` is 1-based, matching what a reader sees in a PDF
    viewer — a citation saying "page 0" would be useless.
    """

    page_number: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class OcrResult:
    pages: list[OcrPage] = field(default_factory=list)
    provider: str = ""
    ocr_used: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def is_empty(self) -> bool:
        return not any(page.text.strip() for page in self.pages)


@runtime_checkable
class OcrProvider(Protocol):
    """What a recognition engine has to be able to do."""

    name: str
    #: True when pages leave the household. Used to enforce the privacy
    #: gate rather than trusting the provider to police itself.
    is_external: bool

    def recognize_pdf(self, path: Path) -> list[OcrPage]:
        """Recognise every page of ``path``.

        Must raise :class:`OcrError` on failure rather than returning an
        empty list, so "the engine broke" is distinguishable from "the
        document is blank".
        """
        ...


class LocalTesseractOcrProvider:
    """OCR on this machine via ``tesseract``.

    The default, and the only one enabled out of the box: recognition
    stays inside the household. Availability is a *runtime* question,
    not an import-time one, because a deployment without the binary
    should still be able to index text PDFs.
    """

    name = "local-tesseract"
    is_external = False

    def __init__(
        self,
        *,
        binary: str | None = None,
        languages: str = "chi_sim+eng",
        timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
    ) -> None:
        self._binary = binary or shutil.which("tesseract") or "tesseract"
        self._languages = languages
        self._timeout = timeout_seconds

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def recognize_pdf(self, path: Path) -> list[OcrPage]:
        if not path.is_file():
            raise OcrError("file not found")
        size = path.stat().st_size
        if size > MAX_OCR_BYTES:
            raise OcrError(f"file is {size} bytes, over the {MAX_OCR_BYTES} limit")

        # Render pages to PNG with pdftoppm, then OCR each one. Keeping
        # the two steps apart means a missing poppler tool fails with a
        # clear message instead of a tesseract parse error.
        images = self._render(path)
        try:
            return [self._recognize_image(image) for image in images]
        finally:
            for image in images:
                image.unlink(missing_ok=True)

    def _render(self, path: Path) -> list[Path]:
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm is None:
            raise OcrError("pdftoppm is not installed; cannot rasterise the PDF")
        prefix = path.with_suffix(".homemind-ocr")
        try:
            subprocess.run(
                [pdftoppm, "-r", "200", "-png", str(path), str(prefix)],
                check=True,
                capture_output=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError("rasterising the PDF timed out") from exc
        except subprocess.CalledProcessError as exc:
            # The stderr is an engine message, not document content.
            raise OcrError(f"rasterising the PDF failed: {exc.returncode}") from exc
        images = sorted(path.parent.glob(f"{prefix.name}-*.png"))
        if not images:
            raise OcrError("rasterising produced no pages")
        return images

    def _recognize_image(self, image: Path) -> OcrPage:
        try:
            completed = subprocess.run(
                [
                    self._binary,
                    str(image),
                    "stdout",
                    "-l",
                    self._languages,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise OcrError("tesseract is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise OcrError("OCR timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise OcrError(f"OCR failed with exit code {exc.returncode}") from exc

        number = _page_number_from_filename(image)
        return OcrPage(page_number=number, text=completed.stdout, confidence=None)


def _page_number_from_filename(image: Path) -> int:
    """Recover the 1-based page index from ``prefix-07.png``."""
    stem = image.stem
    _, _, suffix = stem.rpartition("-")
    try:
        return int(suffix) + 1
    except ValueError:
        return 1


class UnavailableOcrProvider:
    """Stand-in used when no engine is installed.

    Failing loudly beats silently returning empty text: an indexed but
    blank document is worse than a recorded ``UNSUPPORTED`` row,
    because the family would believe their warranty was searchable.
    """

    name = "unavailable"
    is_external = False

    def __init__(self, reason: str = "no OCR engine is installed") -> None:
        self._reason = reason

    def recognize_pdf(self, path: Path) -> list[OcrPage]:
        raise OcrError(self._reason)


def build_default_ocr_provider() -> OcrProvider:
    """Pick the provider for this deployment.

    One engine, chosen here rather than configured per call: the plan is
    explicit that a first useful version beats two half-built ones, and
    an external provider is a family decision, not a global default.
    """
    provider = LocalTesseractOcrProvider()
    if provider.is_available():
        return provider
    logger.info("OCR: tesseract not found; scanned PDFs will record UNSUPPORTED")
    return UnavailableOcrProvider()


__all__ = [
    "DEFAULT_OCR_TIMEOUT_SECONDS",
    "MAX_OCR_BYTES",
    "LocalTesseractOcrProvider",
    "OcrError",
    "OcrPage",
    "OcrProvider",
    "OcrResult",
    "UnavailableOcrProvider",
    "build_default_ocr_provider",
]
