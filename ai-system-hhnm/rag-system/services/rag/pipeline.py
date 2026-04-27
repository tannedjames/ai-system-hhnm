"""
RAG Pipeline — Core Logic
============================
Implements the hybrid retrieval + reranking + LLM generation pipeline.
Communicates with the Embedding and Reranker services via HTTP,
queries Qdrant for hybrid search, and generates answers via Ollama.
"""

import logging
from typing import AsyncIterator, Optional

import httpx
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
    NamedSparseVector,
    NamedVector,
    Prefetch,
    QueryRequest,
    SparseVector as QdrantSparseVector,
    FusionQuery,
    Fusion,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for the RAG LLM
# ---------------------------------------------------------------------------
RAG_SYSTEM_PROMPT = """You are a helpful assistant for a knowledge base system. 
Answer the user's question based ONLY on the provided context documents.
If the context does not contain enough information to answer, say so clearly.

Rules:
- Be concise and accurate.
- Cite the source document(s) when possible using [Source N] format.
- If the context is empty or irrelevant, say "I don't have enough information to answer this question."
- Answer in the same language as the user's question.
"""


class HybridRAGPipeline:
    """
    End-to-end RAG pipeline:
    1. Embed query (dense + sparse) via Embedding Service
    2. Hybrid search on Qdrant (RRF fusion)
    3. Rerank results via Reranker Service
    4. Generate answer via Ollama (Qwen3.5:4b)
    """

    def __init__(
        self,
        qdrant_host: str,
        qdrant_port: int,
        collection_name: str,
        embedding_service_url: str,
        reranker_service_url: str,
        ollama_base_url: str,
        ollama_model: str,
    ):
        self.collection_name = collection_name
        self.embedding_service_url = embedding_service_url.rstrip("/")
        self.reranker_service_url = reranker_service_url.rstrip("/")

        # Qdrant client
        self.qdrant = QdrantClient(host=qdrant_host, port=qdrant_port, timeout=30)

        # Ollama LLM via LangChain
        self.llm = ChatOllama(
            model=ollama_model,
            base_url=ollama_base_url,
            temperature=0.3,
            num_ctx=8192,
        )

        # HTTP client for microservice calls
        self.http_client = httpx.AsyncClient(timeout=60.0)

        logger.info(
            f"Pipeline initialized: collection={collection_name}, "
            f"embed={embedding_service_url}, rerank={reranker_service_url}, "
            f"llm={ollama_model}@{ollama_base_url}"
        )

    async def close(self):
        """Cleanup resources."""
        await self.http_client.aclose()
        self.qdrant.close()

    # ------------------------------------------------------------------
    # Step 1: Embed query via Embedding Service
    # ------------------------------------------------------------------
    async def _embed_query(self, query: str) -> dict:
        """
        Call the Embedding Service to get both dense and sparse vectors
        for the query. Uses instruction-aware formatting for better retrieval.
        """
        resp = await self.http_client.post(
            f"{self.embedding_service_url}/embed/hybrid",
            json={
                "texts": [query],
                "instruction": "Given a web search query, retrieve relevant passages that answer the query",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "dense": data["dense"][0],
            "sparse_indices": data["sparse"][0]["indices"],
            "sparse_values": data["sparse"][0]["values"],
        }

    # ------------------------------------------------------------------
    # Step 2: Hybrid search on Qdrant
    # ------------------------------------------------------------------
    async def _hybrid_search(
        self,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        top_k: int = 20,
    ) -> list[dict]:
        """
        Perform hybrid search on Qdrant using Reciprocal Rank Fusion (RRF).
        Combines dense (semantic) and sparse (BM25 keyword) results.
        """
        try:
            # Use Qdrant's query API with prefetch + fusion
            results = self.qdrant.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    # Dense semantic search
                    Prefetch(
                        query=dense_vector,
                        using="dense",
                        limit=top_k,
                    ),
                    # Sparse keyword search
                    Prefetch(
                        query=QdrantSparseVector(
                            indices=sparse_indices,
                            values=sparse_values,
                        ),
                        using="sparse",
                        limit=top_k,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=top_k,
            )

            documents = []
            for point in results.points:
                documents.append(
                    {
                        "id": str(point.id),
                        "text": point.payload.get("text", ""),
                        "metadata": {
                            k: v
                            for k, v in point.payload.items()
                            if k != "text"
                        },
                        "score": point.score,
                    }
                )
            return documents

        except Exception as e:
            logger.error(f"Qdrant hybrid search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Step 3: Rerank results via Reranker Service
    # ------------------------------------------------------------------
    async def _rerank(
        self, query: str, documents: list[dict], top_k: int = 5
    ) -> list[dict]:
        """
        Call the Reranker Service to rerank retrieved documents.
        """
        if not documents:
            return []

        doc_texts = [d["text"] for d in documents]
        resp = await self.http_client.post(
            f"{self.reranker_service_url}/rerank",
            json={
                "query": query,
                "documents": doc_texts,
                "top_k": top_k,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        # Map reranked results back to original documents with metadata
        reranked = []
        for result in data["results"]:
            original_idx = result["index"]
            doc = documents[original_idx].copy()
            doc["rerank_score"] = result["score"]
            reranked.append(doc)

        return reranked

    # ------------------------------------------------------------------
    # Step 4: Generate answer via Ollama
    # ------------------------------------------------------------------
    def _build_prompt(self, query: str, documents: list[dict]) -> list:
        """Build the LLM prompt with retrieved context."""
        # Format context from retrieved documents
        context_parts = []
        for i, doc in enumerate(documents, 1):
            source = doc.get("metadata", {}).get("filename", f"Document {i}")
            context_parts.append(f"[Source {i}] ({source}):\n{doc['text']}")

        context = "\n\n---\n\n".join(context_parts)

        return [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(
                content=f"Context documents:\n\n{context}\n\n---\n\nQuestion: {query}"
            ),
        ]

    async def generate(self, query: str, documents: list[dict]) -> str:
        """Generate a non-streaming answer."""
        messages = self._build_prompt(query, documents)
        response = await self.llm.ainvoke(messages)
        return response.content

    async def generate_stream(
        self, query: str, documents: list[dict]
    ) -> AsyncIterator[str]:
        """Generate a streaming answer."""
        messages = self._build_prompt(query, documents)
        async for chunk in self.llm.astream(messages):
            if chunk.content:
                yield chunk.content

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    async def query(
        self,
        question: str,
        retrieval_top_k: int = 20,
        rerank_top_k: int = 5,
    ) -> dict:
        """
        Execute the full RAG pipeline:
        1. Embed query → 2. Hybrid search → 3. Rerank → 4. Generate
        """
        # Step 1: Embed the query
        logger.info(f"[1/4] Embedding query: {question[:80]}...")
        vectors = await self._embed_query(question)

        # Step 2: Hybrid search
        logger.info(f"[2/4] Hybrid search (top_k={retrieval_top_k})...")
        retrieved = await self._hybrid_search(
            dense_vector=vectors["dense"],
            sparse_indices=vectors["sparse_indices"],
            sparse_values=vectors["sparse_values"],
            top_k=retrieval_top_k,
        )
        logger.info(f"  → Retrieved {len(retrieved)} documents")

        # Step 3: Rerank
        logger.info(f"[3/4] Reranking (top_k={rerank_top_k})...")
        reranked = await self._rerank(question, retrieved, top_k=rerank_top_k)
        logger.info(f"  → Reranked to {len(reranked)} documents")

        # Step 4: Generate answer
        logger.info("[4/4] Generating answer via Ollama...")
        answer = await self.generate(question, reranked)

        return {
            "answer": answer,
            "sources": [
                {
                    "text": doc["text"][:500],
                    "metadata": doc.get("metadata", {}),
                    "rerank_score": doc.get("rerank_score", 0),
                }
                for doc in reranked
            ],
            "retrieval_count": len(retrieved),
            "rerank_count": len(reranked),
        }

    async def query_stream(
        self,
        question: str,
        retrieval_top_k: int = 20,
        rerank_top_k: int = 5,
    ) -> tuple[AsyncIterator[str], list[dict]]:
        """
        Execute the RAG pipeline with streaming LLM output.
        Returns (stream_iterator, sources).
        """
        # Steps 1-3 same as non-streaming
        vectors = await self._embed_query(question)
        retrieved = await self._hybrid_search(
            dense_vector=vectors["dense"],
            sparse_indices=vectors["sparse_indices"],
            sparse_values=vectors["sparse_values"],
            top_k=retrieval_top_k,
        )
        reranked = await self._rerank(question, retrieved, top_k=rerank_top_k)

        sources = [
            {
                "text": doc["text"][:500],
                "metadata": doc.get("metadata", {}),
                "rerank_score": doc.get("rerank_score", 0),
            }
            for doc in reranked
        ]

        # Return stream + sources
        return self.generate_stream(question, reranked), sources
