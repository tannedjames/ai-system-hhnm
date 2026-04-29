import os
from functools import lru_cache

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "rag_collection")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "100"))
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "400"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "100"))
TOP_K = int(os.getenv("RAG_TOP_K", "3"))
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")


class BatchedGoogleEmbeddings(Embeddings):
    def __init__(self):
        self.document_embeddings = GoogleGenerativeAIEmbeddings(
            model=EMBEDDING_MODEL,
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBEDDING_DIMENSION,
        )
        self.query_embeddings = GoogleGenerativeAIEmbeddings(
            model=EMBEDDING_MODEL,
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIMENSION,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            embeddings.extend(self.document_embeddings.embed_documents(batch))
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.query_embeddings.embed_query(text)


@lru_cache(maxsize=1)
def get_embeddings():
    return BatchedGoogleEmbeddings()


@lru_cache(maxsize=1)
def get_qdrant_client():
    return QdrantClient(url=QDRANT_URL)


def get_llm(model_name: str | None = None):
    return ChatGoogleGenerativeAI(model=model_name or LLM_MODEL)


def collection_exists() -> bool:
    client = get_qdrant_client()
    return client.collection_exists(COLLECTION_NAME)


def _prepare_documents(
    docs: list[Document],
    *,
    source_name: str,
    source_type: str,
    source_uri: str | None = None,
    doc_id: int | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
):
    for page_index, doc in enumerate(docs):
        page = doc.metadata.get("page", page_index)
        doc.metadata["page"] = page
        doc.metadata["source"] = source_name
        doc.metadata["type"] = source_type
        if source_uri:
            doc.metadata["source_uri"] = source_uri
        if doc_id is not None:
            doc.metadata["doc_id"] = doc_id

    resolved_chunk_size = chunk_size or CHUNK_SIZE
    resolved_chunk_overlap = chunk_overlap if chunk_overlap is not None else CHUNK_OVERLAP
    if resolved_chunk_overlap >= resolved_chunk_size:
        resolved_chunk_overlap = max(0, resolved_chunk_size - 1)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=resolved_chunk_size,
        chunk_overlap=resolved_chunk_overlap,
    )
    return splitter.split_documents(docs)


def ingest_documents(
    docs: list[Document],
    *,
    source_name: str,
    source_type: str,
    source_uri: str | None = None,
    doc_id: int | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
):
    chunks = _prepare_documents(
        docs,
        source_name=source_name,
        source_type=source_type,
        source_uri=source_uri,
        doc_id=doc_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    vector_store = QdrantVectorStore.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        url=QDRANT_URL,
        collection_name=COLLECTION_NAME,
        force_recreate=False,
    )
    return vector_store, len(chunks)


def build_rag(pdf_path: str):
    loader = PyPDFLoader(pdf_path)
    docs = loader.load()
    return ingest_documents(docs, source_name=os.path.basename(pdf_path), source_type="pdf")[0]


def build_rag_from_folder(folder_path: str):
    all_docs: list[Document] = []

    for file_name in os.listdir(folder_path):
        if not file_name.lower().endswith(".pdf"):
            continue

        pdf_path = os.path.join(folder_path, file_name)
        loader = PyPDFLoader(pdf_path)
        all_docs.extend(loader.load())

    if not all_docs:
        raise ValueError(f"No PDF files found in {folder_path}")

    return ingest_documents(all_docs, source_name=os.path.basename(folder_path), source_type="pdf")[0]


def load_rag():
    if not collection_exists():
        raise ValueError(f"Collection {COLLECTION_NAME} does not exist yet")

    return QdrantVectorStore.from_existing_collection(
        collection_name=COLLECTION_NAME,
        embedding=get_embeddings(),
        url=QDRANT_URL,
    )


def delete_document_chunks(doc_id: int):
    if not collection_exists():
        return

    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.doc_id",
                        match=models.MatchValue(value=doc_id),
                    )
                ]
            )
        ),
    )


def retrieve_with_scores(qdrant, question: str, top_k: int | None = None):
    return qdrant.similarity_search_with_score(question, k=top_k or TOP_K)


def _page_number(raw_page) -> int:
    try:
        return int(raw_page) + 1
    except (TypeError, ValueError):
        return 1


def _build_context(docs: list[Document]) -> str:
    blocks = []
    for index, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source", "unknown")
        page = _page_number(doc.metadata.get("page", 0))
        blocks.append(f"[{index}] source={source}, page={page}\n{doc.page_content}")
    return "\n\n".join(blocks)


def build_prompt(docs: list[Document], question: str) -> str:
    context = _build_context(docs)
    return f"""
You are a precise AI assistant for internal knowledge-base question answering.
You must only answer from the context below.
If the answer is not in context, reply exactly: "I don't have enough information".
When you use evidence, cite it inline using [1], [2], ... matching the context blocks.
Keep the answer concise.

Context:
{context}

Question:
{question}

Answer:
""".strip()


def build_citations(results):
    citations = []
    for index, (doc, score) in enumerate(results, start=1):
        page = _page_number(doc.metadata.get("page", 0))
        text = " ".join(doc.page_content.split())
        citations.append(
            {
                "id": index,
                "doc_id": doc.metadata.get("doc_id"),
                "source": doc.metadata.get("source"),
                "source_type": doc.metadata.get("type"),
                "source_uri": doc.metadata.get("source_uri"),
                "page": page,
                "score": float(score),
                "snippet": text[:240],
            }
        )
    return citations


def ask_rag(
    qdrant,
    question: str,
    top_k: int | None = None,
    llm_model: str | None = None,
):
    results = retrieve_with_scores(qdrant, question, top_k=top_k)
    docs = [doc for doc, _score in results]
    citations = build_citations(results)

    if not docs:
        return {"answer": "I don't have enough information", "citations": []}

    llm = get_llm(llm_model)
    prompt = build_prompt(docs, question)
    response = llm.invoke(prompt)
    return {"answer": str(response.content).strip(), "citations": citations}
