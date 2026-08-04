"""
P2 Slice 2 candidate provider (pure interface + injectable client) — offline-testable
GO-INFRA ตาม Codex (KB_P2_SLICE1_FIX3_CODEX_CONFIRM). actual cross-encoder/Qdrant run = Slice 2

guardrail:
- รับเฉพาะ **trusted EffectiveAccess** (ไม่รับ raw role ที่ไม่ resolve) → caller ต้อง resolve ก่อน
- ใช้ compiled filter เดียวกับ API (`policy.compile_retrieval_filter` + `to_qdrant_filter`) → reranker/
  scorer เห็นเฉพาะ authorized candidates (M4) ; filter อยู่ใน query_points ก่อน retrieval
- ไม่แตะ public API cap (`/search top_k<=10`) — internal provider คุม top_n เอง (N sweep)
"""
from __future__ import annotations

import policy
import rerank
from qdrant_filter import to_qdrant_filter

MAX_RERANK_TEXT_CHARS = 512   # deterministic truncation (adapter จริง tokenize ด้วย model tokenizer)


def build_rerank_text(payload: dict, max_chars: int = MAX_RERANK_TEXT_CHARS) -> str:
    """
    deterministic rerank text = heading + child (ห้ามสลับ parent/child ตามความยาวระหว่าง arm — attribution ชัด)
    ใช้ field 'rerank_text' ถ้ามี (synthetic corpus) มิฉะนั้น heading + text
    """
    if isinstance(payload.get("rerank_text"), str) and payload["rerank_text"].strip():
        return payload["rerank_text"].strip()[:max_chars]
    heading = (payload.get("heading") or "").strip()
    child = (payload.get("text") or "").strip()
    return f"{heading} {child}".strip()[:max_chars]


def build_candidates(client, collection: str, access, query_vector, top_n: int,
                     filter_adapter=to_qdrant_filter) -> list:
    """
    รับ trusted EffectiveAccess → compile filter → query_points(filter) → Candidate list (validated)
    filter_adapter inject ได้เพื่อ test offline (default = to_qdrant_filter สำหรับ Qdrant จริง)
    """
    if not isinstance(access, policy.EffectiveAccess):
        raise TypeError("build_candidates รับเฉพาะ trusted policy.EffectiveAccess (ไม่รับ raw role)")
    if type(top_n) is not int or top_n < 1:
        raise ValueError(f"top_n ต้อง positive int: {top_n!r}")

    spec = policy.compile_retrieval_filter(access)
    res = client.query_points(collection_name=collection, query=query_vector,
                              query_filter=filter_adapter(spec), limit=top_n, with_payload=True)
    cands = []
    for i, p in enumerate(res.points, 1):
        payload = getattr(p, "payload", None) or {}
        cands.append({
            "point_id": str(getattr(p, "id", "")),
            "source": str(payload.get("source", "")),
            "dense_score": float(getattr(p, "score", 0.0)),
            "dense_rank": i,
            "rerank_text": build_rerank_text(payload),
        })
    rerank.validate_candidates(cands)              # contract guard (unique id/rank, finite score, non-blank text)
    return cands


def resolve_and_build(client, collection: str, principal, requested_role: str, query_vector,
                      top_n: int, filter_adapter=to_qdrant_filter) -> list:
    """entry จาก principal + requested role → resolve (fail-closed) → build_candidates (ไม่รับ raw role ตรง ๆ)"""
    access = policy.resolve_effective_access(principal, requested_role)
    return build_candidates(client, collection, access, query_vector, top_n, filter_adapter)
