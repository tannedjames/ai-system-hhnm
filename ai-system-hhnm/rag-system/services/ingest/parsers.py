"""
Document Parsers
==================
Extract text from PDF, DOCX, and TXT files.
"""

import io
import logging

logger = logging.getLogger(__name__)


def parse_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF file using PyMuPDF."""
    import fitz  # PyMuPDF

    text_parts = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, 1):
            page_text = page.get_text("text")
            if page_text.strip():
                text_parts.append(f"--- Page {page_num} ---\n{page_text}")

    result = "\n\n".join(text_parts)
    logger.info(f"Parsed PDF: {len(doc)} pages, {len(result)} chars")
    return result


def parse_docx(file_bytes: bytes) -> str:
    """Extract text from a DOCX file using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    result = "\n\n".join(paragraphs)
    logger.info(f"Parsed DOCX: {len(paragraphs)} paragraphs, {len(result)} chars")
    return result


def parse_txt(file_bytes: bytes) -> str:
    """Extract text from a plain text file."""
    # Try UTF-8 first, fall back to latin-1
    try:
        result = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        result = file_bytes.decode("latin-1")
    logger.info(f"Parsed TXT: {len(result)} chars")
    return result


def parse_file(file_bytes: bytes, filename: str) -> str:
    """
    Parse a file based on its extension.
    Supported: .pdf, .docx, .txt
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        return parse_pdf(file_bytes)
    elif ext == "docx":
        return parse_docx(file_bytes)
    elif ext in ("txt", "md", "csv", "log"):
        return parse_txt(file_bytes)
    else:
        raise ValueError(f"Unsupported file type: .{ext}")
