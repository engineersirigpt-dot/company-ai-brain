"""
P2 run plan + decision analysis (M1) — pure/offline, pre-register ก่อนเห็นผลโมเดล
ให้ real runner (Slice 2) เพียงเติม observations จาก Qdrant/model — ลด post-hoc decision + rerun

Contract (fail-closed ต่อ evidence ที่ไม่ครบ/ไม่ pre-register):
- **root RunManifest** immutable: contract version, split/counts, N set {10,20,30,50}, seed, resamples,
  metrics/thresholds, eval/corpus/index digests, **model+tokenizer commit (full), model file-manifest,
  image digest, inference config** → run_manifest_sha256 (ผูกทุก evidence เข้ากับ run เดียว)
- select_n: **บน dev เท่านั้น** + ผูก run_manifest + N ต้องอยู่ใน N_SET ที่ pre-register + metric finite [0,1]
  + completed==expected ; เลือก N ต่ำสุดที่ CandidateRecall@N (point+doc) >= 0.95 และ CandidateHit@N = 1.00
- paired analysis: intent-set ต้องครบ/เท่ากันทุก arm (ห้ามตัด intent เงียบ) ; nDCG@5 ทุกค่า finite
- paired_bootstrap: 95% CI ของ ΔnDCG@5 ระดับ intent (10,000 resamples, fixed seed)
- decide_arm: rerank/fused eligibility + **ต้องมี hard-negative evidence ครบ gate categories** (ไม่ vacuous)
- latency: stage set/warm-up/count ต้องครบเท่ากันทุก stage + error/OOM=0 + digest ก่อนถือ within budget
- decide_p2: **entry point เดียว fail-closed** — คืน NOT_DECISION_ELIGIBLE ถ้า bundle ไม่ครบ, arm verdict เมื่อครบ
"""
from __future__ import annotations
import hashlib
import json
import math
import random
import re

import retrieval_metrics as M
import p2_eval as E
import p2_reranker as RK

N_SET = (10, 20, 30, 50)
RESAMPLES = 10000
ARMS = ("dense", "rerank", "fused")
LATENCY_STAGES = ("candidate_retrieval", "rerank", "rrf", "total")
BENCHMARK_CONTRACT_VERSION = E.BENCHMARK_CONTRACT_VERSION
DEFAULT_THRESHOLDS = {
    "candidate_recall": 0.95, "candidate_hit": 1.0,
    "min_delta_ndcg": 0.02, "ci_lower_min": 0.0, "noninferior_floor": -0.01,
    "fused_vs_rerank_min": 0.01, "hardneg_floor": -0.05,
    "rerank_p95_ms": 1500, "total_p95_ms": 2500, "rrf_p95_ms": 10,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FULL_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_COUNT_KEYS = ("dev_intents", "dev_queries", "test_intents", "test_queries")


def _is_sha256(x) -> bool:
    return isinstance(x, str) and bool(_SHA256.match(x))


def _is_full_commit(x) -> bool:
    return isinstance(x, str) and bool(_FULL_COMMIT.match(x))


def _is_finite_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_unit_float(x) -> bool:
    return _is_finite_number(x) and 0.0 <= x <= 1.0


def _exact_zero_int(x) -> bool:
    return type(x) is int and x == 0


# ── root RunManifest (immutable, pre-registered) ───────────────────────────────
def validate_run_plan(plan) -> list:
    """คืน list ของ error (ว่าง = valid). root manifest ต้อง pre-register + hash ทุก field ก่อนรัน model"""
    if not isinstance(plan, dict):
        return ["run_plan ต้องเป็น dict"]
    errs = []
    if not isinstance(plan.get("run_id"), str) or not plan.get("run_id", "").strip():
        errs.append("run_id หาย/ผิดชนิด")
    if plan.get("benchmark_contract_version") != BENCHMARK_CONTRACT_VERSION:
        errs.append(f"benchmark_contract_version ต้องเป็น {BENCHMARK_CONTRACT_VERSION}")
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
    if not isinstance(ec, dict) or any(type(ec.get(k)) is not int or ec.get(k, 0) < 1 for k in _COUNT_KEYS):
        errs.append(f"expected_counts ต้องมี {_COUNT_KEYS} เป็น positive int")
    dg = plan.get("artifact_digests")
    if not isinstance(dg, dict) or not all(_is_sha256(dg.get(k)) for k in
                                           ("eval_set_sha256", "corpus_manifest_sha256", "retrieval_index_manifest_sha256")):
        errs.append("artifact_digests (eval/corpus/index) ต้องเป็น sha256 ครบ")
    # B4: model/image/config binding — evidence จะประกอบข้าม model/image ไม่ได้ถ้า root ผูกไว้
    if not _is_full_commit(plan.get("model_commit")):
        errs.append("model_commit ต้องเป็น full immutable commit (40/64 hex)")
    if not _is_full_commit(plan.get("tokenizer_commit")):
        errs.append("tokenizer_commit ต้องเป็น full immutable commit (40/64 hex)")
    if not _is_sha256(plan.get("model_file_manifest_sha256")):
        errs.append("model_file_manifest_sha256 ต้องเป็น sha256")
    if not E._is_image_digest(plan.get("image_digest")):
        errs.append("image_digest ต้องเป็น sha256:<64hex>")
    ic = plan.get("inference_config")
    if not isinstance(ic, dict):
        errs.append("inference_config ต้องเป็น dict")
    else:
        mc = plan.get("model_commit")
        errs += [f"inference_config/{e}"
                 for e in RK.validate_pin(ic.get("model_name"), mc if isinstance(mc, str) else "",
                                          ic.get("max_length"), ic.get("batch_size"))]
        if not E._good_str(ic.get("device")):
            errs.append("inference_config.device ว่าง/ผิดชนิด")
        if not E._good_str(ic.get("dtype")):
            errs.append("inference_config.dtype ว่าง/ผิดชนิด")
    return errs


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def run_manifest_sha256(plan) -> str:
    if validate_run_plan(plan):
        raise ValueError("run_plan ยัง invalid — สร้าง manifest hash ไม่ได้")
    return hashlib.sha256(_canonical(plan)).hexdigest()


# ── N selection (dev เท่านั้น, ผูก root, N ∈ N_SET) ────────────────────────────
def validate_dev_evidence(dev_ev, run_manifest, expected_counts, thresholds=DEFAULT_THRESHOLDS) -> list:
    """dev N-sweep evidence ต้องผูก run เดียวกัน, split=dev, N ครบ N_SET, metric finite [0,1], count==expected"""
    if not isinstance(dev_ev, dict):
        return ["dev_evidence ต้องเป็น dict"]
    errs = []
    if dev_ev.get("split") != "dev":
        errs.append("dev_evidence split ต้องเป็น 'dev'")
    if dev_ev.get("run_manifest_sha256") != run_manifest:
        errs.append("dev_evidence run_manifest_sha256 ไม่ตรง root")
    if not _is_sha256(dev_ev.get("raw_result_digest")):
        errs.append("dev_evidence raw_result_digest ต้องเป็น sha256")
    by_n = dev_ev.get("by_n")
    if not isinstance(by_n, dict):
        errs.append("dev_evidence by_n ต้องเป็น dict")
        return errs
    if {k for k in by_n if type(k) is int} != set(N_SET):
        errs.append(f"dev_evidence by_n ต้องมี N = {N_SET} ครบ (exact int keys)")
    exp_q, exp_i = expected_counts.get("dev_queries"), expected_counts.get("dev_intents")
    for k in sorted(kk for kk in by_n if type(kk) is int):
        d, tag = by_n[k], f"dev N={k}"
        if not isinstance(d, dict):
            errs.append(f"{tag}: ไม่ใช่ dict")
            continue
        for m in ("point_recall", "doc_recall", "candidate_hit"):
            if not _is_unit_float(d.get(m)):
                errs.append(f"{tag}: {m} ต้อง finite ใน [0,1]")
        if type(d.get("completed_queries")) is not int or d.get("completed_queries") != exp_q:
            errs.append(f"{tag}: completed_queries != expected dev_queries ({exp_q})")
        if type(d.get("completed_intents")) is not int or d.get("completed_intents") != exp_i:
            errs.append(f"{tag}: completed_intents != expected dev_intents ({exp_i})")
    return errs


def select_n(dev_ev, run_manifest, expected_counts, thresholds=DEFAULT_THRESHOLDS) -> dict:
    """
    เลือก N ต่ำสุด **ใน N_SET** ที่ผ่าน CandidateRecall (point+doc>=0.95) และ CandidateHit=1.00 บน dev
    reject (ValueError) ถ้า dev_evidence ไม่ผูก root/ไม่ครบ N_SET/metric ไม่ finite/count ไม่ตรง
    """
    errs = validate_dev_evidence(dev_ev, run_manifest, expected_counts, thresholds)
    if errs:
        raise ValueError(f"dev_evidence invalid: {errs[:3]}")
    by_n = dev_ev["by_n"]
    for n in sorted(N_SET):                        # วนเฉพาะ N ที่ pre-register เท่านั้น
        d = by_n[n]
        if d["point_recall"] >= thresholds["candidate_recall"] \
                and d["doc_recall"] >= thresholds["candidate_recall"] \
                and d["candidate_hit"] == thresholds["candidate_hit"]:
            return {"selected_n": n, "status": "SELECTED"}
    return {"selected_n": None, "status": "CANDIDATE_GENERATION_LIMITED"}


# ── quality evidence + paired bootstrap CI (intent-level) ──────────────────────
def validate_quality_evidence(q_ev, run_manifest, expected_counts) -> list:
    """
    test quality evidence — ผูก root, split=test, ทุก case มี arms ครบ {dense,rerank,fused}
    ที่ nDCG@5 finite [0,1] ; query_id ไม่ซ้ำ ; intent/query count == expected (ห้ามหาย/เกิน)
    """
    if not isinstance(q_ev, dict):
        return ["quality_evidence ต้องเป็น dict"]
    errs = []
    if q_ev.get("split") != "test":
        errs.append("quality_evidence split ต้องเป็น 'test'")
    if q_ev.get("run_manifest_sha256") != run_manifest:
        errs.append("quality_evidence run_manifest_sha256 ไม่ตรง root")
    if not _is_sha256(q_ev.get("raw_result_digest")):
        errs.append("quality_evidence raw_result_digest ต้องเป็น sha256")
    pq = q_ev.get("per_query")
    if not isinstance(pq, list) or not pq:
        errs.append("quality_evidence per_query ว่าง/ไม่ใช่ list")
        return errs
    seen_qid, intents = set(), set()
    for i, row in enumerate(pq):
        tag = f"per_query[{i}]"
        if not isinstance(row, dict):
            errs.append(f"{tag}: ไม่ใช่ dict")
            continue
        qid = row.get("query_id")
        if not isinstance(qid, str) or not qid.strip():
            errs.append(f"{tag}: query_id ว่าง/ผิดชนิด")
        elif qid in seen_qid:
            errs.append(f"{tag}: query_id ซ้ำ {qid}")
        else:
            seen_qid.add(qid)
        iid = row.get("intent_id")
        if not isinstance(iid, str) or not iid.strip():
            errs.append(f"{tag}: intent_id ว่าง/ผิดชนิด")
        else:
            intents.add(iid)
        arms = row.get("arms")
        if not isinstance(arms, dict) or set(arms) != set(ARMS):
            errs.append(f"{tag}: arms ต้องมี {ARMS} ครบ (exact)")
        else:
            for a in ARMS:
                v = arms[a].get("ndcg@5") if isinstance(arms[a], dict) else None
                if not _is_unit_float(v):
                    errs.append(f"{tag}: {a} ndcg@5 ต้อง finite ใน [0,1]")
    exp_q, exp_i = expected_counts.get("test_queries"), expected_counts.get("test_intents")
    if type(exp_q) is int and len(pq) != exp_q:
        errs.append(f"per_query count {len(pq)} != expected test_queries {exp_q}")
    if type(exp_i) is int and len(intents) != exp_i:
        errs.append(f"intent count {len(intents)} != expected test_intents {exp_i}")
    return errs


def per_intent_ndcg(per_query: list, arm: str) -> dict:
    """mean nDCG@5 ต่อ intent_id (รวม paraphrase เป็น 1 intent) — ห้ามมี None (evidence ต้องครบ)"""
    by = {}
    for q in per_query:
        v = q["arms"][arm]["ndcg@5"]
        if v is None:
            raise ValueError(f"per_intent_ndcg: {arm} ndcg@5 เป็น None (evidence ไม่ครบ)")
        by.setdefault(q["intent_id"], []).append(v)
    return {iid: sum(vs) / len(vs) for iid, vs in by.items()}


def paired_deltas(per_query: list, arm: str, baseline: str) -> list:
    a, b = per_intent_ndcg(per_query, arm), per_intent_ndcg(per_query, baseline)
    if set(a) != set(b):
        raise ValueError("paired_deltas: intent set ของ arm/baseline ไม่ตรง (ห้ามตัด intent เงียบ)")
    return [a[i] - b[i] for i in sorted(a)]


def paired_bootstrap(deltas: list, resamples: int = RESAMPLES, seed: int = 0) -> dict:
    """paired bootstrap ระดับ intent — 95% percentile CI. deltas = ΔnDCG@5 ต่อ intent (ต้อง finite ทุกค่า)"""
    if not isinstance(deltas, list) or not deltas:
        raise ValueError("ไม่มี paired intent สำหรับ bootstrap")
    if not all(_is_finite_number(d) for d in deltas):
        raise ValueError("paired_bootstrap: deltas ต้อง finite ทุกค่า (reject NaN/Inf/partial)")
    if type(resamples) is not int or resamples < RESAMPLES:
        raise ValueError(f"resamples ต้องเป็น int >= {RESAMPLES}")
    if type(seed) is not int:
        raise ValueError("seed ต้องเป็น int (exact, จาก RunPlan)")
    n = len(deltas)
    rng = random.Random(seed)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return {"mean_delta": sum(deltas) / n, "ci_lower": means[int(0.025 * resamples)],
            "ci_upper": means[min(resamples - 1, int(0.975 * resamples))], "n_intents": n,
            "resamples": resamples, "seed": seed}


# ── arm decision (hard-negative evidence ต้องครบ gate categories) ──────────────
def decide_arm(rerank_vs_dense: dict, fused_vs_rerank: dict,
               hardneg_rerank: dict, hardneg_fused: dict,
               required_hardneg=(), thr=DEFAULT_THRESHOLDS) -> dict:
    """
    rerank แทน dense เมื่อ mean Δ>=min_delta และ CI lower>=0 และ hard-neg evidence ครบ+ไม่ต่ำกว่า floor
    (hard-neg dict ว่าง หรือ ขาด gate category ที่ required = ไม่ผ่าน — ไม่ใช่ vacuous True)
    fused แทน rerank เมื่อ +>=fused_vs_rerank_min และ CI lower>=0 และ hard-neg ผ่าน ; มิฉะนั้น arm ที่ง่ายกว่า
    """
    req = set(required_hardneg)

    def hn_ok(hn):
        if not isinstance(hn, dict) or not hn or not req.issubset(hn):
            return False
        return all(_is_finite_number(v) and v >= thr["hardneg_floor"] for v in hn.values())

    rvd_ok = _is_finite_number(rerank_vs_dense.get("mean_delta")) and _is_finite_number(rerank_vs_dense.get("ci_lower"))
    rerank_eligible = (rvd_ok
                       and rerank_vs_dense["mean_delta"] >= thr["min_delta_ndcg"]
                       and rerank_vs_dense["ci_lower"] >= thr["ci_lower_min"]
                       and hn_ok(hardneg_rerank))
    if not rerank_eligible:
        ci = rerank_vs_dense.get("ci_lower")
        reason = ("rerank non-inferior แต่ไม่คุ้ม complexity → คง dense"
                  if _is_finite_number(ci) and ci >= thr["noninferior_floor"] and hn_ok(hardneg_rerank)
                  else "rerank ไม่ผ่าน (CI ต่ำ / hard-neg ขาดหรือ regression) → dense")
        return {"arm": "dense", "reason": reason}
    fvr_ok = _is_finite_number(fused_vs_rerank.get("mean_delta")) and _is_finite_number(fused_vs_rerank.get("ci_lower"))
    fused_eligible = (fvr_ok
                      and fused_vs_rerank["mean_delta"] >= thr["fused_vs_rerank_min"]
                      and fused_vs_rerank["ci_lower"] >= thr["ci_lower_min"]
                      and hn_ok(hardneg_fused))
    return {"arm": "fused" if fused_eligible else "rerank",
            "reason": "fused ชนะ rerank อย่างมีสาระ" if fused_eligible else "rerank ชนะ dense; fused ไม่คุ้มเพิ่ม"}


# ── latency (stage set/count/error ต้องครบก่อน within budget) ──────────────────
def validate_latency_evidence(lat_ev, run_manifest, expected_count, thr=DEFAULT_THRESHOLDS) -> list:
    """stage ครบ exact + finite non-negative + post-warmup count เท่ากันทุก stage == expected + error/OOM=0 + digest"""
    if not isinstance(lat_ev, dict):
        return ["latency_evidence ต้องเป็น dict"]
    errs = []
    if lat_ev.get("run_manifest_sha256") != run_manifest:
        errs.append("latency run_manifest_sha256 ไม่ตรง root")
    if not _is_sha256(lat_ev.get("raw_latency_digest")):
        errs.append("latency raw_latency_digest ต้องเป็น sha256")
    if not _exact_zero_int(lat_ev.get("error_count")):
        errs.append("latency error_count ต้อง 0 (exact int)")
    if not _exact_zero_int(lat_ev.get("oom_count")):
        errs.append("latency oom_count ต้อง 0 (exact int)")
    warm = lat_ev.get("warmup")
    if type(warm) is not int or warm < 0:
        errs.append("latency warmup ต้องเป็น non-negative exact int")
    if type(expected_count) is not int or expected_count < 1:
        errs.append("expected_count ต้องเป็น positive int")
    stages = lat_ev.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(LATENCY_STAGES):
        errs.append(f"latency stages ต้องมี {LATENCY_STAGES} ครบ (exact)")
        return errs
    for st in LATENCY_STAGES:
        xs = stages.get(st)
        if not isinstance(xs, list) or not all(_is_finite_number(v) and v >= 0 for v in xs):
            errs.append(f"latency {st} ต้องเป็น list ของ finite non-negative")
            continue
        if type(warm) is int and warm >= 0 and type(expected_count) is int:
            if len(xs) - warm != expected_count:
                errs.append(f"latency {st}: post-warmup count {len(xs) - warm} != expected {expected_count}")
    return errs


def latency_summary(lat_ev, run_manifest, expected_count, thr=DEFAULT_THRESHOLDS) -> dict:
    """p50/p95 ต่อ stage (ตัด warm-up) + within_budget — reject (ValueError) ถ้า evidence ไม่ครบ/ไม่เท่ากัน"""
    errs = validate_latency_evidence(lat_ev, run_manifest, expected_count, thr)
    if errs:
        raise ValueError(f"latency_evidence invalid: {errs[:3]}")
    warm, out = lat_ev["warmup"], {}
    for st in LATENCY_STAGES:
        xs = list(lat_ev["stages"][st])[warm:]
        p = M.percentiles(xs, (50, 95))
        out[st] = {"p50": p[50], "p95": p[95], "n": len(xs)}
    budgets = {"rerank": thr["rerank_p95_ms"], "total": thr["total_p95_ms"], "rrf": thr["rrf_p95_ms"]}
    ok = all(out[st]["p95"] is not None and out[st]["p95"] <= budget for st, budget in budgets.items())
    out["within_budget"] = ok
    return out


# ── single fail-closed decision entry point ────────────────────────────────────
def _not_eligible(reasons) -> dict:
    return {"status": "NOT_DECISION_ELIGIBLE", "decision_eligible": False, "arm": None, "reasons": reasons}


def _hardneg_complete(hn, gate_tags) -> bool:
    """hard-neg evidence ต้องครอบทุก frozen gate category ด้วยค่า finite (ว่าง/ขาด = ไม่ครบ)"""
    if not isinstance(hn, dict) or not set(gate_tags).issubset(hn):
        return False
    return all(_is_finite_number(v) for v in hn.values())


def decide_p2(plan, dev_evidence, quality_evidence, latency_evidence,
              hardneg_rerank, hardneg_fused, m4_evidence, canary_evidence, signoff,
              cases, corpus, known_roles, evaluated_roles, gate_tags,
              thr=DEFAULT_THRESHOLDS) -> dict:
    """
    B3/B4: **decision entry point เดียว** — fail-closed. คืน arm verdict ก็ต่อเมื่อ bundle ครบทุกด่าน:
      valid RunPlan → selected N (dev, ∈N_SET) → quality (test, intent/arm ครบ) → paired CI (seed/resamples จาก plan)
      → latency (stage/count/error ครบ, within budget สำหรับ arm ที่ไม่ใช่ dense)
      → hard-neg gate ครบ → M4 PASS + canary PASS (ผูก root + model/image เดียวกัน) → Data Owner sign-off
    ทุกกรณีที่ไม่ครบ → NOT_DECISION_ELIGIBLE (ไม่มีเส้นทางคืน arm จาก partial bundle)
    """
    plan_errs = validate_run_plan(plan)
    if plan_errs:
        return _not_eligible(["run_plan invalid"] + plan_errs)
    root = run_manifest_sha256(plan)
    ec = plan["expected_counts"]

    try:
        sel = select_n(dev_evidence, root, ec, thr)
    except ValueError as e:
        return _not_eligible([f"dev_evidence: {e}"])
    if sel["status"] != "SELECTED":
        return _not_eligible([f"N selection: {sel['status']}"])

    q_errs = validate_quality_evidence(quality_evidence, root, ec)
    if q_errs:
        return _not_eligible(["quality_evidence invalid"] + q_errs[:5])
    pq = quality_evidence["per_query"]
    try:
        rvd = paired_bootstrap(paired_deltas(pq, "rerank", "dense"), plan["resamples"], plan["seed"])
        fvr = paired_bootstrap(paired_deltas(pq, "fused", "rerank"), plan["resamples"], plan["seed"])
    except ValueError as e:
        return _not_eligible([f"paired analysis: {e}"])

    lat_errs = validate_latency_evidence(latency_evidence, root, ec["test_queries"], thr)
    if lat_errs:
        return _not_eligible(["latency invalid"] + lat_errs[:5])
    lat = latency_summary(latency_evidence, root, ec["test_queries"], thr)

    # B3: hard-neg evidence ต้องครบ frozen gate categories ก่อนตัด arm (ว่าง/ขาด = decision ไม่ eligible)
    for name, hn in (("rerank", hardneg_rerank), ("fused", hardneg_fused)):
        if not _hardneg_complete(hn, gate_tags):
            return _not_eligible([f"hard-negative evidence ({name}) ไม่ครบ frozen gate categories {list(gate_tags)}"])

    arm = decide_arm(rvd, fvr, hardneg_rerank, hardneg_fused, required_hardneg=gate_tags, thr=thr)
    if arm["arm"] in ("rerank", "fused") and not lat["within_budget"]:
        return _not_eligible([f"latency over budget สำหรับ arm {arm['arm']} → arm นี้เลือกไม่ได้"])

    # B4: model/image ของ evidence ต้องตรง root plan ก่อนเข้า evidence gate
    binding = []
    for name, ev in (("m4", m4_evidence), ("canary", canary_evidence)):
        if not isinstance(ev, dict):
            binding.append(f"{name}_evidence หาย/ผิดชนิด")
            continue
        if ev.get("model_revision") != plan["model_commit"]:
            binding.append(f"{name} model_revision != root model_commit")
        if ev.get("image_digest") != plan["image_digest"]:
            binding.append(f"{name} image_digest != root image_digest")
    if binding:
        return _not_eligible(binding)

    try:
        bench = E.decision_benchmark_manifest(cases, corpus, known_roles, evaluated_roles,
                                              gate_tags, signoff, m4_evidence, canary_evidence,
                                              run_manifest_sha256=root)
    except ValueError as e:
        return _not_eligible([f"decision benchmark gate: {e}"])

    return {
        "status": "DECISION", "decision_eligible": True,
        "arm": arm["arm"], "reason": arm["reason"], "selected_n": sel["selected_n"],
        "run_manifest_sha256": root,
        "rerank_vs_dense": rvd, "fused_vs_rerank": fvr, "latency": lat, "benchmark": bench,
    }
