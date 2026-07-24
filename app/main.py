"""
Company AI Brain — FastAPI Service
Knowledge API พร้อม RBAC filter

Endpoints:
    GET  /health         — สถานะระบบ + จำนวน vectors
    POST /search         — ค้นหาเอกสารตาม role (retrieval อย่างเดียว)
    POST /ask            — ถาม-ตอบจากคลังความรู้ (retrieval + LLM เรียบเรียง + citation)
    GET  /collections    — สถิติแต่ละ collection (admin only)
"""
import os
import sys
from contextlib import asynccontextmanager

import anthropic
import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "company_docs")
MODEL_NAME = os.getenv("MODEL_NAME", "BAAI/bge-m3")
MAX_CONTENT_CHARS = 2000  # จำกัดขนาด content ต่อ result กัน response ใหญ่เกิน

# LLM สำหรับ /ask — จุดสลับ cloud↔local อยู่ที่ generate_answer() จุดเดียว
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-4-8")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))  # คำตอบสั้นโดยตั้งใจ (ใช้กับ voicebot)

VALID_ROLES = [
    "admin", "management", "production", "prepress", "qc",
    "engineering", "sales", "purchasing", "logistics", "hr", "it",
]


# ─── Startup / Shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Loading {MODEL_NAME}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.eval()
    app.state.tokenizer = tokenizer
    app.state.model = model
    app.state.qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    print(f"Connected to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
    # LLM client — ไม่มี key ก็รันได้ (/search ใช้ปกติ, /ask จะตอบ 503)
    app.state.llm = anthropic.Anthropic() if os.getenv("ANTHROPIC_API_KEY") else None
    print(f"LLM: {LLM_MODEL if app.state.llm else 'not configured'}")
    print("API ready")
    yield


app = FastAPI(
    title="Company AI Brain",
    description="Enterprise Knowledge Retrieval API with RBAC",
    version="1.0.0-poc",
    lifespan=lifespan,
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def embed(tokenizer, model, text: str) -> list[float]:
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs)
    vec = out.last_hidden_state[:, 0, :]
    vec = F.normalize(vec, p=2, dim=1)
    return vec[0].tolist()


def make_rbac_filter(role: str) -> Filter | None:
    if role == "admin":
        return None
    return Filter(
        must=[FieldCondition(key="allowed_roles", match=MatchAny(any=[role]))]
    )


ANSWER_SYSTEM_PROMPT = """คุณคือผู้ช่วยตอบคำถามจากคลังความรู้ภายในบริษัท (Company AI Brain)

กติกา:
- ตอบจาก "ข้อมูลอ้างอิง" ที่ให้มาเท่านั้น ห้ามเดาหรือใช้ความรู้ภายนอก
- ถ้าข้อมูลอ้างอิงไม่เพียงพอที่จะตอบ ให้บอกตรงๆ ว่าไม่พบข้อมูลในระบบ อย่าแต่งคำตอบ
- ตอบภาษาไทย กระชับ ตรงประเด็น จัดเป็นขั้นตอนเมื่อเหมาะสม
- ท้ายประโยคที่อ้างเอกสารใด ให้ใส่หมายเลขอ้างอิง เช่น [1] [2]
- ข้อความอ้างอิงบางส่วนอาจมีตัวอักษรไทยเพี้ยนจากการแปลงไฟล์ PDF ให้ตีความตามบริบท
  และสะกดให้ถูกต้องในคำตอบเสมอ"""


def generate_answer(llm, question: str, context: str) -> "anthropic.types.Message":
    """จุดเดียวที่คุยกับ LLM — สลับ cloud↔local เปลี่ยนที่ฟังก์ชันนี้"""
    return llm.messages.create(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=ANSWER_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"ข้อมูลอ้างอิง:\n\n{context}\n\nคำถาม: {question}",
        }],
    )


def build_content(payload: dict) -> str:
    """เนื้อหาสำหรับให้ LLM เรียบเรียงตอบ — อ่านจาก payload ล้วน ไม่แตะ disk

    parent_text = section context ~2000 ตัว (backfill ตอน ingest ด้วย
    build_parent_payload.py) ช่วย chunk สั้นๆ ให้มีบริบทพอตอบ
    ถ้าไม่มี/สั้นกว่า chunk เดิม -> fallback เป็น chunk (กันได้เนื้อน้อยกว่าเดิม)
    """
    chunk = (payload.get("text", "") or "")[:MAX_CONTENT_CHARS]
    parent = (payload.get("parent_text", "") or "")[:MAX_CONTENT_CHARS]
    return parent if len(parent) >= len(chunk) else chunk


# ─── Schemas ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    role: str
    top_k: int = 3


class SearchResult(BaseModel):
    rank: int
    score: float
    source: str
    collection: str
    level: int
    heading: str
    preview: str
    content: str  # เนื้อหาเต็มของ chunk (สำหรับให้ LLM เรียบเรียงตอบ) — additive, ของเดิมคงไว้ทั้งหมด


class SearchResponse(BaseModel):
    query: str
    role: str
    results: list[SearchResult]


class AskRequest(BaseModel):
    question: str
    role: str
    top_k: int = 4


class AskCitation(BaseModel):
    ref: int          # หมายเลขที่ LLM ใช้อ้างใน answer เช่น [1]
    source: str
    heading: str
    score: float


class AskResponse(BaseModel):
    question: str
    role: str
    answer: str
    citations: list[AskCitation]
    model: str


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health(request: Request):
    try:
        info = request.app.state.qdrant.get_collection(COLLECTION_NAME)
        return {
            "status": "ok",
            "collection": COLLECTION_NAME,
            "vectors": info.points_count,
            "model": MODEL_NAME,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, request: Request):
    if req.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{req.role}'. Valid roles: {VALID_ROLES}",
        )
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    if not 1 <= req.top_k <= 10:
        raise HTTPException(status_code=400, detail="top_k must be 1-10")

    vec = embed(request.app.state.tokenizer, request.app.state.model, req.query)

    points = request.app.state.qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vec,
        query_filter=make_rbac_filter(req.role),
        limit=req.top_k,
        with_payload=True,
    ).points

    return SearchResponse(
        query=req.query,
        role=req.role,
        results=[
            SearchResult(
                rank=i + 1,
                score=round(r.score, 4),
                source=r.payload.get("source", ""),
                collection=r.payload.get("collection_group", "?"),
                level=r.payload.get("confidentiality_level", 0),
                heading=r.payload.get("heading", ""),
                preview=r.payload.get("text", "")[:400].replace("\n", " "),
                content=build_content(r.payload),
            )
            for i, r in enumerate(points)
        ],
    )


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, request: Request):
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(status_code=503,
                            detail="LLM not configured — set ANTHROPIC_API_KEY")
    if req.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{req.role}'. Valid roles: {VALID_ROLES}",
        )
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if not 1 <= req.top_k <= 10:
        raise HTTPException(status_code=400, detail="top_k must be 1-10")

    # 1) Retrieval — RBAC filter เหมือน /search ทุกประการ
    vec = embed(request.app.state.tokenizer, request.app.state.model, req.question)
    points = request.app.state.qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vec,
        query_filter=make_rbac_filter(req.role),
        limit=req.top_k,
        with_payload=True,
    ).points

    if not points:
        return AskResponse(
            question=req.question, role=req.role,
            answer="ไม่พบข้อมูลที่เกี่ยวข้องในระบบ หรือคุณไม่มีสิทธิ์เข้าถึงเอกสารในเรื่องนี้",
            citations=[], model=LLM_MODEL,
        )

    # 2) สร้าง context ให้ LLM (ใช้ parent content เดียวกับ /search)
    context = "\n\n".join(
        f"[{i + 1}] เอกสาร: {p.payload.get('source', '')}\n"
        f"หัวข้อ: {p.payload.get('heading', '')}\n"
        f"เนื้อหา:\n{build_content(p.payload)}"
        for i, p in enumerate(points)
    )

    # 3) Generate
    try:
        resp = generate_answer(llm, req.question, context)
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="LLM rate limited — retry later")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach LLM provider")
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e.message}")

    if resp.stop_reason == "refusal":
        answer = "ระบบไม่สามารถตอบคำถามนี้ได้ กรุณาติดต่อผู้ดูแลระบบ"
    else:
        answer = "".join(b.text for b in resp.content if b.type == "text").strip()

    return AskResponse(
        question=req.question,
        role=req.role,
        answer=answer,
        citations=[
            AskCitation(
                ref=i + 1,
                source=p.payload.get("source", ""),
                heading=p.payload.get("heading", ""),
                score=round(p.score, 4),
            )
            for i, p in enumerate(points)
        ],
        model=resp.model,
    )


@app.get("/collections")
def list_collections(role: str, request: Request):
    if role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    qdrant = request.app.state.qdrant

    # scroll และนับตาม collection_group
    stats: dict[str, int] = {}
    offset = None
    while True:
        results, next_offset = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            limit=500,
            offset=offset,
            with_payload=["collection_group"],
            with_vectors=False,
        )
        for p in results:
            coll = p.payload.get("collection_group", "UNKNOWN")
            stats[coll] = stats.get(coll, 0) + 1
        if next_offset is None:
            break
        offset = next_offset

    return {"collections": stats, "total": sum(stats.values())}
