"""
Unit test ของ M4Evidence v4 (per-case authoritative) — pure/offline
security invariant ต่อ case/role (กัน cross-role leak) + recompute raw/pair + frozen manifest +
QueryProbe ผูก frozen + exact M4RunRequest (pin/image/index) + rank position + coverage + no-crash

    python test_p2_m4.py
"""
import copy
import hashlib
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_eval as E

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))


_H = "a" * 64
def _s(x): return hashlib.sha256(x.encode()).hexdigest()
def _pair(pid, txt): return E._pair_sha256(pid, txt)
def _raw(pcs): return hashlib.sha256(E._canonical_json(pcs)).hexdigest()

AID, ATX = _s("A"), _s("tA"); PA = _pair(AID, ATX)
BID, BTX = _s("B"), _s("tB"); PB = _pair(BID, BTX)
SID, STX = _s("S"), _s("tS"); PS = _pair(SID, STX)
QC, SALES = _s("qc"), _s("sales")
CQC, CSA = _s("case-qc"), _s("case-sales")
QVC, QVS = _s("qv-" + CQC), _s("qv-" + CSA)
QTC, QTS = _s("qt-" + CQC), _s("qt-" + CSA)
IC = {"model_name": "BAAI/bge-reranker-v2-m3", "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
EXP = {"model_revision": "a" * 40, "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H,
       "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC), "retrieval_index_manifest_sha256": _H}


_COLL = _s("coll")
def iso_proof(marker="m4-marker", **over):
    b = {"project_id_sha256": _s("proj"), "network_id_sha256": _s("net"), "volume_id_sha256": _s("vol"),
         "collection_id_sha256": _COLL, "marker_written_sha256": _s(marker), "marker_readback_sha256": _s(marker),
         "initial_point_count": 0, "network_published_ports": 0, "endpoint_is_production": False}
    b.update(over)
    b["isolation_proof_sha256"] = E.m4_isolation_proof_sha256(b)
    return b
def oracle_proof(frozen, man=None, index=_H, coll=None, observed=None):
    src = observed if observed is not None else [
        {"case_id_sha256": cid, "observed_authorized_pairs": fc["authorized_pairs"], "observed_sentinel_pairs": fc["sentinel_pairs"]}
        for cid, fc in frozen["cases"].items()]
    obs = [{"case_id_sha256": o["case_id_sha256"], "observed_authorized_pairs": sorted(o["observed_authorized_pairs"]),
            "observed_sentinel_pairs": sorted(o["observed_sentinel_pairs"])} for o in src]
    b = {"frozen_manifest_sha256": man or E.m4_case_manifest_sha256(frozen), "retrieval_index_manifest_sha256": index,
         "collection_id_sha256": coll or _COLL, "observation_sha256": E.m4_observation_sha256(obs), "observed_visibility": obs}
    b["oracle_proof_sha256"] = E.m4_oracle_proof_sha256(b)
    return b


def comp(pid, txt): return {"point_id_sha256": pid, "rerank_text_sha256": txt, "pair_sha256": _pair(pid, txt)}
def fcase(role, erole, category, auth, sent, qv, qt):
    return {"role_identity_sha256": role, "effective_role": erole, "category": category,
            "query_text_sha256": qt, "query_vector_sha256": qv, "authorized_pairs": auth, "sentinel_pairs": sent}
def pcase(case_id, role, category, auth_pair, comps, n=10, erole="qc", qv=None, qt=None):
    qv = qv or _s("qv-" + case_id)
    qt = qt or _s("qt-" + case_id)
    return {"case_id_sha256": case_id, "role_identity_sha256": role, "effective_role": erole,
            "category": category, "selected_n": n, "query_text_sha256": qt, "query_vector_sha256": qv,
            "unfiltered_query_vector_sha256": qv, "filtered_query_vector_sha256": qv,
            "unfiltered_limit": n, "filtered_limit": n, "pair_components": comps,
            "unfiltered_topn_pairs": [PS, auth_pair], "observed_sentinel_ranks": [[PS, 1]],
            "provider_pairs": [auth_pair], "model_input_pairs": [auth_pair], "rerank_output_pairs": [auth_pair],
            "model_call_count": 1, "model_input_count": 1, "score_count": 1, "all_scores_finite": True, "status": "PASS"}
def m4(pcs, man, frozen=None, **over):
    fr = frozen if frozen is not None else FROZEN1
    m = {"schema_version": E.M4_SCHEMA_VERSION, "status": "PASS", "isolated_interlock": "PASS", "independent_oracle": "PASS",
         "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0, "scorer_kind": "pinned-cross-encoder",
         "evidence_stage": "selected-n", "selected_n": 10, "selection_digest": _H,
         "m4_case_manifest_sha256": man, "per_case": pcs, "raw_evidence_sha256": _raw(pcs),
         "isolation_proof": iso_proof(), "oracle_proof": oracle_proof(fr),
         "model_revision": "a" * 40, "tokenizer_revision": "a" * 40, "image_digest": "sha256:" + "e" * 64,
         "model_file_manifest_sha256": _H, "inference_config": dict(IC), "run_receipt_sha256": _H,
         "retrieval_index_manifest_sha256": _H, "run_id": "run-1", "eval_set_sha256": _H, "corpus_manifest_sha256": _H}
    ebs = over.pop("evidence_body_sha256", None)
    m.update(over)
    m["evidence_body_sha256"] = ebs if ebs is not None else hashlib.sha256(
        E._canonical_json({k: v for k, v in m.items() if k not in ("run_receipt_sha256", "evidence_body_sha256")})).hexdigest()
    return m
def V(m4d, frozen, **kw):
    return E.validate_m4_run_evidence(m4d, frozen, kw.pop("expected", EXP), _H, _H, **kw)


FROZEN1 = {"cases": {CQC: fcase(QC, "qc", "negation", [PA], [PS], QVC, QTC)},
           "required_categories": ["negation"], "evaluated_roles": ["qc"]}
MAN1 = E.m4_case_manifest_sha256(FROZEN1)
PC1 = [pcase(CQC, QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)])]
_M4 = m4(PC1, MAN1)

check("m4 v4 per-case PASS -> valid", V(_M4, FROZEN1) == [], V(_M4, FROZEN1))
check("require frozen (public gate ห้าม fail-open) -> error เมื่อ frozen None", V(_M4, None) != [])
check("require expected M4RunRequest -> error เมื่อ expected None", E.validate_m4_run_evidence(_M4, FROZEN1, None, _H, _H) != [])
check("schema_version ผิด -> error", any("schema_version" in e for e in V(m4(PC1, MAN1, schema_version="v3"), FROZEN1)))

# ── B3: recompute raw + pair ──────────────────────────────────────────────────
check("B3: raw_evidence_sha256 ไม่ตรง recompute -> error", any("raw_evidence" in e for e in V(m4(PC1, MAN1, raw_evidence_sha256=_H), FROZEN1)))
_badpair = copy.deepcopy(PC1); _badpair[0]["pair_components"][0]["pair_sha256"] = "0" * 64
check("B3: pair_sha256 ไม่ตรงสูตร -> error", any("ไม่ตรงสูตร" in e for e in V(m4(_badpair, MAN1, raw_evidence_sha256=_raw(_badpair)), FROZEN1)))
_undrv = copy.deepcopy(PC1); _undrv[0]["provider_pairs"] = ["9" * 64]
check("B3: pair ไม่ได้ derive จาก components -> error", any("derive" in e for e in V(m4(_undrv, MAN1, raw_evidence_sha256=_raw(_undrv)), FROZEN1)))
check("M1: run_receipt_sha256 หาย -> error", any("run_receipt" in e for e in V(m4(PC1, MAN1, run_receipt_sha256="x"), FROZEN1)))

# ── B1: within-case security ──────────────────────────────────────────────────
def _mut1(fn):
    pcs = copy.deepcopy(PC1); fn(pcs[0]); return V(m4(pcs, MAN1, raw_evidence_sha256=_raw(pcs)), FROZEN1)
check("B1: sentinel ถึง model_input (leak) -> error", any("LEAK" in e for e in _mut1(lambda c: (c["pair_components"].append(comp(SID, STX)), c.__setitem__("model_input_pairs", [PS]), c.__setitem__("provider_pairs", [PA, PS])))))
check("B1: provider ⊄ authorized -> error", any("provider ไม่ ⊆" in e for e in _mut1(lambda c: (c["pair_components"].append(comp(BID, BTX)), c.__setitem__("provider_pairs", [PB]), c.__setitem__("model_input_pairs", [PB]), c.__setitem__("rerank_output_pairs", [PB])))))
check("B1: rerank ไม่ใช่ permutation -> error", any("permutation" in e for e in _mut1(lambda c: (c["pair_components"].append(comp(BID, BTX)), c.__setitem__("rerank_output_pairs", [PB])))))
check("B1: sentinel ไม่ติด unfiltered top-N -> error", any("unfiltered" in e for e in _mut1(lambda c: c.__setitem__("unfiltered_topn_pairs", [PA]))))
check("B1: sentinel rank เท็จ (unfiltered[0]=authorized) -> error", any("rank ไม่ตรงตำแหน่ง" in e for e in _mut1(lambda c: (c.__setitem__("unfiltered_topn_pairs", [PA, PS]), c.__setitem__("observed_sentinel_ranks", [[PS, 1]])))))
check("B1: unfiltered_topn ซ้ำ -> error", any("ซ้ำ" in e for e in _mut1(lambda c: c.__setitem__("unfiltered_topn_pairs", [PS, PA, PS]))))
check("B3: status != PASS -> error", any("status" in e for e in _mut1(lambda c: c.__setitem__("status", "INCOMPLETE"))))
check("B3: score_count != model_input_count -> error", any("score_count" in e for e in _mut1(lambda c: c.__setitem__("score_count", 3))))

# ── B1 ⭐ cross-role swap: aggregate ผ่านแต่ per-case จับได้ ────────────────────
FROZEN2 = {"cases": {CQC: fcase(QC, "qc", "negation", [PA], [PS], QVC, QTC),
                     CSA: fcase(SALES, "sales", "table-row", [PB], [PS], QVS, QTS)},
           "required_categories": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"]}
MAN2 = E.m4_case_manifest_sha256(FROZEN2)
PC_SWAP = [pcase(CQC, QC, "negation", PB, [comp(BID, BTX), comp(SID, STX)], erole="qc"),
           pcase(CSA, SALES, "table-row", PA, [comp(AID, ATX), comp(SID, STX)], erole="sales")]
check("B1 ⭐ cross-role swap -> error (per-case จับได้ แม้ aggregate subset ผ่าน)",
      any("provider ไม่ ⊆" in e for e in V(m4(PC_SWAP, MAN2, frozen=FROZEN2, raw_evidence_sha256=_raw(PC_SWAP)), FROZEN2)))
PC2 = [pcase(CQC, QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)], erole="qc"),
       pcase(CSA, SALES, "table-row", PB, [comp(BID, BTX), comp(SID, STX)], erole="sales")]
check("2-case ถูกต้อง -> valid", V(m4(PC2, MAN2, frozen=FROZEN2, raw_evidence_sha256=_raw(PC2)), FROZEN2) == [], V(m4(PC2, MAN2, frozen=FROZEN2, raw_evidence_sha256=_raw(PC2)), FROZEN2))

# ── B1: durable evidence-body root (post-run body swap ต้อง fail) ─────────────
check("B1: evidence_body_sha256 ไม่ตรง top-level body -> error", any("evidence_body_sha256" in e for e in V(m4(PC1, MAN1, evidence_body_sha256="0" * 64), FROZEN1)))
_swap = m4(PC1, MAN1); _swap["oracle_proof"] = oracle_proof(FROZEN1, coll=_s("swapped"))   # สลับ proof หลังคำนวณ body
check("B1: สลับ oracle_proof หลัง body digest -> error", any("evidence_body_sha256" in e for e in V(_swap, FROZEN1)))

# ── B3: IsolationProof = interlock observation (ไม่ใช่แค่ names) ────────────────
check("B3: isolation_proof หาย -> error", any("isolation_proof" in e for e in V(m4(PC1, MAN1, isolation_proof=None), FROZEN1)))
check("B3: resource id ไม่ distinct -> error", any("distinct" in e for e in V(m4(PC1, MAN1, isolation_proof=iso_proof(network_id_sha256=_s("proj"))), FROZEN1)))
check("B3: collection ไม่ว่างก่อน seed -> error", any("initial_point_count" in e for e in V(m4(PC1, MAN1, isolation_proof=iso_proof(initial_point_count=3)), FROZEN1)))
check("B3: network publish port -> error", any("network_published_ports" in e for e in V(m4(PC1, MAN1, isolation_proof=iso_proof(network_published_ports=1)), FROZEN1)))
check("B3: production endpoint -> error", any("endpoint_is_production" in e for e in V(m4(PC1, MAN1, isolation_proof=iso_proof(endpoint_is_production=True)), FROZEN1)))
check("B3: marker readback != written -> error", any("marker readback" in e for e in V(m4(PC1, MAN1, isolation_proof=iso_proof(marker_readback_sha256=_s("other"))), FROZEN1)))

# ── B2: OracleProof = independent observed visibility (ไม่ derive จาก frozen เฉย ๆ) ──
check("B2: oracle observed_visibility ว่าง -> error", any("observed_visibility" in e for e in V(m4(PC1, MAN1, oracle_proof=oracle_proof(FROZEN1, observed=[])), FROZEN1)))
check("B2: observed_authorized != frozen -> error", any("observed_authorized_pairs" in e for e in V(m4(PC1, MAN1, oracle_proof=oracle_proof(FROZEN1, observed=[{"case_id_sha256": CQC, "observed_authorized_pairs": [PB], "observed_sentinel_pairs": [PS]}])), FROZEN1)))
check("B2: observed sentinel != frozen -> error", any("observed_sentinel_pairs" in e for e in V(m4(PC1, MAN1, oracle_proof=oracle_proof(FROZEN1, observed=[{"case_id_sha256": CQC, "observed_authorized_pairs": [PA], "observed_sentinel_pairs": [PB]}])), FROZEN1)))
check("B2: observed case ไม่ครอบ frozen -> error", any("observed case set" in e for e in V(m4(PC2, MAN2, frozen=FROZEN2, oracle_proof=oracle_proof(FROZEN2, observed=[{"case_id_sha256": CQC, "observed_authorized_pairs": [PA], "observed_sentinel_pairs": [PS]}]), raw_evidence_sha256=_raw(PC2)), FROZEN2)))
check("B2: oracle frozen_manifest ผิด -> error", any("oracle_proof frozen_manifest" in e for e in V(m4(PC1, MAN1, oracle_proof=oracle_proof(FROZEN1, man="0" * 64)), FROZEN1)))
check("B2: oracle index != M4RunRequest -> error", any("oracle_proof retrieval_index" in e for e in V(m4(PC1, MAN1, oracle_proof=oracle_proof(FROZEN1, index="9" * 64)), FROZEN1)))
check("B2: isolation/oracle collection ไม่ตรง -> error", any("collection_id ไม่ตรง" in e for e in V(m4(PC1, MAN1, oracle_proof=oracle_proof(FROZEN1, coll=_s("other-coll"))), FROZEN1)))

# ── B1 (QueryProbe ผูก frozen): เลือก vector หลังเห็นผลไม่ได้ ──────────────────
_qp = copy.deepcopy(PC1)
_qp[0]["query_vector_sha256"] = _qp[0]["unfiltered_query_vector_sha256"] = _qp[0]["filtered_query_vector_sha256"] = "9" * 64
check("B1: QueryProbe เปลี่ยนทั้งสาม (ไม่ตรง frozen) -> error", any("frozen QueryProbe" in e for e in V(m4(_qp, MAN1, raw_evidence_sha256=_raw(_qp)), FROZEN1)))
_qt = copy.deepcopy(PC1); _qt[0]["query_text_sha256"] = "9" * 64
check("B2: query_text_sha256 ไม่ตรง frozen (คง vector) -> error", any("query_text_sha256" in e for e in V(m4(_qt, MAN1, raw_evidence_sha256=_raw(_qt)), FROZEN1)))
_qtf = copy.deepcopy(PC1); _qtf[0]["query_text_sha256"] = "not-a-hash"
check("B2: query_text_sha256 format ผิด -> error", any("query_text_sha256" in e for e in V(m4(_qtf, MAN1, raw_evidence_sha256=_raw(_qtf)), FROZEN1)))
check("M2: filtered query vector != unfiltered -> error", any("query vector" in e for e in _mut1(lambda c: c.__setitem__("filtered_query_vector_sha256", _s("other")))))

# ── B2: exact M4RunRequest (M4a/M4b) ──────────────────────────────────────────
check("B2: expected model_revision ไม่ตรง -> error", any("expected M4RunRequest" in e for e in V(_M4, FROZEN1, expected={**EXP, "model_revision": "f" * 40})))
check("B2: expected image_digest ไม่ตรง -> error", any("expected M4RunRequest" in e for e in V(_M4, FROZEN1, expected={**EXP, "image_digest": "sha256:" + "0" * 64})))
check("B2: expected ขาด inference_config -> error", any("ขาด inference_config" in e for e in V(_M4, FROZEN1, expected={k: v for k, v in EXP.items() if k != "inference_config"})))
check("B2: m4 ไม่มี inference_config (missing) -> error", any("inference_config" in e for e in V(m4(PC1, MAN1, inference_config=None), FROZEN1)))

# ── B2: frozen binding + role coverage ────────────────────────────────────────
check("B2: m4_case_manifest_sha256 != frozen -> error", any("m4_case_manifest_sha256" in e for e in V(m4(PC1, "0" * 64), FROZEN1)))
FROZEN_NOCASE = {"cases": {CQC: fcase(QC, "qc", "negation", [PA], [PS], QVC, QTC)},
                 "required_categories": ["negation"], "evaluated_roles": ["qc", "sales"]}
check("B2: evaluated_role 'sales' ไม่มี case -> frozen manifest error", any("evaluated_roles ไม่ครบ" in e for e in E.validate_m4_frozen_manifest(FROZEN_NOCASE)))
check("B2: run evidence กับ frozen role ไม่ครบ -> error", V(m4(PC1, E.m4_case_manifest_sha256(FROZEN_NOCASE)), FROZEN_NOCASE) != [])

# ── B3/M2: malformed frozen -> error list ไม่ crash ───────────────────────────
FROZEN_BAD = {"cases": {CQC: fcase(QC, "qc", "negation", [PA], [PS], QVC, QTC)}, "required_categories": ["negation", None], "evaluated_roles": ["qc"]}
check("B3: required_categories มี None -> error (ไม่ crash)", E.validate_m4_frozen_manifest(FROZEN_BAD) != [])
check("B3: run evidence กับ malformed frozen -> error (fail-closed)", V(_M4, FROZEN_BAD) != [])
check("B3: _safe_m4_manifest_digest(malformed) -> None", E._safe_m4_manifest_digest(FROZEN_BAD) is None)
check("M2: mixed-type unknown keys -> error list (ไม่ TypeError)", E.validate_m4_frozen_manifest({None: 1, 5: 2, "cases": {}}) != [])
check("B2: frozen authorized_pairs ซ้ำ -> error", any("ซ้ำ" in e for e in E.validate_m4_frozen_manifest({"cases": {CQC: fcase(QC, "qc", "negation", [PA, PA], [PS], QVC, QTC)}, "required_categories": ["negation"], "evaluated_roles": ["qc"]})))

# ── M1: exact hash-only (reject raw/unknown) ──────────────────────────────────
_leak = copy.deepcopy(PC1); _leak[0]["raw_text"] = "SECRET-QUERY"
check("M1: per_case มี raw_text -> error (exact hash-only)", any("unknown/raw" in e for e in V(m4(_leak, MAN1, raw_evidence_sha256=_raw(_leak)), FROZEN1)))
check("M1: top-level m4 unknown field -> error", any("unknown/raw" in e for e in V(m4(PC1, MAN1, secret="x"), FROZEN1)))

# ── M1/M3: stage contract ─────────────────────────────────────────────────────
_pf_pc = [pcase(CQC, QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)], n=50)]
_PF = m4(_pf_pc, MAN1, evidence_stage="preflight-n50", selected_n=50, selection_digest=None, decision_eligible=False, raw_evidence_sha256=_raw(_pf_pc))
check("M3: preflight standalone -> valid", V(_PF, FROZEN1) == [], V(_PF, FROZEN1))
check("M1: preflight ใน decision path (require selected-n) -> error", any("evidence_stage" in e for e in V(_PF, FROZEN1, require_stage=E.M4_STAGE_SELECTED)))
check("B2: preflight (M4a) exact pin ผิด -> error (ไม่ format-only)", any("expected M4RunRequest" in e for e in V(_PF, FROZEN1, expected={**EXP, "model_revision": "f" * 40})))
check("M3: selected-n ขาด selection_digest -> error", any("selection_digest" in e for e in V(m4(PC1, MAN1, selection_digest=None), FROZEN1)))
check("M1: run_manifest binding ไม่ตรง -> error", any("run_manifest" in e for e in V(m4(PC1, MAN1, run_manifest_sha256=_H), FROZEN1, run_manifest_sha256="b" * 64)))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
