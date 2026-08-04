"""
P2 benchmark harness scaffolding — dense / rerank / fused_rrf บน candidate universe **เดียว** ต่อ query
metrics แยกจาก permission (Codex guardrail). **output = mechanics-smoke UNAPPROVED เท่านั้น**
ไม่ wire เข้า decision_benchmark_manifest จน B3.1 confirm — arm verdict/freeze ยัง NO-GO

pure/injectable: client (fake), embed_query, scorer (MockScorer) → test offline โดยไม่ต้อง Docker/model
"""
from __future__ import annotations

import rerank
import retrieval_metrics as M
import p2_provider

ARMS = ("dense", "rerank", "fused")
KS = (1, 3, 5)


def rank_arms(query: str, cands: list, scorer) -> dict:
    """คืน {arm: ranked point_ids} — ทุก arm บน candidate ID universe เดียว (Codex G2)"""
    dense = rerank.dense_order(cands)
    rr = rerank.rerank_order(query, cands, scorer.score)
    fused = rerank.fused_rrf({"dense": dense, "rerank": rr}, dense_rank_map=rerank.dense_rank_map(cands))
    return {"dense": dense, "rerank": rr, "fused": fused}


def eval_query(query: str, cands: list, relevance: dict, scorer, ks=KS, n: int = None) -> dict:
    """metric ต่อ arm สำหรับ query เดียว (retrieval-only, primary = nDCG@5)"""
    cand_ids = [c["point_id"] for c in cands]
    rel_ids = set(relevance)
    per_arm = {}
    for arm, order in rank_arms(query, cands, scorer).items():
        m = {f"hit@{k}": M.hit_at_k(order, rel_ids, k) for k in ks}
        m["mrr@5"] = M.mrr_at_k(order, rel_ids, 5)
        m["ndcg@5"] = M.ndcg_at_k(order, relevance, 5)
        m["recall@5"] = M.recall_at_k(order, rel_ids, 5)
        per_arm[arm] = m
    return {"candidate_recall@n": M.candidate_recall_at_n(cand_ids, rel_ids, n or len(cand_ids)),
            "n_candidates": len(cand_ids), "arms": per_arm}


def aggregate(per_query: list) -> dict:
    """mean ต่อ arm ต่อ metric (ignore None) — mechanics เท่านั้น (ยังไม่ทำ paired CI — Slice 2 decision)"""
    agg = {a: {} for a in ARMS}
    if not per_query:
        return agg
    metric_keys = list(per_query[0]["arms"]["dense"].keys())
    for a in ARMS:
        for k in metric_keys:
            agg[a][k] = M.mean_ignore_none([q["arms"][a][k] for q in per_query])
    agg["candidate_recall@n"] = M.mean_ignore_none([q["candidate_recall@n"] for q in per_query])
    return agg


def run_smoke(client, collection: str, cases: list, principal_for, embed_query, scorer,
              top_n: int, filter_adapter=p2_provider.to_qdrant_filter) -> dict:
    """
    mechanics smoke run ต่อ ranking cases → resolve access (fail-closed) → build candidates → eval arms
    output ติดป้าย **approved=False, decision_eligible=False** — ห้ามใช้เลือก arm/freeze
    """
    per_query, skipped = [], []
    for c in cases:
        principal = principal_for(c["role"])
        try:
            cands = p2_provider.resolve_and_build(client, collection, principal, c["role"],
                                                  embed_query(c["query"]), top_n, filter_adapter)
        except Exception as e:
            skipped.append({"query_id": c.get("query_id"), "reason": f"{type(e).__name__}: {e}"})
            continue
        per_query.append({"query_id": c.get("query_id"), "role": c["role"],
                          "intent_id": c.get("intent_id"), **eval_query(c["query"], cands, c["relevance"], scorer)})
    return {"kind": "mechanics-smoke-unapproved", "approved": False, "decision_eligible": False,
            "top_n": top_n, "n_queries": len(per_query), "n_skipped": len(skipped), "skipped": skipped,
            "scorer": getattr(scorer, "metadata", lambda: {"model": "unknown"})(),
            "aggregate": aggregate(per_query), "per_query": per_query}
