"""
Unit test ของ p2_m4_harness + public M4a gate — pure/offline
one-shot sealed CaseTrace · real query text เข้า cross-encoder + bind frozen · validate ก่อน delegate ·
run_meta ทับ verdict ไม่ได้ · verdict มาจาก proof · typed id

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
QT1, QT2 = "คำถาม negation", "คำถาม table-row"


class RecScorer:
    """MockScorer ที่บันทึก query ที่ underlying ได้รับจริง"""
    def __init__(self, smap): self.smap = smap; self.queries = []
    def score(self, q, texts): self.queries.append(q); return [self.smap.get(t, 0.0) for t in texts]


FROZEN = HN.build_frozen_manifest(
    cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_text=QT1, query_vector=VEC1,
                                     authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
           "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_text=QT2, query_vector=VEC2,
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


def _case(case_id, erole, category, qt, vec, auth):
    tr = HN.score_case(query_text=qt, query_vector=vec, candidates=[auth],
                       authorized_pairs=[HN.component(*auth)["pair_sha256"]], scorer=RecScorer({auth[1]: 2.0}))
    return HN.build_case_record(case_id=case_id, effective_role=erole, category=category, selected_n=50,
                                unfiltered_items=[("S", "ts"), auth], sentinel_items=[("S", "ts")], trace=tr)


PER_CASE = [_case("case-qc", "qc", "negation", QT1, VEC1, ("A", "ta")),
            _case("case-sales", "sales", "table-row", QT2, VEC2, ("B", "tb"))]
VERDICTS = HN.build_verdicts(isolation="PASS", oracle="PASS", case_count=2, traced_count=2)
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

# ── B2: real query text เข้า cross-encoder + bind frozen ──────────────────────
_rec = RecScorer({"ta": 2.0})
HN.score_case(query_text=QT1, query_vector=VEC1, candidates=[("A", "ta")], authorized_pairs=[HN.component("A", "ta")["pair_sha256"]], scorer=_rec)
check("B2: underlying scorer ได้ query ของ case จริง (ไม่ใช่ 'm4')", _rec.queries == [QT1])
check("B2: query_text_sha256 อยู่ใน evidence + ตรง frozen", EV["per_case"][0]["query_text_sha256"] == HN._text_hash(QT1))
_qt = copy.deepcopy(PER_CASE); _qt[0]["query_text_sha256"] = HN._text_hash("query อื่น")
_ev_qt = HN.assemble_evidence(_qt, stage="preflight-n50", run_meta=RUN_META, verdicts=VERDICTS)
check("B2: เปลี่ยน query_text (คง vector) -> gate fail", RP.validate_m4_preflight_bundle(PLAN, FROZEN, {**_ev_qt, "run_receipt_sha256": EV["run_receipt_sha256"]}, RC) != [])

# ── B1: run_meta ทับ verdict ไม่ได้ + verdict มาจาก proof ─────────────────────
check("B1: run_meta มี key protected (status) -> raise", raises(lambda: HN.assemble_evidence(PER_CASE, stage="preflight-n50", run_meta={**RUN_META, "status": "PASS"}, verdicts=VERDICTS), ValueError))
check("B1: run_meta ทับ per_case/raw -> raise", raises(lambda: HN.assemble_evidence(PER_CASE, stage="preflight-n50", run_meta={**RUN_META, "per_case": []}, verdicts=VERDICTS), ValueError))
_fail_verd = HN.build_verdicts(isolation="FAIL", oracle="PASS", case_count=2, traced_count=2)
_ev_fail = HN.assemble_evidence(PER_CASE, stage="preflight-n50", run_meta=RUN_META, verdicts=_fail_verd)
check("B1: proof FAIL -> evidence status FAIL -> gate fail", _ev_fail["status"] == "FAIL" and _ev_fail["isolated_interlock"] == "FAIL"
      and RP.validate_m4_preflight_bundle(PLAN, FROZEN, {**_ev_fail, "run_receipt_sha256": EV["run_receipt_sha256"]}, RC) != [])

# ── M1: one-shot sealed CaseTrace (immutable, แก้ย้อนหลังไม่ได้) ───────────────
_tr = HN.score_case(query_text=QT1, query_vector=VEC1, candidates=[("A", "ta")], authorized_pairs=[HN.component("A", "ta")["pair_sha256"]], scorer=RecScorer({"ta": 2.0}))
check("M1: CaseTrace immutable (แก้ pairs ไม่ได้)", raises(lambda: setattr(_tr, "pairs", ("x",)), AttributeError))
check("M1: build_case_record รับเฉพาะ CaseTrace (dict -> TypeError)", raises(lambda: HN.build_case_record(case_id="c", effective_role="qc", category="negation", selected_n=50, unfiltered_items=[], sentinel_items=[], trace={"pairs": []}), TypeError))

# ── M2/B1: validate ก่อน delegate — sentinel/NaN ไม่แตะ underlying ─────────────
_r1 = RecScorer({"ts": 9.0})
check("guard: sentinel เข้า score_case -> PermissionError ก่อน delegate", raises(lambda: HN.score_case(query_text=QT1, query_vector=VEC1, candidates=[("S", "ts")], authorized_pairs=[HN.component("A", "ta")["pair_sha256"]], scorer=_r1), PermissionError))
check("guard: underlying scorer ไม่ถูกเรียก (sentinel)", _r1.queries == [])
_r2 = RecScorer({"ta": 2.0})
check("M2: query vector NaN -> ValueError ก่อน delegate", raises(lambda: HN.score_case(query_text=QT1, query_vector=[0.1, float("nan")], candidates=[("A", "ta")], authorized_pairs=[HN.component("A", "ta")["pair_sha256"]], scorer=_r2), ValueError))
check("M2: underlying scorer ไม่ถูกเรียก (bad vector)", _r2.queries == [])

# ── M3 / misc ─────────────────────────────────────────────────────────────────
check("M3: component(1,'x') != component('1','x')", HN.component(1, "x")["pair_sha256"] != HN.component("1", "x")["pair_sha256"])
check("M1: argv ambiguity", HN._argv_hash(["a b", "c"]) != HN._argv_hash(["a", "b c"]))
check("M1: stdout ต้อง bytes", raises(lambda: HN._bytes_sha256("x"), TypeError))
check("model_input == provider (จาก trace)", EV["per_case"][0]["model_input_pairs"] == EV["per_case"][0]["provider_pairs"])
check("receipt finished<started -> error", any("finished_utc <" in e for e in E.validate_m4_run_receipt({**RC, "finished_utc": "2026-08-05T04:00:00+07:00"}, ROOT, MAN, {**EXP, "run_id": "run-1"}, EV)))
check("B3: receipt NaN -> gate error list (ไม่ crash)", isinstance(RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, {**RC, "exit_code": float("nan")}), list))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
