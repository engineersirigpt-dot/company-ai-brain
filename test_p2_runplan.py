"""
Unit test ของ P2 run plan + decision analysis (M1) — pure, offline
พิสูจน์ RunPlan/N-sweep/paired-bootstrap/arm-decision/latency ก่อนเปิด Docker

    python test_p2_runplan.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_runplan as RP

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn):
    try:
        fn(); return False
    except (ValueError, KeyError):
        return True


_H = "a" * 64
PLAN = {"run_id": "run-1", "n_set": [10, 20, 30, 50], "seed": 12345, "resamples": 10000,
        "primary_metric": "ndcg@5", "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "expected_counts": {"test_intents": 50},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H}}

# ── RunPlan validate + hash ────────────────────────────────────────────────────
check("run_plan valid -> ไม่มี error", RP.validate_run_plan(PLAN) == [], RP.validate_run_plan(PLAN))
check("seed หาย -> error", any("seed" in e for e in RP.validate_run_plan({**PLAN, "seed": None})))
check("n_set ผิด -> error", any("n_set" in e for e in RP.validate_run_plan({**PLAN, "n_set": [10, 20]})))
check("resamples < 10000 -> error", any("resamples" in e for e in RP.validate_run_plan({**PLAN, "resamples": 999})))
check("primary_metric ไม่ใช่ ndcg@5 -> error", any("primary_metric" in e for e in RP.validate_run_plan({**PLAN, "primary_metric": "mrr@5"})))
check("artifact_digests ไม่ใช่ sha256 -> error",
      any("artifact_digests" in e for e in RP.validate_run_plan({**PLAN, "artifact_digests": {"eval_set_sha256": "x", "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H}})))
check("run_manifest_sha256 deterministic", RP.run_manifest_sha256(PLAN) == RP.run_manifest_sha256(PLAN))
check("run_manifest_sha256 invalid plan -> ValueError", raises(lambda: RP.run_manifest_sha256({**PLAN, "seed": None})))

# ── select_n (dev เท่านั้น) — เลือก N ต่ำสุดที่ผ่าน ─────────────────────────────
dev_by_n = {10: {"point_recall": 0.80, "doc_recall": 0.82, "candidate_hit": 0.9},
            20: {"point_recall": 0.96, "doc_recall": 0.97, "candidate_hit": 1.0},
            30: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0}}
check("select_n เลือก N ต่ำสุดที่ผ่าน (20)", RP.select_n(dev_by_n)["selected_n"] == 20)
check("select_n ไม่มี N ผ่าน -> CANDIDATE_GENERATION_LIMITED",
      RP.select_n({10: {"point_recall": 0.5, "doc_recall": 0.5, "candidate_hit": 0.5}})["status"] == "CANDIDATE_GENERATION_LIMITED")

# ── paired bootstrap (intent-level) ────────────────────────────────────────────
bs = RP.paired_bootstrap([0.05] * 20, seed=1)
check("bootstrap deltas คงที่ -> mean/CI = 0.05", abs(bs["mean_delta"] - 0.05) < 1e-9 and abs(bs["ci_lower"] - 0.05) < 1e-9)
bs2 = RP.paired_bootstrap([0.1, 0.0, 0.05, -0.02, 0.08, 0.03] * 5, seed=42)
check("bootstrap varied -> ci_lower <= mean <= ci_upper", bs2["ci_lower"] <= bs2["mean_delta"] <= bs2["ci_upper"])
check("bootstrap deterministic ตาม seed", RP.paired_bootstrap([0.1, 0.0, 0.05], seed=7) == RP.paired_bootstrap([0.1, 0.0, 0.05], seed=7))
check("bootstrap ว่าง -> ValueError", raises(lambda: RP.paired_bootstrap([])))

# ── per_intent grouping (paraphrase รวมเป็น 1 intent) ──────────────────────────
per_query = [{"intent_id": "i1", "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
             {"intent_id": "i1", "arms": {"dense": {"ndcg@5": 0.7}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
             {"intent_id": "i2", "arms": {"dense": {"ndcg@5": 1.0}, "rerank": {"ndcg@5": 0.8}, "fused": {"ndcg@5": 0.9}}}]
pin = RP.per_intent_ndcg(per_query, "dense")
check("per_intent_ndcg: i1 = mean(0.5,0.7)=0.6", abs(pin["i1"] - 0.6) < 1e-9 and len(pin) == 2)
deltas = RP.paired_deltas(per_query, "rerank", "dense")
check("paired_deltas ต่อ intent (i1: 0.9-0.6=0.3, i2: 0.8-1.0=-0.2)", sorted(round(d, 4) for d in deltas) == [-0.2, 0.3])

# ── decide_arm ─────────────────────────────────────────────────────────────────
good_hn = {"sibling": 0.01, "negation": 0.0}
check("decide: rerank Δ0.03 CI0.01, fused ไม่คุ้ม -> rerank",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0.005, "ci_lower": 0.0}, good_hn, good_hn)["arm"] == "rerank")
check("decide: fused Δ0.02 CI0.005 -> fused",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0.02, "ci_lower": 0.005}, good_hn, good_hn)["arm"] == "fused")
check("decide: rerank Δ0.01 (below min) CI-0.005 -> dense (non-inferior)",
      RP.decide_arm({"mean_delta": 0.01, "ci_lower": -0.005}, {"mean_delta": 0, "ci_lower": 0}, good_hn, good_hn)["arm"] == "dense")
check("decide: rerank CI -0.05 -> dense (ไม่ผ่าน)",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": -0.05}, {"mean_delta": 0, "ci_lower": 0}, good_hn, good_hn)["arm"] == "dense")
check("decide: hard-neg category -0.06 < floor -> dense (regression)",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {"sibling": -0.06}, good_hn)["arm"] == "dense")

# ── latency_summary ────────────────────────────────────────────────────────────
lat = RP.latency_summary({"candidate_retrieval": [10] * 20, "rerank": [100] * 20, "rrf": [1] * 20, "total": [150] * 20})
check("latency within budget (rerank 100<=1500, total 150<=2500, rrf 1<=10)", lat["within_budget"] is True)
lat2 = RP.latency_summary({"rerank": [2000] * 20, "total": [3000] * 20, "rrf": [1] * 20})
check("latency over budget -> within_budget False", lat2["within_budget"] is False)
check("latency p50/p95 ต่อ stage + ตัด warm-up", lat["rerank"]["p95"] == 100 and lat["rerank"]["n"] == 10)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
