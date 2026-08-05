"""
P2 M4 harness — **pure/injectable producer** ของ M4 evidence + M4RunReceipt (permission-leak proof)
Codex GO harness (pure) ; **real run บน isolated Qdrant ยัง NO-GO** จน runner review + atomic controls ผ่าน

provenance seam ปิดครบ:
  - `run_case(...)` = boundary เดียว: validate **scorer.metadata() == M4RunRequest (pin)** ก่อน delegate → mock/wrong pin ผ่านไม่ได้
    · validate query text/vector/candidates/authorized ก่อน delegate · guard sentinel · ส่ง query จริงเข้า model
    · CaseTrace เป็น private (ไม่มี public consumer รับ trace ที่ caller ปั้นเอง)
  - `scorer_kind`/pin ใน evidence มาจาก **validated ScorerProof** (ไม่ hardcode, ไม่รับสำเนาอิสระจาก run_meta)
  - verdict มาจาก **IsolationProof/OracleProof + case records จริง** (derive status/counts เอง, ไม่รับ string/count ลอย)
  - isolation marker = load-bearing (proof body ผูก marker เดียวกับ receipt)
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
    # M2: ใช้กติกาเดียวกับ eval contract — non-blank หลัง strip + ไม่มี control(Cc)/lone-surrogate(Cs)
    if E._bad_str(text):
        raise ValueError(f"text ต้อง non-blank + ไม่มี control/surrogate (eval-contract rule): {text!r}")
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


# ── scorer provenance (B1): pinned model ต้องพิสูจน์ตัวเองผ่าน metadata() == M4RunRequest ─────
_SCORER_PROOF_KEYS = frozenset({"scorer_kind", "model_revision", "tokenizer_revision",
                                "model_file_manifest_sha256", "inference_config"})


def validate_scorer_metadata(scorer, expected) -> dict:
    """
    ตรวจว่า scorer เป็น pinned model จริงและตรง M4RunRequest ก่อน delegate — **raise** ถ้า mock/ไม่มี metadata/pin ผิด
    คืน ScorerProof (แหล่งเดียวของ scorer_kind + pin ที่จะ stamp เข้า evidence)
    """
    if not isinstance(expected, dict):
        raise ValueError("expected (M4RunRequest) จำเป็นสำหรับ scorer provenance")
    meta_fn = getattr(scorer, "metadata", None)
    if not callable(meta_fn):
        raise TypeError("scorer ต้องมี metadata() — mock/ไม่มี metadata ห้าม emit pinned evidence")
    md = meta_fn()
    if not isinstance(md, dict):
        raise TypeError("scorer.metadata() ต้องคืน dict")
    if md.get("kind") != "pinned-cross-encoder":
        raise ValueError(f"scorer kind ต้อง 'pinned-cross-encoder' (ได้ {md.get('kind')!r})")
    for k in ("model_revision", "tokenizer_revision", "model_file_manifest_sha256", "inference_config"):
        if md.get(k) != expected.get(k):
            raise ValueError(f"scorer metadata {k} != M4RunRequest (pin/inference_config ไม่ตรง)")
    exp_ic = expected.get("inference_config")
    if not isinstance(exp_ic, dict) or md.get("model_name") != exp_ic.get("model_name"):
        raise ValueError("scorer model_name != inference_config.model_name")
    return {"scorer_kind": "pinned-cross-encoder", "model_revision": md["model_revision"],
            "tokenizer_revision": md["tokenizer_revision"], "model_file_manifest_sha256": md["model_file_manifest_sha256"],
            "inference_config": md["inference_config"]}


# ── one-shot execution (private) — trace เป็น implementation detail ไม่มี public consumer ──────
class _CaseTrace(NamedTuple):
    query_text_sha256: str
    query_vector_sha256: str
    components: tuple
    pairs: tuple
    scores: tuple
    call_count: int


def _score_case(*, query_text, query_vector, candidates, authorized_pairs, scorer) -> _CaseTrace:
    qt = _text_hash(query_text)                      # validate query text ก่อน delegate (M2)
    qv = _vec_hash(query_vector)                      # validate vector ก่อน delegate (M2)
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise ValueError("candidates ว่าง/ผิดชนิด")
    auth = set(authorized_pairs)
    comps = [component(pid, txt) for pid, txt in candidates]
    for c in comps:                                   # guard ก่อน delegate (sentinel/unauthorized)
        if c["pair_sha256"] not in auth:
            raise PermissionError(f"unauthorized/sentinel pair ถึง model boundary: {c['pair_sha256']}")
    scores = [float(s) for s in scorer.score(query_text, [txt for _, txt in candidates])]   # query จริง (B2)
    if len(scores) != len(candidates) or not all(math.isfinite(s) for s in scores):
        raise ValueError("scores ต้องครบทุก candidate และ finite")
    return _CaseTrace(query_text_sha256=qt, query_vector_sha256=qv,
                      components=tuple(comps), pairs=tuple(c["pair_sha256"] for c in comps),
                      scores=tuple(scores), call_count=1)


def _build_case_record(*, case_id, effective_role, category, selected_n,
                       unfiltered_items, sentinel_items, trace: _CaseTrace) -> dict:
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


def run_case(*, expected, scorer, case_id, frozen_case, query_text, query_vector, candidates,
             unfiltered_items, sentinel_items, selected_n) -> dict:
    """
    boundary เดียว score→record: validate scorer pin + query bind frozen + inputs → delegate → build record
    (CaseTrace ไม่ออกจาก function นี้ — ไม่มีทาง build record โดยไม่ score จาก pinned scorer จริง)
    """
    validate_scorer_metadata(scorer, expected)        # B1: mock/wrong pin → raise ก่อน delegate
    if not isinstance(frozen_case, dict):
        raise TypeError("frozen_case ต้องเป็น dict (frozen QueryProbe)")
    if _text_hash(query_text) != frozen_case.get("query_text_sha256"):
        raise ValueError("query_text ไม่ตรง frozen QueryProbe (เลือก query หลังเห็นผลไม่ได้)")
    if _vec_hash(query_vector) != frozen_case.get("query_vector_sha256"):
        raise ValueError("query_vector ไม่ตรง frozen QueryProbe")
    trace = _score_case(query_text=query_text, query_vector=query_vector, candidates=candidates,
                        authorized_pairs=frozen_case.get("authorized_pairs") or [], scorer=scorer)
    return _build_case_record(case_id=case_id, effective_role=frozen_case["effective_role"],
                              category=frozen_case["category"], selected_n=selected_n,
                              unfiltered_items=unfiltered_items, sentinel_items=sentinel_items, trace=trace)


def run_m4_cases(*, expected, frozen, scorer, inputs, selected_n):
    """validate scorer ครั้งเดียว → run ทุก case ผ่าน boundary เดียว → (case_records, ScorerProof)"""
    proof = validate_scorer_metadata(scorer, expected)
    records = []
    for it in inputs:
        fc = (frozen.get("cases") or {}).get(_id_hash(it["case_id"]))
        if fc is None:
            raise KeyError(f"case {it['case_id']!r} ไม่อยู่ frozen manifest")
        records.append(run_case(expected=expected, scorer=scorer, case_id=it["case_id"], frozen_case=fc,
                                query_text=it["query_text"], query_vector=it["query_vector"], candidates=it["candidates"],
                                unfiltered_items=it["unfiltered_items"], sentinel_items=it["sentinel_items"],
                                selected_n=selected_n))
    return records, proof


# ── frozen seed manifest (fixture author ประกาศ visibility matrix — independent oracle) ──────
def frozen_case(*, effective_role, category, query_text, query_vector, authorized_items, sentinel_items) -> dict:
    def _pairs(items):
        return [component(p, t)["pair_sha256"] for p, t in items]
    return {"role_identity_sha256": _id_hash(effective_role), "effective_role": effective_role, "category": category,
            "query_text_sha256": _text_hash(query_text), "query_vector_sha256": _vec_hash(query_vector),
            "authorized_pairs": _pairs(authorized_items), "sentinel_pairs": _pairs(sentinel_items)}


def build_frozen_manifest(cases: dict, required_categories, evaluated_roles) -> dict:
    return {"cases": {_id_hash(cid): fc for cid, fc in cases.items()},
            "required_categories": list(required_categories), "evaluated_roles": list(evaluated_roles)}


# ── run-level proofs (B2): isolation interlock + independent oracle ───────────────────────────
def build_isolation_proof(*, project_uuid, network_uuid, volume_uuid, collection_uuid, marker) -> dict:
    b = {"project_uuid_sha256": _id_hash(project_uuid), "network_uuid_sha256": _id_hash(network_uuid),
         "volume_uuid_sha256": _id_hash(volume_uuid), "collection_uuid_sha256": _id_hash(collection_uuid),
         "marker_sha256": _id_hash(marker)}
    b["isolation_proof_sha256"] = E.m4_isolation_proof_sha256(b)
    return b


def build_oracle_proof(*, frozen, index_sha256) -> dict:
    b = {"frozen_manifest_sha256": E.m4_case_manifest_sha256(frozen),
         "retrieval_index_manifest_sha256": index_sha256,
         "case_set_sha256": E._m4_case_set_sha256(list((frozen.get("cases") or {})))}
    b["oracle_proof_sha256"] = E.m4_oracle_proof_sha256(b)
    return b


def build_run_verdicts(*, expected, isolation_proof, oracle_proof, case_records, frozen) -> dict:
    """derive verdict จาก **validated proof + case records จริง** (ไม่รับ string/count ลอย ; count เป็น int จริง)"""
    if not isinstance(case_records, list) or not case_records:
        raise ValueError("case_records ว่าง")
    iso_ok = E.validate_m4_isolation_proof(isolation_proof) == []
    man = E._safe_m4_manifest_digest(frozen)
    oracle_ok = man is not None and E.validate_m4_oracle_proof(
        oracle_proof, man, expected.get("retrieval_index_manifest_sha256"), list(frozen.get("cases") or {})) == []
    cases = frozen.get("cases") or {}
    unauth = sentinel_hit = passed = traced = 0
    for r in case_records:
        fc = cases.get(r.get("case_id_sha256")) if isinstance(r, dict) else None
        auth = set(fc.get("authorized_pairs") or []) if isinstance(fc, dict) else set()
        sent = set(fc.get("sentinel_pairs") or []) if isinstance(fc, dict) else set()
        mip = set(r.get("model_input_pairs") or []) if isinstance(r, dict) else set()
        unauth += len(mip - auth)
        sentinel_hit += len(mip & sent)
        if isinstance(r, dict) and r.get("status") == "PASS":
            passed += 1
        if isinstance(r, dict) and type(r.get("model_call_count")) is int and r["model_call_count"] >= 1:
            traced += 1
    n = len(case_records)
    ok = iso_ok and oracle_ok and passed == n and traced == n and unauth == 0 and sentinel_hit == 0
    return {"status": "PASS" if ok else "FAIL",
            "isolated_interlock": "PASS" if iso_ok else "FAIL",
            "independent_oracle": "PASS" if oracle_ok else "FAIL",
            "sentinel_reached_model": sentinel_hit > 0, "unauthorized_in_model_inputs": unauth}


# ── assemble evidence/receipt ─────────────────────────────────────────────────────────────────
# B1: run_meta อนุญาตเฉพาะ metadata ที่ runner รู้เอง — **pin/inference_config ไม่อยู่** (มาจาก ScorerProof เท่านั้น)
_RUN_META_KEYS = frozenset({"m4_case_manifest_sha256", "run_id", "run_manifest_sha256", "image_digest",
                            "retrieval_index_manifest_sha256", "eval_set_sha256", "corpus_manifest_sha256",
                            "selected_n", "decision_eligible", "selection_digest"})
_M4_VERDICT_KEYS = ("status", "isolated_interlock", "independent_oracle", "sentinel_reached_model",
                    "unauthorized_in_model_inputs")


def assemble_evidence(case_records: list, *, stage, run_meta: dict, scorer_proof: dict,
                      isolation_proof: dict, oracle_proof: dict, verdicts: dict) -> dict:
    extra = set(run_meta) - _RUN_META_KEYS
    if extra:
        raise ValueError(f"run_meta มี key ต้องห้าม (ชน pin/verdict/evidence fields): {sorted(extra)}")
    if set(scorer_proof) != _SCORER_PROOF_KEYS:
        raise ValueError("scorer_proof ต้องมาจาก validate_scorer_metadata (key ครบพอดี)")
    if set(verdicts) != set(_M4_VERDICT_KEYS):
        raise ValueError("verdicts ต้องมาจาก build_run_verdicts (key ครบพอดี)")
    ev = dict(run_meta)                               # metadata ก่อน
    ev.update({k: verdicts[k] for k in _M4_VERDICT_KEYS})       # verdict เขียนทับ run_meta เสมอ
    ev["scorer_kind"] = scorer_proof["scorer_kind"]            # scorer_kind/pin จาก validated ScorerProof (ไม่ hardcode)
    for k in ("model_revision", "tokenizer_revision", "model_file_manifest_sha256", "inference_config"):
        ev[k] = scorer_proof[k]
    ev["isolation_proof"] = isolation_proof
    ev["oracle_proof"] = oracle_proof
    ev["schema_version"] = E.M4_SCHEMA_VERSION
    ev["evidence_stage"] = stage
    ev["per_case"] = case_records
    ev["raw_evidence_sha256"] = hashlib.sha256(E._canonical_json(case_records)).hexdigest()
    return ev


def assemble_receipt(evidence: dict, *, run_manifest, m4_case_manifest, expected, argv, stdout, stderr,
                     isolation_proof, started_utc, finished_utc, exit_code=0) -> dict:
    return {"schema_version": E.M4_RECEIPT_SCHEMA_VERSION, "run_id": evidence["run_id"],
            "run_manifest_sha256": run_manifest, "m4_case_manifest_sha256": m4_case_manifest,
            "raw_evidence_sha256": evidence["raw_evidence_sha256"], "command_sha256": _argv_hash(argv),
            "started_utc": started_utc, "finished_utc": finished_utc, "exit_code": exit_code,
            "stdout_sha256": _bytes_sha256(stdout), "stderr_sha256": _bytes_sha256(stderr),
            "isolation_marker_sha256": isolation_proof["marker_sha256"],   # marker load-bearing (ผูก IsolationProof)
            "retrieval_index_manifest_sha256": expected["retrieval_index_manifest_sha256"],
            "model_revision": expected["model_revision"], "image_digest": expected["image_digest"], "status": "PASS"}
