"""
Unit test ของ M4Evidence v4 (per-case authoritative) — pure/offline
พิสูจน์ว่า security invariant ตรวจ **ต่อ case/role** (กัน cross-role leak ซ่อนใน aggregate) +
recompute raw/pair digests จาก body + bind frozen manifest + per-case rank/category coverage

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

# points: A (authorized-qc), B (authorized-sales), S (sentinel), for cross-role test
AID, ATX = _s("A"), _s("tA"); PA = _pair(AID, ATX)
BID, BTX = _s("B"), _s("tB"); PB = _pair(BID, BTX)
SID, STX = _s("S"), _s("tS"); PS = _pair(SID, STX)
QC, SALES = _s("qc"), _s("sales")
CQC, CSA = _s("case-qc"), _s("case-sales")


def comp(pid, txt): return {"point_id_sha256": pid, "rerank_text_sha256": txt, "pair_sha256": _pair(pid, txt)}
def pcase(case_id, role, category, auth_pair, comps, n=10, erole="qc"):
    qv = _s("qv-" + case_id)
    return {"case_id_sha256": case_id, "role_identity_sha256": role, "effective_role": erole,
            "category": category, "selected_n": n, "query_vector_sha256": qv,
            "unfiltered_query_vector_sha256": qv, "filtered_query_vector_sha256": qv,
            "unfiltered_limit": n, "filtered_limit": n,
            "pair_components": comps,
            "unfiltered_topn_pairs": [PS, auth_pair], "observed_sentinel_ranks": [[PS, 1]],
            "provider_pairs": [auth_pair], "model_input_pairs": [auth_pair], "rerank_output_pairs": [auth_pair],
            "model_call_count": 1, "model_input_count": 1, "score_count": 1, "all_scores_finite": True, "status": "PASS"}

# single-case frozen + evidence (QC / negation)
FROZEN1 = {"cases": {CQC: {"role_identity_sha256": QC, "effective_role": "qc", "category": "negation", "authorized_pairs": [PA], "sentinel_pairs": [PS]}},
           "required_categories": ["negation"], "evaluated_roles": ["qc"]}
MAN1 = E.m4_case_manifest_sha256(FROZEN1)
PC1 = [pcase(CQC, QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)])]
def m4(pcs, man, **over):
    m = {"schema_version": "p2-m4-v4", "status": "PASS", "isolated_interlock": "PASS", "independent_oracle": "PASS",
         "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0, "scorer_kind": "pinned-cross-encoder",
         "evidence_stage": "selected-n", "selected_n": 10, "selection_digest": _H,
         "m4_case_manifest_sha256": man, "per_case": pcs, "raw_evidence_sha256": _raw(pcs),
         "model_revision": "a" * 40, "tokenizer_revision": "a" * 40, "image_digest": "sha256:" + "e" * 64,
         "model_file_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H, "run_id": "run-1",
         "eval_set_sha256": _H, "corpus_manifest_sha256": _H}
    m.update(over)
    return m
_M4 = m4(PC1, MAN1)

check("m4 v4 per-case PASS -> valid", E.validate_m4_run_evidence(_M4, FROZEN1, _H, _H) == [], E.validate_m4_run_evidence(_M4, FROZEN1, _H, _H))
check("require frozen (public gate ห้าม fail-open) -> error เมื่อ frozen None", E.validate_m4_run_evidence(_M4, None, _H, _H) != [])
check("schema_version ผิด -> error", any("schema_version" in e for e in E.validate_m4_run_evidence(m4(PC1, MAN1, schema_version="v3"), FROZEN1, _H, _H)))

# ── B3: recompute raw + pair จาก body ──────────────────────────────────────────
check("B3: raw_evidence_sha256 ไม่ตรง recompute -> error", any("raw_evidence" in e for e in E.validate_m4_run_evidence(m4(PC1, MAN1, raw_evidence_sha256=_H), FROZEN1, _H, _H)))
_badpair = copy.deepcopy(PC1); _badpair[0]["pair_components"][0]["pair_sha256"] = "0" * 64
check("B3: pair_sha256 ไม่ตรงสูตร -> error", any("ไม่ตรงสูตร" in e for e in E.validate_m4_run_evidence(m4(_badpair, MAN1, raw_evidence_sha256=_raw(_badpair)), FROZEN1, _H, _H)))
_undrv = copy.deepcopy(PC1); _undrv[0]["provider_pairs"] = ["9" * 64]
check("B3: pair ไม่ได้ derive จาก components -> error", any("derive" in e for e in E.validate_m4_run_evidence(m4(_undrv, MAN1, raw_evidence_sha256=_raw(_undrv)), FROZEN1, _H, _H)))

# ── B1: within-case security ──────────────────────────────────────────────────
def _mut1(fn):
    pcs = copy.deepcopy(PC1); fn(pcs[0]); return E.validate_m4_run_evidence(m4(pcs, MAN1, raw_evidence_sha256=_raw(pcs)), FROZEN1, _H, _H)
check("B1: sentinel ถึง model_input (leak) -> error", any("LEAK" in e for e in _mut1(lambda c: (c["pair_components"].append(comp(SID, STX)), c.__setitem__("model_input_pairs", [PS]), c.__setitem__("provider_pairs", [PA, PS])))))
check("B1: provider ⊄ authorized -> error", any("provider ไม่ ⊆" in e for e in _mut1(lambda c: (c["pair_components"].append(comp(BID, BTX)), c.__setitem__("provider_pairs", [PB]), c.__setitem__("model_input_pairs", [PB]), c.__setitem__("rerank_output_pairs", [PB])))))
check("B1: rerank ไม่ใช่ permutation -> error", any("permutation" in e for e in _mut1(lambda c: (c["pair_components"].append(comp(BID, BTX)), c.__setitem__("rerank_output_pairs", [PB])))))
check("B1: sentinel ไม่ติด unfiltered top-N -> error", any("unfiltered" in e for e in _mut1(lambda c: c.__setitem__("unfiltered_topn_pairs", [PA]))))
check("B1: sentinel observed rank > N -> error", any("rank" in e for e in _mut1(lambda c: c.__setitem__("observed_sentinel_ranks", [[PS, 99]]))))
check("B3: status != PASS (skip) -> error", any("status" in e for e in _mut1(lambda c: c.__setitem__("status", "INCOMPLETE"))))
check("B3: score_count != model_input_count -> error", any("score_count" in e for e in _mut1(lambda c: c.__setitem__("score_count", 3))))

# ── B1 cross-role swap: aggregate ผ่านแต่ per-case จับได้ ⭐ ────────────────────
FROZEN2 = {"cases": {CQC: {"role_identity_sha256": QC, "effective_role": "qc", "category": "negation", "authorized_pairs": [PA], "sentinel_pairs": [PS]},
                     CSA: {"role_identity_sha256": SALES, "effective_role": "sales", "category": "table-row", "authorized_pairs": [PB], "sentinel_pairs": [PS]}},
           "required_categories": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"]}
MAN2 = E.m4_case_manifest_sha256(FROZEN2)
# QC ได้ผลของ SALES (PB) และ SALES ได้ผลของ QC (PA) — aggregate authorized/provider = {PA,PB} ตรงกัน แต่รั่วทั้งคู่
PC_SWAP = [pcase(CQC, QC, "negation", PB, [comp(BID, BTX), comp(SID, STX)], erole="qc"),
           pcase(CSA, SALES, "table-row", PA, [comp(AID, ATX), comp(SID, STX)], erole="sales")]
check("B1 ⭐ cross-role swap -> error (per-case จับได้ แม้ aggregate subset ผ่าน)",
      any("provider ไม่ ⊆" in e for e in E.validate_m4_run_evidence(m4(PC_SWAP, MAN2, raw_evidence_sha256=_raw(PC_SWAP)), FROZEN2, _H, _H)))
# correct 2-case (ครบ required categories) -> valid
PC2 = [pcase(CQC, QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)], erole="qc"),
       pcase(CSA, SALES, "table-row", PB, [comp(BID, BTX), comp(SID, STX)], erole="sales")]
check("2-case ถูกต้อง (ครบ negation+table-row) -> valid", E.validate_m4_run_evidence(m4(PC2, MAN2, raw_evidence_sha256=_raw(PC2)), FROZEN2, _H, _H) == [], E.validate_m4_run_evidence(m4(PC2, MAN2, raw_evidence_sha256=_raw(PC2)), FROZEN2, _H, _H))

# ── M2: category coverage + case set exact ────────────────────────────────────
check("M2: required category ไม่ครบ (ขาด table-row) -> error",
      any("required category ไม่ครบ" in e for e in E.validate_m4_run_evidence(m4(PC1, MAN2, m4_case_manifest_sha256=MAN2, raw_evidence_sha256=_raw(PC1)), FROZEN2, _H, _H)))
_extra = PC1 + [pcase(_s("ghost"), QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)])]
check("M2: case_id ไม่อยู่ frozen manifest -> error", any("frozen manifest" in e or "case set" in e for e in E.validate_m4_run_evidence(m4(_extra, MAN1, raw_evidence_sha256=_raw(_extra)), FROZEN1, _H, _H)))

# ── B2: frozen manifest binding ───────────────────────────────────────────────
check("B2: m4_case_manifest_sha256 != frozen -> error", any("m4_case_manifest_sha256" in e for e in E.validate_m4_run_evidence(m4(PC1, "0" * 64), FROZEN1, _H, _H)))
_pcrole = copy.deepcopy(PC1); _pcrole[0]["role_identity_sha256"] = _s("evil")
check("B2: role_identity ไม่ตรง frozen -> error", any("role_identity" in e for e in E.validate_m4_run_evidence(m4(_pcrole, MAN1, raw_evidence_sha256=_raw(_pcrole)), FROZEN1, _H, _H)))

# ── M1/M3: stage contract ─────────────────────────────────────────────────────
# preflight: N=50 → per_case selected_n/limits ต้อง 50 ด้วย
_pf_pc = [pcase(CQC, QC, "negation", PA, [comp(AID, ATX), comp(SID, STX)], n=50)]
_PF = m4(_pf_pc, MAN1, evidence_stage="preflight-n50", selected_n=50, selection_digest=None, decision_eligible=False, raw_evidence_sha256=_raw(_pf_pc))
check("M3: preflight standalone -> valid", E.validate_m4_run_evidence(_PF, FROZEN1, _H, _H) == [], E.validate_m4_run_evidence(_PF, FROZEN1, _H, _H))
check("M1: preflight ใน decision path (require selected-n) -> error", any("evidence_stage" in e for e in E.validate_m4_run_evidence(_PF, FROZEN1, _H, _H, require_stage=E.M4_STAGE_SELECTED)))
check("M3: preflight มี selection_digest -> error", any("selection_digest" in e for e in E.validate_m4_run_evidence(m4(_pf_pc, MAN1, evidence_stage="preflight-n50", selected_n=50, decision_eligible=False, raw_evidence_sha256=_raw(_pf_pc)), FROZEN1, _H, _H)))
check("M3: selected-n ขาด selection_digest -> error", any("selection_digest" in e for e in E.validate_m4_run_evidence(m4(PC1, MAN1, selection_digest=None), FROZEN1, _H, _H)))
check("M1: run_manifest binding ไม่ตรง -> error", any("run_manifest" in e for e in E.validate_m4_run_evidence(m4(PC1, MAN1, run_manifest_sha256=_H), FROZEN1, _H, _H, run_manifest_sha256="b" * 64)))

# ── B1 (targeted): observed rank เท็จ (ไม่ตรงตำแหน่งจริงใน unfiltered) ──────────
_rk = copy.deepcopy(PC1); _rk[0]["unfiltered_topn_pairs"] = [PA, PS]; _rk[0]["observed_sentinel_ranks"] = [[PS, 1]]
check("B1: sentinel rank เท็จ (unfiltered[0]=authorized, อ้าง sentinel rank 1) -> error",
      any("rank ไม่ตรงตำแหน่ง" in e for e in E.validate_m4_run_evidence(m4(_rk, MAN1, raw_evidence_sha256=_raw(_rk)), FROZEN1, _H, _H)))
_dup = copy.deepcopy(PC1); _dup[0]["unfiltered_topn_pairs"] = [PS, PA, PS]
check("B1: unfiltered_topn มี pair ซ้ำ -> error", any("ซ้ำ" in e for e in E.validate_m4_run_evidence(m4(_dup, MAN1, raw_evidence_sha256=_raw(_dup)), FROZEN1, _H, _H)))

# ── B2 (targeted): evaluated_role ประกาศแต่ไม่มี case ─────────────────────────
FROZEN_NOCASE = {"cases": {CQC: {"role_identity_sha256": QC, "effective_role": "qc", "category": "negation", "authorized_pairs": [PA], "sentinel_pairs": [PS]}},
                 "required_categories": ["negation"], "evaluated_roles": ["qc", "sales"]}
check("B2: frozen evaluated_role 'sales' ไม่มี case -> validate_m4_frozen_manifest error",
      any("evaluated_roles ไม่ครบ" in e for e in E.validate_m4_frozen_manifest(FROZEN_NOCASE)))
check("B2: run evidence กับ frozen role ไม่ครบ -> error (ไม่ผ่านผิด)",
      E.validate_m4_run_evidence(m4(PC1, E.m4_case_manifest_sha256(FROZEN_NOCASE)), FROZEN_NOCASE, _H, _H) != [])

# ── B3 (targeted): malformed frozen -> error list (ไม่ crash) ──────────────────
FROZEN_BAD = {"cases": {CQC: {"role_identity_sha256": QC, "effective_role": "qc", "category": "negation", "authorized_pairs": [PA], "sentinel_pairs": [PS]}},
              "required_categories": ["negation", None], "evaluated_roles": ["qc"]}
check("B3: required_categories มี None -> error list (ไม่ TypeError crash)", E.validate_m4_frozen_manifest(FROZEN_BAD) != [])
check("B3: run evidence กับ malformed frozen -> error list (fail-closed)", E.validate_m4_run_evidence(_M4, FROZEN_BAD, _H, _H) != [])
check("B3: _safe_m4_manifest_digest(malformed) -> None (ไม่ crash)", E._safe_m4_manifest_digest(FROZEN_BAD) is None)

# ── M1 (targeted): unknown/raw field ใน per_case ──────────────────────────────
_raw_leak = copy.deepcopy(PC1); _raw_leak[0]["raw_text"] = "SECRET-QUERY"
check("M1: per_case มี raw_text (raw/secret) -> error (exact hash-only schema)",
      any("unknown/raw" in e for e in E.validate_m4_run_evidence(m4(_raw_leak, MAN1, raw_evidence_sha256=_raw(_raw_leak)), FROZEN1, _H, _H)))
check("M1: top-level m4 มี unknown field -> error", any("unknown/raw" in e for e in E.validate_m4_run_evidence(m4(PC1, MAN1, secret="x"), FROZEN1, _H, _H)))

# ── M2 (targeted): filtered query vector ไม่ตรง (คนละ probe) ───────────────────
_qm = copy.deepcopy(PC1); _qm[0]["filtered_query_vector_sha256"] = _s("other")
check("M2: filtered query vector != unfiltered -> error (same-query control)",
      any("query vector" in e for e in E.validate_m4_run_evidence(m4(_qm, MAN1, raw_evidence_sha256=_raw(_qm)), FROZEN1, _H, _H)))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
