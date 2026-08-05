"""
Unit test ของ P2 run plan + decision analysis (M1) — pure, offline
พิสูจน์ authoritative root RunManifest / N-sweep (dev, N∈N_SET) / SelectionManifest binding /
paired-bootstrap / derived hard-neg / latency + **decide_p2 = public approval surface เดียว**

Codex re-review acceptance (6aef5f9):
  plan threshold ถูกใช้จริง (override ไม่ได้) · root eval/corpus/model/image/config ไม่ตรง actual → ไม่ eligible ·
  approval path ต้องผ่าน decide_p2 เท่านั้น · quality/latency จาก N อื่น หรือ raw digest ผิด → reject ·
  empty/ลด gate tags + extra by_n key → reject

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


_COMMIT = "b" * 40
_IMG = "sha256:" + "c" * 64
_FM = "d" * 64            # model file-manifest sha256
_IDX = "e" * 64          # retrieval index manifest sha256
GATE_TAGS = ["sibling-hard-negative", "table-row", "negation", "current-superseded",
             "lexical-overlap", "multi-constraint"]
KNOWN = {"qc", "admin", "sales", "hr"}
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}


# ── frozen synthetic corpus/cases (mechanics fixtures — ไม่ใช่ decision จริง) ───
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


def base_plan(**over):
    plan = {
        "run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 12345, "resamples": 10000,
        "primary_metric": "ndcg@5", "intent_grouping": "intent_id",
        "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "gate_tags": list(GATE_TAGS), "evaluated_roles": ["qc"],
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 50, "test_queries": 50},
        "artifact_digests": {"eval_set_sha256": _EH, "corpus_manifest_sha256": _CH, "retrieval_index_manifest_sha256": _IDX},
        "model_commit": _COMMIT, "tokenizer_commit": _COMMIT,
        "model_file_manifest_sha256": _FM, "image_digest": _IMG, "inference_config": dict(IC),
    }
    plan.update(over)
    return plan


PLAN = base_plan()
ROOT = RP.run_manifest_sha256(PLAN)

# ── root RunManifest validate + hash (B1/B4) ───────────────────────────────────
check("run_plan valid -> ไม่มี error", RP.validate_run_plan(PLAN) == [], RP.validate_run_plan(PLAN))
check("seed หาย -> error", any("seed" in e for e in RP.validate_run_plan(base_plan(seed=None))))
check("n_set ผิด -> error", any("n_set" in e for e in RP.validate_run_plan(base_plan(n_set=[10, 20]))))
check("resamples < 10000 -> error", any("resamples" in e for e in RP.validate_run_plan(base_plan(resamples=999))))
check("contract version ผิด -> error", any("contract_version" in e for e in RP.validate_run_plan(base_plan(benchmark_contract_version="x"))))
# B1: threshold schema/range + frozen gate_tags/evaluated_roles
check("B1: threshold ขาด key -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={"candidate_recall": 0.95}))))
check("B1: threshold ค่าไม่ใช่ number -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "min_delta_ndcg": "x"}))))
check("B1: threshold latency budget <=0 -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "rerank_p95_ms": 0}))))
# M2: threshold domain/relationship (reject ค่าที่หลุดโดเมนของ metric)
check("M2: candidate_recall=0.0001 (นอก target) -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "candidate_recall": 0.0001}))))
check("M2: candidate_hit != 1.0 -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "candidate_hit": 0.9}))))
check("M2: ci_lower_min=-999 -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "ci_lower_min": -999}))))
check("M2: noninferior_floor=-999 -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "noninferior_floor": -999}))))
check("M2: hardneg_floor=-999 -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "hardneg_floor": -999}))))
check("M2: latency budget เรียงผิด (rerank>total) -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "rerank_p95_ms": 3000}))))
check("M2: noninferior_floor > ci_lower_min -> error", any("thresholds" in e for e in RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "noninferior_floor": 0.5}))))
check("M2: min_delta_ndcg=0.99 (ในโดเมน) -> ยังผ่าน validate", RP.validate_run_plan(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "min_delta_ndcg": 0.99})) == [])
check("B1: gate_tags ว่าง -> error (reject empty frozen set)", any("gate_tags" in e for e in RP.validate_run_plan(base_plan(gate_tags=[]))))
check("B1: evaluated_roles ว่าง -> error", any("evaluated_roles" in e for e in RP.validate_run_plan(base_plan(evaluated_roles=[]))))
check("B4: model_commit abbreviated (7 hex) -> error", any("model_commit" in e for e in RP.validate_run_plan(base_plan(model_commit="b" * 7))))
check("B4: model_commit full 64-hex -> ผ่าน", RP.validate_run_plan(base_plan(model_commit="b" * 64, tokenizer_commit="b" * 64)) == [])
check("B4: image_digest หาย -> error", any("image_digest" in e for e in RP.validate_run_plan(base_plan(image_digest=None))))
check("B4: model_file_manifest ไม่ใช่ sha256 -> error", any("model_file_manifest" in e for e in RP.validate_run_plan(base_plan(model_file_manifest_sha256="x"))))
check("B4: inference_config batch_size 0 -> error", any("inference_config" in e for e in RP.validate_run_plan(base_plan(inference_config={**IC, "batch_size": 0}))))
check("B4: inference_config model นอก allowlist -> error", any("inference_config" in e for e in RP.validate_run_plan(base_plan(inference_config={**IC, "model_name": "evil/model"}))))
check("run_manifest_sha256 deterministic", RP.run_manifest_sha256(PLAN) == RP.run_manifest_sha256(PLAN))
check("run_manifest_sha256 invalid plan -> ValueError", raises(lambda: RP.run_manifest_sha256(base_plan(seed=None))))
check("gate_tags/thresholds เปลี่ยน -> root hash เปลี่ยน (frozen ใน hash)",
      RP.run_manifest_sha256(base_plan(gate_tags=["only-one"])) != ROOT
      and RP.run_manifest_sha256(base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "min_delta_ndcg": 0.5})) != ROOT)

# ── select_n (dev, ผูก root, N∈N_SET, exact int keys, raw digest) ───────────────
EC = PLAN["expected_counts"]
def dev_by_n(**recall):
    base = {n: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0,
                "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}
    for n, r in recall.items():
        base[int(n)] = {**base[int(n)], **r}
    return base
def dev_ev(by_n=None, digest=None, **over):
    bn = by_n if by_n is not None else dev_by_n()
    d = {"split": "dev", "run_manifest_sha256": ROOT,
         "raw_result_digest": RP.raw_digest(bn) if digest is None else digest, "by_n": bn}
    d.update(over)
    return d

_sel = RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"point_recall": 0.80, "doc_recall": 0.82}})), ROOT, EC)
check("select_n เลือก N ต่ำสุดที่ผ่าน (20)", _sel["selected_n"] == 20 and _sel["status"] == "SELECTED", _sel)
_fail_all = {n: {"point_recall": 0.5, "doc_recall": 0.5, "candidate_hit": 0.5,
                 "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}
check("select_n ไม่มี N ผ่าน -> CANDIDATE_GENERATION_LIMITED",
      RP.select_n(dev_ev(by_n=_fail_all), ROOT, EC)["status"] == "CANDIDATE_GENERATION_LIMITED")
check("B1: N=1 นอก N_SET -> reject", raises(lambda: RP.select_n(dev_ev(by_n={1: {"point_recall": 1.0, "doc_recall": 1.0, "candidate_hit": 1.0, "completed_queries": 1, "completed_intents": 1}}), ROOT, EC)))
check("M1: extra key '10x' (string) -> reject (exact int key set)",
      raises(lambda: RP.select_n(dev_ev(by_n={**dev_by_n(), "10x": {"point_recall": 1.0, "doc_recall": 1.0, "candidate_hit": 1.0, "completed_queries": 1, "completed_intents": 1}}, digest="a" * 64), ROOT, EC)))
check("B1: dev ไม่ผูก root -> reject", raises(lambda: RP.select_n(dev_ev(run_manifest_sha256="x"), ROOT, EC)))
check("B1: split=test -> reject", raises(lambda: RP.select_n(dev_ev(split="test"), ROOT, EC)))
check("B1: metric NaN -> reject", raises(lambda: RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"point_recall": float("nan")}})), ROOT, EC)))
check("B1: completed_queries != expected -> reject", raises(lambda: RP.select_n(dev_ev(by_n=dev_by_n(**{"10": {"completed_queries": 0}})), ROOT, EC)))
check("B4: raw_result_digest ไม่ตรง recompute -> reject", raises(lambda: RP.select_n(dev_ev(digest="a" * 64), ROOT, EC)))

# ── paired bootstrap (intent-level) ────────────────────────────────────────────
check("bootstrap deltas คงที่ -> mean/CI = 0.05", abs(RP.paired_bootstrap([0.05] * 20, seed=1)["ci_lower"] - 0.05) < 1e-9)
check("bootstrap deterministic ตาม seed", RP.paired_bootstrap([0.1, 0.0, 0.05], seed=7) == RP.paired_bootstrap([0.1, 0.0, 0.05], seed=7))
check("bootstrap ว่าง -> ValueError", raises(lambda: RP.paired_bootstrap([])))
check("B2: bootstrap มี NaN delta -> reject", raises(lambda: RP.paired_bootstrap([0.1, float("nan"), 0.05])))
check("B2: bootstrap resamples < 10000 -> reject", raises(lambda: RP.paired_bootstrap([0.1, 0.2], resamples=100)))
check("B2: bootstrap seed ไม่ใช่ int -> reject", raises(lambda: RP.paired_bootstrap([0.1, 0.2], seed="x")))

per_query = [{"query_id": "q1", "intent_id": "i1", "challenge_tags": ["direct"], "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
             {"query_id": "q2", "intent_id": "i1", "challenge_tags": ["direct"], "arms": {"dense": {"ndcg@5": 0.7}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
             {"query_id": "q3", "intent_id": "i2", "challenge_tags": ["direct"], "arms": {"dense": {"ndcg@5": 1.0}, "rerank": {"ndcg@5": 0.8}, "fused": {"ndcg@5": 0.9}}}]
check("per_intent_ndcg: i1 = mean(0.5,0.7)=0.6", abs(RP.per_intent_ndcg(per_query, "dense")["i1"] - 0.6) < 1e-9)
check("paired_deltas ต่อ intent (i1:0.3, i2:-0.2)", sorted(round(d, 4) for d in RP.paired_deltas(per_query, "rerank", "dense")) == [-0.2, 0.3])
check("B2: per_intent_ndcg เจอ None -> reject", raises(lambda: RP.per_intent_ndcg([{"intent_id": "i1", "arms": {"dense": {"ndcg@5": None}}}], "dense")))

# ── B4: derive hard-neg deltas จาก per-query rows (ไม่รับ naked dict) ───────────
_hnpq = [{"query_id": "q1", "intent_id": "i1", "challenge_tags": ["negation"], "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}},
         {"query_id": "q2", "intent_id": "i2", "challenge_tags": ["table-row"], "arms": {"dense": {"ndcg@5": 0.8}, "rerank": {"ndcg@5": 0.7}, "fused": {"ndcg@5": 0.8}}}]
_hn = RP.derive_hardneg_deltas(_hnpq, "rerank", "dense", ["negation", "table-row"])
check("B4: derive hard-neg negation=+0.4, table-row=-0.1", abs(_hn["negation"] - 0.4) < 1e-9 and abs(_hn["table-row"] + 0.1) < 1e-9, _hn)
check("B4: category ไม่มี evidence -> reject", raises(lambda: RP.derive_hardneg_deltas(_hnpq, "rerank", "dense", ["negation", "missing-cat"])))

# ── validate_quality_evidence (test, arms exact, count, raw digest, selection) ──
EC2 = {"dev_intents": 1, "dev_queries": 1, "test_intents": 2, "test_queries": 3}
def q_ev(pq=None, digest=None, **over):
    p = pq if pq is not None else per_query
    d = {"split": "test", "run_manifest_sha256": ROOT,
         "raw_result_digest": RP.raw_digest(p) if digest is None else digest, "per_query": p}
    d.update(over)
    return d
check("quality valid (2 intents/3 queries) -> []", RP.validate_quality_evidence(q_ev(), ROOT, EC2) == [], RP.validate_quality_evidence(q_ev(), ROOT, EC2))
check("B2: quality intent count ไม่ตรง -> error", any("intent count" in e for e in RP.validate_quality_evidence(q_ev(), ROOT, {**EC2, "test_intents": 5})))
check("B2: quality arm ขาด -> error",
      any("arms" in e for e in RP.validate_quality_evidence(q_ev(pq=[{"query_id": "q1", "intent_id": "i1", "challenge_tags": ["d"], "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}}}]), ROOT, {"test_intents": 1, "test_queries": 1})))
check("B2: quality ndcg NaN -> error",
      any("ndcg@5" in e for e in RP.validate_quality_evidence(q_ev(pq=[{"query_id": "q1", "intent_id": "i1", "challenge_tags": ["d"], "arms": {"dense": {"ndcg@5": float("nan")}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}}], digest="f" * 64), ROOT, {"test_intents": 1, "test_queries": 1})))
check("B2: quality challenge_tags ว่าง -> error",
      any("challenge_tags" in e for e in RP.validate_quality_evidence(q_ev(pq=[{"query_id": "q1", "intent_id": "i1", "challenge_tags": [], "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}}]), ROOT, {"test_intents": 1, "test_queries": 1})))
check("B4: quality raw_result_digest ผิด (format-only) -> error", any("raw_result_digest" in e for e in RP.validate_quality_evidence(q_ev(digest="f" * 64), ROOT, EC2)))
check("B4: quality selection_digest ไม่ตรง -> error", any("selection_digest" in e for e in RP.validate_quality_evidence(q_ev(selection_digest="x"), ROOT, EC2, sel_digest="y")))
check("B2: quality ไม่ผูก root -> error", any("run_manifest" in e for e in RP.validate_quality_evidence(q_ev(run_manifest_sha256="x"), ROOT, EC2)))

# ── decide_arm (hard-neg gate ต้องครบ, ไม่ vacuous) ────────────────────────────
good_hn = {"sibling": 0.01, "negation": 0.0}
check("decide: rerank Δ0.03 CI0.01, fused ไม่คุ้ม -> rerank",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0.005, "ci_lower": 0.0}, good_hn, good_hn)["arm"] == "rerank")
check("decide: fused Δ0.02 CI0.005 -> fused",
      RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0.02, "ci_lower": 0.005}, good_hn, good_hn)["arm"] == "fused")
check("decide: rerank Δ0.01 (below min) -> dense", RP.decide_arm({"mean_delta": 0.01, "ci_lower": -0.005}, {"mean_delta": 0, "ci_lower": 0}, good_hn, good_hn)["arm"] == "dense")
check("decide: hard-neg -0.06 < floor -> dense", RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {"sibling": -0.06}, good_hn)["arm"] == "dense")
check("B3: hard-neg ว่าง {} -> dense (ไม่ vacuous)", RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {}, good_hn)["arm"] == "dense")
check("B3: hard-neg ขาด required -> dense", RP.decide_arm({"mean_delta": 0.03, "ci_lower": 0.01}, {"mean_delta": 0, "ci_lower": 0}, {"sibling": 0.0}, {"sibling": 0.0}, required_hardneg=["sibling", "negation"])["arm"] == "dense")

# ── latency (stage/count/error/raw digest) ─────────────────────────────────────
def lat_ev(stages=None, digest=None, **over):
    s = stages if stages is not None else {"candidate_retrieval": [10] * 30, "rerank": [100] * 30, "rrf": [1] * 30, "total": [150] * 30}
    d = {"run_manifest_sha256": ROOT, "raw_latency_digest": RP.raw_digest(s) if digest is None else digest,
         "error_count": 0, "oom_count": 0, "warmup": 10, "stages": s}
    d.update(over)
    return d
lat = RP.latency_summary(lat_ev(), ROOT, 20)
check("latency within budget", lat["within_budget"] is True and lat["rerank"]["p95"] == 100 and lat["rerank"]["n"] == 20)
check("latency over budget -> False", RP.latency_summary(lat_ev(stages={"candidate_retrieval": [10] * 30, "rerank": [2000] * 30, "rrf": [1] * 30, "total": [3000] * 30}), ROOT, 20)["within_budget"] is False)
check("M2: stage count ไม่เท่ากัน -> reject", raises(lambda: RP.latency_summary(lat_ev(stages={"candidate_retrieval": [10] * 30, "rerank": [100] * 31, "rrf": [1] * 30, "total": [150] * 30}), ROOT, 20)))
check("M2: error_count != 0 -> reject", raises(lambda: RP.latency_summary(lat_ev(error_count=1), ROOT, 20)))
check("M2: warmup ติดลบ -> reject", raises(lambda: RP.latency_summary(lat_ev(warmup=-1), ROOT, 20)))
check("M2: stage ขาด -> reject", raises(lambda: RP.latency_summary(lat_ev(stages={"rerank": [100] * 30, "total": [150] * 30, "rrf": [1] * 30}), ROOT, 20)))
check("B4: latency raw_latency_digest ผิด -> reject", raises(lambda: RP.latency_summary(lat_ev(digest="f" * 64), ROOT, 20)))


# ── full bundle factory สำหรับ decide_p2 (bound ถูกต้องกับ plan ที่ให้) ─────────
def build_bundle(plan):
    root = RP.run_manifest_sha256(plan)
    by_n = {n: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0,
                "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}
    dev_raw = RP.raw_digest(by_n)
    dev = {"split": "dev", "run_manifest_sha256": root, "raw_result_digest": dev_raw, "by_n": by_n}
    seln = 10
    sd = RP.selection_digest(root, dev_raw, seln)
    pq = [{"query_id": f"tq{i}", "intent_id": f"ti{i}", "role": "qc", "challenge_tags": [GATE_TAGS[i % len(GATE_TAGS)]],
           "arms": {"dense": {"ndcg@5": 0.5}, "rerank": {"ndcg@5": 0.9}, "fused": {"ndcg@5": 0.9}}} for i in range(50)]
    quality = {"split": "test", "run_manifest_sha256": root, "selection_digest": sd, "selected_n": seln,
               "raw_result_digest": RP.raw_digest(pq), "per_query": pq}
    stages = {"candidate_retrieval": [10] * 60, "rerank": [100] * 60, "rrf": [1] * 60, "total": [150] * 60}
    latency = {"run_manifest_sha256": root, "selection_digest": sd, "selected_n": seln,
               "raw_latency_digest": RP.raw_digest(stages), "error_count": 0, "oom_count": 0, "warmup": 10, "stages": stages}
    m4 = {"status": "PASS", "isolated_interlock": "PASS", "independent_oracle": "PASS",
          "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0,
          "model_invocation_count": 2, "evidence_stage": "selected-n",
          "authorized_candidate_id_hashes": ["a" * 64, "b" * 64, "c" * 64],
          "authorized_candidate_text_hashes": ["1" * 64, "2" * 64, "3" * 64],
          "provider_candidate_id_hashes": ["a" * 64, "b" * 64],
          "provider_candidate_text_hashes": ["1" * 64, "2" * 64],
          "model_input_id_hashes": ["a" * 64], "model_input_text_hashes": ["1" * 64],
          "unauthorized_sentinel_id_hashes": ["f" * 64], "unauthorized_sentinel_text_hashes": ["9" * 64],
          "model_revision": _COMMIT, "tokenizer_revision": _COMMIT, "model_file_manifest_sha256": _FM,
          "image_digest": _IMG, "inference_config": dict(IC), "retrieval_index_manifest_sha256": _IDX,
          "selection_digest": sd, "run_manifest_sha256": root, "run_id": "runX",
          "eval_set_sha256": _EH, "corpus_manifest_sha256": _CH}
    can = {"status": "PASS", "leak_count": 0, "auth_status": "VERIFIED",
           "arm_status": {"dense": "PASS", "rerank": "PASS", "fused": "PASS"},
           "arm_error_counts": {"dense": 0, "rerank": 0, "fused": 0},
           "expected_query_count": 50, "actual_query_count": 50,
           "model_revision": _COMMIT, "image_digest": _IMG, "retrieval_index_manifest_sha256": _IDX,
           "selection_digest": sd, "run_manifest_sha256": root, "run_id": "runX",
           "eval_set_sha256": _EH, "corpus_manifest_sha256": _CH}
    signoff = {"eval_set_sha256": _EH, "corpus_manifest_sha256": _CH,
               "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION, "git_commit": "2364bb1",
               "reviewer": "owner", "data_owner_role": "QA Lead",
               "reviewed_at": "2026-08-04T10:00:00+07:00", "decision": "approved"}
    return dict(dev_evidence=dev, quality_evidence=quality, latency_evidence=latency,
                m4_evidence=m4, canary_evidence=can, signoff=signoff)
def decide(plan=None, **override):
    p = plan if plan is not None else PLAN
    b = build_bundle(p)
    b.update(override)
    return RP.decide_p2(plan=p, cases=CASES, corpus=CORPUS, known_roles=KNOWN, **b)

# ── decide_p2: happy path (bundle ครบ) -> DECISION ─────────────────────────────
_d = decide()
check("decide_p2 bundle ครบ -> DECISION arm=rerank + approved", _d["status"] == "DECISION" and _d["arm"] == "rerank" and _d.get("approved") is True, _d)
check("decide_p2 selected_n=10 + ผูก root + selection_digest", _d["selected_n"] == 10 and _d["run_manifest_sha256"] == ROOT and "selection_digest" in _d)
check("decide_p2 hard-neg deltas derive มาแล้ว (ครบ gate_tags)", set(_d["hardneg_rerank"]) == set(GATE_TAGS))

# ── B1: plan threshold เป็น authoritative (override ไม่ได้) ─────────────────────
check("B1: plan min_delta_ndcg=0.99 -> rerank ไม่ผ่าน -> arm=dense (ใช้ค่าจาก plan)",
      decide(plan=base_plan(thresholds={**RP.DEFAULT_THRESHOLDS, "min_delta_ndcg": 0.99}))["arm"] == "dense")

# ── B2: root artifact/model digests ต้องตรง actual (เปลี่ยน field เดียว -> ไม่ eligible) ──
check("B2: root eval_set_sha256 ไม่ตรง cases จริง -> NOT_DECISION_ELIGIBLE",
      decide(plan=base_plan(artifact_digests={"eval_set_sha256": "0" * 64, "corpus_manifest_sha256": _CH, "retrieval_index_manifest_sha256": _IDX}))["status"] == "NOT_DECISION_ELIGIBLE")
check("B2: root corpus_manifest ไม่ตรง -> NOT_DECISION_ELIGIBLE",
      decide(plan=base_plan(artifact_digests={"eval_set_sha256": _EH, "corpus_manifest_sha256": "0" * 64, "retrieval_index_manifest_sha256": _IDX}))["status"] == "NOT_DECISION_ELIGIBLE")
check("B2: m4 model_revision != root model_commit -> NOT_DECISION_ELIGIBLE",
      decide(m4_evidence={**build_bundle(PLAN)["m4_evidence"], "model_revision": "f" * 40})["status"] == "NOT_DECISION_ELIGIBLE")
check("M1: decide_p2 กับ M4a (preflight-n50) -> NOT_DECISION_ELIGIBLE (ต้องเป็น M4b selected-n)",
      decide(m4_evidence={**build_bundle(PLAN)["m4_evidence"], "evidence_stage": "preflight-n50"})["status"] == "NOT_DECISION_ELIGIBLE")
check("B2: m4 model_file_manifest != root -> NOT_DECISION_ELIGIBLE",
      decide(m4_evidence={**build_bundle(PLAN)["m4_evidence"], "model_file_manifest_sha256": "0" * 64})["status"] == "NOT_DECISION_ELIGIBLE")
check("B2: m4 inference_config != root -> NOT_DECISION_ELIGIBLE",
      decide(m4_evidence={**build_bundle(PLAN)["m4_evidence"], "inference_config": {**IC, "batch_size": 8}})["status"] == "NOT_DECISION_ELIGIBLE")
check("B2: m4 index != root -> NOT_DECISION_ELIGIBLE",
      decide(m4_evidence={**build_bundle(PLAN)["m4_evidence"], "retrieval_index_manifest_sha256": "0" * 64})["status"] == "NOT_DECISION_ELIGIBLE")

# ── B4: quality/latency จาก N อื่น หรือ raw digest ผิด -> reject ────────────────
check("B4: quality selection_digest จาก N อื่น -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence={**build_bundle(PLAN)["quality_evidence"], "selection_digest": RP.selection_digest(ROOT, RP.raw_digest({}), 50)})["status"] == "NOT_DECISION_ELIGIBLE")
check("B4: latency raw digest ผิด -> NOT_DECISION_ELIGIBLE",
      decide(latency_evidence={**build_bundle(PLAN)["latency_evidence"], "raw_latency_digest": "0" * 64})["status"] == "NOT_DECISION_ELIGIBLE")

# ── B3: fail-closed ต่อ partial/invalid bundle (ไม่มีเส้นทางคืน arm) ────────────
check("B3: invalid plan -> NOT_DECISION_ELIGIBLE",
      RP.decide_p2(plan=base_plan(seed=None), cases=CASES, corpus=CORPUS, known_roles=KNOWN, **build_bundle(PLAN))["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: signoff หาย -> NOT_DECISION_ELIGIBLE + arm=None", decide(signoff=None)["decision_eligible"] is False and decide(signoff=None)["arm"] is None)
check("B3: dev recall ต่ำทุก N -> NOT_DECISION_ELIGIBLE",
      decide(dev_evidence={**build_bundle(PLAN)["dev_evidence"], "by_n": {n: {"point_recall": 0.5, "doc_recall": 0.5, "candidate_hit": 0.5, "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}, "raw_result_digest": RP.raw_digest({n: {"point_recall": 0.5, "doc_recall": 0.5, "candidate_hit": 0.5, "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)})})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: latency over budget (rerank) -> NOT_DECISION_ELIGIBLE",
      decide(latency_evidence={**build_bundle(PLAN)["latency_evidence"], "stages": {"candidate_retrieval": [10] * 60, "rerank": [2000] * 60, "rrf": [1] * 60, "total": [3000] * 60}, "raw_latency_digest": RP.raw_digest({"candidate_retrieval": [10] * 60, "rerank": [2000] * 60, "rrf": [1] * 60, "total": [3000] * 60})})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: canary ไม่ผูก root run_manifest -> NOT_DECISION_ELIGIBLE",
      decide(canary_evidence={**build_bundle(PLAN)["canary_evidence"], "run_manifest_sha256": "x"})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: m4/canary คนละ run_id -> NOT_DECISION_ELIGIBLE",
      decide(canary_evidence={**build_bundle(PLAN)["canary_evidence"], "run_id": "runY"})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: quality intent ไม่ครบ 50 -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence={**build_bundle(PLAN)["quality_evidence"], "per_query": build_bundle(PLAN)["quality_evidence"]["per_query"][:40]})["status"] == "NOT_DECISION_ELIGIBLE")
check("B3: ไม่มี public decision_benchmark_manifest builder แล้ว", not hasattr(E, "decision_benchmark_manifest"))

# ── M1: quality rows ต้องเป็นผลของ frozen eval queries จริง (join by query_id) ──
def q_mut(mutate):
    base = build_bundle(PLAN)["quality_evidence"]
    pq = [dict(r) for r in base["per_query"]]
    mutate(pq)
    return {**base, "per_query": pq, "raw_result_digest": RP.raw_digest(pq)}   # digest self-consistent -> join เท่านั้นที่จับได้
def _fake_all(pq):
    for j, r in enumerate(pq):
        r["query_id"] = f"fake{j}"
check("M1: fabricated query IDs (digest self-consistent) -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence=q_mut(_fake_all))["status"] == "NOT_DECISION_ELIGIBLE")
check("M1: changed intent_id (query_id เดิม) -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence=q_mut(lambda pq: pq[5].__setitem__("intent_id", "tiX")))["status"] == "NOT_DECISION_ELIGIBLE")
check("M1: changed challenge_tags -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence=q_mut(lambda pq: pq[5].__setitem__("challenge_tags", ["multi-constraint"] if pq[5]["challenge_tags"] != ["multi-constraint"] else ["negation"])))["status"] == "NOT_DECISION_ELIGIBLE")
check("M1: changed role -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence=q_mut(lambda pq: pq[5].__setitem__("role", "admin")))["status"] == "NOT_DECISION_ELIGIBLE")
check("M1: unknown/extra query id -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence=q_mut(lambda pq: pq[0].__setitem__("query_id", "tqX")))["status"] == "NOT_DECISION_ELIGIBLE")
check("M1: missing query (49 rows) -> NOT_DECISION_ELIGIBLE",
      decide(quality_evidence=q_mut(lambda pq: pq.pop()))["status"] == "NOT_DECISION_ELIGIBLE")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
