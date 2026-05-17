"""Text chunking utilities for RAG."""
from typing import List, Dict, Any


def chunk_text(
    text: str,
    source: str,
    chunk_size: int = 500,
    overlap: int = 100,
    page: int = 0,
) -> List[Dict[str, Any]]:
    """
    Split text into overlapping chunks.

    Args:
        text: Full text to split
        source: Source filename (for citations)
        chunk_size: Target words per chunk
        overlap: Words to overlap between chunks
        page: Page number (for PDFs)

    Returns:
        List of chunk dicts with keys: text, source, page, chunk_index
    """
    if not text or not text.strip():
        return []

    words = text.split()
    if len(words) <= chunk_size:
        return [{
            "text": text.strip(),
            "source": source,
            "page": page,
            "chunk_index": 0,
        }]

    chunks = []
    start = 0
    chunk_idx = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_text_content = " ".join(words[start:end]).strip()

        if chunk_text_content:
            chunks.append({
                "text": chunk_text_content,
                "source": source,
                "page": page,
                "chunk_index": chunk_idx,
            })
            chunk_idx += 1

        if end >= len(words):
            break

        start = end - overlap

    return chunks


def chunk_pdf_pages(pages: List[str], source: str, chunk_size: int = 500, overlap: int = 100) -> List[Dict[str, Any]]:
    """
    Chunk PDF text page-by-page (preserves page numbers for citations).

    Args:
        pages: List of text strings, one per page
        source: Source filename
        chunk_size: Target words per chunk
        overlap: Words to overlap

    Returns:
        List of chunk dicts
    """
    all_chunks = []
    global_chunk_idx = 0

    for page_num, page_text in enumerate(pages, start=1):
        if not page_text or not page_text.strip():
            continue

        page_chunks = chunk_text(
            text=page_text,
            source=source,
            chunk_size=chunk_size,
            overlap=overlap,
            page=page_num,
        )

        # Reassign chunk_index globally across pages
        for chunk in page_chunks:
            chunk["chunk_index"] = global_chunk_idx
            global_chunk_idx += 1

        all_chunks.extend(page_chunks)

    return all_chunks
