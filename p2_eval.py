"""
P2 eval-set validation + freeze (Codex B2) — pure, offline
p2_eval_set.json ต้องผ่าน validate ก่อน benchmark ; freeze hash กันเลือก label/param ตามผล
"""
from __future__ import annotations
import hashlib
import json


def validate_eval_set(cases: list, corpus: dict, known_roles) -> list:
    """
    คืน list ของ error (ว่าง = ผ่าน). fail เมื่อ:
      query_id ซ้ำ/ว่าง · query ว่าง · role ไม่รู้จัก · relevance ว่าง · grade ไม่ใช่ int>=1 ·
      relevant point ไม่อยู่ใน frozen corpus · relevant point ไม่ authorized สำหรับ role นั้น
      corpus: {point_id: {"allowed_roles":[...], "source": str}}
    """
    errs, seen = [], set()
    known = set(known_roles)
    for i, c in enumerate(cases):
        qid = c.get("query_id")
        tag = qid or f"case[{i}]"
        if not qid:
            errs.append(f"{tag}: query_id ว่าง")
        elif qid in seen:
            errs.append(f"query_id ซ้ำ: {qid}")
        else:
            seen.add(qid)
        if not c.get("query"):
            errs.append(f"{tag}: query ว่าง")
        role = c.get("role")
        if role not in known:
            errs.append(f"{tag}: role ไม่รู้จัก: {role!r}")
        rel = c.get("relevance")
        if not isinstance(rel, dict) or not rel:
            errs.append(f"{tag}: relevance ว่าง")
            continue
        for pid, grade in rel.items():
            if not isinstance(grade, int) or isinstance(grade, bool) or grade < 1:
                errs.append(f"{tag}: grade ผิด ({pid}={grade!r})")
            if pid not in corpus:
                errs.append(f"{tag}: relevant point {pid} ไม่อยู่ใน frozen corpus")
            elif role and role not in corpus[pid].get("allowed_roles", []):
                errs.append(f"{tag}: relevant point {pid} ไม่ authorized สำหรับ role {role}")
    return errs


def frozen_hash(cases: list) -> str:
    """dataset hash (freeze ก่อน benchmark) — canonical JSON"""
    return hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def permission_gate_ok(permission_exit_code: int) -> bool:
    """
    quality report จะ valid ต่อเมื่อ permission suite เขียว (leak=0, auth VERIFIED) — Codex hard gate
    permission fail/ERROR/INCONCLUSIVE (exit != 0) → ห้ามรายงาน metric เหมือนผ่าน
    """
    return permission_exit_code == 0
