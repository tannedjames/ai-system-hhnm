"""Extract text from various document formats."""
import io
import logging
from typing import List

logger = logging.getLogger(__name__)


def extract_pdf(file_content: bytes) -> List[str]:
    """Extract text from PDF, returning list of pages."""
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(file_content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    logger.info(f"Extracted {len(pages)} pages from PDF")
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
