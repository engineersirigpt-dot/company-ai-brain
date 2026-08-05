"""
P2 evidence adapter — แปลง raw output จาก `p2_harness` เป็น **bound evidence** สำหรับ `p2_runplan.decide_p2()`
pure/offline · consumer อย่างเดียว (ไม่รัน harness/Qdrant/model เอง)

หลักการ (Codex GO scope):
- identity/tags = **frozen eval cases เป็น authoritative** — join ด้วย `query_id`, ไม่เชื่อ intent_id/role/challenge_tags
  ที่ harness/caller ส่งมา (harness อาจไม่ได้ผลิต field เหล่านี้ให้ถูกด้วยซ้ำ)
- บังคับ exact query set, reject duplicate/missing/extra, arm completeness, finite metrics
- digest/schema ใช้ canonical ของ `p2_runplan` (ไม่ทำซ้ำเอง)
- **output ไม่มี `approved`/decision** — public approval surface ยังเป็น `decide_p2()` เท่านั้น

flow (Slice 2 runner):
    raw = p2_harness.run_ranking(...)                      # mechanics-unapproved / raw
    quality = build_quality_evidence(raw, cases, root, sel_digest, selected_n)
    decide_p2(plan, dev, quality, latency, m4, canary, signoff, cases, corpus, known_roles)
"""
from __future__ import annotations

import p2_runplan as RP

ARMS = ("dense", "rerank", "fused")
_RAW_KIND = "mechanics-ranking-unapproved"


def _test_cases_by_qid(cases) -> dict:
    return {c["query_id"]: c for c in cases
            if isinstance(c, dict) and c.get("split") == "test" and isinstance(c.get("query_id"), str)}


def build_quality_rows(raw_ranking, cases) -> list:
    """
    join raw per-query (จาก `run_ranking`) กับ frozen test cases ด้วย query_id — **identity/tags จาก frozen cases**
    เก็บเฉพาะ arms.ndcg@5 (สิ่งที่ quality gate ใช้). raise ValueError ถ้า:
    kind ผิด · per_query ว่าง · query_id ซ้ำ/ไม่อยู่ใน frozen/ไม่ครบ set · arm ไม่ครบ · ndcg@5 ไม่ finite [0,1]
    """
    if not isinstance(raw_ranking, dict) or raw_ranking.get("kind") != _RAW_KIND:
        raise ValueError(f"raw_ranking ต้องเป็น output ของ run_ranking (kind={_RAW_KIND!r})")
    if raw_ranking.get("approved") is not False or raw_ranking.get("status") != "COMPLETE":
        raise ValueError("raw_ranking ต้อง approved=False + status=COMPLETE (zero-skip)")
    raw = raw_ranking.get("per_query")
    if not isinstance(raw, list) or not raw:
        raise ValueError("raw_ranking.per_query ว่าง/ไม่ใช่ list")
    by_qid = _test_cases_by_qid(cases)
    seen, rows = set(), []
    for r in raw:
        if not isinstance(r, dict):
            raise ValueError("raw per_query row ไม่ใช่ dict")
        qid = r.get("query_id")
        if qid in seen:
            raise ValueError(f"duplicate query_id ใน raw: {qid!r}")
        seen.add(qid)
        c = by_qid.get(qid)
        if c is None:
            raise ValueError(f"query_id {qid!r} ไม่อยู่ใน frozen test cases")
        arms = r.get("arms")
        if not isinstance(arms, dict) or set(arms) != set(ARMS):
            raise ValueError(f"{qid}: arms ต้องครบ {ARMS}")
        nd = {}
        for a in ARMS:
            v = arms[a].get("ndcg@5") if isinstance(arms[a], dict) else None
            if not RP._is_unit_float(v):
                raise ValueError(f"{qid}: {a} ndcg@5 ไม่ finite ใน [0,1]")
            nd[a] = {"ndcg@5": v}
        # identity/tags จาก frozen case เท่านั้น (ไม่เชื่อ r["intent_id"]/r["role"])
        rows.append({"query_id": qid, "intent_id": c.get("intent_id"), "role": c.get("role"),
                     "challenge_tags": list(c.get("challenge_tags") or []), "arms": nd})
    if seen != set(by_qid):
        missing = sorted(set(by_qid) - seen)
        extra = sorted(str(q) for q in seen - set(by_qid))
        raise ValueError(f"quality query set != frozen test cases (missing={missing[:3]} extra={extra[:3]})")
    return rows


def build_quality_evidence(raw_ranking, cases, run_manifest, sel_digest, selected_n) -> dict:
    """quality_evidence ผูก root + selection — per_query identity มาจาก frozen cases ; raw_result_digest = recompute"""
    rows = build_quality_rows(raw_ranking, cases)
    return {"split": "test", "run_manifest_sha256": run_manifest,
            "selection_digest": sel_digest, "selected_n": selected_n,
            "raw_result_digest": RP.raw_digest(rows), "per_query": rows}


def _coerce_n_key(k) -> int:
    """
    M3: normalize N key แบบไม่กลืนข้อมูลเงียบ — รับเฉพาะ **positive int (ไม่ใช่ bool)** หรือ
    **canonical decimal string ที่ round-trip exact** ; reject float/whitespace/sign/exponent/leading-zero/bool
    (กัน int(10.5)->10 truncate และ "010"/" 10"/"1e1" ที่ค่าไม่ตรงตัวอักษร)
    """
    if type(k) is int:                                   # bool ถูกกัน (type(True) is bool ไม่ใช่ int)
        n = k
    elif isinstance(k, str) and k.isascii() and k.isdigit():
        n = int(k)
        if str(n) != k:                                  # reject leading zeros / non-canonical
            raise ValueError(f"by_n key ไม่ canonical (round-trip ไม่ตรง): {k!r}")
    else:
        raise ValueError(f"by_n key ต้องเป็น positive int หรือ canonical decimal string: {k!r}")
    if n <= 0:
        raise ValueError(f"by_n key ต้อง positive: {k!r}")
    return n


def build_dev_evidence(raw_by_n, run_manifest) -> dict:
    """
    dev_evidence ผูก root — raw_by_n = {N:{point_recall,doc_recall,candidate_hit,completed_queries,completed_intents}}
    normalize key แบบ fail-closed (M3) + reject normalized-key collision ; raw_result_digest = recompute
    (decide_p2/validate_dev_evidence ตรวจ N_SET/count/finite เอง)
    """
    if not isinstance(raw_by_n, dict):
        raise ValueError("raw_by_n ต้องเป็น dict")
    by_n = {}
    for k, v in raw_by_n.items():
        if not isinstance(v, dict):
            raise ValueError(f"raw_by_n value ต้องเป็น dict: {k!r}")
        n = _coerce_n_key(k)
        if n in by_n:
            raise ValueError(f"by_n normalized-key collision ที่ N={n} (เช่น {{10, '10'}})")
        by_n[n] = dict(v)
    return {"split": "dev", "run_manifest_sha256": run_manifest,
            "raw_result_digest": RP.raw_digest(by_n), "by_n": by_n}


def build_latency_evidence(raw_stages, run_manifest, sel_digest, selected_n, warmup,
                           error_count=0, oom_count=0) -> dict:
    """latency_evidence ผูก root + selection — stages ตรงตาม LATENCY_STAGES ; raw_latency_digest = recompute"""
    if not isinstance(raw_stages, dict):
        raise ValueError("raw_stages ต้องเป็น dict")
    stages = {k: list(v) for k, v in raw_stages.items()}
    return {"run_manifest_sha256": run_manifest, "selection_digest": sel_digest, "selected_n": selected_n,
            "raw_latency_digest": RP.raw_digest(stages), "error_count": error_count, "oom_count": oom_count,
            "warmup": warmup, "stages": stages}
