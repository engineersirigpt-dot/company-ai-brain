"""
Unit test ของ P2 Slice 1 (pure) — retrieval_metrics + rerank + p2_eval
พิสูจน์ Slice 1 contract + required behaviors ตาม Codex (ไม่ต้อง model/Qdrant)

    python test_p2.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import retrieval_metrics as M
import rerank as R
import p2_eval as E

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn):
    try:
        fn(); return False
    except ValueError:
        return True


def cand(pid, rank, score=None, source=None, text=None):
    return {"point_id": pid, "source": (f"doc-{pid}" if source is None else source),
            "dense_score": score if score is not None else 1.0 / rank,
            "dense_rank": rank, "rerank_text": (f"text-{pid}" if text is None else text)}


CANDS = [cand("a", 1), cand("b", 2), cand("c", 3)]

# ── validate_candidates (Codex contract) ───────────────────────────────────────
check("valid candidates -> ok", R.validate_candidates([cand("a", 1), cand("b", 2)]) is not None)
check("point_id ซ้ำ -> fail", raises(lambda: R.validate_candidates([cand("a", 1), cand("a", 2)])))
check("point_id ว่าง -> fail", raises(lambda: R.validate_candidates([cand("", 1)])))
check("dense_rank ซ้ำ -> fail", raises(lambda: R.validate_candidates([cand("a", 1), cand("b", 1)])))
check("dense_rank ไม่ positive -> fail", raises(lambda: R.validate_candidates([cand("a", 0, score=0.5)])))
check("dense_score NaN -> fail", raises(lambda: R.validate_candidates([cand("a", 1, score=float("nan"))])))
check("dense_score Inf -> fail", raises(lambda: R.validate_candidates([cand("a", 1, score=float("inf"))])))
check("dense_score bool -> fail", raises(lambda: R.validate_candidates([cand("a", 1, score=True)])))
check("rerank_text ว่าง -> fail", raises(lambda: R.validate_candidates([cand("a", 1, text=" ")])))
check("source ว่าง -> fail", raises(lambda: R.validate_candidates([cand("a", 1, source="")])))

# ── dense_order + input ไม่ mutate ─────────────────────────────────────────────
snapshot = [dict(c) for c in CANDS]
check("dense_order ตาม dense_rank", R.dense_order(CANDS) == ["a", "b", "c"])
check("dense_order ไม่ mutate input", CANDS == snapshot)

# ── rerank_order ───────────────────────────────────────────────────────────────
def scorer(smap):
    seen = {"texts": []}
    def fn(query, texts):
        seen["texts"].extend(texts)
        return [smap.get(t, 0.0) for t in texts]
    return fn, seen
sf, _ = scorer({"text-c": 3.0, "text-a": 2.0, "text-b": 1.0})
check("rerank_order เรียงตาม score desc", R.rerank_order("q", CANDS, sf) == ["c", "a", "b"])
check("rerank_order ไม่ mutate input", CANDS == snapshot)
check("score count != candidate -> fail", raises(lambda: R.rerank_order("q", CANDS, lambda q, t: [1.0, 2.0])))
check("score NaN -> fail", raises(lambda: R.rerank_order("q", CANDS, lambda q, t: [float("nan")] * len(t))))
# tie-break: score เท่ากัน -> dense_rank แล้ว point_id
sf_tie, _ = scorer({})  # ทุก text score 0 -> เท่ากันหมด
check("rerank tie -> tie-break ด้วย dense_rank", R.rerank_order("q", CANDS, sf_tie) == ["a", "b", "c"])
check("rerank empty -> empty", R.rerank_order("q", [], lambda q, t: []) == [])
check("rerank one -> same", R.rerank_order("q", [cand("a", 1)], lambda q, t: [5.0]) == ["a"])
check("rerank output = permutation ของ candidate IDs",
      sorted(R.rerank_order("q", CANDS, sf)) == ["a", "b", "c"])

# ── instrumented score_fn: unauthorized sentinel ไม่เคยถึง score_fn ─────────────
sf_probe, seen = scorer({})
authorized = [cand("a", 1), cand("b", 2)]           # candidate ที่ผ่าน filter แล้ว (มีแค่ a,b)
R.assert_candidates_authorized(authorized, {"a", "b"})
R.rerank_order("q", authorized, sf_probe)
check("unauthorized sentinel ไม่ถึง score_fn (score_fn เห็นแค่ candidate text)",
      "text-SENTINEL" not in seen["texts"] and set(seen["texts"]) == {"text-a", "text-b"}, seen["texts"])
check("assert_candidates_authorized: point นอกสิทธิ์หลุด -> fail",
      raises(lambda: R.assert_candidates_authorized([cand("SENTINEL", 1)], {"a", "b"})))

# ── fused_rrf ──────────────────────────────────────────────────────────────────
drm = R.dense_rank_map(CANDS)
fused = R.fused_rrf({"dense": ["a", "b", "c"], "rerank": ["a", "c", "b"]}, dense_rank_map=drm)
check("fused_rrf: a สูงสุด (rank1 ทั้งสอง arm), b ก่อน c (tie -> dense_rank)", fused == ["a", "b", "c"], fused)
check("fused_rrf output = permutation", sorted(fused) == ["a", "b", "c"])
check("fused_rrf: ranking มี id ซ้ำ -> fail",
      raises(lambda: R.fused_rrf({"x": ["a", "a", "b"]})))
check("fused_rrf: universe ไม่ตรง -> fail",
      raises(lambda: R.fused_rrf({"x": ["a", "b"], "y": ["a", "c"]})))
check("fused_rrf deterministic", R.fused_rrf({"d": ["a", "b", "c"], "r": ["a", "c", "b"]}, dense_rank_map=drm) == fused)
# RRF favors item ที่ rank สูงใน arm ใดๆ มากกว่า middling ทั้งคู่ (1/x convex):
# dense=[a,b,c], rerank=[c,b,a] -> a=1/61+1/63, c=1/63+1/61 (เท่ากัน, สูงสุด), b=2/62 (ต่ำกว่า)
f2 = R.fused_rrf({"dense": ["a", "b", "c"], "rerank": ["c", "b", "a"]}, dense_rank_map=drm)
check("fused_rrf: a,c (rank1 ใน arm หนึ่ง) นำ b (rank2 ทั้งคู่); tie a/c -> dense_rank a ก่อน c",
      f2 == ["a", "c", "b"], f2)

# ── metrics ────────────────────────────────────────────────────────────────────
rel = {"a": 3, "c": 1}                                # a,c relevant (graded)
check("candidate_recall@2: {a,c} ใน [a,b] top2 -> 0.5", M.candidate_recall_at_n(["a", "b", "c"], rel, 2) == 0.5)
check("candidate_recall@3 -> 1.0", M.candidate_recall_at_n(["a", "b", "c"], rel, 3) == 1.0)
check("hit@1: a relevant -> 1.0", M.hit_at_k(["a", "b"], rel, 1) == 1.0)
check("hit@1: b ไม่ relevant -> 0.0", M.hit_at_k(["b", "a"], rel, 1) == 0.0)
check("mrr@5: relevant อันดับ2 -> 0.5", M.mrr_at_k(["b", "a"], rel, 5) == 0.5)
check("recall@2: [a,b] มี a จาก {a,c} -> 0.5", M.recall_at_k(["a", "b"], rel, 2) == 0.5)
check("ndcg perfect order -> 1.0", M.ndcg_at_k(["a", "c"], rel, 2) == 1.0)
check("ndcg reversed -> ~0.71", round(M.ndcg_at_k(["c", "a"], rel, 2), 2) == 0.71, M.ndcg_at_k(["c", "a"], rel, 2))
check("ndcg ไม่มี graded -> None", M.ndcg_at_k(["a"], {}, 5) is None)
check("recall ไม่มี relevant -> None", M.recall_at_k(["a"], {}, 5) is None)

# document-level
p2d = {"a": "D1", "b": "D1", "c": "D2"}              # a,b เอกสารเดียว
check("to_document_ranking: collapse chunk เอกสารเดียว", M.to_document_ranking(["a", "b", "c"], p2d) == ["D1", "D2"])
check("document_relevance: D1 grade สูงสุด", M.document_relevance(rel, p2d) == {"D1": 3, "D2": 1})
check("percentiles p50/p95", M.percentiles([10, 20, 30, 40])[50] == 20)
check("mean_ignore_none", M.mean_ignore_none([1.0, None, 3.0]) == 2.0)

# ── p2_eval validation ─────────────────────────────────────────────────────────
CORPUS = {"pa": {"allowed_roles": ["qc", "admin"], "source": "D1"},
          "pb": {"allowed_roles": ["sales", "admin"], "source": "D2"}}
good = [{"query_id": "q1", "query": "x", "role": "qc", "relevance": {"pa": 2}}]
check("eval-set ดี -> ไม่มี error", E.validate_eval_set(good, CORPUS, {"qc", "admin", "sales"}) == [])
def _errs(cases):
    return E.validate_eval_set(cases, CORPUS, {"qc", "admin", "sales"})
check("query_id ซ้ำ -> error",
      any("ซ้ำ" in e for e in _errs([good[0], dict(good[0])])))
check("role ไม่รู้จัก -> error",
      any("ไม่รู้จัก" in e for e in _errs([{"query_id": "q2", "query": "x", "role": "ceo", "relevance": {"pa": 1}}])))
check("relevant point ไม่อยู่ใน corpus -> error",
      any("ไม่อยู่ใน" in e for e in _errs([{"query_id": "q3", "query": "x", "role": "qc", "relevance": {"pz": 1}}])))
check("relevant point ไม่ authorized สำหรับ role -> error (pb เป็นของ sales, role=qc)",
      any("authorized" in e for e in _errs([{"query_id": "q4", "query": "x", "role": "qc", "relevance": {"pb": 1}}])))
check("grade ผิด (0) -> error",
      any("grade" in e for e in _errs([{"query_id": "q5", "query": "x", "role": "qc", "relevance": {"pa": 0}}])))
check("relevance ว่าง -> error",
      any("relevance" in e for e in _errs([{"query_id": "q6", "query": "x", "role": "qc", "relevance": {}}])))
check("frozen_hash deterministic", E.frozen_hash(good) == E.frozen_hash(good))
check("frozen_hash เปลี่ยนตามข้อมูล", E.frozen_hash(good) != E.frozen_hash(good + good))
check("permission_gate_ok: exit 0 -> valid", E.permission_gate_ok(0) is True)
check("permission_gate_ok: exit 1 (leak/ERROR) -> invalid", E.permission_gate_ok(1) is False)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
