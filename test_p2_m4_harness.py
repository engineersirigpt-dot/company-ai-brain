"""
Unit test ของ p2_m4_harness + public M4a gate (validate_m4_preflight_bundle) — pure/offline
พิสูจน์ harness ผลิต evidence+receipt ที่ผ่าน gate จริง + gate เป็น trust anchor กับ validated RunPlan
(เปลี่ยน evidence/frozen โดยคง RunPlan เดิม → gate ต้อง fail)

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


_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}

# ── frozen seed (2 cases: qc/negation, sales/table-row) ────────────────────────
FROZEN = HN.build_frozen_manifest(
    cases={
        "case-qc": HN.frozen_case(effective_role="qc", category="negation", query_probe="q1",
                                  authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
        "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_probe="q2",
                                     authorized_items=[("B", "tb")], sentinel_items=[("S", "ts")]),
    },
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


def _case_record(case_id, erole, category, qp, auth):
    spy = HN.SpyScorer(RK.MockScorer({auth[1]: 2.0}))
    spy.score(qp, [auth[1]])   # authorized text เข้า model จริง (1 call, 1 finite score)
    return HN.build_case_record(case_id=case_id, effective_role=erole, category=category, query_probe=qp, selected_n=50,
                                unfiltered=[("S", "ts"), auth], provider=[auth], model_input=[auth],
                                rerank_output=[auth], sentinel_items=[("S", "ts")], spy=spy)


PER_CASE = [_case_record("case-qc", "qc", "negation", "q1", ("A", "ta")),
            _case_record("case-sales", "sales", "table-row", "q2", ("B", "tb"))]
RUN_META = {"m4_case_manifest_sha256": MAN, "run_id": "m4run", "run_manifest_sha256": ROOT,
            "model_revision": "a" * 40, "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H,
            "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC), "retrieval_index_manifest_sha256": _H,
            "eval_set_sha256": _H, "corpus_manifest_sha256": _H, "selected_n": 50, "decision_eligible": False}
EV = HN.assemble_evidence(PER_CASE, stage="preflight-n50", run_meta=RUN_META)
RC = HN.assemble_receipt(EV, run_manifest=ROOT, m4_case_manifest=MAN, expected={**EXP, "run_id": "m4run"},
                         argv=["python", "p2_m4_runner.py", "--preflight"], stdout=b"SMOKE", stderr=b"",
                         isolation_marker="m4-run-uuid-xyz", started_utc="2026-08-05T05:00:00+07:00",
                         finished_utc="2026-08-05T05:03:00+07:00", exit_code=0)
EV["run_receipt_sha256"] = E.m4_run_receipt_sha256(RC)

# ── harness → public M4a gate ─────────────────────────────────────────────────
check("harness bundle -> validate_m4_preflight_bundle ผ่าน (M4a PASS mechanics)",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, RC) == [], RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, RC))
check("assemble_evidence recompute raw digest จาก per_case body",
      EV["raw_evidence_sha256"] == hashlib.sha256(E._canonical_json(PER_CASE)).hexdigest())
check("run_receipt digest ผูก evidence", E.m4_run_receipt_sha256(RC) == EV["run_receipt_sha256"])

# ── trust anchor: เปลี่ยน evidence/frozen โดยคง RunPlan → gate fail ────────────
_ev_pin = copy.deepcopy(EV); _ev_pin["model_revision"] = "f" * 40
check("anchor: evidence pin != RunPlan (แม้ evidence self-consistent) -> fail",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, _ev_pin, RC) != [])
# เปลี่ยน frozen query hash → manifest digest เปลี่ยน → != RunPlan.m4_case_manifest_sha256
_frz = copy.deepcopy(FROZEN)
for _cid in _frz["cases"]:
    _frz["cases"][_cid]["query_vector_sha256"] = "9" * 64
check("anchor: frozen เปลี่ยน (digest != RunPlan) -> fail", RP.validate_m4_preflight_bundle(PLAN, _frz, EV, RC) != [])
check("anchor: invalid RunPlan -> fail (validate_run_plan ก่อน)", RP.validate_m4_preflight_bundle(_plan(seed=None), FROZEN, EV, RC) != [])

# ── receipt body validation ───────────────────────────────────────────────────
check("receipt exit_code != 0 -> gate fail", RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, {**RC, "exit_code": 1}) != [])
_rc2 = {**RC, "run_manifest_sha256": "b" * 64}
check("receipt run_manifest != root -> fail", any("run_manifest" in e for e in E.validate_m4_run_receipt(_rc2, ROOT, MAN, {**EXP, "run_id": "m4run"}, EV)))
check("receipt tampered body (digest mismatch) -> gate fail",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, {**RC, "isolation_marker_sha256": "c" * 64}) != [])
check("receipt raw_evidence != evidence -> fail", any("raw_evidence" in e for e in E.validate_m4_run_receipt({**RC, "raw_evidence_sha256": "d" * 64}, ROOT, MAN, {**EXP, "run_id": "m4run"}, EV)))

# ── SpyScorer derive counts จริง (ไม่ self-stamp) ──────────────────────────────
_spy = HN.SpyScorer(RK.MockScorer({"x": 1.0, "y": 2.0}))
_spy.score("q", ["x", "y"])
check("SpyScorer จับ call/score จริง", _spy.calls == 1 and _spy.scores == [1.0, 2.0])

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
