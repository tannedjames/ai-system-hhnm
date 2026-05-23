"""Extract text from various document formats."""
import io
import logging
from typing import Any, List

from backend.config import settings

logger = logging.getLogger(__name__)


def _ocr_pdf_page(page: Any, enabled: bool | None = None, langs: str | None = None, dpi: int | None = None) -> str:
    if not (settings.OCR_ENABLED if enabled is None else enabled):
        return ""
    try:
        import pytesseract

        image = page.to_image(resolution=dpi or settings.OCR_DPI).original
        return (pytesseract.image_to_string(image, lang=langs or settings.OCR_LANGS) or "").strip()
    except Exception as exc:
        logger.warning("OCR fallback failed on page %s: %s", getattr(page, "page_number", "?"), exc)
        return ""


def extract_pdf_with_diagnostics(
    file_content: bytes,
    ocr_enabled: bool | None = None,
    ocr_langs: str | None = None,
    ocr_dpi: int | None = None,
    ocr_min_text_chars: int | None = None,
) -> tuple[List[str], dict[str, int]]:
    """Extract PDF text with optional OCR fallback and page-level diagnostics."""
    import pdfplumber

    pages = []
    empty_page_indexes = []
    ocr_pages_count = 0
    min_text_chars = ocr_min_text_chars or settings.OCR_MIN_TEXT_CHARS
    with pdfplumber.open(io.BytesIO(file_content)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()
            if len(text) < min_text_chars:
                empty_page_indexes.append(page_index)
            pages.append(text)

        empty_ratio = len(empty_page_indexes) / max(1, len(pages))
        should_ocr = (
            (settings.OCR_ENABLED if ocr_enabled is None else ocr_enabled)
            and empty_page_indexes
            and (
                len(pages) < settings.PDF_MIN_PAGES_FOR_TEXT_GUARD
                or empty_ratio >= settings.PDF_EMPTY_PAGE_RATIO_THRESHOLD
            )
        )
        if should_ocr:
            for page_index in empty_page_indexes:
                ocr_text = _ocr_pdf_page(pdf.pages[page_index], enabled=ocr_enabled, langs=ocr_langs, dpi=ocr_dpi)
                if len(ocr_text) >= min_text_chars:
                    pages[page_index] = ocr_text
                    ocr_pages_count += 1
    diagnostics = {
        "pages_count": len(pages),
        "empty_pages_count": len(empty_page_indexes),
        "ocr_pages_count": ocr_pages_count,
    }
    logger.info(
        "Extracted %s PDF pages (%s empty/low-text, %s OCR)",
        diagnostics["pages_count"],
        diagnostics["empty_pages_count"],
        diagnostics["ocr_pages_count"],
    )
    return pages, diagnostics


def extract_pdf(file_content: bytes) -> List[str]:
    """Extract text from PDF, returning list of pages."""
    pages, _ = extract_pdf_with_diagnostics(file_content)
    return pages


def extract_docx(file_content: bytes) -> str:
    """Extract text from DOCX file."""
    from docx import Document as DocxDocument

    doc = DocxDocument(io.BytesIO(file_content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_txt(file_content: bytes) -> str:
    """Extract text from plain text file (UTF-8)."""
    return file_content.decode("utf-8", errors="replace")


def extract_text(filename: str, file_content: bytes):
    """
    Route to the appropriate extractor based on file extension.

    Returns:
        - For PDFs: list of page strings
        - For others: single string
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        return extract_pdf(file_content)
    elif ext == "docx":
        return extract_docx(file_content)
    elif ext in ("txt", "md"):
        return extract_txt(file_content)
    else:
        raise ValueError(f"Unsupported file type: .{ext}")


def extract_text_with_diagnostics(filename: str, file_content: bytes, extraction_options: dict | None = None):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        extraction_options = extraction_options or {}
        return extract_pdf_with_diagnostics(
            file_content,
            ocr_enabled=extraction_options.get("ocr_enabled"),
            ocr_langs=extraction_options.get("ocr_langs"),
            ocr_dpi=extraction_options.get("ocr_dpi"),
            ocr_min_text_chars=extraction_options.get("ocr_min_text_chars"),
        )
    text = extract_text(filename, file_content)
    return text, {
        "pages_count": 1,
        "empty_pages_count": 0 if str(text).strip() else 1,
        "ocr_pages_count": 0,
    }
