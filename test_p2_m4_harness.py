"""
Unit test ของ p2_m4_harness + public M4a gate — pure/offline
scorer provenance (metadata==M4RunRequest) · single run_case boundary (trace private) ·
IsolationProof/OracleProof derive verdict · marker load-bearing · real query · validate ก่อน delegate

    python test_p2_m4_harness.py
"""
import copy
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


class PinnedScorer:
    """cross-encoder จำลองที่ประกาศ metadata ตรง M4RunRequest (pinned model จริง)"""
    def __init__(self, smap): self.smap = smap; self.queries = []
    def metadata(self):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": "a" * 40,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H, "inference_config": dict(IC)}
    def score(self, q, texts): self.queries.append(q); return [self.smap.get(t, 0.0) for t in texts]


class MockScorer:
    """ไม่มี metadata() — ห้าม emit pinned evidence"""
    def __init__(self, smap): self.smap = smap; self.queries = []
    def score(self, q, texts): self.queries.append(q); return [self.smap.get(t, 0.0) for t in texts]


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


FROZEN = HN.build_frozen_manifest(
    cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_text=QT1, query_vector=VEC1,
                                     authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
           "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_text=QT2, query_vector=VEC2,
                                        authorized_items=[("B", "tb")], sentinel_items=[("S", "ts")])},
    required_categories=["negation", "table-row"], evaluated_roles=["qc", "sales"])
MAN = E.m4_case_manifest_sha256(FROZEN)
PLAN = _plan()
ROOT = RP.run_manifest_sha256(PLAN)
EXP = RP.m4_run_request(PLAN)
check("frozen manifest valid", E.validate_m4_frozen_manifest(FROZEN) == [], E.validate_m4_frozen_manifest(FROZEN))

INPUTS = [{"case_id": "case-qc", "query_text": QT1, "query_vector": VEC1, "candidates": [("A", "ta")],
           "unfiltered_items": [("S", "ts"), ("A", "ta")], "sentinel_items": [("S", "ts")]},
          {"case_id": "case-sales", "query_text": QT2, "query_vector": VEC2, "candidates": [("B", "tb")],
           "unfiltered_items": [("S", "ts"), ("B", "tb")], "sentinel_items": [("S", "ts")]}]

SCORER = PinnedScorer({"ta": 2.0, "tb": 2.0})
RECORDS, PROOF = HN.run_m4_cases(expected=EXP, frozen=FROZEN, scorer=SCORER, inputs=INPUTS, selected_n=50)
OBS = [{"case_id_sha256": cid, "observed_authorized_pairs": fc["authorized_pairs"], "observed_sentinel_pairs": fc["sentinel_pairs"]}
       for cid, fc in FROZEN["cases"].items()]
ISO = HN.build_isolation_proof(project_id="proj-u", network_id="net-u", volume_id="vol-u",
                               collection_id="coll-u", marker="m4-run-uuid")
ORACLE = HN.build_oracle_proof(frozen=FROZEN, index_sha256=_H, collection_id="coll-u", observed_visibility=OBS)
VERDICTS = HN.build_run_verdicts(expected=EXP, isolation_proof=ISO, oracle_proof=ORACLE, case_records=RECORDS, frozen=FROZEN)
RUN_META = {"m4_case_manifest_sha256": MAN, "run_id": "run-1", "run_manifest_sha256": ROOT,
            "image_digest": "sha256:" + "e" * 64, "retrieval_index_manifest_sha256": _H,
            "eval_set_sha256": _H, "corpus_manifest_sha256": _H, "selected_n": 50, "decision_eligible": False}
EV = HN.assemble_evidence(RECORDS, stage="preflight-n50", run_meta=RUN_META, scorer_proof=PROOF,
                          isolation_proof=ISO, oracle_proof=ORACLE, verdicts=VERDICTS)
RC = HN.assemble_receipt(EV, run_manifest=ROOT, m4_case_manifest=MAN, expected={**EXP, "run_id": "run-1"},
                         argv=["python", "p2_m4_runner.py", "--preflight"], stdout=b"ok", stderr=b"",
                         isolation_proof=ISO, started_utc="2026-08-05T05:00:00+07:00",
                         finished_utc="2026-08-05T05:03:00+07:00", exit_code=0)
EV["run_receipt_sha256"] = E.m4_run_receipt_sha256(RC)

check("run_m4_cases -> M4a gate ผ่าน", RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, RC) == [], RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, RC))
check("VERDICTS derive = PASS", VERDICTS["status"] == "PASS" and VERDICTS["unauthorized_in_model_inputs"] == 0)

# ── B1: scorer provenance — mock/no-metadata/wrong pin ห้าม emit pinned evidence ──────────────
check("B1: mock (ไม่มี metadata) -> validate_scorer_metadata raise", raises(lambda: HN.validate_scorer_metadata(MockScorer({"ta": 2.0}), EXP), TypeError))
check("B1: run_case ด้วย mock -> raise ก่อน delegate", raises(lambda: HN.run_case(expected=EXP, scorer=MockScorer({"ta": 2.0}), case_id="case-qc", frozen_case=FROZEN["cases"][HN._id_hash("case-qc")], query_text=QT1, query_vector=VEC1, candidates=[("A", "ta")], unfiltered_items=[("S", "ts"), ("A", "ta")], sentinel_items=[("S", "ts")], selected_n=50), TypeError))
class _WrongRev(PinnedScorer):
    def metadata(self): m = super().metadata(); m["model_revision"] = "f" * 40; return m
check("B1: scorer revision ผิด -> raise", raises(lambda: HN.validate_scorer_metadata(_WrongRev({}), EXP), ValueError))
class _WrongKind(PinnedScorer):
    def metadata(self): m = super().metadata(); m["kind"] = "mock"; return m
check("B1: scorer kind=mock -> raise", raises(lambda: HN.validate_scorer_metadata(_WrongKind({}), EXP), ValueError))
class _WrongIC(PinnedScorer):
    def metadata(self): m = super().metadata(); m["inference_config"] = {**IC, "device": "cuda"}; return m
check("B1: inference_config ผิด -> raise", raises(lambda: HN.validate_scorer_metadata(_WrongIC({}), EXP), ValueError))
class _WrongFM(PinnedScorer):
    def metadata(self): m = super().metadata(); m["model_file_manifest_sha256"] = "9" * 64; return m
check("B1: model_file_manifest ผิด -> raise", raises(lambda: HN.validate_scorer_metadata(_WrongFM({}), EXP), ValueError))
check("B1: scorer_kind/pin ใน evidence มาจาก ScorerProof", EV["scorer_kind"] == "pinned-cross-encoder" and EV["model_revision"] == "a" * 40 and EV["inference_config"] == IC)
check("B1: run_meta ใส่ model_revision (pin) -> raise (มาจาก ScorerProof เท่านั้น)", raises(lambda: HN.assemble_evidence(RECORDS, stage="preflight-n50", run_meta={**RUN_META, "model_revision": "a" * 40}, scorer_proof=PROOF, isolation_proof=ISO, oracle_proof=ORACLE, verdicts=VERDICTS), ValueError))
check("B1: run_meta ใส่ status (verdict) -> raise", raises(lambda: HN.assemble_evidence(RECORDS, stage="preflight-n50", run_meta={**RUN_META, "status": "PASS"}, scorer_proof=PROOF, isolation_proof=ISO, oracle_proof=ORACLE, verdicts=VERDICTS), ValueError))

# ── B1: single boundary — CaseTrace/build_case_record ไม่ใช่ public seam ───────────────────────
check("B1: ไม่มี public build_case_record/score_case (trace = private)", not hasattr(HN, "build_case_record") and not hasattr(HN, "score_case"))
check("B1: underlying scorer ได้ query ของ case จริง (ไม่ใช่ 'm4')", SCORER.queries == [QT1, QT2])
check("B1: query_text_sha256 ใน evidence ตรง frozen", EV["per_case"][0]["query_text_sha256"] == HN._text_hash(QT1))

# ── B1: durable evidence-body root — post-run proof swap ต้อง fail (receipt เดิม) ──────────────
_swap_iso = HN.build_isolation_proof(project_id="EVIL-p", network_id="EVIL-n", volume_id="EVIL-v", collection_id="coll-u", marker="m4-run-uuid")
_ev_swap = {**EV, "isolation_proof": _swap_iso}   # สลับ isolation หลังรัน, ไม่ recompute evidence_body, receipt เดิม
check("B1: สลับ IsolationProof resource id หลังรัน (receipt เดิม) -> gate fail", RP.validate_m4_preflight_bundle(PLAN, FROZEN, _ev_swap, RC) != [])
_ev_swap2 = HN.assemble_evidence(RECORDS, stage="preflight-n50", run_meta=RUN_META, scorer_proof=PROOF, isolation_proof=_swap_iso, oracle_proof=ORACLE, verdicts=HN.build_run_verdicts(expected=EXP, isolation_proof=_swap_iso, oracle_proof=ORACLE, case_records=RECORDS, frozen=FROZEN))
_ev_swap2["run_receipt_sha256"] = EV["run_receipt_sha256"]   # recompute evidence_body แต่ receipt เดิม (evidence_body_sha256 mismatch)
check("B1: recompute evidence_body แต่ไม่ออก receipt ใหม่ -> gate fail", any("evidence_body_sha256" in e for e in RP.validate_m4_preflight_bundle(PLAN, FROZEN, _ev_swap2, RC)))

# ── B2/B3: verdict derive จาก proof + records จริง ; proof = observation ───────────────────────
_bad_iso = {**ISO, "isolation_proof_sha256": "9" * 64}
_v_iso = HN.build_run_verdicts(expected=EXP, isolation_proof=_bad_iso, oracle_proof=ORACLE, case_records=RECORDS, frozen=FROZEN)
check("B2: isolation_proof recompute ไม่ตรง -> isolated_interlock FAIL + status FAIL", _v_iso["isolated_interlock"] == "FAIL" and _v_iso["status"] == "FAIL")
check("B3: initial_point_count != 0 -> isolation invalid", E.validate_m4_isolation_proof(HN.build_isolation_proof(project_id="p", network_id="n", volume_id="v", collection_id="c", marker="m", initial_point_count=5)) != [])
check("B3: production endpoint -> isolation invalid", E.validate_m4_isolation_proof(HN.build_isolation_proof(project_id="p", network_id="n", volume_id="v", collection_id="c", marker="m", endpoint_is_production=True)) != [])
check("B3: marker readback != written -> isolation invalid", E.validate_m4_isolation_proof(HN.build_isolation_proof(project_id="p", network_id="n", volume_id="v", collection_id="c", marker="m", marker_readback="different")) != [])
check("B2: oracle observed != frozen visibility -> oracle invalid", E.validate_m4_oracle_proof(HN.build_oracle_proof(frozen=FROZEN, index_sha256=_H, collection_id="coll-u", observed_visibility=[{"case_id_sha256": cid, "observed_authorized_pairs": [HN.component("Z", "tz")["pair_sha256"]], "observed_sentinel_pairs": fc["sentinel_pairs"]} for cid, fc in FROZEN["cases"].items()]), MAN, _H, FROZEN) != [])
check("B2: oracle case ไม่ครอบ frozen -> oracle invalid", E.validate_m4_oracle_proof(HN.build_oracle_proof(frozen=FROZEN, index_sha256=_H, collection_id="coll-u", observed_visibility=OBS[:1]), MAN, _H, FROZEN) != [])
check("B2: isolation/oracle collection ไม่ตรง -> gate fail", any("collection_id" in e for e in E.validate_m4_run_evidence({**EV, "oracle_proof": HN.build_oracle_proof(frozen=FROZEN, index_sha256=_H, collection_id="OTHER", observed_visibility=OBS)}, FROZEN, EXP, _H, _H)))

# ── B2: marker load-bearing (proof ผูก marker เดียวกับ receipt) ────────────────────────────────
_iso2 = HN.build_isolation_proof(project_id="proj-u", network_id="net-u", volume_id="vol-u", collection_id="coll-u", marker="OTHER-marker")
_rc2 = HN.assemble_receipt(EV, run_manifest=ROOT, m4_case_manifest=MAN, expected={**EXP, "run_id": "run-1"}, argv=["python", "p2_m4_runner.py", "--preflight"], stdout=b"ok", stderr=b"", isolation_proof=_iso2, started_utc="2026-08-05T05:00:00+07:00", finished_utc="2026-08-05T05:03:00+07:00", exit_code=0)
_ev2 = {**EV, "run_receipt_sha256": E.m4_run_receipt_sha256(_rc2)}
check("B2: receipt marker != evidence isolation_proof.marker -> gate fail", any("marker" in e for e in RP.validate_m4_preflight_bundle(PLAN, FROZEN, _ev2, _rc2)))

# ── B2: sentinel/unauthorized ใน record -> verdict FAIL (derive จริง) ──────────────────────────
_leak_rec = copy.deepcopy(RECORDS)
_leak_rec[0]["model_input_pairs"] = [HN.component("S", "ts")["pair_sha256"]]
_v_leak = HN.build_run_verdicts(expected=EXP, isolation_proof=ISO, oracle_proof=ORACLE, case_records=_leak_rec, frozen=FROZEN)
check("B2: model เห็น sentinel -> sentinel_reached_model=True + status FAIL", _v_leak["sentinel_reached_model"] is True and _v_leak["status"] == "FAIL")

# ── M2: whitespace/control-only query ไม่ถึง scorer ───────────────────────────────────────────
_ws = PinnedScorer({"ta": 2.0})
check("M2: query whitespace-only -> raise ก่อน delegate", raises(lambda: HN.run_case(expected=EXP, scorer=_ws, case_id="case-qc", frozen_case=FROZEN["cases"][HN._id_hash("case-qc")], query_text="   ", query_vector=VEC1, candidates=[("A", "ta")], unfiltered_items=[("S", "ts"), ("A", "ta")], sentinel_items=[("S", "ts")], selected_n=50), ValueError))
check("M2: underlying scorer ไม่ถูกเรียก (whitespace query)", _ws.queries == [])
_nan = PinnedScorer({"ta": 2.0})
check("M2: query vector NaN -> raise ก่อน delegate", raises(lambda: HN.run_case(expected=EXP, scorer=_nan, case_id="case-qc", frozen_case=FROZEN["cases"][HN._id_hash("case-qc")], query_text=QT1, query_vector=[0.1, float("nan")], candidates=[("A", "ta")], unfiltered_items=[("S", "ts"), ("A", "ta")], sentinel_items=[("S", "ts")], selected_n=50), ValueError))
check("M2: underlying scorer ไม่ถูกเรียก (bad vector)", _nan.queries == [])
_sen = PinnedScorer({"ts": 9.0})
check("guard: sentinel candidate เข้า run_case -> PermissionError", raises(lambda: HN.run_case(expected=EXP, scorer=_sen, case_id="case-qc", frozen_case=FROZEN["cases"][HN._id_hash("case-qc")], query_text=QT1, query_vector=VEC1, candidates=[("S", "ts")], unfiltered_items=[("S", "ts")], sentinel_items=[("S", "ts")], selected_n=50), PermissionError))
check("guard: underlying scorer ไม่ถูกเรียก (sentinel)", _sen.queries == [])

# ── M1: real PinnedCrossEncoder.metadata() ตรง contract (positive/negative) ────────────────────
_ce = RK.PinnedCrossEncoder(None, None, model_name=RK.RERANKER_MODEL, model_commit="a" * 40, tokenizer_commit="a" * 40,
                            file_manifest_sha256=_H, dtype="float32", torch_version="x", transformers_version="y",
                            device="cpu", max_length=512, batch_size=16)
_pce_proof = HN.validate_scorer_metadata(_ce, EXP)
check("M1: PinnedCrossEncoder.metadata() ผ่าน validate_scorer_metadata (real contract)", _pce_proof["scorer_kind"] == "pinned-cross-encoder" and _pce_proof["inference_config"] == IC)
_ce_bad = RK.PinnedCrossEncoder(None, None, model_name=RK.RERANKER_MODEL, model_commit="f" * 40, tokenizer_commit="a" * 40,
                                file_manifest_sha256=_H, dtype="float32", torch_version="x", transformers_version="y")
check("M1: PinnedCrossEncoder revision ผิด -> raise", raises(lambda: HN.validate_scorer_metadata(_ce_bad, EXP), ValueError))

# ── M1: schema v5 + run bind ตรวจซ้ำ ──────────────────────────────────────────────────────────
check("M1: evidence schema_version = p2-m4-v5", EV["schema_version"] == "p2-m4-v5" and E.M4_SCHEMA_VERSION == "p2-m4-v5")
check("M3: component(1,'x') != component('1','x')", HN.component(1, "x")["pair_sha256"] != HN.component("1", "x")["pair_sha256"])
check("M1: argv ambiguity", HN._argv_hash(["a b", "c"]) != HN._argv_hash(["a", "b c"]))
check("M1: stdout ต้อง bytes", raises(lambda: HN._bytes_sha256("x"), TypeError))
check("model_input == provider (จาก trace)", EV["per_case"][0]["model_input_pairs"] == EV["per_case"][0]["provider_pairs"])
check("receipt finished<started -> error", any("finished_utc <" in e for e in E.validate_m4_run_receipt({**RC, "finished_utc": "2026-08-05T04:00:00+07:00"}, ROOT, MAN, {**EXP, "run_id": "run-1"}, EV)))
check("B3: receipt NaN -> gate error list (ไม่ crash)", isinstance(RP.validate_m4_preflight_bundle(PLAN, FROZEN, EV, {**RC, "exit_code": float("nan")}), list))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
