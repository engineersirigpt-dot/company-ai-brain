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
    """P1 policy path เดียวกับ retrieval (admin ไม่มี bypass; stale/quarantine/wrong-version ไม่ผ่าน)"""
    if role not in P.KNOWN_ROLES:
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
        if not isinstance(rsrc, list) or not rsrc or any(_bad_str(s) for s in rsrc):
            errs.append(f"{tag}: relevant_sources ว่าง/ผิดชนิด")
            rsrc = []
        rel = c.get("relevance")
        if not isinstance(rel, dict) or not rel:
            errs.append(f"{tag}: relevance ว่าง (ranking ต้องมี relevant point)")
            continue
        for pid, grade in rel.items():
            if _bad_str(pid):
                errs.append(f"{tag}: relevant point id ว่าง")
                continue
            if not _is_grade(grade):
                errs.append(f"{tag}: grade นอก allowlist {LOCKED_GRADES} ({pid}={grade!r})")
            if pid not in corpus:
                errs.append(f"{tag}: relevant point {pid} ไม่อยู่ใน frozen corpus")
                continue
            entry = corpus[pid]
            if role in known and not is_authorized(entry.get("payload", {}), role):
                errs.append(f"{tag}: relevant point {pid} ไม่ authorized (P1 policy) สำหรับ role {role}")
            if entry.get("source") not in rsrc:
                errs.append(f"{tag}: source ของ {pid} ({entry.get('source')!r}) ไม่อยู่ใน relevant_sources")
    return errs


# ── freeze (M1) — ต้องมี corpus hash ไม่ใช่แค่ cases ─────────────────────────────
def eval_set_sha256(cases) -> str:
    return hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def corpus_manifest_sha256(corpus: dict, rerank_text_version: str = RERANK_TEXT_VERSION) -> str:
    rows = [{
        "point_id": pid,
        "source": corpus[pid].get("source"),
        "rerank_text_sha256": hashlib.sha256((corpus[pid].get("rerank_text") or "").encode("utf-8")).hexdigest(),
        "payload": corpus[pid].get("payload"),
        "rerank_text_version": rerank_text_version,
    } for pid in sorted(corpus)]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def benchmark_manifest(cases, corpus: dict) -> dict:
    """hash ที่ต้องผูกกับ git commit/model/tokenizer/image ใน run metadata ก่อน benchmark (Slice 2)"""
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
