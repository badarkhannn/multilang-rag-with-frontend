import os
from fastapi import FastAPI, Request
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List, Dict
from sentence_transformers import SentenceTransformer
from langchain.embeddings.base import Embeddings
from langchain_pinecone import PineconeVectorStore
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from uuid import uuid4

# Load environment variables
load_dotenv()
pinecone_key = os.getenv("PINECONE_API_KEY")
os.environ["PINECONE_API_KEY"] = pinecone_key

# FastAPI initialization
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend files
app.mount("/static", StaticFiles(directory="../frontend"), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join("../frontend", "index.html"))

@app.get("/api/about")
def read_root():
    return {"Hello": "This is the backend for the RAG system."}

# Request model
class QuestionRequest(BaseModel):
    question: str
    session_id: str = None  # Optional session ID

# Embedding model setup
embedding_model = SentenceTransformer("BAAI/bge-m3")

class BGEEmbeddings(Embeddings):
    def embed_query(self, text: str) -> List[float]:
        return embedding_model.encode(text, normalize_embeddings=True).tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return embedding_model.encode(texts, normalize_embeddings=True).tolist()

embeddings = {
    "BAAI/bge-m3": BGEEmbeddings()
}

# Pinecone vector store setup
index_name = "baai-bge-m3"
model_id = "BAAI/bge-m3"
docsearch = PineconeVectorStore.from_existing_index(index_name=index_name, embedding=embeddings[model_id])

# MMR-based retriever setup
retriever = docsearch.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 3, "lambda_mult": 0.5}
)

# OpenAI Chat model setup
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

# Prompt Template with chat history
prompt = PromptTemplate(
    template="""
You are a helpful financial assistant.
You will answer the user's question based on the given context and the past conversation history.

Chat History:
{history}

Context:
{context}

Question: {question}

Answer only from the context. If not enough context, say "I don't know".
""",
    input_variables=["context", "question", "history"]
)

# In-memory chat memory (can be replaced with DB/Redis)
chat_memory: Dict[str, List[str]] = {}

@app.post("/ask")
async def ask_question(payload: QuestionRequest):
    question = payload.question
    session_id = payload.session_id or str(uuid4())

    # Ensure memory exists
    if session_id not in chat_memory:
        chat_memory[session_id] = []

    # Retrieve context from Pinecone
    retrieved_docs = retriever.invoke(question)
    context_text = "\n\n".join(doc.page_content for doc in retrieved_docs)

    # Create chat history summary (up to last 3 turns)
    chat_history = "\n".join(chat_memory[session_id][-6:])  # Last 3 QA pairs

    # Create prompt with history
    final_prompt = prompt.invoke({
        "context": context_text,
        "question": question,
        "history": chat_history
    })

    # Get response
    answer = llm.invoke(final_prompt)

    # Update chat memory
    chat_memory[session_id].append(f"User: {question}")
    chat_memory[session_id].append(f"Bot: {answer.content}")

    return {
        "answer": answer.content,
        "session_id": session_id  # return for client to store
    }
