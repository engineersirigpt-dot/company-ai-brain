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
MAX_TOP_N = 200               # internal cap (M2) — harness sweep ล็อกที่ {10,20,30,50}


def _assert_trusted_access(access) -> None:
    """
    B1: access ต้องเป็น **verified** จริง ไม่ใช่ EffectiveAccess ที่สร้างมือ/unverified
      - principal authenticated + AUTH_MODE=enforce (principal.verified)
      - effective_role ∈ KNOWN_ROLES และ ∈ principal.allowed_roles (กัน forged/role-mismatch)
    """
    if not isinstance(access, policy.EffectiveAccess):
        raise TypeError("รับเฉพาะ policy.EffectiveAccess (ไม่รับ raw role)")
    pr = access.principal
    if not isinstance(pr, policy.ServicePrincipal) or not pr.verified:
        raise PermissionError("EffectiveAccess ไม่ verified (ต้อง authenticated + AUTH_MODE=enforce)")
    if access.effective_role not in policy.KNOWN_ROLES:
        raise PermissionError(f"effective_role ไม่รู้จัก: {access.effective_role!r}")
    if access.effective_role not in pr.allowed_roles:
        raise PermissionError("effective_role ไม่อยู่ใน principal scope (forged access?)")


def build_rerank_text(payload: dict, max_chars: int = MAX_RERANK_TEXT_CHARS) -> str:
    """
    deterministic rerank text = heading + child (ห้ามสลับ parent/child ตามความยาว — attribution ชัด)
    M2: field ต้องเป็น str จริง (ไม่ coerce ค่าผิดชนิดให้ดู valid) ; max_chars positive int
    """
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError(f"max_chars ต้อง positive int: {max_chars!r}")
    rt = payload.get("rerank_text")
    if rt is not None:
        if not isinstance(rt, str):
            raise ValueError("rerank_text ผิดชนิด (ต้อง str)")
        if rt.strip():
            return rt.strip()[:max_chars]
    heading, child = payload.get("heading", ""), payload.get("text", "")
    if not isinstance(heading, str) or not isinstance(child, str):
        raise ValueError("heading/text ผิดชนิด (ต้อง str)")
    return f"{heading.strip()} {child.strip()}".strip()[:max_chars]


def build_candidates(client, collection: str, access, query_vector, top_n: int,
                     filter_adapter=to_qdrant_filter) -> list:
    """
    รับ trusted+verified EffectiveAccess → compile filter → query_points(filter) → Candidate list
    **B2 postcondition (fail-closed detector):** payload ที่ backend คืนทุกจุดต้องเป็น policy-v1 ที่ shape
    ถูก และ matches compiled access อีกครั้ง — ถ้าเจอ mismatch **fail ทั้ง batch** (ไม่ drop เงียบ กัน policy drift)
    Qdrant filter ยังเป็นด่านหลักก่อน retrieval; นี่คือ detector ไม่ใช่ย้าย enforcement มาหลัง retrieval
    """
    _assert_trusted_access(access)                       # B1
    if type(top_n) is not int or top_n < 1 or top_n > MAX_TOP_N:
        raise ValueError(f"top_n ต้อง int ใน 1..{MAX_TOP_N}: {top_n!r}")

    spec = policy.compile_retrieval_filter(access)
    res = client.query_points(collection_name=collection, query=query_vector,
                              query_filter=filter_adapter(spec), limit=top_n, with_payload=True)
    cands = []
    for i, p in enumerate(res.points, 1):
        payload = getattr(p, "payload", None)
        pt_id = str(getattr(p, "id", ""))
        if not isinstance(payload, dict):
            raise PermissionError(f"backend คืน payload ผิดรูป (point {pt_id})")
        if not policy.payload_is_policy_v1(payload):
            raise PermissionError(f"backend คืน payload ไม่ใช่ policy-v1 (point {pt_id})")
        ok, _ = policy.validate_stored_payload(payload)
        if not ok or not policy.matches_policy(payload, spec):
            raise PermissionError(f"backend คืน point ผิดสิทธิ์/ผิด contract (point {pt_id}) — fail batch")
        src = payload.get("source")
        if not isinstance(src, str) or not src.strip():
            raise ValueError(f"payload.source ว่าง/ผิดชนิด (point {pt_id})")
        score = getattr(p, "score", None)
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError(f"dense_score ผิดชนิด (point {pt_id})")
        cands.append({"point_id": pt_id, "source": src, "dense_score": float(score),
                      "dense_rank": i, "rerank_text": build_rerank_text(payload)})
    rerank.validate_candidates(cands)                    # contract guard
    return cands


def resolve_and_build(client, collection: str, principal, requested_role: str, query_vector,
                      top_n: int, filter_adapter=to_qdrant_filter) -> list:
    """entry จาก principal + requested role → resolve (fail-closed) → build_candidates (ไม่รับ raw role ตรง ๆ)"""
    access = policy.resolve_effective_access(principal, requested_role)
    return build_candidates(client, collection, access, query_vector, top_n, filter_adapter)
