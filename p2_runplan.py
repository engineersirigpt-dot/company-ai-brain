"""
P2 run plan + decision analysis (M1) — pure/offline, pre-register ก่อนเห็นผลโมเดล
ให้ real runner (Slice 2) เพียงเติม observations จาก Qdrant/model — ลด post-hoc decision + rerun

Contract (fail-closed ; ค่าที่ hash ใน RunPlan คือค่า **บังคับจริง** ตอนตัดสิน):
- **root RunManifest** immutable + hashed: contract version, split/counts, N set {10,20,30,50}, seed,
  resamples, primary metric, intent grouping, **thresholds (schema+range), frozen gate_tags, evaluated_roles**,
  eval/corpus/index digests, **full model+tokenizer commit, model file-manifest, image digest, inference config**
- decide_p2 ใช้ threshold/gate/role จาก plan เท่านั้น (ไม่มี override ผ่าน argument)
- root artifact/model digests ถูก **เทียบกับ artifact จริง + evidence metadata จริง** ก่อน analysis
- select_n → **SelectionManifest digest** (root + dev-result digest + selected N) ที่ quality/latency/M4/canary ต้องอ้าง
- raw digests ถูก **recompute จาก evidence body** (ไม่ใช่ตรวจแค่ 64-hex)
- hard-negative deltas ถูก **derive จาก per-query rows** ที่ผูกไว้ (ไม่รับ naked dict)
- **decide_p2 = public approval surface เดียว** ที่คืน approved=True ; ทุก bundle ไม่ครบ → NOT_DECISION_ELIGIBLE
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


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def raw_digest(obj) -> str:
    """canonical sha256 ของ evidence body — ให้ recompute เทียบ payload ได้ (ไม่ใช่แค่ตรวจ 64-hex)"""
    return hashlib.sha256(_canonical(obj)).hexdigest()


def _safe_digest(obj):
    """recompute digest แบบไม่ crash — body ที่ canonicalize ไม่ได้ (NaN/mixed-key/surrogate) คืน None"""
    try:
        return raw_digest(obj)
    except (TypeError, ValueError):
        return None


# ── root RunManifest (immutable, pre-registered, authoritative) ────────────────
def _valid_thresholds(th) -> bool:
    """
    schema เป๊ะ + finite + **domain/relationship ของ metric** (ไม่ใช่แค่ +/-) — ค่าที่ hash ต้องมีความหมาย:
    - CandidateRecall/Hit target = frozen P2 acceptance (0.95 / 1.0 exact) — ห้าม preregister ค่าที่อ่อนกว่า
    - nDCG delta/CI/floor อยู่ในโดเมนของ metric ([-1,1]/[0,1]/[-1,0]) + noninferior_floor <= ci_lower_min
    - latency budget positive + เรียง rrf <= rerank <= total
    """
    if not isinstance(th, dict) or set(th) != set(DEFAULT_THRESHOLDS):
        return False
    if not all(_is_finite_number(v) for v in th.values()):
        return False
    # frozen acceptance targets (P2) — lock exact กัน preregister policy ที่อ่อนกว่า
    if th["candidate_recall"] != 0.95 or th["candidate_hit"] != 1.0:
        return False
    # nDCG-delta knobs ต้องอยู่ในโดเมนของ metric (nDCG delta ∈ [-1,1])
    if not (0.0 <= th["min_delta_ndcg"] <= 1.0 and 0.0 <= th["fused_vs_rerank_min"] <= 1.0):
        return False
    if not (-1.0 <= th["ci_lower_min"] <= 1.0):
        return False
    if not (-1.0 <= th["noninferior_floor"] <= 0.0 and -1.0 <= th["hardneg_floor"] <= 0.0):
        return False
    if th["noninferior_floor"] > th["ci_lower_min"]:
        return False
    # latency budget (ms) — positive + เรียง rrf <= rerank <= total
    rr, r, tot = th["rrf_p95_ms"], th["rerank_p95_ms"], th["total_p95_ms"]
    if not (0 < rr <= r <= tot):
        return False
    return True


def _valid_str_list(x) -> bool:
    return isinstance(x, list) and len(x) >= 1 and all(E._good_str(s) for s in x) and len(set(x)) == len(x)


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
    # B1: threshold schema+range เป็น authoritative + gate_tags/evaluated_roles frozen ใน plan
    if not _valid_thresholds(plan.get("thresholds")):
        errs.append("thresholds ต้องครบ schema + finite + ช่วง/เครื่องหมายถูกต้อง")
    if not _valid_str_list(plan.get("gate_tags")):
        errs.append("gate_tags ต้องเป็น list ของ str ไม่ว่าง/ไม่ซ้ำ (frozen — reject empty)")
    if not _valid_str_list(plan.get("evaluated_roles")):
        errs.append("evaluated_roles ต้องเป็น list ของ str ไม่ว่าง/ไม่ซ้ำ (frozen)")
    # B2: freeze M4 case/visibility manifest binding ใน root (กัน M4b รันคนละ case/role/visibility)
    if not _is_sha256(plan.get("m4_case_manifest_sha256")):
        errs.append("m4_case_manifest_sha256 ต้องเป็น sha256 (frozen M4 manifest binding)")
    if not _valid_str_list(plan.get("required_categories")):
        errs.append("required_categories ต้องเป็น list ของ str ไม่ว่าง/ไม่ซ้ำ (frozen)")
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


def run_manifest_sha256(plan) -> str:
    if validate_run_plan(plan):
        raise ValueError("run_plan ยัง invalid — สร้าง manifest hash ไม่ได้")
    return hashlib.sha256(_canonical(plan)).hexdigest()


# ── N selection (dev เท่านั้น, ผูก root, N ∈ N_SET, exact int keys) ─────────────
def validate_dev_evidence(dev_ev, run_manifest, expected_counts, thresholds=DEFAULT_THRESHOLDS) -> list:
    """dev N-sweep evidence ต้องผูก run เดียวกัน, split=dev, N keys == N_SET (int ทั้งหมด), metric finite [0,1],
    count==expected, raw_result_digest == recompute จาก by_n"""
    if not isinstance(dev_ev, dict):
        return ["dev_evidence ต้องเป็น dict"]
    errs = []
    if dev_ev.get("split") != "dev":
        errs.append("dev_evidence split ต้องเป็น 'dev'")
    if dev_ev.get("run_manifest_sha256") != run_manifest:
        errs.append("dev_evidence run_manifest_sha256 ไม่ตรง root")
    by_n = dev_ev.get("by_n")
    if not isinstance(by_n, dict):
        errs.append("dev_evidence by_n ต้องเป็น dict")
        return errs
    # M1: exact key set — reject key เช่น "10"/False/extra
    if not (all(type(k) is int for k in by_n) and set(by_n) == set(N_SET)):
        errs.append(f"dev_evidence by_n keys ต้อง == {N_SET} (int ทั้งหมด, ไม่มี extra/สตริง)")
    exp = _safe_digest(by_n)
    if exp is None:
        errs.append("dev_evidence by_n ไม่ canonicalizable (malformed)")
    elif dev_ev.get("raw_result_digest") != exp:
        errs.append("dev_evidence raw_result_digest ไม่ตรง recompute จาก by_n")
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
    เลือก N ต่ำสุด **ใน N_SET** ที่ผ่าน CandidateRecall (point+doc>=recall) และ CandidateHit=hit บน dev
    reject (ValueError) ถ้า dev_evidence ไม่ผูก root/ไม่ครบ N_SET/metric ไม่ finite/count/digest ไม่ตรง
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


def selection_digest(run_manifest, dev_raw_result_digest, selected_n) -> str:
    """SelectionManifest digest — ผูกผล test/latency/M4/canary เข้ากับ N ที่เลือกจาก dev เท่านั้น"""
    return raw_digest({"run_manifest_sha256": run_manifest,
                       "dev_raw_result_digest": dev_raw_result_digest, "selected_n": selected_n})


# ── quality evidence + paired bootstrap CI (intent-level) ──────────────────────
def validate_quality_evidence(q_ev, run_manifest, expected_counts,
                              sel_digest=None, selected_n=None) -> list:
    """
    test quality evidence — ผูก root + (ถ้ามี) selection digest/selected_n, split=test,
    ทุก case มี arms ครบ {dense,rerank,fused} finite [0,1] + challenge_tags (ไว้ derive hard-neg) ;
    query_id ไม่ซ้ำ ; intent/query count == expected ; raw_result_digest == recompute จาก per_query
    """
    if not isinstance(q_ev, dict):
        return ["quality_evidence ต้องเป็น dict"]
    errs = []
    if q_ev.get("split") != "test":
        errs.append("quality_evidence split ต้องเป็น 'test'")
    if q_ev.get("run_manifest_sha256") != run_manifest:
        errs.append("quality_evidence run_manifest_sha256 ไม่ตรง root")
    if sel_digest is not None and q_ev.get("selection_digest") != sel_digest:
        errs.append("quality_evidence selection_digest ไม่ตรง N ที่เลือก")
    if selected_n is not None and q_ev.get("selected_n") != selected_n:
        errs.append("quality_evidence selected_n ไม่ตรง N ที่เลือก")
    pq = q_ev.get("per_query")
    if not isinstance(pq, list) or not pq:
        errs.append("quality_evidence per_query ว่าง/ไม่ใช่ list")
        return errs
    exp = _safe_digest(pq)
    if exp is None:
        errs.append("quality_evidence per_query ไม่ canonicalizable (malformed)")
    elif q_ev.get("raw_result_digest") != exp:
        errs.append("quality_evidence raw_result_digest ไม่ตรง recompute จาก per_query")
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
        ct = row.get("challenge_tags")
        if not isinstance(ct, list) or not ct or any(not E._good_str(t) for t in ct):
            errs.append(f"{tag}: challenge_tags ว่าง/ผิดชนิด (ต้องมีไว้ derive hard-neg)")
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


def derive_hardneg_deltas(per_query: list, arm: str, baseline: str, categories) -> dict:
    """
    B4: derive ΔnDCG@5 ต่อ hard-neg category จาก per-query rows ที่ผูกไว้ (ไม่รับ naked dict)
    ต่อ category: จับ intent ที่ challenge_tags มี category → per-intent mean → mean ของ delta
    raise ถ้า category ใดไม่มี evidence เลย (bundle ไม่ครบ)
    """
    out = {}
    for cat in categories:
        arm_by, base_by = {}, {}
        for row in per_query:
            if cat in row.get("challenge_tags", []):
                arm_by.setdefault(row["intent_id"], []).append(row["arms"][arm]["ndcg@5"])
                base_by.setdefault(row["intent_id"], []).append(row["arms"][baseline]["ndcg@5"])
        if not arm_by:
            raise ValueError(f"hard-neg category {cat!r} ไม่มี evidence ใน per_query")
        deltas = [sum(arm_by[i]) / len(arm_by[i]) - sum(base_by[i]) / len(base_by[i]) for i in arm_by]
        out[cat] = sum(deltas) / len(deltas)
    return out


# ── arm decision (hard-negative deltas ต้อง derive มาแล้ว + ครบ gate categories) ─
def decide_arm(rerank_vs_dense: dict, fused_vs_rerank: dict,
               hardneg_rerank: dict, hardneg_fused: dict,
               required_hardneg=(), thr=DEFAULT_THRESHOLDS) -> dict:
    """
    rerank แทน dense เมื่อ mean Δ>=min_delta และ CI lower>=0 และ hard-neg ครบ required+ไม่ต่ำกว่า floor
    (hard-neg dict ว่าง หรือ ขาด gate category = ไม่ผ่าน — ไม่ vacuous True)
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
def validate_latency_evidence(lat_ev, run_manifest, expected_count, thr=DEFAULT_THRESHOLDS,
                              sel_digest=None, selected_n=None) -> list:
    """stage ครบ exact + finite non-negative + post-warmup count เท่ากันทุก stage == expected + error/OOM=0
    + raw_latency_digest == recompute จาก stages + (ถ้ามี) ผูก selection digest/selected_n"""
    if not isinstance(lat_ev, dict):
        return ["latency_evidence ต้องเป็น dict"]
    errs = []
    if lat_ev.get("run_manifest_sha256") != run_manifest:
        errs.append("latency run_manifest_sha256 ไม่ตรง root")
    if sel_digest is not None and lat_ev.get("selection_digest") != sel_digest:
        errs.append("latency selection_digest ไม่ตรง N ที่เลือก")
    if selected_n is not None and lat_ev.get("selected_n") != selected_n:
        errs.append("latency selected_n ไม่ตรง N ที่เลือก")
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
    exp = _safe_digest(stages)
    if exp is None:
        errs.append("latency stages ไม่ canonicalizable (malformed)")
    elif lat_ev.get("raw_latency_digest") != exp:
        errs.append("latency raw_latency_digest ไม่ตรง recompute จาก stages")
    for st in LATENCY_STAGES:
        xs = stages.get(st)
        if not isinstance(xs, list) or not all(_is_finite_number(v) and v >= 0 for v in xs):
            errs.append(f"latency {st} ต้องเป็น list ของ finite non-negative")
            continue
        if type(warm) is int and warm >= 0 and type(expected_count) is int:
            if len(xs) - warm != expected_count:
                errs.append(f"latency {st}: post-warmup count {len(xs) - warm} != expected {expected_count}")
    return errs


def latency_summary(lat_ev, run_manifest, expected_count, thr=DEFAULT_THRESHOLDS,
                    sel_digest=None, selected_n=None) -> dict:
    """p50/p95 ต่อ stage (ตัด warm-up) + within_budget — reject (ValueError) ถ้า evidence ไม่ครบ/ไม่เท่ากัน"""
    errs = validate_latency_evidence(lat_ev, run_manifest, expected_count, thr, sel_digest, selected_n)
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


# ── single fail-closed decision entry point (public approval surface เดียว) ────
def _not_eligible(reasons) -> dict:
    return {"status": "NOT_DECISION_ELIGIBLE", "decision_eligible": False, "arm": None, "reasons": reasons}


def _resolve_quality_rows(per_query, cases):
    """
    M1: join quality rows กับ frozen test cases ด้วย query_id — quality ต้องเป็นผลของ frozen eval queries จริง
    บังคับ exact query-id set + intent_id/role/challenge_tags ตรง case เดิม (ไม่เชื่อ identity/tag จาก evidence)
    คืน (resolved_rows | None, errors). analysis ใช้ identity/tags จาก frozen cases เท่านั้น
    """
    test_by_qid = {c["query_id"]: c for c in cases
                   if isinstance(c, dict) and c.get("split") == "test" and isinstance(c.get("query_id"), str)}
    row_qids = [r.get("query_id") for r in per_query]
    if set(row_qids) != set(test_by_qid):
        missing = sorted(set(test_by_qid) - set(row_qids))
        extra = sorted(str(q) for q in set(row_qids) - set(test_by_qid))
        return None, [f"quality query-id set != frozen test cases (missing={missing[:3]} extra={extra[:3]})"]
    errs, resolved = [], []
    for r in per_query:
        c = test_by_qid[r["query_id"]]
        if r.get("intent_id") != c.get("intent_id"):
            errs.append(f"quality {r['query_id']}: intent_id ไม่ตรง frozen case")
        if r.get("role") != c.get("role"):
            errs.append(f"quality {r['query_id']}: role ไม่ตรง frozen case")
        if set(r.get("challenge_tags") or []) != set(c.get("challenge_tags") or []):
            errs.append(f"quality {r['query_id']}: challenge_tags ไม่ตรง frozen case")
        resolved.append({"query_id": r["query_id"], "intent_id": c["intent_id"], "role": c.get("role"),
                         "challenge_tags": list(c.get("challenge_tags") or []), "arms": r["arms"]})
    if errs:
        return None, errs
    return resolved, []


def _root_binding_errors(plan, cases, corpus, m4, canary, sel_digest) -> list:
    """
    B2/B4: root artifact/model digests ต้องตรง artifact จริง + evidence metadata จริง (ไม่ใช่แค่รู้ root hash)
    """
    errs, dg = [], plan["artifact_digests"]
    try:
        if E.eval_set_sha256(cases) != dg["eval_set_sha256"]:
            errs.append("root eval_set_sha256 ไม่ตรง cases จริง")
        if E.corpus_manifest_sha256(corpus) != dg["corpus_manifest_sha256"]:
            errs.append("root corpus_manifest_sha256 ไม่ตรง corpus จริง")
    except (ValueError, TypeError, AttributeError) as e:
        return [f"artifacts hash ไม่ได้ (malformed): {type(e).__name__}"]
    idx = dg["retrieval_index_manifest_sha256"]
    checks = {
        "retrieval_index_manifest_sha256": idx,
        "model_revision": plan["model_commit"], "tokenizer_revision": plan["tokenizer_commit"],
        "model_file_manifest_sha256": plan["model_file_manifest_sha256"], "image_digest": plan["image_digest"],
        "inference_config": plan["inference_config"], "selection_digest": sel_digest,
    }
    for field, want in checks.items():
        if m4.get(field) != want:
            errs.append(f"m4 {field} ไม่ตรง root/selection ({field})")
    # canary bind: index + model + image + selection (metadata ชุดเดียวกับ M4/root)
    for field, want in (("retrieval_index_manifest_sha256", idx), ("model_revision", plan["model_commit"]),
                        ("image_digest", plan["image_digest"]), ("selection_digest", sel_digest)):
        if canary.get(field) != want:
            errs.append(f"canary {field} ไม่ตรง root/selection ({field})")
    return errs


def decide_p2(plan, dev_evidence, quality_evidence, latency_evidence,
              m4_evidence, canary_evidence, signoff, cases, corpus, known_roles, m4_frozen_manifest) -> dict:
    """
    **decision entry point เดียว** — fail-closed + authoritative. คืน DECISION (approved) ก็ต่อเมื่อ:
      valid RunPlan → root/model digests ตรง artifact+evidence จริง → selected N (dev,∈N_SET) →
      quality/latency ผูก SelectionManifest (N เดียวกัน) + raw digest ตรง body → paired CI (seed/resamples จาก plan)
      → hard-neg deltas derive จาก per-query ครบ gate categories → arm ผ่าน + latency within budget (arm≠dense)
      → M4 PASS + canary PASS (ผูก root) → Data Owner sign-off
    threshold/gate_tags/evaluated_roles อ่านจาก plan เท่านั้น (ไม่มี override) ; partial bundle → NOT_DECISION_ELIGIBLE
    """
    plan_errs = validate_run_plan(plan)
    if plan_errs:
        return _not_eligible(["run_plan invalid"] + plan_errs)
    root = run_manifest_sha256(plan)
    thr = plan["thresholds"]
    gate_tags = plan["gate_tags"]
    evaluated_roles = plan["evaluated_roles"]
    ec = plan["expected_counts"]

    # B2: root eval/corpus ต้องตรง artifact จริงก่อน (กัน DECISION จาก root hash ที่ไม่ตรงของจริง)
    try:
        eval_hash = E.eval_set_sha256(cases)
        corpus_hash = E.corpus_manifest_sha256(corpus)
    except (ValueError, TypeError, AttributeError) as e:
        return _not_eligible([f"artifacts hash ไม่ได้ (malformed): {type(e).__name__}"])
    if eval_hash != plan["artifact_digests"]["eval_set_sha256"]:
        return _not_eligible(["root eval_set_sha256 ไม่ตรง cases จริง"])
    if corpus_hash != plan["artifact_digests"]["corpus_manifest_sha256"]:
        return _not_eligible(["root corpus_manifest_sha256 ไม่ตรง corpus จริง"])

    try:
        sel = select_n(dev_evidence, root, ec, thr)
    except ValueError as e:
        return _not_eligible([f"dev_evidence: {e}"])
    if sel["status"] != "SELECTED":
        return _not_eligible([f"N selection: {sel['status']}"])
    selected_n = sel["selected_n"]
    sel_digest = selection_digest(root, dev_evidence["raw_result_digest"], selected_n)

    q_errs = validate_quality_evidence(quality_evidence, root, ec, sel_digest, selected_n)
    if q_errs:
        return _not_eligible(["quality_evidence invalid"] + q_errs[:5])
    # M1: quality rows ต้องเป็นผลของ frozen eval queries จริง (join by query_id, identity/tags จาก cases)
    resolved, join_errs = _resolve_quality_rows(quality_evidence["per_query"], cases)
    if join_errs:
        return _not_eligible(["quality identity join"] + join_errs[:5])
    try:
        rvd = paired_bootstrap(paired_deltas(resolved, "rerank", "dense"), plan["resamples"], plan["seed"])
        fvr = paired_bootstrap(paired_deltas(resolved, "fused", "rerank"), plan["resamples"], plan["seed"])
        hn_rerank = derive_hardneg_deltas(resolved, "rerank", "dense", gate_tags)
        hn_fused = derive_hardneg_deltas(resolved, "fused", "rerank", gate_tags)
    except ValueError as e:
        return _not_eligible([f"analysis: {e}"])

    lat_errs = validate_latency_evidence(latency_evidence, root, ec["test_queries"], thr, sel_digest, selected_n)
    if lat_errs:
        return _not_eligible(["latency invalid"] + lat_errs[:5])
    lat = latency_summary(latency_evidence, root, ec["test_queries"], thr, sel_digest, selected_n)

    arm = decide_arm(rvd, fvr, hn_rerank, hn_fused, required_hardneg=gate_tags, thr=thr)
    if arm["arm"] in ("rerank", "fused") and not lat["within_budget"]:
        return _not_eligible([f"latency over budget สำหรับ arm {arm['arm']} → arm นี้เลือกไม่ได้"])

    # B2/B4: root/selection binding vs evidence metadata จริง
    if not isinstance(m4_evidence, dict) or not isinstance(canary_evidence, dict):
        return _not_eligible(["m4/canary evidence หาย/ผิดชนิด"])
    binding = _root_binding_errors(plan, cases, corpus, m4_evidence, canary_evidence, sel_digest)
    if binding:
        return _not_eligible(binding)

    # B2: frozen M4 case/visibility manifest ต้องผูกกับ RunPlan (digest) ก่อนเข้า evidence gate
    if not isinstance(m4_frozen_manifest, dict):
        return _not_eligible(["m4_frozen_manifest หาย/ผิดชนิด"])
    if E.m4_case_manifest_sha256(m4_frozen_manifest) != plan["m4_case_manifest_sha256"]:
        return _not_eligible(["frozen M4 manifest digest != RunPlan.m4_case_manifest_sha256"])

    # evidence + signoff gate (labels/coverage/m4/canary/signoff) — ผูก root + frozen M4 manifest, ค่า gate/role จาก plan
    ev_errs = E.decision_evidence_errors(cases, corpus, known_roles, evaluated_roles, gate_tags, signoff,
                                         m4_evidence, canary_evidence, root, m4_frozen_manifest, eval_hash, corpus_hash)
    if ev_errs:
        return _not_eligible([f"decision evidence gate ({len(ev_errs)}): {ev_errs[:3]}"])

    return {
        "status": "DECISION", "decision_eligible": True, "approved": True,
        "arm": arm["arm"], "reason": arm["reason"], "selected_n": selected_n,
        "run_manifest_sha256": root, "selection_digest": sel_digest,
        "benchmark_contract_version": BENCHMARK_CONTRACT_VERSION,
        "eval_set_sha256": eval_hash, "corpus_manifest_sha256": corpus_hash,
        "rerank_vs_dense": rvd, "fused_vs_rerank": fvr,
        "hardneg_rerank": hn_rerank, "hardneg_fused": hn_fused, "latency": lat,
        "signoff": signoff, "m4_evidence": m4_evidence, "canary_evidence": canary_evidence,
    }
