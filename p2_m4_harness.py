"""
P2 M4 harness — **pure/injectable producer** ของ M4 evidence + M4RunReceipt (permission-leak proof)
Codex GO harness ; **real run บน isolated Qdrant ยัง NO-GO** จน runner review + atomic controls ผ่าน

execution source เดียว: `score_case(query_text, query_vector, candidates, authorized_pairs, scorer)`
  - validate **ทุกอย่างก่อน delegate** (query text/vector/candidates/authorized) — malformed ไม่แตะ model
  - guard sentinel/unauthorized ก่อนเรียก underlying (underlying call = 0)
  - ส่ง **query text จริงของ case** เข้า cross-encoder (ไม่ใช่ค่าคงที่)
  - คืน **frozen CaseTrace (immutable)** ครั้งเดียว → build_case_record consume ก้อนนี้ (แก้ย้อนหลังไม่ได้)
verdict มาจาก validated interlock/oracle proof (ไม่ self-stamp) · run_meta มี exact allowlist กันทับ protected fields
"""
from __future__ import annotations
import hashlib
import math
from typing import NamedTuple

import p2_eval as E


def _id_hash(point_id) -> str:
    if isinstance(point_id, bool) or not isinstance(point_id, (int, str)):
        raise TypeError(f"point_id ต้องเป็น int/str: {point_id!r}")
    tag = "i" if isinstance(point_id, int) else "s"
    return hashlib.sha256(f"{tag}:{point_id}".encode("utf-8")).hexdigest()


def _text_hash(text) -> str:
    if not isinstance(text, str) or not text:
        raise TypeError(f"text ต้องเป็น non-empty str: {text!r}")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vec_hash(vector) -> str:
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


class CaseTrace(NamedTuple):
    """frozen one-shot trace หลัง score สำเร็จ — build_case_record consume ก้อนนี้ (immutable, แก้ย้อนหลังไม่ได้)"""
    query_text_sha256: str
    query_vector_sha256: str
    components: tuple
    pairs: tuple
    scores: tuple
    call_count: int


def score_case(*, query_text, query_vector, candidates, authorized_pairs, scorer) -> CaseTrace:
    """
    execution source เดียว (one-shot). validate ทุก input **ก่อน** delegate → guard sentinel → เรียก underlying
    ด้วย query_text จริง → คืน frozen CaseTrace. malformed/sentinel = raise ก่อน underlying ถูกเรียก
    """
    qt = _text_hash(query_text)                     # validate query text ก่อน (M2/B2)
    qv = _vec_hash(query_vector)                     # validate vector ก่อน delegate (M2)
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise ValueError("candidates ว่าง/ผิดชนิด")
    auth = set(authorized_pairs)
    comps = [component(pid, txt) for pid, txt in candidates]
    for c in comps:                                  # guard ก่อน delegate (B1)
        if c["pair_sha256"] not in auth:
            raise PermissionError(f"unauthorized/sentinel pair ถึง model boundary: {c['pair_sha256']}")
    scores = [float(s) for s in scorer.score(query_text, [txt for _, txt in candidates])]   # query จริง (B2)
    if len(scores) != len(candidates) or not all(math.isfinite(s) for s in scores):
        raise ValueError("scores ต้องครบทุก candidate และ finite")
    return CaseTrace(query_text_sha256=qt, query_vector_sha256=qv,
                     components=tuple(comps), pairs=tuple(c["pair_sha256"] for c in comps),
                     scores=tuple(scores), call_count=1)


# ── frozen seed manifest (fixture author ประกาศ visibility matrix — independent oracle) ──
def frozen_case(*, effective_role, category, query_text, query_vector, authorized_items, sentinel_items) -> dict:
    def _pairs(items):
        return [component(p, t)["pair_sha256"] for p, t in items]
    return {"role_identity_sha256": _id_hash(effective_role), "effective_role": effective_role, "category": category,
            "query_text_sha256": _text_hash(query_text), "query_vector_sha256": _vec_hash(query_vector),
            "authorized_pairs": _pairs(authorized_items), "sentinel_pairs": _pairs(sentinel_items)}


def build_frozen_manifest(cases: dict, required_categories, evaluated_roles) -> dict:
    return {"cases": {_id_hash(cid): fc for cid, fc in cases.items()},
            "required_categories": list(required_categories), "evaluated_roles": list(evaluated_roles)}


def build_case_record(*, case_id, effective_role, category, selected_n,
                      unfiltered_items, sentinel_items, trace: CaseTrace) -> dict:
    """consume **finalized CaseTrace** — model_input/counts/finite มาจาก trace เท่านั้น (แก้ย้อนหลังไม่ได้)"""
    if not isinstance(trace, CaseTrace):
        raise TypeError("trace ต้องเป็น CaseTrace (จาก score_case)")
    comps = {c["pair_sha256"]: c for c in trace.components}

    def _add(items):
        out = []
        for pid, txt in items:
            c = component(pid, txt)
            comps[c["pair_sha256"]] = c
            out.append(c["pair_sha256"])
        return out

    unf, sent = _add(unfiltered_items), _add(sentinel_items)
    minp = list(trace.pairs)
    order = sorted(range(len(minp)), key=lambda i: -trace.scores[i])
    rer = [minp[i] for i in order]
    pos = {p: i + 1 for i, p in enumerate(unf)}
    ranks = [[sp, pos[sp]] for sp in sent if sp in pos]
    finite = bool(trace.scores) and all(math.isfinite(s) for s in trace.scores)
    return {"case_id_sha256": _id_hash(case_id), "role_identity_sha256": _id_hash(effective_role),
            "effective_role": effective_role, "category": category, "selected_n": selected_n,
            "query_text_sha256": trace.query_text_sha256, "query_vector_sha256": trace.query_vector_sha256,
            "unfiltered_query_vector_sha256": trace.query_vector_sha256, "filtered_query_vector_sha256": trace.query_vector_sha256,
            "unfiltered_limit": selected_n, "filtered_limit": selected_n, "pair_components": list(comps.values()),
            "unfiltered_topn_pairs": unf, "observed_sentinel_ranks": ranks,
            "provider_pairs": minp, "model_input_pairs": minp, "rerank_output_pairs": rer,
            "model_call_count": trace.call_count, "model_input_count": len(minp), "score_count": len(trace.scores),
            "all_scores_finite": finite, "status": "PASS"}


# ── verdict มาจาก validated proof (ไม่ self-stamp) ─────────────────────────────
def build_verdicts(*, isolation, oracle, case_count, traced_count) -> dict:
    """derive verdict จากผล IsolationProof/OracleProof + จำนวน case ที่ trace สำเร็จ (ไม่มี sentinel ถึง model)"""
    ok = isolation == "PASS" and oracle == "PASS" and case_count > 0 and traced_count == case_count
    return {"status": "PASS" if ok else "FAIL", "isolated_interlock": isolation, "independent_oracle": oracle,
            "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0}


# B1: run_meta อนุญาตเฉพาะ field ระบุ — กันทับ protected/verdict/evidence fields
_RUN_META_KEYS = frozenset({"m4_case_manifest_sha256", "run_id", "run_manifest_sha256", "model_revision",
                            "tokenizer_revision", "model_file_manifest_sha256", "image_digest", "inference_config",
                            "retrieval_index_manifest_sha256", "eval_set_sha256", "corpus_manifest_sha256",
                            "selected_n", "decision_eligible", "selection_digest"})
_M4_VERDICT_KEYS = ("status", "isolated_interlock", "independent_oracle", "sentinel_reached_model",
                    "unauthorized_in_model_inputs")


def assemble_evidence(per_case: list, *, stage, run_meta: dict, verdicts: dict) -> dict:
    extra = set(run_meta) - _RUN_META_KEYS
    if extra:
        raise ValueError(f"run_meta มี key ต้องห้าม (ชน protected/evidence/verdict fields): {sorted(extra)}")
    if set(verdicts) != set(_M4_VERDICT_KEYS):
        raise ValueError("verdicts ต้องมี key ครบตาม _M4_VERDICT_KEYS พอดี")
    ev = dict(run_meta)                              # เขียน metadata ก่อน
    ev.update({k: verdicts[k] for k in _M4_VERDICT_KEYS})   # verdict/protected เขียนทับทีหลัง (run_meta แตะไม่ได้)
    ev["schema_version"] = E.M4_SCHEMA_VERSION
    ev["scorer_kind"] = "pinned-cross-encoder"
    ev["evidence_stage"] = stage
    ev["per_case"] = per_case
    ev["raw_evidence_sha256"] = hashlib.sha256(E._canonical_json(per_case)).hexdigest()
    return ev


def assemble_receipt(evidence: dict, *, run_manifest, m4_case_manifest, expected, argv, stdout, stderr,
                     isolation_marker, started_utc, finished_utc, exit_code=0) -> dict:
    return {"schema_version": E.M4_RECEIPT_SCHEMA_VERSION, "run_id": evidence["run_id"],
            "run_manifest_sha256": run_manifest, "m4_case_manifest_sha256": m4_case_manifest,
            "raw_evidence_sha256": evidence["raw_evidence_sha256"], "command_sha256": _argv_hash(argv),
            "started_utc": started_utc, "finished_utc": finished_utc, "exit_code": exit_code,
            "stdout_sha256": _bytes_sha256(stdout), "stderr_sha256": _bytes_sha256(stderr),
            "isolation_marker_sha256": _id_hash(isolation_marker),
            "retrieval_index_manifest_sha256": expected["retrieval_index_manifest_sha256"],
            "model_revision": expected["model_revision"], "image_digest": expected["image_digest"], "status": "PASS"}
