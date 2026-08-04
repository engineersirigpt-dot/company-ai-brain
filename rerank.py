"""
Ordering arms (P2a Slice 1) — pure, offline: dense / rerank / fused_rrf บน candidate-ID universe เดียว
cross-encoder อยู่หลัง interface `score_fn(query, texts) -> list[float]` (inject mock ตอน test / โมเดลจริง Slice 2)

guardrail: rerank_order เห็นเฉพาะ rerank_text ของ candidate ที่ผ่าน filter แล้ว (authorized) — ดู
assert_candidates_authorized ; caller (candidate provider) ต้อง filter ACL ก่อนสร้าง candidate เสมอ
"""
from __future__ import annotations
import math

RRF_K_DEFAULT = 60


def _finite_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_candidates(cands: list) -> list:
    """
    Candidate contract (Codex): point_id non-empty unique · source stable str · dense_score finite ·
    dense_rank unique positive int · rerank_text non-empty. raise ValueError เมื่อผิด
    """
    if not isinstance(cands, list):
        raise ValueError("candidates ต้องเป็น list")
    ids, ranks = set(), set()
    for c in cands:
        if not isinstance(c, dict):
            raise ValueError("candidate ต้องเป็น object")
        pid = c.get("point_id")
        if not isinstance(pid, str) or not pid.strip():
            raise ValueError("point_id ว่าง/ไม่ใช่ str")
        if pid in ids:
            raise ValueError(f"point_id ซ้ำ: {pid}")
        ids.add(pid)
        if not isinstance(c.get("source"), str) or not c.get("source"):
            raise ValueError(f"source ว่าง ({pid})")
        if not _finite_number(c.get("dense_score")):
            raise ValueError(f"dense_score ไม่ finite ({pid})")
        dr = c.get("dense_rank")
        if not isinstance(dr, int) or isinstance(dr, bool) or dr < 1:
            raise ValueError(f"dense_rank ต้อง positive int ({pid})")
        if dr in ranks:
            raise ValueError(f"dense_rank ซ้ำ: {dr}")
        ranks.add(dr)
        if not isinstance(c.get("rerank_text"), str) or not c.get("rerank_text").strip():
            raise ValueError(f"rerank_text ว่าง ({pid})")
    return cands


def assert_candidates_authorized(cands: list, authorized_ids) -> None:
    """defense-in-depth: candidate ทุกตัวต้องอยู่ใน authorized set (filter ต้องทำก่อนถึงตรงนี้)"""
    allow = set(authorized_ids)
    leaked = [c["point_id"] for c in cands if c["point_id"] not in allow]
    if leaked:
        raise ValueError(f"candidate นอกสิทธิ์หลุดเข้ามา (filter ก่อน rerank ล้มเหลว): {leaked}")


def dense_order(cands: list) -> list:
    """ลำดับ dense เดิม (ตาม dense_rank)"""
    validate_candidates(cands)
    return [c["point_id"] for c in sorted(cands, key=lambda c: c["dense_rank"])]


def rerank_order(query: str, cands: list, score_fn) -> list:
    """
    cross-encoder rerank: score_fn(query, [rerank_text...]) -> [float] เรียงตรงกับ cands
    tie-break: dense_rank แล้ว point_id. input ไม่ถูก mutate. output = permutation ของ candidate IDs
    """
    validate_candidates(cands)
    texts = [c["rerank_text"] for c in cands]
    scores = score_fn(query, texts)
    if not isinstance(scores, (list, tuple)) or len(scores) != len(cands):
        raise ValueError(f"score count ({len(scores) if hasattr(scores, '__len__') else '?'}) != candidate count ({len(cands)})")
    for s in scores:
        if not _finite_number(s):
            raise ValueError(f"score ไม่ finite (NaN/Inf): {s!r}")
    paired = sorted(zip(cands, scores), key=lambda cs: (-cs[1], cs[0]["dense_rank"], cs[0]["point_id"]))
    return [c["point_id"] for c, _ in paired]


def fused_rrf(rankings: dict, rrf_k: int = RRF_K_DEFAULT, dense_rank_map: dict = None) -> list:
    """
    Reciprocal Rank Fusion: rrf_score(id) = Σ_i 1/(rrf_k + rank_i(id)) ; rank 1-based
    tie-break: dense_rank แล้ว point_id. ทุก ranking ต้องเป็น permutation ของ universe เดียว (exact set-equality)
      rankings: {arm_name: [ranked point_ids]}
    """
    if not rankings:
        raise ValueError("rankings ว่าง")
    universe = None
    for name, r in rankings.items():
        if len(set(r)) != len(r):
            raise ValueError(f"ranking '{name}' มี id ซ้ำ")
        s = set(r)
        if universe is None:
            universe = s
        elif s != universe:
            raise ValueError(f"ranking '{name}' ไม่ใช่ permutation ของ universe เดียว (missing/extra id)")
    scores: dict = {pid: 0.0 for pid in universe}
    for r in rankings.values():
        for rank, pid in enumerate(r, 1):
            scores[pid] += 1.0 / (rrf_k + rank)
    dr = dense_rank_map or {}
    return sorted(universe, key=lambda pid: (-scores[pid], dr.get(pid, 10 ** 9), pid))


def dense_rank_map(cands: list) -> dict:
    return {c["point_id"]: c["dense_rank"] for c in cands}
