"""Text chunking utilities for RAG.

Design notes
------------
* Chunks are sized by word count (a stable proxy for tokens that doesn't
  require pulling in a tokenizer).
* For PDFs we stitch all pages into one continuous stream and chunk that,
  so paragraphs that cross page boundaries stay intact. Each chunk records
  the first page it covers (`page`) and the last page it covers (`page_end`)
  so the UI can cite a range when needed.
* Chunks break on sentence boundaries near the target size rather than
  mid-word, which makes retrieval cleaner.
"""
import re
from typing import List, Dict, Any, Tuple


_SENT_END = re.compile(r"(?<=[\.\?!…。！？])\s+|\n\n+")


def _split_into_sentences(text: str) -> List[str]:
    """Cheap sentence-ish splitter that works for EN, VN, and most Latin-script langs."""
    if not text:
        return []
    parts = [p.strip() for p in _SENT_END.split(text) if p and p.strip()]
    return parts or [text.strip()]


def _words(s: str) -> List[str]:
    return s.split()


def chunk_text(
    text: str,
    source: str,
    chunk_size: int = 500,
    overlap: int = 100,
    page: int = 0,
) -> List[Dict[str, Any]]:
    """Split text into overlapping, sentence-aware chunks.

    Returns chunks with keys: text, source, page, page_end, chunk_index
    """
    if not text or not text.strip():
        return []
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 5)

    sentences = _split_into_sentences(text)

    chunks: List[Dict[str, Any]] = []
    current: List[str] = []
    current_word_count = 0
    chunk_idx = 0

    def flush():
        nonlocal current, current_word_count, chunk_idx
        if not current:
            return
        chunk_str = " ".join(current).strip()
        if chunk_str:
            chunks.append({
                "text": chunk_str,
                "source": source,
                "page": page,
                "page_end": page,
                "chunk_index": chunk_idx,
            })
            chunk_idx += 1

        # Carry-over tail sentences (~overlap words) into the next chunk
        carry: List[str] = []
        carry_words = 0
        for s in reversed(current):
            w = len(_words(s))
            if carry_words + w > overlap and carry:
                break
            carry.insert(0, s)
            carry_words += w
        current = carry
        current_word_count = carry_words

    for sent in sentences:
        w = len(_words(sent))
        # A single huge sentence: hard-split by words
        if w > chunk_size:
            if current:
                flush()
            words = _words(sent)
            i = 0
            while i < len(words):
                piece = " ".join(words[i:i + chunk_size]).strip()
                if piece:
                    chunks.append({
                        "text": piece,
                        "source": source,
                        "page": page,
                        "page_end": page,
                        "chunk_index": chunk_idx,
                    })
                    chunk_idx += 1
                i += max(1, chunk_size - overlap)
            current = []
            current_word_count = 0
            continue

        if current_word_count + w > chunk_size and current:
            flush()
        current.append(sent)
        current_word_count += w

    if current:
        flush()

    return chunks


def _word_idx_to_page(page_word_offsets: List[Tuple[int, int, int]], word_idx: int) -> int:
    """Given a word index into the joined doc, return its page number.

    Empty pages (start == end) are skipped so content on page N doesn't get
    mis-attributed to a preceding empty page.
    """
    if not page_word_offsets:
        return 0
    for page_num, start, end in page_word_offsets:
        if end <= start:  # empty page - no words live here
            continue
        if start <= word_idx < end:
            return page_num
    # Past the end of all real pages -> return the last non-empty page
    for page_num, start, end in reversed(page_word_offsets):
        if end > start:
            return page_num
    return page_word_offsets[-1][0]


def chunk_pdf_pages(
    pages: List[str],
    source: str,
    chunk_size: int = 500,
    overlap: int = 100,
) -> List[Dict[str, Any]]:
    """Chunk a PDF as a continuous document while tracking page numbers per chunk.

    This avoids the old behaviour where every page was chunked independently and
    paragraphs spanning page boundaries were torn apart. Now we stitch pages
    together, chunk normally, and figure out which page(s) each chunk covers
    by matching word offsets.
    """
    page_word_offsets: List[Tuple[int, int, int]] = []  # (page_num, start_word, end_word)
    full_words: List[str] = []

    for page_num, page_text in enumerate(pages, start=1):
        if not page_text or not page_text.strip():
            page_word_offsets.append((page_num, len(full_words), len(full_words)))
            continue
        start = len(full_words)
        full_words.extend(page_text.split())
        end = len(full_words)
        page_word_offsets.append((page_num, start, end))

    if not full_words:
        return []

    joined = " ".join(full_words)
    raw_chunks = chunk_text(
        text=joined,
        source=source,
        chunk_size=chunk_size,
        overlap=overlap,
        page=0,
    )

    cursor_word = 0
    final: List[Dict[str, Any]] = []
    for idx, c in enumerate(raw_chunks):
        chunk_word_count = len(c["text"].split())
        chunk_start = cursor_word
        chunk_end = cursor_word + chunk_word_count
        cursor_word += max(1, chunk_word_count - overlap)

        first_page = _word_idx_to_page(page_word_offsets, chunk_start)
        last_page = _word_idx_to_page(page_word_offsets, max(chunk_start, chunk_end - 1))

        c["page"] = first_page
        c["page_end"] = last_page
        c["chunk_index"] = idx
        final.append(c)

    return final


# Back-compat alias for older worker code that imported the helper directly.
pages_idx_to_page = _word_idx_to_page
