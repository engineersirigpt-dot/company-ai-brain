"""
P2 run plan + decision analysis (M1) — pure/offline, pre-register ก่อนเห็นผลโมเดล
ให้ real runner (Slice 2) เพียงเติม observations จาก Qdrant/model — ลด post-hoc decision + rerun

- RunPlan immutable: split, N set {10,20,30,50}, seed, resamples, metrics/thresholds, expected counts,
  artifact digests (eval/corpus/index/model) → run_manifest_sha256
- select_n: เลือก N ต่ำสุดที่ CandidateRecall@N (point+doc) >= 0.95 และ CandidateHit@N = 1.00 **บน dev**
- paired_bootstrap: 95% CI ของ ΔnDCG@5 ระดับ intent (10,000 resamples, fixed seed) — group ตาม intent_id
- decide_arm: กติกา rerank/fused eligibility + hard-negative regression floor
- latency_summary: แยก stage, ตัด warm-up, p50/p95
"""
from __future__ import annotations
import hashlib
import json
import random
import re

import retrieval_metrics as M

N_SET = (10, 20, 30, 50)
RESAMPLES = 10000
DEFAULT_THRESHOLDS = {
    "candidate_recall": 0.95, "candidate_hit": 1.0,
    "min_delta_ndcg": 0.02, "ci_lower_min": 0.0, "noninferior_floor": -0.01,
    "fused_vs_rerank_min": 0.01, "hardneg_floor": -0.05,
    "rerank_p95_ms": 1500, "total_p95_ms": 2500, "rrf_p95_ms": 10,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(x) -> bool:
    return isinstance(x, str) and bool(_SHA256.match(x))


# ── RunPlan (immutable, pre-registered) ────────────────────────────────────────
def validate_run_plan(plan) -> list:
    """คืน list ของ error (ว่าง = valid). ต้อง pre-register ทุก field ก่อนรัน model"""
    if not isinstance(plan, dict):
        return ["run_plan ต้องเป็น dict"]
    errs = []
    if not isinstance(plan.get("run_id"), str) or not plan.get("run_id", "").strip():
        errs.append("run_id หาย/ผิดชนิด")
    if tuple(plan.get("n_set", ())) != N_SET:
        errs.append(f"n_set ต้องเป็น {N_SET}")
    if type(plan.get("seed")) is not int:
        errs.append("seed ต้องเป็น int (fixed, บันทึกก่อนรัน)")
    if type(plan.get("resamples")) is not int or plan.get("resamples", 0) < RESAMPLES:
        errs.append(f"resamples ต้อง >= {RESAMPLES}")
    if plan.get("primary_metric") != "ndcg@5":
        errs.append("primary_metric ต้องเป็น ndcg@5")
    if plan.get("intent_grouping") != "intent_id":
        errs.append("intent_grouping ต้องเป็น intent_id (ไม่นับ paraphrase แยก)")
    th = plan.get("thresholds")
    if not isinstance(th, dict) or any(k not in th for k in DEFAULT_THRESHOLDS):
        errs.append("thresholds ไม่ครบ")
    ec = plan.get("expected_counts")
    if not isinstance(ec, dict) or type(ec.get("test_intents")) is not int:
        errs.append("expected_counts.test_intents ต้องเป็น int")
    dg = plan.get("artifact_digests")
    if not isinstance(dg, dict) or not all(_is_sha256(dg.get(k)) for k in
                                           ("eval_set_sha256", "corpus_manifest_sha256", "retrieval_index_manifest_sha256")):
        errs.append("artifact_digests (eval/corpus/index) ต้องเป็น sha256 ครบ")
    return errs


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def run_manifest_sha256(plan) -> str:
    if validate_run_plan(plan):
        raise ValueError("run_plan ยัง invalid — สร้าง manifest hash ไม่ได้")
    return hashlib.sha256(_canonical(plan)).hexdigest()


# ── N selection (dev เท่านั้น) ─────────────────────────────────────────────────
def select_n(dev_by_n: dict, thresholds=DEFAULT_THRESHOLDS) -> dict:
    """
    dev_by_n = {N: {"point_recall":float, "doc_recall":float, "candidate_hit":float}} จาก dev
    เลือก N ต่ำสุดที่ผ่านทั้ง point+doc CandidateRecall และ CandidateHit=1.00 ; ไม่มี → candidate-limited
    """
    for n in sorted(dev_by_n):
        d = dev_by_n[n]
        if d.get("point_recall", 0) >= thresholds["candidate_recall"] \
                and d.get("doc_recall", 0) >= thresholds["candidate_recall"] \
                and d.get("candidate_hit", 0) == thresholds["candidate_hit"]:
            return {"selected_n": n, "status": "SELECTED"}
    return {"selected_n": None, "status": "CANDIDATE_GENERATION_LIMITED"}


# ── paired bootstrap CI (intent-level) ─────────────────────────────────────────
def per_intent_ndcg(per_query: list, arm: str) -> dict:
    """mean nDCG@5 ต่อ intent_id (รวม paraphrase เป็น 1 intent) — กันนับ paraphrase เป็น independent"""
    by = {}
    for q in per_query:
        v = q["arms"][arm]["ndcg@5"]
        if v is not None:
            by.setdefault(q["intent_id"], []).append(v)
    return {iid: sum(vs) / len(vs) for iid, vs in by.items()}


def paired_deltas(per_query: list, arm: str, baseline: str) -> list:
    a, b = per_intent_ndcg(per_query, arm), per_intent_ndcg(per_query, baseline)
    return [a[i] - b[i] for i in sorted(set(a) & set(b))]


def paired_bootstrap(deltas: list, resamples: int = RESAMPLES, seed: int = 0) -> dict:
    """paired bootstrap ระดับ intent — 95% percentile CI. deltas = ΔnDCG@5 ต่อ intent"""
    n = len(deltas)
    if n == 0:
        raise ValueError("ไม่มี paired intent สำหรับ bootstrap")
    rng = random.Random(seed)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return {"mean_delta": sum(deltas) / n, "ci_lower": means[int(0.025 * resamples)],
            "ci_upper": means[min(resamples - 1, int(0.975 * resamples))], "n_intents": n,
            "resamples": resamples, "seed": seed}


# ── arm decision ───────────────────────────────────────────────────────────────
def decide_arm(rerank_vs_dense: dict, fused_vs_rerank: dict,
               hardneg_rerank: dict, hardneg_fused: dict, thr=DEFAULT_THRESHOLDS) -> dict:
    """
    rerank แทน dense เมื่อ mean Δ>=min_delta และ CI lower>=0 และไม่มี hard-neg category < floor
    non-inferior (CI lower>=noninferior_floor แต่ไม่ถึง) → คง dense ; CI lower<floor → rerank ไม่ผ่าน
    fused แทน rerank เมื่อ +>=fused_vs_rerank_min และ CI lower>=0 และ hard-neg ผ่าน ; มิฉะนั้น arm ที่ง่ายกว่า
    """
    def hn_ok(hn):
        return all(v >= thr["hardneg_floor"] for v in hn.values())

    rerank_eligible = (rerank_vs_dense["mean_delta"] >= thr["min_delta_ndcg"]
                       and rerank_vs_dense["ci_lower"] >= thr["ci_lower_min"] and hn_ok(hardneg_rerank))
    if not rerank_eligible:
        reason = ("rerank non-inferior แต่ไม่คุ้ม complexity → คง dense"
                  if rerank_vs_dense["ci_lower"] >= thr["noninferior_floor"]
                  else "rerank ไม่ผ่าน (CI lower ต่ำ/hard-neg regression) → dense")
        return {"arm": "dense", "reason": reason}
    fused_eligible = (fused_vs_rerank["mean_delta"] >= thr["fused_vs_rerank_min"]
                      and fused_vs_rerank["ci_lower"] >= thr["ci_lower_min"] and hn_ok(hardneg_fused))
    return {"arm": "fused" if fused_eligible else "rerank",
            "reason": "fused ชนะ rerank อย่างมีสาระ" if fused_eligible else "rerank ชนะ dense; fused ไม่คุ้มเพิ่ม"}


# ── latency ────────────────────────────────────────────────────────────────────
def latency_summary(samples_ms: dict, warmup: int = 10, thr=DEFAULT_THRESHOLDS) -> dict:
    """
    samples_ms = {"candidate_retrieval":[...], "rerank":[...], "rrf":[...], "total":[...]} (ms)
    ตัด warm-up, p50/p95 ต่อ stage, ตรวจ budget. error/OOM ต้อง 0 (ส่งแยก)
    """
    out, ok = {}, True
    for stage, vals in samples_ms.items():
        xs = list(vals)[warmup:]
        p = M.percentiles(xs, (50, 95))
        out[stage] = {"p50": p[50], "p95": p[95], "n": len(xs)}
    budgets = {"rerank": thr["rerank_p95_ms"], "total": thr["total_p95_ms"], "rrf": thr["rrf_p95_ms"]}
    for stage, budget in budgets.items():
        p95 = out.get(stage, {}).get("p95")
        if p95 is None or p95 > budget:
            ok = False
    out["within_budget"] = ok
    return out
