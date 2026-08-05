"""
Unit test ของ p2_m4_harness + public M4a gate — pure/offline
model_input มาจาก **spy trace เท่านั้น** (sentinel ถูก guard ก่อนถึง underlying) · run_id จาก RunPlan ·
receipt no-crash + timestamp order · typed identity · verdict ไม่ self-stamp

    python test_p2_m4_harness.py
"""
import copy
import hashlib
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_eval as E
import p2_reranker as RK
import p2_runplan as RP
import p2_m4_harness as HN

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True


_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
VEC1, VEC2 = [0.1, 0.2, 0.3], [0.4, 0.5, 0.6]


class Counting:
    def __init__(self): self.n = 0
    def score(self, q, texts): self.n += 1; return [2.0] * len(texts)


FROZEN = HN.build_frozen_manifest(
    cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_vector=VEC1,
                                     authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
           "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_vector=VEC2,
                                        authorized_items=[("B", "tb")], sentinel_items=[("S", "ts")])},
    required_categories=["negation", "table-row"], evaluated_roles=["qc", "sales"])
MAN = E.m4_case_manifest_sha256(FROZEN)
check("frozen manifest valid", E.validate_m4_frozen_manifest(FROZEN) == [], E.validate_m4_frozen_manifest(FROZEN))


def _plan(**over):
    p = {"run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION,
         "n_set": [10, 20, 30, 50], "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5",
         "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
         "gate_tags": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"],
         "m4_case_manifest_sha256": MAN, "required_categories": ["negation", "table-row"],
         "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 1, "test_queries": 1},
         "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H},
         "model_commit": "a" * 40, "tokenizer_commit": "a" * 40, "model_file_manifest_sha256": _H,
         "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC)}
    p.update(over)
    return p
PLAN = _plan()
ROOT = RP.run_manifest_sha256(PLAN)
EXP = RP.m4_run_request(PLAN)


def _case(case_id, erole, category, vec, auth):
    scorer = HN.M4Scorer(RK.MockScorer({auth[1]: 2.0}), authorized_pairs=[HN.component(*auth)["pair_sha256"]])
    scorer.score_candidates(vec, [auth])   # authorized candidate เข้า model จริง
    return HN.build_case_record(case_id=case_id, effective_role=erole, category=category, query_vector=vec,
                                selected_n=50, unfiltered_items=[("S", "ts"), auth], sentinel_items=[("S", "ts")], scorer=scorer)


PER_CASE = [_case("case-qc", "qc", "negation", VEC1, ("A", "ta")),
            _case("case-sales", "sales", "table-row", VEC2, ("B", "tb"))]
VERDICTS = {"status": "PASS", "isolated_interlock": "PASS", "independent_oracle": "PASS",
            "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0}
RUN_META = {"m4_case_manifest_sha256": MAN, "run_id": "run-1", "run_manifest_sha256": ROOT,
            "model_revision": "a" * 40, "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H,
            "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC), "retrieval_index_manifest_sha256": _H,
            "eval_set_sha256": _H, "corpus_manifest_sha256": _H, "selected_n": 50, "decision_eligible": False}
EV = HN.assemble_evidence(PER_CASE, stage="preflight-n50", run_meta=RUN_META, verdicts=VERDICTS)
RC = HN.assemble_receipt(EV, run_manifest=ROOT, m4_case_manifest=MAN, expected={**EXP, "run_id": "run-1"},
                         argv=["python", "p2_m4_runner.py", "--preflight"], stdout=b"ok", stderr=b"",
                         isolation_marker="m4-run-uuid", started_utc="2026-08-05T05:00:00+07:00",
                         finished_utc="2026-08-05T05:03:00+07:00", exit_code=0)
EV["run_receipt_sha256"] = E.m4_run_receipt_sha256(RC)

check("harness bundle -> M4a gate ผ่าน", RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, RC) == [], RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, RC))
check("model_input มาจาก spy trace (== provider)", EV["per_case"][0]["model_input_pairs"] == EV["per_case"][0]["provider_pairs"] and len(EV["per_case"][0]["model_input_pairs"]) == 1)
check("raw_evidence recompute จาก body", EV["raw_evidence_sha256"] == hashlib.sha256(E._canonical_json(PER_CASE)).hexdigest())

# ── B1 ⭐ sentinel ถึง boundary -> raise ก่อน underlying (mock call = 0) ────────
_cnt = Counting()
_sc = HN.M4Scorer(_cnt, authorized_pairs=[HN.component("A", "ta")["pair_sha256"]])
check("B1 ⭐ sentinel เข้า score_candidates -> PermissionError", raises(lambda: _sc.score_candidates(VEC1, [("S", "ts")]), PermissionError))
check("B1 ⭐ underlying scorer ไม่ถูกเรียก (call=0) + sentinel_reached", _cnt.n == 0 and _sc.sentinel_reached is True)
check("B1: build_case_record ไม่รับ model_input จาก caller (derive จาก trace)", "model_input" not in HN.build_case_record.__code__.co_varnames)

# ── B2: run_id จาก RunPlan (ไม่ circular) ─────────────────────────────────────
check("B2: evidence run_id != plan.run_id -> gate fail", RP.validate_m4_preflight_bundle(PLAN, FROZEN, {**EV, "run_id": "other"}, RC) != [])
# receipt+evidence เลือก run_id เดียวกันเองที่ไม่ใช่ plan.run_id -> fail
_ev2 = {**EV, "run_id": "m4run"}
_rc2 = HN.assemble_receipt(_ev2, run_manifest=ROOT, m4_case_manifest=MAN, expected={**EXP, "run_id": "m4run"},
                           argv=["x"], stdout=b"", stderr=b"", isolation_marker="z",
                           started_utc="2026-08-05T05:00:00+07:00", finished_utc="2026-08-05T05:01:00+07:00")
_ev2["run_receipt_sha256"] = E.m4_run_receipt_sha256(_rc2)
check("B2: receipt+evidence run_id เดียวกันแต่ != plan -> gate fail", RP.validate_m4_preflight_bundle(PLAN, FROZEN, _ev2, _rc2) != [])

# ── B3: malformed receipt (NaN) -> gate error list ไม่ crash ──────────────────
_nan_rc = {**RC, "exit_code": float("nan")}
check("B3: receipt NaN -> gate คืน error list (ไม่ crash)", isinstance(RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, _nan_rc), list) and RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, _nan_rc) != [])
check("B3: _safe_m4_receipt_digest(NaN body) -> None", E._safe_m4_receipt_digest({**RC, "status": float("nan")}) is None)

# ── M1: timestamp order + command/bytes ───────────────────────────────────────
_rev_rc = {**RC, "started_utc": "2026-08-05T05:03:00+07:00", "finished_utc": "2026-08-05T05:00:00+07:00"}
check("M1: finished < started -> receipt error", any("finished_utc <" in e for e in E.validate_m4_run_receipt(_rev_rc, ROOT, MAN, {**EXP, "run_id": "run-1"}, EV)))
check("M1: argv ambiguity ['a b','c'] != ['a','b c']", HN._argv_hash(["a b", "c"]) != HN._argv_hash(["a", "b c"]))
check("M1: stdout ต้องเป็น bytes (ไม่ auto-coerce)", raises(lambda: HN._bytes_sha256("not-bytes"), TypeError))

# ── M2: verdict ไม่ self-stamp — assemble_evidence เอา verdict จากผล proof ─────
_bad_verd = HN.assemble_evidence(PER_CASE, stage="preflight-n50", run_meta=RUN_META,
                                 verdicts={**VERDICTS, "sentinel_reached_model": True})
check("M2: verdict sentinel_reached_model=True -> gate fail (builder ไม่ปั้น PASS)",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, {**_bad_verd, "run_receipt_sha256": EV["run_receipt_sha256"]}, RC) != [])

# ── M3: typed identity ────────────────────────────────────────────────────────
check("M3: component(1,'x') != component('1','x') (typed)", HN.component(1, "x")["pair_sha256"] != HN.component("1", "x")["pair_sha256"])
check("M3: query vector NaN -> ValueError", raises(lambda: HN._vec_hash([0.1, float("nan")]), ValueError))
check("M3: point_id ผิดชนิด -> TypeError", raises(lambda: HN.component({"bad": 1}, "x"), TypeError))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
