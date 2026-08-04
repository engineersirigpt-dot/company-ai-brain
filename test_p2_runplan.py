"""
Unit test ของ P2 run plan + decision analysis (M1) — pure, offline
พิสูจน์ root RunManifest / N-sweep (dev, N∈N_SET) / paired-bootstrap / arm-decision / latency
+ single fail-closed decision entry point (decide_p2) ก่อนเปิด Docker

Negative tests (Codex re-review acceptance):
  unknown/partial N set · test split · NaN/string metric · missing intent/arm · empty hard-neg ·
  latency over budget / sample-error count ไม่ครบ · root manifest ขาด model/image/config ·
  final decision entry point ไม่มีเส้นทางคืน arm verdict จาก partial bundle

    python test_p2_runplan.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_runplan as RP
import p2_eval as E
import p2_reranker as RK

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
_COMMIT = "b" * 40
_IMG = "sha256:" + "c" * 64
GATE_TAGS = ["sibling-hard-negative", "table-row", "negation", "current-superseded",
             "lexical-overlap", "multi-constraint"]
KNOWN = {"qc", "admin", "sales", "hr"}


def base_plan(**over):
    plan = {
        "run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 12345, "resamples": 10000,
        "primary_metric": "ndcg@5", "intent_grouping": "intent_id",
        "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 50, "test_queries": 50},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H},
        "model_commit": _COMMIT, "tokenizer_commit": _COMMIT,
        "model_file_manifest_sha256": _H, "image_digest": _IMG,
        "inference_config": {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16,
                             "device": "cpu", "dtype": "float32"},
    }
    plan.update(over)
    return plan


PLAN = base_plan()
ROOT = RP.run_manifest_sha256(PLAN)

# ── root RunManifest validate + hash (B4) ──────────────────────────────────────
check("run_plan valid -> ไม่มี error", RP.validate_run_plan(PLAN) == [], RP.validate_run_plan(PLAN))
check("seed หาย -> error", any("seed" in e for e in RP.validate_run_plan(base_plan(seed=None))))
check("n_set ผิด -> error", any("n_set" in e for e in RP.validate_run_plan(base_plan(n_set=[10, 20]))))
check("resamples < 10000 -> error", any("resamples" in e for e in RP.validate_run_plan(base_plan(resamples=999))))
check("primary_metric ไม่ใช่ ndcg@5 -> error", any("primary_metric" in e for e in RP.validate_run_plan(base_plan(primary_metric="mrr@5"))))
check("contract version ผิด -> error", any("contract_version" in e for e in RP.validate_run_plan(base_plan(benchmark_contract_version="x"))))
check("expected_counts ขาด test_queries -> error",
      any("expected_counts" in e for e in RP.validate_run_plan(base_plan(expected_counts={"dev_intents": 1, "dev_queries": 1, "test_intents": 50}))))
check("artifact_digests ไม่ใช่ sha256 -> error",
      any("artifact_digests" in e for e in RP.validate_run_plan(base_plan(artifact_digests={"eval_set_sha256": "x", "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H}))))
# B4: model/image/config binding
check("B4: model_commit abbreviated (7 hex) -> error (full commit)", any("model_commit" in e for e in RP.validate_run_plan(base_plan(model_commit="b" * 7))))
check("B4: model_commit full 64-hex -> ผ่าน", RP.validate_run_plan(base_plan(model_commit="b" * 64, tokenizer_commit="b" * 64)) == [])
check("B4: image_digest หาย -> error", any("image_digest" in e for e in RP.validate_run_plan(base_plan(image_digest=None))))
check("B4: model_file_manifest ไม่ใช่ sha256 -> error", any("model_file_manifest" in e for e in RP.validate_run_plan(base_plan(model_file_manifest_sha256="x"))))
check("B4: inference_config batch_size 0 -> error", any("inference_config" in e for e in RP.validate_run_plan(base_plan(inference_config={**PLAN["inference_config"], "batch_size": 0}))))
check("B4: inference_config model นอก allowlist -> error", any("inference_config" in e for e in RP.validate_run_plan(base_plan(inference_config={**PLAN["inference_config"], "model_name": "evil/model"}))))
check("run_manifest_sha256 deterministic", RP.run_manifest_sha256(PLAN) == RP.run_manifest_sha256(PLAN))
check("run_manifest_sha256 invalid plan -> ValueError", raises(lambda: RP.run_manifest_sha256(base_plan(seed=None))))

# ── select_n (dev, ผูก root, N∈N_SET) ──────────────────────────────────────────
EC = PLAN["expected_counts"]
def dev_by_n(**recall):
    base = {n: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0,
                "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}
    for n, r in recall.items():
        base[int(n)] = {**base[int(n)], **r}
    return base
def dev_ev(by_n=None, **over):
    d = {"split": "dev", "run_manifest_sha256": ROOT, "raw_result_digest": _H,
         "by_n": by_n if by_n is not None else dev_by_n()}
    d.update(over)
    return d

# N=10 ไม่ผ่าน (recall ต่ำ), 20+ ผ่าน -> เลือก 20
_sel = RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"point_recall": 0.80, "doc_recall": 0.82}})), ROOT, EC)
check("select_n เลือก N ต่ำสุดที่ผ่าน (20)", _sel["selected_n"] == 20 and _sel["status"] == "SELECTED", _sel)
_fail_all = {n: {"point_recall": 0.5, "doc_recall": 0.5, "candidate_hit": 0.5,
                 "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}
check("select_n ไม่มี N ผ่าน -> CANDIDATE_GENERATION_LIMITED",
      RP.select_n(dev_ev(by_n=_fail_all), ROOT, EC)["status"] == "CANDIDATE_GENERATION_LIMITED")
# B1 negatives
check("B1: N=1 นอก N_SET -> reject", raises(lambda: RP.select_n(dev_ev(by_n={1: {"point_recall": 1.0, "doc_recall": 1.0, "candidate_hit": 1.0, "completed_queries": 1, "completed_intents": 1}}), ROOT, EC)))
check("B1: by_n ขาด N (partial set) -> reject", raises(lambda: RP.select_n(dev_ev(by_n={k: dev_by_n()[k] for k in (10, 20)}), ROOT, EC)))
check("B1: dev ไม่ผูก root -> reject", raises(lambda: RP.select_n(dev_ev(run_manifest_sha256="x"), ROOT, EC)))
check("B1: split=test (ไม่ใช่ dev) -> reject", raises(lambda: RP.select_n(dev_ev(split="test"), ROOT, EC)))
check("B1: metric NaN -> reject", raises(lambda: RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"point_recall": float("nan")}})), ROOT, EC)))
check("B1: metric string -> reject", raises(lambda: RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"point_recall": "0.99"}})), ROOT, EC)))
check("B1: completed_queries != expected -> reject", raises(lambda: RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"completed_queries": 0}})), ROOT, EC)))
check("B1: raw_result_digest หาย -> reject", raises(lambda: RP.select_n(dev_ev(raw_result_digest="x"), ROOT, EC)))

# ── paired bootstrap (intent-level) ────────────────────────────────────────────
bs = RP.paired_bootstrap([0.05] * 20, seed=1)
check("bootstrap deltas คงที่ -> mean/CI = 0.05", abs(bs["mean_delta"] - 0.05) < 1e-9 and abs(bs["ci_lower"] - 0.05) < 1e-9)
bs2 = RP.paired_bootstrap([0.1, 0.0, 0.05, -0.02, 0.08, 0.03] * 5, seed=42)
check("bootstrap varied -> ci_lower <= mean <= ci_upper", bs2["ci_lower"] <= bs2["mean_delta"] <= bs2["ci_upper"])
check("bootstrap deterministic ตาม seed", RP.paired_bootstrap([0.1, 0.0, 0.05], seed=7) == RP.paired_bootstrap([0.1, 0.0, 0.05], seed=7))
check("bootstrap ว่าง -> ValueError", raises(lambda: RP.paired_bootstrap([])))
# B2 negatives
check("B2: bootstrap มี NaN delta -> reject", raises(lambda: RP.paired_bootstrap([0.1, float("nan"), 0.05])))
check("B2: bootstrap resamples < 10000 -> reject", raises(lambda: RP.paired_bootstrap([0.1, 0.2], resamples=100)))
check("B2: bootstrap seed ไม่ใช่ int -> reject", raises(lambda: RP.paired_bootstrap([0.1, 0.2], seed="x")))

# ── per_intent grouping + paired_deltas (ห้ามตัด intent เงียบ) ─────────────────
per_query = [{"query_id": "q1", "intent_id": "i1", "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
             {"query_id": "q2", "intent_id": "i1", "arms": {"dense": {"ndcg@5": 0.7}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
             {"query_id": "q3", "intent_id": "i2", "arms": {"dense": {"ndcg@5": 1.0}, "rerank": {"ndcg@5": 0.8}, "fused": {"ndcg@5": 0.9}}}]
pin = RP.per_intent_ndcg(per_query, "dense")
check("per_intent_ndcg: i1 = mean(0.5,0.7)=0.6", abs(pin["i1"] - 0.6) < 1e-9 and len(pin) == 2)
deltas = RP.paired_deltas(per_query, "rerank", "dense")
check("paired_deltas ต่อ intent (i1: 0.9-0.6=0.3, i2: 0.8-1.0=-0.2)", sorted(round(d, 4) for d in deltas) == [-0.2, 0.3])
check("B2: per_intent_ndcg เจอ None -> reject (ไม่ตัดเงียบ)",
      raises(lambda: RP.per_intent_ndcg([{"intent_id": "i1", "arms": {"dense": {"ndcg@5": None}}}], "dense")))

# ── validate_quality_evidence (test, arms exact, count ตรง) ────────────────────
EC2 = {"dev_intents": 1, "dev_queries": 1, "test_intents": 2, "test_queries": 3}
def q_ev(pq=None, **over):
    d = {"split": "test", "run_manifest_sha256": ROOT, "raw_result_digest": _H,
         "per_query": pq if pq is not None else per_query}
    d.update(over)
    return d
check("quality valid (2 intents/3 queries) -> []", RP.validate_quality_evidence(q_ev(), ROOT, EC2) == [], RP.validate_quality_evidence(q_ev(), ROOT, EC2))
check("B2: quality intent count ไม่ตรง expected -> error", any("intent count" in e for e in RP.validate_quality_evidence(q_ev(), ROOT, {**EC2, "test_intents": 5})))
check("B2: quality arm ขาด (missing fused) -> error",
      any("arms" in e for e in RP.validate_quality_evidence(q_ev(pq=[{"query_id": "q1", "intent_id": "i1", "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}}}]), ROOT, {"test_intents": 1, "test_queries": 1})))
check("B2: quality ndcg NaN -> error",
      any("ndcg@5" in e for e in RP.validate_quality_evidence(q_ev(pq=[{"query_id": "q1", "intent_id": "i1", "arms": {"dense": {"ndcg@5": float("nan")}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}}]), ROOT, {"test_intents": 1, "test_queries": 1})))
check("B2: quality query_id ซ้ำ -> error",
      any("ซ้ำ" in e for e in RP.validate_quality_evidence(q_ev(pq=[per_query[0], per_query[0]]), ROOT, {"test_intents": 1, "test_queries": 2})))
check("B2: quality ไม่ผูก root -> error", any("run_manifest" in e for e in RP.validate_quality_evidence(q_ev(run_manifest_sha256="x"), ROOT, EC2)))
check("B2: quality split=dev -> error", any("split" in e for e in RP.validate_quality_evidence(q_ev(split="dev"), ROOT, EC2)))

# ── decide_arm (hard-neg gate ต้องครบ, ไม่ vacuous) ────────────────────────────
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
# B3: empty / missing-category hard-neg -> ไม่ vacuous True
check("B3: hard-neg ว่าง {} -> dense (ไม่ vacuous)",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {}, good_hn)["arm"] == "dense")
check("B3: hard-neg ขาด required category -> dense",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {"sibling": 0.0}, {"sibling": 0.0}, required_hardneg=["sibling", "negation"])["arm"] == "dense")
check("B3: hard-neg ครบ required category -> rerank",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {"sibling": 0.0, "negation": 0.0}, good_hn, required_hardneg=["sibling", "negation"])["arm"] == "rerank")

# ── latency (stage/count/error ต้องครบก่อน within budget) ──────────────────────
def lat_ev(stages=None, **over):
    d = {"run_manifest_sha256": ROOT, "raw_latency_digest": _H, "error_count": 0, "oom_count": 0, "warmup": 10,
         "stages": stages if stages is not None else {"candidate_retrieval": [10] * 30, "rerank": [100] * 30, "rrf": [1] * 30, "total": [150] * 30}}
    d.update(over)
    return d
lat = RP.latency_summary(lat_ev(), ROOT, 20)
check("latency within budget (rerank 100<=1500, total 150<=2500, rrf 1<=10)", lat["within_budget"] is True)
check("latency p50/p95 ต่อ stage + ตัด warm-up", lat["rerank"]["p95"] == 100 and lat["rerank"]["n"] == 20)
lat2 = RP.latency_summary(lat_ev(stages={"candidate_retrieval": [10] * 30, "rerank": [2000] * 30, "rrf": [1] * 30, "total": [3000] * 30}), ROOT, 20)
check("latency over budget -> within_budget False", lat2["within_budget"] is False)
# M2 negatives
check("M2: stage count ไม่เท่ากัน -> reject", raises(lambda: RP.latency_summary(lat_ev(stages={"candidate_retrieval": [10] * 30, "rerank": [100] * 31, "rrf": [1] * 30, "total": [150] * 30}), ROOT, 20)))
check("M2: error_count != 0 -> reject", raises(lambda: RP.latency_summary(lat_ev(error_count=1), ROOT, 20)))
check("M2: oom_count != 0 -> reject", raises(lambda: RP.latency_summary(lat_ev(oom_count=1), ROOT, 20)))
check("M2: warmup ติดลบ -> reject", raises(lambda: RP.latency_summary(lat_ev(warmup=-1), ROOT, 20)))
check("M2: stage ขาด -> reject", raises(lambda: RP.latency_summary(lat_ev(stages={"rerank": [100] * 30, "total": [150] * 30, "rrf": [1] * 30}), ROOT, 20)))
check("M2: latency ไม่ผูก root -> reject", raises(lambda: RP.latency_summary(lat_ev(run_manifest_sha256="x"), ROOT, 20)))


# ── full bundle สำหรับ decide_p2 (synthetic fixtures — mechanics test เท่านั้น) ─
def pl(roles):
    return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
CORPUS = {"pa": {"source": "D1", "rerank_text": "alpha", "payload": pl(["qc", "admin"])}}
def tcase(i, tag):
    return {"query_id": f"tq{i}", "intent_id": f"ti{i}", "query": "ถาม", "role": "qc", "lang": "th",
            "category": tag, "challenge_tags": [tag], "split": "test", "case_type": "ranking",
            "relevance": {"pa": 3}, "hard_negative_ids": [], "relevant_sources": ["D1"],
            "label_status": "human-reviewed", "reviewed_by": "tester", "review_revision": "r1"}
DEVCASE = {"query_id": "dq0", "intent_id": "di0", "query": "ถาม", "role": "qc", "lang": "th",
           "category": "direct", "challenge_tags": ["direct"], "split": "dev", "case_type": "ranking",
           "relevance": {"pa": 3}, "hard_negative_ids": [], "relevant_sources": ["D1"],
           "label_status": "human-reviewed", "reviewed_by": "tester", "review_revision": "r1"}
CASES = [tcase(i, GATE_TAGS[i % len(GATE_TAGS)]) for i in range(50)] + [DEVCASE]
_EH, _CH = E.eval_set_sha256(CASES), E.corpus_manifest_sha256(CORPUS)

BPLAN = base_plan(expected_counts={"dev_intents": 1, "dev_queries": 1, "test_intents": 50, "test_queries": 50})
BROOT = RP.run_manifest_sha256(BPLAN)
B_dev = {"split": "dev", "run_manifest_sha256": BROOT, "raw_result_digest": _H,
         "by_n": {n: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0,
                      "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}}
B_quality = {"split": "test", "run_manifest_sha256": BROOT, "raw_result_digest": _H,
             "per_query": [{"query_id": f"tq{i}", "intent_id": f"ti{i}",
                            "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}}
                           for i in range(50)]}
B_latency = {"run_manifest_sha256": BROOT, "raw_latency_digest": _H, "error_count": 0, "oom_count": 0, "warmup": 10,
             "stages": {"candidate_retrieval": [10] * 60, "rerank": [100] * 60, "rrf": [1] * 60, "total": [150] * 60}}
B_hn = {t: 0.0 for t in GATE_TAGS}
B_m4 = {"status": "PASS", "isolated_interlock": "PASS", "independent_oracle": "PASS",
        "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0,
        "unauthorized_sentinel_id_hashes": ["d" * 64], "model_input_id_hashes": ["e" * 64],
        "model_revision": _COMMIT, "tokenizer_revision": _COMMIT, "image_digest": _IMG,
        "retrieval_index_manifest_sha256": _H, "run_id": "runX", "run_manifest_sha256": BROOT,
        "eval_set_sha256": _EH, "corpus_manifest_sha256": _CH}
B_can = {"status": "PASS", "leak_count": 0, "auth_status": "VERIFIED",
         "arm_status": {"dense": "PASS", "rerank": "PASS", "fused": "PASS"},
         "arm_error_counts": {"dense": 0, "rerank": 0, "fused": 0},
         "expected_query_count": 50, "actual_query_count": 50,
         "model_revision": _COMMIT, "image_digest": _IMG,
         "retrieval_index_manifest_sha256": _H, "run_id": "runX", "run_manifest_sha256": BROOT,
         "eval_set_sha256": _EH, "corpus_manifest_sha256": _CH}
B_signoff = {"eval_set_sha256": _EH, "corpus_manifest_sha256": _CH,
             "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION, "git_commit": "2364bb1",
             "reviewer": "owner", "data_owner_role": "QA Lead",
             "reviewed_at": "2026-08-04T10:00:00+07:00", "decision": "approved"}
def decide(**over):
    kw = dict(plan=BPLAN, dev_evidence=B_dev, quality_evidence=B_quality, latency_evidence=B_latency,
              hardneg_rerank=B_hn, hardneg_fused=B_hn, m4_evidence=B_m4, canary_evidence=B_can, signoff=B_signoff,
              cases=CASES, corpus=CORPUS, known_roles=KNOWN, evaluated_roles=["qc"], gate_tags=GATE_TAGS)
    kw.update(over)
    return RP.decide_p2(**kw)

# ── decide_p2: happy path (bundle ครบ) -> DECISION ─────────────────────────────
_d = decide()
check("decide_p2 bundle ครบ -> DECISION arm=rerank + eligible", _d["status"] == "DECISION" and _d["arm"] == "rerank" and _d["decision_eligible"] is True, _d)
check("decide_p2 selected_n = 10 (ต่ำสุดที่ผ่าน) + ผูก root", _d["selected_n"] == 10 and _d["run_manifest_sha256"] == BROOT)
check("decide_p2 benchmark manifest approved=True", _d["benchmark"]["approved"] is True)
# ── decide_p2: fail-closed ต่อ partial/invalid bundle (ไม่มีเส้นทางคืน arm) ─────
check("B3: invalid plan -> NOT_DECISION_ELIGIBLE (ไม่คืน arm)",
      decide(plan=base_plan(seed=None))["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: signoff หาย -> NOT_DECISION_ELIGIBLE (ไม่ leak arm verdict)",
      decide(signoff=None)["decision_eligible"] is False and decide(signoff=None)["arm"] is None)
check("B3: dev evidence ไม่ผ่าน (recall ต่ำทุก N) -> NOT_DECISION_ELIGIBLE",
      decide(dev_evidence={**B_dev, "by_n": {n: {"point_recall": 0.5, "doc_recall": 0.5, "candidate_hit": 0.5, "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: hard-neg ว่าง {} -> NOT_DECISION_ELIGIBLE (evidence ไม่ครบ gate categories)",
      decide(hardneg_rerank={})["status"] == "NOT_DECISION_ELIGIBLE" and decide(hardneg_rerank={})["arm"] is None)
check("B3: latency over budget (rerank) -> NOT_DECISION_ELIGIBLE (arm นี้เลือกไม่ได้)",
      decide(latency_evidence={**B_latency, "stages": {"candidate_retrieval": [10] * 60, "rerank": [2000] * 60, "rrf": [1] * 60, "total": [3000] * 60}})["status"] == "NOT_DECISION_ELIGIBLE")
check("B4: m4 model_revision != root model_commit -> NOT_DECISION_ELIGIBLE",
      decide(m4_evidence={**B_m4, "model_revision": "f" * 40})["status"] == "NOT_DECISION_ELIGIBLE")
check("B4: canary ไม่ผูก root run_manifest -> NOT_DECISION_ELIGIBLE",
      decide(canary_evidence={**B_can, "run_manifest_sha256": "x"})["status"] == "NOT_DECISION_ELIGIBLE")
check("B4: m4/canary คนละ run_id -> NOT_DECISION_ELIGIBLE",
      decide(canary_evidence={**B_can, "run_id": "runY"})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: quality intent count ไม่ครบ 50 -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence={**B_quality, "per_query": B_quality["per_query"][:40]})["status"] == "NOT_DECISION_ELIGIBLE")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
