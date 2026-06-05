"""
Company AI Brain — FastAPI Service
Retrieval-only API พร้อม RBAC filter

Endpoints:
    GET  /health         — สถานะระบบ + จำนวน vectors
    POST /search         — ค้นหาเอกสารตาม role
    GET  /collections    — สถิติแต่ละ collection (admin only)
"""
import os
import sys
from contextlib import asynccontextmanager

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


class SearchResponse(BaseModel):
    query: str
    role: str
    results: list[SearchResult]


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
            )
            for i, r in enumerate(points)
        ],
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
