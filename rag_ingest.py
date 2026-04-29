import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document

from rag import delete_document_chunks, ingest_documents

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".html", ".htm"}


def _load_from_file(file_path: str) -> list[Document]:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return PyPDFLoader(file_path).load()

    if suffix in {".txt", ".md"}:
        return TextLoader(file_path, encoding="utf-8").load()

    if suffix in {".html", ".htm"}:
        raw = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        title, text = _clean_web_text(raw)
        source_name = title or path.name
        return [Document(page_content=text, metadata={"source": source_name, "page": 0})]

    raise ValueError(f"Unsupported file extension: {suffix}")


def _clean_web_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""

    for tag in soup(["script", "style", "noscript"]):
        tag.extract()

    text = " ".join(soup.get_text(separator=" ").split())
    return title, text


def fetch_web_text(url: str, timeout_seconds: int = 20) -> tuple[str, str]:
    response = requests.get(
        url,
        timeout=timeout_seconds,
        headers={"User-Agent": "RAG-IngestBot/1.0"},
    )
    response.raise_for_status()

    title, text = _clean_web_text(response.text)
    if not text:
        raise ValueError("Fetched web page contains no readable text")

    parsed = urlparse(url)
    fallback_title = parsed.netloc or "web-document"
    return title or fallback_title, text


def ingest_file(
    file_path: str,
    doc_id: int,
    *,
    source_name: str | None = None,
    source_type: str = "file",
    source_uri: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
):
    documents = _load_from_file(file_path)
    source_label = source_name or os.path.basename(file_path)

    delete_document_chunks(doc_id)
    return ingest_documents(
        documents,
        source_name=source_label,
        source_type=source_type,
        source_uri=source_uri,
        doc_id=doc_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def ingest_text(
    text: str,
    doc_id: int,
    *,
    source_name: str,
    source_type: str,
    source_uri: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
):
    if not text.strip():
        raise ValueError("Text source is empty")

    documents = [Document(page_content=text, metadata={"page": 0})]
    delete_document_chunks(doc_id)
    return ingest_documents(
        documents,
        source_name=source_name,
        source_type=source_type,
        source_uri=source_uri,
        doc_id=doc_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
