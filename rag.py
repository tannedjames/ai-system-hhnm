import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings 
from langchain_qdrant import QdrantVectorStore           
from langchain_google_genai import ChatGoogleGenerativeAI
from qdrant_client import QdrantClient

QDRANT_PATH = "./qdrant_storage"
COLLECTION_NAME = "rag_collection"

def build_rag(pdf_path: str):
    """Build RAG from a single PDF"""
    loader = PyPDFLoader(pdf_path)
    docs = loader.load()

    for i, doc in enumerate(docs):
        doc.metadata["page"] = i
        doc.metadata["source"] = os.path.basename(pdf_path)
        doc.metadata["type"] = "pdf"

    splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=100)
    chunks = splitter.split_documents(docs)

    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    qdrant = QdrantVectorStore.from_documents(
        documents=chunks,
        embedding=embeddings,
        path=QDRANT_PATH,
        collection_name=COLLECTION_NAME,
        force_recreate=True 
    )

    return qdrant


def build_rag_from_folder(folder_path: str):
    """Build RAG from all PDFs in a folder"""
    all_docs = []

    for file in os.listdir(folder_path):
        if file.endswith(".pdf"):
            pdf_path = os.path.join(folder_path, file)
            print(f"📄 Loading {file}...")
            
            loader = PyPDFLoader(pdf_path)
            docs = loader.load()

            for i, doc in enumerate(docs):
                doc.metadata.update({
                    "page": i,
                    "source": file,
                    "type": "pdf"
                })

            all_docs.extend(docs)

    if not all_docs:
        raise ValueError(f"No PDF files found in {folder_path}")

    print(f"✅ Loaded {len(all_docs)} pages from PDFs")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=100
    )

    chunks = splitter.split_documents(all_docs)
    print(f"✅ Split into {len(chunks)} chunks")

    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )

    qdrant = QdrantVectorStore.from_documents(
        documents=chunks,
        embedding=embeddings,
        path=QDRANT_PATH,
        collection_name=COLLECTION_NAME,
        force_recreate=True
    )

    print("✅ Vector DB created successfully")
    return qdrant

def load_rag():
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    client = QdrantClient(path=QDRANT_PATH)
    return QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embeddings=embeddings
    )

def ask_rag(qdrant, question: str):
    """Query the RAG system with an improved prompt"""
    docs = qdrant.similarity_search(question, k=3)
    context = "\n\n".join([d.page_content for d in docs])

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")

    prompt = f"""
You are a precise AI assistant that answers questions based on provided context.

Rules:
- Only use information from the provided context
- If the context doesn't contain the answer, say "I don't know" or "This information is not available"
- When possible, cite the source document and page number
- Be concise and accurate

Context:
{context}

Question:
{question}

Answer:
"""
    response = llm.invoke(prompt)
    return response.content, docs
