"""
Company AI Brain — FastAPI Service
Knowledge API พร้อม RBAC filter

Endpoints:
    GET  /health         — สถานะระบบ + จำนวน vectors
    POST /search         — ค้นหาเอกสารตาม role (retrieval อย่างเดียว)
    POST /ask            — ถาม-ตอบจากคลังความรู้ (retrieval + LLM เรียบเรียง + citation)
    GET  /collections    — สถิติแต่ละ collection (admin only)
"""
import hashlib
import json
import os
import sys
import time
from contextlib import asynccontextmanager

import anthropic
import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient

import policy  # P1 policy compiler (pure) — auth/effective-ACL/filter contracts
from qdrant_filter import to_qdrant_filter  # spec -> Qdrant Filter (adapter เดียวกับ P5b harness)

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "company_docs")
MODEL_NAME = os.getenv("MODEL_NAME", "BAAI/bge-m3")
MAX_CONTENT_CHARS = 2000  # จำกัดขนาด content ต่อ result กัน response ใหญ่เกิน

# LLM สำหรับ /ask — จุดสลับ cloud↔local อยู่ที่ generate_answer() จุดเดียว
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-4-8")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))  # คำตอบสั้นโดยตั้งใจ (ใช้กับ voicebot)

# Inbound service auth (แนวทางใน STATUS.md ข้อ 6) — key ต่อ service, เก็บเป็น sha256 hash
# AUTH_MODE: off = ไม่ตรวจ | warn = ปล่อยผ่านแต่ log (ช่วงเปลี่ยนผ่าน) | enforce = block
AUTH_MODE = os.getenv("AUTH_MODE", "warn")
API_KEYS_FILE = os.getenv("API_KEYS_FILE", "")

# canonical role set มาจาก policy.KNOWN_ROLES จุดเดียว (กัน drift ระหว่าง auth กับ compiler)
VALID_ROLES = sorted(policy.KNOWN_ROLES)


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
    # Service API keys — {sha256_hex: {"service": str, "allowed_roles": [...]}}
    if AUTH_MODE not in ("off", "warn", "enforce"):
        raise RuntimeError(
            f"invalid AUTH_MODE={AUTH_MODE!r} (ต้องเป็น off|warn|enforce)")
    app.state.api_keys = load_api_keys(API_KEYS_FILE)
    # fail-closed: enforce แต่ไม่มี key ที่ใช้ได้ → ไม่ยอม start (กัน enforce กลายเป็น fail-open)
    if AUTH_MODE == "enforce" and not app.state.api_keys:
        raise RuntimeError(
            "AUTH_MODE=enforce แต่โหลด API key ไม่ได้เลย — refusing to start (fail-closed)")
    print(f"Auth: mode={AUTH_MODE}, service keys={len(app.state.api_keys)}")
    print("API ready")
    yield


app = FastAPI(
    title="Company AI Brain",
    description="Enterprise Knowledge Retrieval API with RBAC",
    version="1.0.0-poc",
    lifespan=lifespan,
)

# CORS — default ปิด (server-to-server เช่น voicebot ไม่ต้องใช้)
# เปิดให้ frontend เรียกตรงได้โดยตั้ง CORS_ORIGINS="http://host:3000,https://..." ใน .env
_cors = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,           # ระบุ origin ชัดเจน ไม่ใช้ "*" คู่กับ header auth
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


# ─── Helpers ───────────────────────────────────────────────────────────────────

def embed(tokenizer, model, text: str) -> list[float]:
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs)
    vec = out.last_hidden_state[:, 0, :]
    vec = F.normalize(vec, p=2, dim=1)
    return vec[0].tolist()


def load_api_keys(path: str) -> dict:
    """โหลด+validate service key registry — โครงผิดถือว่า fatal (จะไป fail-closed ตอน startup)

    แต่ละ entry: key = sha256 hex 64 ตัว, service = str, allowed_roles = subset ของ VALID_ROLES
    """
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path}: ต้องเป็น JSON object")
    valid = set(VALID_ROLES) | {"admin"}
    for k, v in raw.items():
        if len(k) != 64 or any(c not in "0123456789abcdef" for c in k.lower()):
            raise RuntimeError(f"{path}: key '{k[:12]}...' ไม่ใช่ sha256 hex 64 ตัว")
        if not isinstance(v, dict) or not v.get("service"):
            raise RuntimeError(f"{path}: entry ของ '{k[:12]}...' ต้องมี field 'service'")
        bad = set(v.get("allowed_roles", [])) - valid
        if bad:
            raise RuntimeError(f"{path}: service '{v['service']}' มี role ไม่รู้จัก {bad}")
    return raw


def authenticate_service(request: Request) -> policy.ServicePrincipal:
    """X-API-Key -> ServicePrincipal (trusted identity) — ไม่ตัดสิน enforce ที่นี่ (แยก identity/decision §1)"""
    keys = request.app.state.api_keys
    raw = request.headers.get("X-API-Key", "")
    entry = keys.get(hashlib.sha256(raw.encode()).hexdigest()) if raw else None
    return policy.authenticate_service(entry, service_hint="unknown", auth_mode=AUTH_MODE)


def authorize(request: Request, role: str, endpoint: str) -> policy.EffectiveAccess:
    """
    auth + resolve effective role (fail-closed §6) + audit — ทุก retrieval endpoint ผ่านตรงนี้
    caller เลือก role นอก scope ไม่ได้; role ว่าง/ไม่รู้จัก -> deny ก่อน retrieval
    """
    principal = authenticate_service(request)
    try:
        access = policy.resolve_effective_access(principal, role)
    except policy.AuthError as e:
        print(f"[AUDIT] ts={time.strftime('%Y-%m-%dT%H:%M:%S%z')} service={principal.service_id} "
              f"endpoint={endpoint} role={role} decision=DENY code={e.code} reason={e.reason}",
              flush=True)
        raise HTTPException(status_code=e.code, detail=f"Auth failed: {e.reason}")
    # warn/off: ผ่านได้แต่ principal ยัง unverified — log ให้เห็นระหว่าง migrate consumer เดิม
    if not principal.verified:
        problem = ("missing_or_invalid_key" if not principal.authenticated
                   else (f"role_out_of_scope:{role}" if role not in principal.allowed_roles else None))
        if problem and AUTH_MODE == "warn":
            print(f"[AUTH-WARN] service={principal.service_id} endpoint={endpoint} problem={problem}",
                  flush=True)
    print(f"[AUDIT] ts={time.strftime('%Y-%m-%dT%H:%M:%S%z')} service={principal.service_id} "
          f"endpoint={endpoint} role={access.effective_role} verified={principal.verified}", flush=True)
    return access


def authorized_points(request: Request, access: policy.EffectiveAccess, vector, top_k: int):
    """shared authorized retrieval path — /search และ /ask เรียกตัวเดียวกัน; filter อยู่ใน query ก่อน retrieval"""
    spec = policy.compile_retrieval_filter(access)
    return request.app.state.qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=to_qdrant_filter(spec),
        limit=top_k,
        with_payload=True,
    ).points


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
    query: str = Field(min_length=1, max_length=2000)
    role: str
    top_k: int = 3


class SearchResult(BaseModel):
    rank: int
    point_id: str  # Qdrant point id — additive (P5a: ให้ eval/permission-test assert identity ได้ ไม่ใช่แค่ชื่อ source)
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
    question: str = Field(min_length=1, max_length=2000)
    role: str
    top_k: int = 4


class AskCitation(BaseModel):
    ref: int          # หมายเลขที่ LLM ใช้อ้างใน answer เช่น [1]
    point_id: str     # Qdrant point id — additive (P5a: permission test assert identity)
    source: str
    collection: str   # collection_group — additive (P5a: เช็ค leak ระดับ collection ตาม ACL)
    level: int        # confidentiality_level — additive (P5a: เช็ค clearance)
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
    access = authorize(request, req.role, "/search")   # auth + effective role (role นอก scope -> 403 ที่นี่)
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    if not 1 <= req.top_k <= 10:
        raise HTTPException(status_code=400, detail="top_k must be 1-10")

    vec = embed(request.app.state.tokenizer, request.app.state.model, req.query)
    points = authorized_points(request, access, vec, req.top_k)

    return SearchResponse(
        query=req.query,
        role=req.role,
        results=[
            SearchResult(
                rank=i + 1,
                point_id=str(r.id),
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
    access = authorize(request, req.role, "/ask")   # auth + effective role (fail-closed)
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(status_code=503,
                            detail="LLM not configured — set ANTHROPIC_API_KEY")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if not 1 <= req.top_k <= 10:
        raise HTTPException(status_code=400, detail="top_k must be 1-10")

    # 1) Retrieval — ใช้ shared authorized path เดียวกับ /search (compiler/filter เดียวกัน §5)
    vec = embed(request.app.state.tokenizer, request.app.state.model, req.question)
    points = authorized_points(request, access, vec, req.top_k)

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
                point_id=str(p.id),
                source=p.payload.get("source", ""),
                collection=p.payload.get("collection_group", "?"),
                level=p.payload.get("confidentiality_level", 0),
                heading=p.payload.get("heading", ""),
                score=round(p.score, 4),
            )
            for i, p in enumerate(points)
        ],
        model=resp.model,
    )


@app.get("/collections")
def list_collections(role: str, request: Request):
    access = authorize(request, role, "/collections")
    if access.effective_role != "admin":
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
