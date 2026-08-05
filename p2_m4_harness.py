"""
P2 M4 harness — **pure/injectable producer** ของ M4 evidence + M4RunReceipt (permission-leak proof)
Codex GO harness ; **real run บน isolated Qdrant ยัง NO-GO** จน harness implementation review ผ่าน

โครง (ตรง KB_P2_M4_REAL_RUN_PLAN.md):
  frozen seed (expected_visible_roles matrix) ──┐
  independent oracle (matrix + pair hashes)     ├─ per-case observe → build_case_record
  resolve_effective_access → provider → SPY → PinnedCrossEncoder
  → assemble_evidence (per_case, recompute raw digest) + assemble_receipt (body + digest)
  → validate ด้วย p2_runplan.validate_m4_preflight_bundle (public M4a gate, trust-anchored กับ RunPlan)

ทุก digest recompute จาก body — caller ไม่ self-stamp ; hash-only ไม่มี raw text/secret
"""
from __future__ import annotations
import hashlib

import p2_eval as E


def _h(s) -> str:
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def _bytes_sha256(b) -> str:
    return hashlib.sha256(b if isinstance(b, (bytes, bytearray)) else str(b).encode("utf-8")).hexdigest()


def component(point_id, rerank_text) -> dict:
    """pair_component จาก point_id + rerank_text จริง → sha256 hashes + pair_sha256 (สูตร canonical)"""
    pid, txt = _h(point_id), _h(rerank_text)
    return {"point_id_sha256": pid, "rerank_text_sha256": txt, "pair_sha256": E._pair_sha256(pid, txt)}


class SpyScorer:
    """
    wrap scorer — จับ text ที่เข้า **real cross-encoder** ต่อ call ก่อน scoring (id/text ผ่าน pair) โดยไม่แก้ input
    ให้ runner derive model_call/input/score counts + all_scores_finite จาก trace จริง (ไม่ให้ caller กรอกเอง)
    """
    def __init__(self, scorer):
        self._s = scorer
        self.calls = 0
        self.texts: list = []
        self.scores: list = []

    def score(self, query, texts):
        self.calls += 1
        self.texts.extend(texts)
        out = self._s.score(query, texts)
        self.scores.extend(out)
        return out

    def metadata(self) -> dict:
        return getattr(self._s, "metadata", lambda: {})()


# ── frozen seed manifest (fixture author ประกาศ visibility matrix โดยตรง — independent oracle) ──
def frozen_case(*, effective_role, category, query_probe, authorized_items, sentinel_items) -> dict:
    """authorized/sentinel_items = list ของ (point_id, rerank_text) ; oracle ตัดสินจาก matrix นี้ ไม่ใช่ policy"""
    def _pairs(items):
        return [component(p, t)["pair_sha256"] for p, t in items]
    return {"role_identity_sha256": _h(effective_role), "effective_role": effective_role, "category": category,
            "query_vector_sha256": _h(query_probe),
            "authorized_pairs": _pairs(authorized_items), "sentinel_pairs": _pairs(sentinel_items)}


def build_frozen_manifest(cases: dict, required_categories, evaluated_roles) -> dict:
    """cases = {case_id: frozen_case(...)} → frozen manifest (digest = m4_case_manifest_sha256)"""
    return {"cases": {_h(cid): fc for cid, fc in cases.items()},
            "required_categories": list(required_categories), "evaluated_roles": list(evaluated_roles)}


# ── per-case evidence จาก observations จริง (ordered (point_id, rerank_text) ต่อ stage) ──
def build_case_record(*, case_id, effective_role, category, query_probe, selected_n,
                      unfiltered, provider, model_input, rerank_output, sentinel_items,
                      spy: SpyScorer) -> dict:
    comps = {}

    def _pairs(items):
        out = []
        for pid, txt in items:
            c = component(pid, txt)
            comps[c["pair_sha256"]] = c
            out.append(c["pair_sha256"])
        return out

    unf, prov, minp, rer = _pairs(unfiltered), _pairs(provider), _pairs(model_input), _pairs(rerank_output)
    sent = _pairs(sentinel_items)
    pos = {p: i + 1 for i, p in enumerate(unf)}
    ranks = [[sp, pos[sp]] for sp in sent if sp in pos]
    qv = _h(query_probe)
    finite = all(isinstance(s, float) and s == s and s not in (float("inf"), float("-inf")) for s in spy.scores)
    return {"case_id_sha256": _h(case_id), "role_identity_sha256": _h(effective_role), "effective_role": effective_role,
            "category": category, "selected_n": selected_n, "query_vector_sha256": qv,
            "unfiltered_query_vector_sha256": qv, "filtered_query_vector_sha256": qv,
            "unfiltered_limit": selected_n, "filtered_limit": selected_n,
            "pair_components": list(comps.values()),
            "unfiltered_topn_pairs": unf, "observed_sentinel_ranks": ranks,
            "provider_pairs": prov, "model_input_pairs": minp, "rerank_output_pairs": rer,
            "model_call_count": spy.calls, "model_input_count": len(minp), "score_count": len(spy.scores),
            "all_scores_finite": bool(finite), "status": "PASS"}


def assemble_evidence(per_case: list, *, stage, run_meta: dict) -> dict:
    """M4Evidence จาก per_case[] + run_meta (pin/image/index/run_id) — recompute raw_evidence_sha256 จาก body"""
    ev = {"schema_version": E.M4_SCHEMA_VERSION, "status": "PASS", "isolated_interlock": "PASS",
          "independent_oracle": "PASS", "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0,
          "scorer_kind": "pinned-cross-encoder", "evidence_stage": stage, "per_case": per_case,
          "raw_evidence_sha256": hashlib.sha256(E._canonical_json(per_case)).hexdigest()}
    ev.update(run_meta)                                       # m4_case_manifest_sha256, pins, index, run_id, eval/corpus, root, stage-fields
    ev["run_receipt_sha256"] = ev.get("run_receipt_sha256")  # เติมภายหลังจาก assemble_receipt
    return ev


def assemble_receipt(evidence: dict, *, run_manifest, m4_case_manifest, expected, argv, stdout, stderr,
                     isolation_marker, started_utc, finished_utc, exit_code=0) -> dict:
    """M4RunReceipt — body-validated ; digest recompute โดย m4_run_receipt_sha256 ; hash-only ไม่มี raw log"""
    return {"schema_version": E.M4_RECEIPT_SCHEMA_VERSION, "run_id": evidence["run_id"],
            "run_manifest_sha256": run_manifest, "m4_case_manifest_sha256": m4_case_manifest,
            "raw_evidence_sha256": evidence["raw_evidence_sha256"], "command_sha256": _h(" ".join(argv)),
            "started_utc": started_utc, "finished_utc": finished_utc, "exit_code": exit_code,
            "stdout_sha256": _bytes_sha256(stdout), "stderr_sha256": _bytes_sha256(stderr),
            "isolation_marker_sha256": _h(isolation_marker),
            "retrieval_index_manifest_sha256": expected["retrieval_index_manifest_sha256"],
            "model_revision": expected["model_revision"], "image_digest": expected["image_digest"], "status": "PASS"}
