"""
P2 M4 harness — **pure/injectable producer** ของ M4 evidence + M4RunReceipt (permission-leak proof)
Codex GO harness ; **real run บน isolated Qdrant ยัง NO-GO** จน runner review + atomic failure controls ผ่าน

boundary เดียวเป็นเจ้าของ candidate pairs ตั้งแต่ก่อนเรียก scorer จนถึง evidence:
  M4Scorer.score_candidates(query_vector, [(point_id, rerank_text)]) →
    guard sentinel/unauthorized **ก่อน** delegate (underlying ไม่ถูกเรียก) → record pair trace →
    build_case_record derive model_input/counts/finite **จาก trace เท่านั้น** (ไม่รับ model_input จาก caller)
ทุก digest recompute จาก body · hash-only · typed identity (point_id int != str) · vector = canonical finite floats
"""
from __future__ import annotations
import hashlib
import math

import p2_eval as E


def _id_hash(point_id) -> str:
    """M3: type-tagged — Qdrant point id เป็น int หรือ str (uuid) ; 1 กับ '1' ต้องได้ digest ต่างกัน"""
    if isinstance(point_id, bool) or not isinstance(point_id, (int, str)):
        raise TypeError(f"point_id ต้องเป็น int/str: {point_id!r}")
    tag = "i" if isinstance(point_id, int) else "s"
    return hashlib.sha256(f"{tag}:{point_id}".encode("utf-8")).hexdigest()


def _text_hash(text) -> str:
    if not isinstance(text, str):
        raise TypeError(f"rerank_text ต้องเป็น str: {text!r}")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vec_hash(vector) -> str:
    """canonical JSON ของ finite float list (allow_nan=False) — ไม่ใช้ str(obj)"""
    if not isinstance(vector, (list, tuple)) or not vector:
        raise TypeError("query vector ต้องเป็น list ไม่ว่าง")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector):
        raise ValueError("query vector ต้องเป็น finite number ทุกตัว")
    return hashlib.sha256(E._canonical_json([float(v) for v in vector])).hexdigest()


def _argv_hash(argv) -> str:
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv ต้องเป็น list ของ str (กัน ambiguity)")
    return hashlib.sha256(E._canonical_json(argv)).hexdigest()


def _bytes_sha256(b) -> str:
    if not isinstance(b, (bytes, bytearray)):
        raise TypeError("stdout/stderr ต้องเป็น bytes (ไม่ auto-coerce)")
    return hashlib.sha256(bytes(b)).hexdigest()


def component(point_id, rerank_text) -> dict:
    pid, txt = _id_hash(point_id), _text_hash(rerank_text)
    return {"point_id_sha256": pid, "rerank_text_sha256": txt, "pair_sha256": E._pair_sha256(pid, txt)}


class M4Scorer:
    """
    B1: boundary เดียว. score_candidates(query_vector, candidates) — candidates = ordered [(point_id, rerank_text)]
    - guard: ทุก candidate pair ต้องอยู่ใน authorized_pairs ; sentinel/unauthorized → set sentinel_reached + raise
      **ก่อน**เรียก underlying scorer (underlying call count ต้องยังเป็น 0)
    - record immutable trace (components/pairs/scores/query hash) ให้ build_case_record ใช้ตรง ๆ (ต่อ case = spy ใหม่)
    """
    def __init__(self, scorer, authorized_pairs):
        self._s = scorer
        self._auth = set(authorized_pairs)
        self.query_vec_sha = None
        self.components: list = []
        self.pairs: list = []
        self.scores: list = []
        self.calls = 0
        self.sentinel_reached = False

    def score_candidates(self, query_vector, candidates):
        comps = [component(pid, txt) for pid, txt in candidates]
        for c in comps:
            if c["pair_sha256"] not in self._auth:
                self.sentinel_reached = True
                raise PermissionError(f"unauthorized/sentinel pair ถึง model boundary: {c['pair_sha256']}")
        out = self._s.score("m4", [txt for _, txt in candidates])   # underlying หลัง guard ผ่านหมด
        self.calls += 1
        self.query_vec_sha = _vec_hash(query_vector)
        self.components.extend(comps)
        self.pairs.extend(c["pair_sha256"] for c in comps)
        self.scores.extend(float(s) for s in out)
        return out


# ── frozen seed manifest (fixture author ประกาศ visibility matrix โดยตรง — independent oracle) ──
def frozen_case(*, effective_role, category, query_vector, authorized_items, sentinel_items) -> dict:
    def _pairs(items):
        return [component(p, t)["pair_sha256"] for p, t in items]
    return {"role_identity_sha256": _id_hash(effective_role) if isinstance(effective_role, (int, str)) else None,
            "effective_role": effective_role, "category": category, "query_vector_sha256": _vec_hash(query_vector),
            "authorized_pairs": _pairs(authorized_items), "sentinel_pairs": _pairs(sentinel_items)}


def build_frozen_manifest(cases: dict, required_categories, evaluated_roles) -> dict:
    return {"cases": {_id_hash(cid): fc for cid, fc in cases.items()},
            "required_categories": list(required_categories), "evaluated_roles": list(evaluated_roles)}


def build_case_record(*, case_id, effective_role, category, query_vector, selected_n,
                      unfiltered_items, sentinel_items, scorer: M4Scorer) -> dict:
    """
    model_input/rerank/counts/finite derive จาก **scorer trace เท่านั้น** ; provider == candidate ที่เข้า model
    unfiltered/sentinel = observation แยก (raw query / oracle) ; observed rank จากตำแหน่งจริงใน unfiltered
    """
    comps = {c["pair_sha256"]: c for c in scorer.components}

    def _add(items):
        out = []
        for pid, txt in items:
            c = component(pid, txt)
            comps[c["pair_sha256"]] = c
            out.append(c["pair_sha256"])
        return out

    unf, sent = _add(unfiltered_items), _add(sentinel_items)
    minp = list(scorer.pairs)                                  # จาก trace
    order = sorted(range(len(minp)), key=lambda i: -scorer.scores[i])
    rer = [minp[i] for i in order]
    pos = {p: i + 1 for i, p in enumerate(unf)}
    ranks = [[sp, pos[sp]] for sp in sent if sp in pos]
    qv = _vec_hash(query_vector)
    if scorer.query_vec_sha != qv:
        raise ValueError("query vector ที่เข้า scorer ไม่ตรงกับ case query vector")
    finite = bool(scorer.scores) and all(math.isfinite(s) for s in scorer.scores)
    return {"case_id_sha256": _id_hash(case_id), "role_identity_sha256": _id_hash(effective_role),
            "effective_role": effective_role, "category": category, "selected_n": selected_n,
            "query_vector_sha256": qv, "unfiltered_query_vector_sha256": qv, "filtered_query_vector_sha256": qv,
            "unfiltered_limit": selected_n, "filtered_limit": selected_n, "pair_components": list(comps.values()),
            "unfiltered_topn_pairs": unf, "observed_sentinel_ranks": ranks,
            "provider_pairs": minp, "model_input_pairs": minp, "rerank_output_pairs": rer,
            "model_call_count": scorer.calls, "model_input_count": len(minp), "score_count": len(scorer.scores),
            "all_scores_finite": finite, "status": "PASS"}


def assemble_evidence(per_case: list, *, stage, run_meta: dict, verdicts: dict) -> dict:
    """
    M2: security verdict มาจาก **validated interlock/oracle/spy result** (`verdicts`) — builder ไม่ self-stamp PASS
    verdicts = {status, isolated_interlock, independent_oracle, sentinel_reached_model, unauthorized_in_model_inputs}
    """
    ev = {"schema_version": E.M4_SCHEMA_VERSION, "scorer_kind": "pinned-cross-encoder",
          "status": verdicts["status"], "isolated_interlock": verdicts["isolated_interlock"],
          "independent_oracle": verdicts["independent_oracle"],
          "sentinel_reached_model": verdicts["sentinel_reached_model"],
          "unauthorized_in_model_inputs": verdicts["unauthorized_in_model_inputs"],
          "evidence_stage": stage, "per_case": per_case,
          "raw_evidence_sha256": hashlib.sha256(E._canonical_json(per_case)).hexdigest()}
    ev.update(run_meta)
    return ev


def assemble_receipt(evidence: dict, *, run_manifest, m4_case_manifest, expected, argv, stdout, stderr,
                     isolation_marker, started_utc, finished_utc, exit_code=0) -> dict:
    """M4RunReceipt — hash-only ; command = canonical(argv) ; stdout/stderr เป็น bytes ; digest recompute โดย p2_eval"""
    return {"schema_version": E.M4_RECEIPT_SCHEMA_VERSION, "run_id": evidence["run_id"],
            "run_manifest_sha256": run_manifest, "m4_case_manifest_sha256": m4_case_manifest,
            "raw_evidence_sha256": evidence["raw_evidence_sha256"], "command_sha256": _argv_hash(argv),
            "started_utc": started_utc, "finished_utc": finished_utc, "exit_code": exit_code,
            "stdout_sha256": _bytes_sha256(stdout), "stderr_sha256": _bytes_sha256(stderr),
            "isolation_marker_sha256": _id_hash(isolation_marker),
            "retrieval_index_manifest_sha256": expected["retrieval_index_manifest_sha256"],
            "model_revision": expected["model_revision"], "image_digest": expected["image_digest"], "status": "PASS"}
