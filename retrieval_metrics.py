"""
Retrieval-quality metrics (P2 Slice 1) — pure, offline, unit-testable
retrieval-only (ไม่มี citation — ตัดออกตาม Codex Q1) ; graded relevance รองรับ nDCG

ทุกฟังก์ชันรับ:
  ranked_ids     : list[str] — point_id เรียงตามลำดับที่ arm จัด (dense/rerank/fused)
  relevant_ids   : set/list[str] — point_id ที่ relevant (grade >= 1)
  relevance      : dict[str,int] — point_id -> grade (สำหรับ nDCG)
คืน None เมื่อ undefined (ไม่มี relevance) เพื่อให้ aggregation ข้ามได้ (ไม่ปน 0 หลอก)

point/chunk-level แยกจาก document/source-level (Codex: กันหลาย chunk เอกสารเดียวทำคะแนนหลอก)
"""
from __future__ import annotations
import math

LOCKED_GRADES = (1, 2, 3)   # graded relevance allowlist (ตรงกับ p2_eval)


# ── input guards (M3 — กัน config/input ที่ทำคะแนนผิดความหมาย) ──────────────────
def _check_k(k) -> None:
    if type(k) is not int or k < 1:
        raise ValueError(f"k/n ต้อง positive int: {k!r}")


def _check_ids(ids) -> None:
    if not isinstance(ids, (list, tuple)):
        raise ValueError("ranked/candidate ids ต้องเป็น list")
    seen = set()
    for x in ids:
        if not isinstance(x, str) or not x.strip():
            raise ValueError(f"id ว่าง/ผิดชนิดใน ranking: {x!r}")
        if x in seen:
            raise ValueError(f"id ซ้ำใน ranking: {x}")
        seen.add(x)


def _check_relevant_ids(rids) -> None:
    """relevant_ids ต้องเป็น collection ของ id (ไม่ใช่ string เดี่ยว ที่ set() จะแตกเป็นตัวอักษร)"""
    if isinstance(rids, str):
        raise ValueError("relevant_ids เป็น string เดี่ยว — ต้องเป็น set/list/dict ของ id")
    if not isinstance(rids, (set, frozenset, list, tuple, dict)):
        raise ValueError(f"relevant_ids ผิดชนิด: {type(rids).__name__}")
    ids = list(rids)
    for x in ids:
        if not isinstance(x, str) or not x.strip():
            raise ValueError(f"relevant id ว่าง/ผิดชนิด: {x!r}")
    if len(set(ids)) != len(ids):
        raise ValueError("relevant id ซ้ำ")


def _check_relevance(rel) -> None:
    if not isinstance(rel, dict):
        raise ValueError("relevance ต้องเป็น dict")
    for pid, g in rel.items():
        if not isinstance(pid, str) or not pid.strip():
            raise ValueError(f"relevance key ว่าง/ผิดชนิด: {pid!r}")
        if type(g) is not int or g not in LOCKED_GRADES:
            raise ValueError(f"grade นอก allowlist {LOCKED_GRADES}: {pid}={g!r}")


# ── candidate generation (บังคับ — reranker ช่วย point นอก pool ไม่ได้) ─────────
def candidate_recall_at_n(candidate_ids: list, relevant_ids, n: int):
    """สัดส่วน relevant ที่โผล่ใน candidate pool top-N (ก่อน rerank) — วัด candidate generation"""
    _check_k(n)
    _check_ids(candidate_ids)
    _check_relevant_ids(relevant_ids)
    rel = set(relevant_ids)
    if not rel:
        return None
    return len(rel & set(candidate_ids[:n])) / len(rel)


# ── ordering metrics (point/chunk-level) ───────────────────────────────────────
def hit_at_k(ranked_ids: list, relevant_ids, k: int) -> float:
    _check_k(k)
    _check_ids(ranked_ids)
    _check_relevant_ids(relevant_ids)
    return 1.0 if set(ranked_ids[:k]) & set(relevant_ids) else 0.0


def mrr_at_k(ranked_ids: list, relevant_ids, k: int) -> float:
    _check_k(k)
    _check_ids(ranked_ids)
    _check_relevant_ids(relevant_ids)
    rel = set(relevant_ids)
    for i, pid in enumerate(ranked_ids[:k], 1):
        if pid in rel:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked_ids: list, relevant_ids, k: int):
    _check_k(k)
    _check_ids(ranked_ids)
    _check_relevant_ids(relevant_ids)
    rel = set(relevant_ids)
    if not rel:
        return None
    return len(rel & set(ranked_ids[:k])) / len(rel)


def _dcg(grades: list) -> float:
    # DCG = Σ (2^grade - 1) / log2(pos+1) ; pos 1-based (exponential gain, standard สำหรับ graded)
    return sum((2 ** g - 1) / math.log2(i + 1) for i, g in enumerate(grades, 1))


def ndcg_at_k(ranked_ids: list, relevance: dict, k: int):
    """graded nDCG@k ในช่วง [0,1] ; None ถ้าไม่มี graded relevance (IDCG=0)"""
    _check_k(k)
    _check_ids(ranked_ids)
    _check_relevance(relevance)
    gains = [relevance.get(pid, 0) for pid in ranked_ids[:k]]
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = _dcg(ideal)
    if idcg == 0:
        return None
    val = _dcg(gains) / idcg
    if not math.isfinite(val) or not (0.0 <= val <= 1.0 + 1e-9):
        raise ValueError(f"nDCG นอกช่วง [0,1]: {val} (relevance/ranking ผิด contract?)")
    return min(val, 1.0)


# ── document/source-level (collapse chunk → เอกสาร) ────────────────────────────
def to_document_ranking(ranked_ids: list, point_to_doc: dict) -> list:
    """ยุบเป็นลำดับเอกสาร (first occurrence ของแต่ละ document) — กัน chunk ซ้ำเอกสารทำคะแนนหลอก"""
    seen, out = set(), []
    for pid in ranked_ids:
        d = point_to_doc.get(pid)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def document_relevance(relevance: dict, point_to_doc: dict) -> dict:
    """document -> grade สูงสุดในบรรดา chunk ที่ relevant ของเอกสารนั้น"""
    out: dict = {}
    for pid, g in relevance.items():
        d = point_to_doc.get(pid)
        if d is not None:
            out[d] = max(out.get(d, 0), g)
    return out


# ── latency ────────────────────────────────────────────────────────────────────
def percentiles(values: list, ps=(50, 95)) -> dict:
    """p50/p95 (nearest-rank) ; ผู้เรียกควรตัด warm-up ออกก่อน. latency ต้อง finite non-negative (M3)"""
    for v in values:
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0):
            raise ValueError(f"latency ต้อง finite non-negative: {v!r}")
    s = sorted(values)
    if not s:
        return {p: None for p in ps}
    return {p: s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))] for p in ps}


# ── aggregation helper (mean ข้าม query, ข้าม None) ────────────────────────────
def mean_ignore_none(values: list):
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None
