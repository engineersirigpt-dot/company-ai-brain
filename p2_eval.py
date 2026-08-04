"""
P2 eval-set validation + freeze (Codex B2/M1/M2/M5) — pure, offline
- authorization ใช้ P1 policy path เดียวกับ candidate provider (compile_retrieval_filter + matches_policy)
  ไม่ reimplement membership check (B2)
- freeze ทั้ง eval cases และ corpus manifest (M1) — "frozen corpus" ต้องมี corpus hash ไม่ใช่แค่ cases
- ranking dataset = case_type "ranking" เท่านั้น, relevance ไม่ว่าง ; no-answer แยก abstention suite (M2)
"""
from __future__ import annotations
import hashlib
import json
import unicodedata

import policy as P

BENCHMARK_CONTRACT_VERSION = "p2-bench-v1"
RERANK_TEXT_VERSION = "heading+child-v1"
LOCKED_GRADES = (1, 2, 3)                       # graded relevance allowlist (exact int)
REQUIRED_CASE_FIELDS = ("query_id", "query", "role", "lang", "category", "split",
                        "case_type", "relevance", "relevant_sources", "label_status")
VALID_SPLITS = frozenset({"dev", "test"})
VALID_LABEL_STATUS = frozenset({"human-reviewed"})


def _effective_access(role: str):
    return P.EffectiveAccess(P.ServicePrincipal("p2-eval", (role,), True, "enforce"), role)


def is_authorized(payload: dict, role: str) -> bool:
    """
    P1 policy path เดียวกับ retrieval **บวก stored-shape validation** (B2.1):
    matches_policy เลียนแบบ Qdrant MatchAny → scalar `allowed_roles:"qc"` จะ match ได้ แต่ผิด
    policy-v1 contract (write boundary ต้อง quarantine) → ต้องผ่าน validate_stored_payload ก่อน
    (admin ไม่มี bypass; stale/quarantine/wrong-version/scalar/non-list/bad-schema ไม่ผ่าน)
    """
    if role not in P.KNOWN_ROLES:
        return False
    if not P.payload_is_policy_v1(payload):
        return False
    valid, _ = P.validate_stored_payload(payload)
    if not valid:
        return False
    return P.matches_policy(payload, P.compile_retrieval_filter(_effective_access(role)))


def _bad_str(s) -> bool:
    """ว่าง/ผิดชนิด/มี control char (ยกเว้น none — Thai ปกติผ่าน)"""
    if not isinstance(s, str) or not s.strip():
        return True
    return any(unicodedata.category(ch) == "Cc" for ch in s)


def _is_grade(g) -> bool:
    return type(g) is int and g in LOCKED_GRADES


def validate_ranking_eval_set(cases, corpus: dict, known_roles) -> list:
    """
    คืน list ของ error (ว่าง = ผ่าน). corpus[point_id] = {"source": str, "rerank_text": str, "payload": {v1}}
    fail เมื่อ: field บังคับหาย/ผิดชนิด/control char · query_id/query/source/point ซ้ำ-ว่าง ·
      case_type != ranking · split/label_status ผิด · grade นอก {1,2,3} · relevance ว่าง ·
      relevant point ไม่อยู่ frozen corpus · **ไม่ authorized (P1 policy) สำหรับ role** ·
      source ของ relevant point ไม่อยู่ใน relevant_sources
    """
    if not isinstance(cases, list):
        return ["cases ต้องเป็น list"]
    known = set(known_roles)
    errs, seen_qid = [], set()
    for i, c in enumerate(cases):
        tag = f"case[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{tag}: ไม่ใช่ object")
            continue
        for f in REQUIRED_CASE_FIELDS:
            if f not in c:
                errs.append(f"{tag}: field '{f}' หาย")
        qid = c.get("query_id")
        if isinstance(qid, str) and qid.strip():
            tag = qid
        if _bad_str(qid):
            errs.append(f"{tag}: query_id ว่าง/ผิดชนิด/control char")
        elif qid in seen_qid:
            errs.append(f"query_id ซ้ำ: {qid}")
        else:
            seen_qid.add(qid)
        if _bad_str(c.get("query")):
            errs.append(f"{tag}: query ว่าง/ผิดชนิด/control char")
        if c.get("case_type") != "ranking":
            errs.append(f"{tag}: case_type ต้อง 'ranking' (no-answer -> abstention suite แยก)")
        if c.get("split") not in VALID_SPLITS:
            errs.append(f"{tag}: split ผิด {c.get('split')!r}")
        if c.get("label_status") not in VALID_LABEL_STATUS:
            errs.append(f"{tag}: label_status ต้อง human-reviewed")
        if _bad_str(c.get("lang")):
            errs.append(f"{tag}: lang ว่าง")
        if _bad_str(c.get("category")):
            errs.append(f"{tag}: category ว่าง")
        role = c.get("role")
        if role not in known:
            errs.append(f"{tag}: role ไม่รู้จัก {role!r}")
        rsrc = c.get("relevant_sources")
        rsrc_ok = isinstance(rsrc, list) and rsrc and not any(_bad_str(s) for s in rsrc)
        if not rsrc_ok:
            errs.append(f"{tag}: relevant_sources ว่าง/ผิดชนิด/control char")
        elif len(set(rsrc)) != len(rsrc):
            errs.append(f"{tag}: relevant_sources มี source ซ้ำ")
        rel = c.get("relevance")
        if not isinstance(rel, dict) or not rel:
            errs.append(f"{tag}: relevance ว่าง (ranking ต้องมี relevant point)")
            continue
        derived = set()
        for pid, grade in rel.items():
            if _bad_str(pid):
                errs.append(f"{tag}: relevant point id ว่าง")
                continue
            if not _is_grade(grade):
                errs.append(f"{tag}: grade นอก allowlist {LOCKED_GRADES} ({pid}={grade!r})")
            entry = corpus.get(pid)
            if not isinstance(entry, dict):
                errs.append(f"{tag}: relevant point {pid} ไม่อยู่ใน frozen corpus")
                continue
            if role in known and not is_authorized(entry.get("payload", {}), role):
                errs.append(f"{tag}: relevant point {pid} ไม่ authorized (P1 policy) สำหรับ role {role}")
            if isinstance(entry.get("source"), str):
                derived.add(entry["source"])
        # M5.1: relevant_sources ต้อง exact set-equality กับ source ที่ derive จาก relevant points
        if rsrc_ok:
            want = set(rsrc)
            if derived != want:
                errs.append(f"{tag}: relevant_sources ไม่ตรง exact "
                            f"(missing={sorted(derived - want)} extra={sorted(want - derived)})")
    return errs


# ── frozen-corpus validation (M1.1) — fail-closed ก่อนคำนวณ/hash ────────────────
def validate_corpus(corpus) -> list:
    """
    frozen corpus ต้อง fail-closed: dict ไม่ว่าง ; แต่ละ entry มี point_id/source/rerank_text เป็น
    non-blank str ไม่มี control char ; payload เป็น policy-v1 ที่ validate_stored_payload ผ่าน
    """
    if not isinstance(corpus, dict) or not corpus:
        return ["corpus ว่าง/ไม่ใช่ dict"]
    errs = []
    for pid, e in corpus.items():
        tag = f"corpus[{pid!r}]"
        if _bad_str(pid):
            errs.append(f"{tag}: point_id ว่าง/control char")
        if not isinstance(e, dict):
            errs.append(f"{tag}: entry ไม่ใช่ object")
            continue
        if _bad_str(e.get("source")):
            errs.append(f"{tag}: source ว่าง/control char")
        if _bad_str(e.get("rerank_text")):
            errs.append(f"{tag}: rerank_text ว่าง/ผิดชนิด/control char")
        pay = e.get("payload")
        if not P.payload_is_policy_v1(pay):
            errs.append(f"{tag}: payload ไม่ใช่ policy-v1")
        else:
            ok, reason = P.validate_stored_payload(pay)
            if not ok:
                errs.append(f"{tag}: payload ผิด contract ({reason})")
    return errs


def validate_benchmark(cases, corpus, known_roles) -> list:
    """รวม corpus + cases validation ; benchmark valid ก็ต่อเมื่อคืน []"""
    errs = validate_corpus(corpus)
    if not isinstance(cases, list) or not cases:
        errs.append("ranking cases ว่าง")
    errs += validate_ranking_eval_set(cases, corpus, known_roles)
    return errs


# ── freeze (M1) — ต้องมี corpus hash ไม่ใช่แค่ cases ─────────────────────────────
def eval_set_sha256(cases) -> str:
    return hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def corpus_manifest_sha256(corpus: dict, rerank_text_version: str = RERANK_TEXT_VERSION) -> str:
    def _text_hash(rt):
        if not isinstance(rt, str):
            raise ValueError("rerank_text ต้องเป็น str (validate_corpus ก่อน hash)")
        return hashlib.sha256(rt.encode("utf-8")).hexdigest()
    rows = [{
        "point_id": pid,
        "source": corpus[pid].get("source"),
        "rerank_text_sha256": _text_hash(corpus[pid].get("rerank_text")),
        "payload": corpus[pid].get("payload"),
        "rerank_text_version": rerank_text_version,
    } for pid in sorted(corpus)]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def benchmark_manifest(cases, corpus: dict, known_roles) -> dict:
    """
    สร้างได้เฉพาะเมื่อ validate_benchmark ผ่าน (M1.1) — ผูกกับ git/model/tokenizer/image + Slice 2
    ต้องเพิ่ม retrieval_index_manifest_sha256 (actual vectors/index digest) ใน run metadata
    """
    errs = validate_benchmark(cases, corpus, known_roles)
    if errs:
        raise ValueError(f"benchmark ยังไม่ valid ({len(errs)} errors) — สร้าง manifest ไม่ได้: {errs[:3]}")
    return {
        "benchmark_contract_version": BENCHMARK_CONTRACT_VERSION,
        "eval_set_sha256": eval_set_sha256(cases),
        "corpus_manifest_sha256": corpus_manifest_sha256(corpus),
        "rerank_text_version": RERANK_TEXT_VERSION,
    }


def permission_gate_ok(exit_code) -> bool:
    """
    quality report valid เฉพาะเมื่อ permission suite เขียว (leak=0, auth VERIFIED). B1: type-strict —
    รับเฉพาะ exact int (False/True/0.0/None/"0" -> ValueError กัน fail-open จาก `False == 0`)
    """
    if type(exit_code) is not int:
        raise ValueError(f"permission exit code must be exact int, got {type(exit_code).__name__}")
    return exit_code == 0
