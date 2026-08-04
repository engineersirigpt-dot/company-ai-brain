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


# ── candidate generation (บังคับ — reranker ช่วย point นอก pool ไม่ได้) ─────────
def candidate_recall_at_n(candidate_ids: list, relevant_ids, n: int):
    """สัดส่วน relevant ที่โผล่ใน candidate pool top-N (ก่อน rerank) — วัด candidate generation"""
    rel = set(relevant_ids)
    if not rel:
        return None
    return len(rel & set(candidate_ids[:n])) / len(rel)


# ── ordering metrics (point/chunk-level) ───────────────────────────────────────
def hit_at_k(ranked_ids: list, relevant_ids, k: int) -> float:
    return 1.0 if set(ranked_ids[:k]) & set(relevant_ids) else 0.0


def mrr_at_k(ranked_ids: list, relevant_ids, k: int) -> float:
    rel = set(relevant_ids)
    for i, pid in enumerate(ranked_ids[:k], 1):
        if pid in rel:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked_ids: list, relevant_ids, k: int):
    rel = set(relevant_ids)
    if not rel:
        return None
    return len(rel & set(ranked_ids[:k])) / len(rel)


def _dcg(grades: list) -> float:
    # DCG = Σ (2^grade - 1) / log2(pos+1) ; pos 1-based (exponential gain, standard สำหรับ graded)
    return sum((2 ** g - 1) / math.log2(i + 1) for i, g in enumerate(grades, 1))


def ndcg_at_k(ranked_ids: list, relevance: dict, k: int):
    """graded nDCG@k ; None ถ้าไม่มี graded relevance (IDCG=0)"""
    gains = [relevance.get(pid, 0) for pid in ranked_ids[:k]]
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = _dcg(ideal)
    if idcg == 0:
        return None
    return _dcg(gains) / idcg


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
    """p50/p95 (nearest-rank) ; ผู้เรียกควรตัด warm-up ออกก่อน"""
    s = sorted(values)
    if not s:
        return {p: None for p in ps}
    return {p: s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))] for p in ps}


# ── aggregation helper (mean ข้าม query, ข้าม None) ────────────────────────────
def mean_ignore_none(values: list):
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None
