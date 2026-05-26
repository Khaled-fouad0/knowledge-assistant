from dotenv import load_dotenv
import os
load_dotenv()

from fastapi import FastAPI, UploadFile, File
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import fitz
from langchain_core.documents import Document
import httpx
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.runnables import RunnablePassthrough, RunnableWithMessageHistory
from langchain_groq import ChatGroq
from pydantic import BaseModel
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
import tempfile

app = FastAPI(title="AI knowledge Assistant")

document_summary = None
vectorstore = None
retriever = None

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY")
)

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

def load_pdf(path):
    pdf = fitz.open(path)
    docs = []
    for i, page in enumerate(pdf):
        text = page.get_text()
        docs.append(Document(
            page_content=text,
            metadata={"page": i, "source": path}
        ))
    return docs

def format_docs(docs):
    sources = []
    for d in docs:
        page = d.metadata.get("page", "?")
        sources.append(f"[page {page+1}]\n {d.page_content}")
    return "\n\n--\n\n".join(sources)

rag_prompt = ChatPromptTemplate.from_messages([
    ("system", """
Answer the question based on the context only.
Cite the page number with each piece of information.
If you cannot find the answer, say: 'This information is not available in the file.'
Context:
{context}"""),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}")
])

def get_rag_chain():
    return (
        RunnablePassthrough.assign(
            context=lambda x: format_docs(retriever.invoke(x["input"]))
        )
        | rag_prompt
        | llm
        | StrOutputParser()
    )

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a smart and helpful assistant. Answer accurately and concisely"),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}")
])

chain = prompt | llm | StrOutputParser()
sessions = {}

def get_history(session_id: str) -> ChatMessageHistory:
    if session_id not in sessions:
        sessions[session_id] = ChatMessageHistory()
    return sessions[session_id]

chain_with_memory = RunnableWithMessageHistory(
    chain,
    get_history,
    input_messages_key="input",
    history_messages_key="chat_history"
)

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"

class ChatResponse(BaseModel):
    answer: str
    session_id: str

class URLRequest(BaseModel):
    url: str

@app.get("/")
def root():
    return {"status": "AI Knowledge Assistant is running"}

@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    config = {"configurable": {"session_id": request.session_id}}
    answer = chain_with_memory.invoke(
        {"input": request.message},
        config=config
    )
    return ChatResponse(answer=answer, session_id=request.session_id)

@app.delete("/reset/{session_id}")
def reset(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
    return {"status": "reset", "session_id": session_id}

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    global vectorstore, retriever, document_summary

    if not file.filename.endswith(".pdf"):
        return {"status": "error", "message": "Only PDF files are accepted"}

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        docs = load_pdf(tmp_path)

        if len(docs) == 0:
            return {"status": "error", "message": "PDF is empty or unreadable"}

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200
        )
        chunks = splitter.split_documents(docs)
        vectorstore = FAISS.from_documents(chunks, embeddings)
        retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

        full_text = "\n\n".join([doc.page_content for doc in docs[:5]])
        summary_prompt = f"summarize this document in 3-5 sentences in the same language as the document:\n\n{full_text}"
        document_summary = llm.invoke(summary_prompt).content

        return {
            "status": "success",
            "pages": len(docs),
            "chunks": len(chunks),
            "filename": file.filename
        }

    except Exception as e:
        return {"status": "error", "message": f"Failed to process PDF: {str(e)}"}

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

@app.post("/upload_url")
def upload_url(request: URLRequest):
    global vectorstore, retriever, document_summary

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(request.url)

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        lines = [line.strip() for line in soup.get_text().splitlines() if line.strip()]
        clean_text = "\n".join(lines)

        if not clean_text:
            return {"status": "error", "message": "URL content is empty"}

        docs = [Document(
            page_content=clean_text,
            metadata={"source": request.url, "page": 0}
        )]

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks = splitter.split_documents(docs)
        vectorstore = FAISS.from_documents(chunks, embeddings)
        retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

        full_text = clean_text[:3000]
        summary_prompt = f"summarize this webpage in 3-5 sentences in the same language:\n\n{full_text}"
        document_summary = llm.invoke(summary_prompt).content

        return {
            "status": "success",
            "chunks": len(chunks),
            "url": request.url
        }

    except Exception as e:
        return {"status": "error", "message": f"Failed to process URL: {str(e)}"}

@app.post("/ask")
def ask(request: ChatRequest) -> ChatResponse:
    if retriever is None:
        return ChatResponse(
            answer="No file has been uploaded yet. Use /upload first.",
            session_id=request.session_id
        )

    general_questions = ["موضوع", "عن ماذا", "ما هو", "what is this", "what's this", "summarize", "summary", "overview"]
    is_general = any(q in request.message.lower() for q in general_questions)

    if is_general and document_summary:
        return ChatResponse(
            answer=document_summary,
            session_id=request.session_id
        )

    try:
        rag_chain_with_memory = RunnableWithMessageHistory(
            get_rag_chain(),
            get_history,
            input_messages_key="input",
            history_messages_key="chat_history"
        )
        config = {"configurable": {"session_id": request.session_id}}
        answer = rag_chain_with_memory.invoke(
            {"input": request.message},
            config=config
        )
        return ChatResponse(answer=answer, session_id=request.session_id)

    except Exception as e:
        print(f"Error occurred: {e}")
        return ChatResponse(
            answer="Something went wrong. please try again.",
            session_id=request.session_id
        )