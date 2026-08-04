"""
Baseline evaluation ของ POST /ask (end-to-end: retrieval + LLM + citation)
+ permission-leak probes — **P5a: measurement contract ที่ซ่อมแล้ว** (Codex review B3/M2)

แก้จากเวอร์ชันเดิมที่ "เขียวผิดเหตุผล":
  - ส่ง `X-API-Key` (จาก --api-key / env KB_EVAL_API_KEY) ; ไม่มี key → เตือน (enforce จะได้ DENIED)
  - แยกผลเป็น OK / NO_RESULT / DENIED(401/403) / ERROR (ผ่าน eval_contract) — **auth/HTTP error ไม่นับว่า 'ไม่รั่ว'**
  - permission leak เช็ค **collection ของ citation ⊆ allow-set ของ role** (จาก rbac_config, ไม่ใช่แค่ keyword) + banned canary
  - citation แยก retrieval-source-hit ออกจาก citation-reference-validity (`[n]` ที่ answer อ้างจริง)
  - exit non-zero ถ้ามี LEAK หรือ ERROR (measurement พัง/รั่ว) — ให้ CI จับได้

Usage (รันบน server ที่ API อยู่):
    KB_EVAL_API_KEY=... python ask_eval.py [--api http://localhost:8002] [--role admin] [--limit N]
ผลลัพธ์: ask_eval_raw.json + สรุปทาง stdout
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

import eval_contract as ec
from rbac_config import COLLECTIONS

# role → เซ็ต collection ที่เข้าถึงได้ (allow-set สำหรับ leak check — invert จาก ACL จริง)
ROLE_COLLECTIONS: dict[str, set[str]] = {}
for _col, _meta in COLLECTIONS.items():
    for _r in _meta["allowed_roles"]:
        ROLE_COLLECTIONS.setdefault(_r, set()).add(_col)

# probe: (คำถามที่เนื้อหาอยู่ใน collection หวงห้ามสำหรับ role นี้, role ที่ไม่มีสิทธิ์, canary ที่ห้ามโผล่)
PERMISSION_PROBES = [
    ("ขั้นตอนการเรียกคืนผลิตภัณฑ์จากลูกค้าทำอย่างไร", "logistics", ["852", "GP-580", "เรียกคืน"]),
    ("การซ้อม Mock Recall ต้องทำอะไรบ้าง",             "production", ["GP-580", "852"]),
    ("การอบรมพนักงานใหม่มีหัวข้ออะไรบ้าง",              "production", ["Training", "JD"]),
    ("ขั้นตอนการขายและเสนอราคางานทำอย่างไร",           "qc",         ["721", "722", "731"]),
    ("การจัดซื้อวัตถุดิบต้องขออนุมัติใคร",               "prepress",   ["741", "754"]),
]


def call(api: str, path: str, body: dict, key: str, timeout: int = 180):
    """เรียก API — คืน (status_code, exc, resp_dict) โดย **ไม่กลืน error เป็น citations ว่าง**"""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(f"{api}{path}", json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, None, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e, {"answer": "", "citations": [], "results": []}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, e, {"answer": "", "citations": [], "results": []}


def said_no_answer(answer: str) -> bool:
    return any(kw in answer for kw in ("ไม่พบข้อมูล", "ไม่มีข้อมูล", "ไม่พบเอกสาร", "ไม่สามารถตอบ"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8002")
    ap.add_argument("--eval-set", default="eval_set.json")
    ap.add_argument("--api-key", default=os.getenv("KB_EVAL_API_KEY", ""))
    ap.add_argument("--role", default="admin", help="role สำหรับ has_answer/no_answer cases (key ต้อง scope role นี้)")
    ap.add_argument("--limit", type=int, default=0, help="รันแค่ N ข้อแรก (0 = ทั้งหมด)")
    args = ap.parse_args()

    if not args.api_key:
        print("WARNING: ไม่มี --api-key / KB_EVAL_API_KEY — ถ้า API เป็น AUTH_MODE=enforce จะได้ DENIED ทุกข้อ "
              "(suite จะ fail ตามสัญญาใหม่ ไม่ใช่เขียวลวง)", flush=True)

    with open(args.eval_set, encoding="utf-8") as f:
        items = json.load(f)
    if args.limit:
        items = items[: args.limit]

    # ---- has_answer / no_answer ----
    results, lat = [], []
    for i, item in enumerate(items):
        t0 = time.time()
        status, exc, resp = call(args.api, "/ask",
                                 {"question": item["question"], "role": args.role, "top_k": 4}, args.api_key)
        dt = time.time() - t0
        lat.append(dt)
        cits = resp.get("citations", [])
        outcome = ec.classify_outcome(status, exc, len(cits))
        sources = [c.get("source", "") for c in cits]
        exp = item.get("expected_source", "")
        retrieval_hit = (any(exp in s or s in exp for s in sources) if exp else None)
        ci = ec.citation_integrity(resp.get("answer", ""), len(cits))
        results.append({
            "i": i, "category": item["category"], "outcome": outcome,
            "retrieval_hit": retrieval_hit, "citation_valid": ci["valid"], "cited_any": ci["cited_any"],
            "invalid_refs": ci["invalid_refs"], "said_no_answer": said_no_answer(resp.get("answer", "")),
            "latency_s": round(dt, 1), "error": str(exc) if exc else None,
        })
        tag = outcome if outcome != ec.OK else ("HIT" if retrieval_hit else "miss")
        print(f"  [{i + 1}/{len(items)}] {item['category']:10s} {tag:9s} {dt:5.1f}s", flush=True)

    # ---- permission probes (leak = citation.collection ⊄ allow-set ของ role, หรือ banned canary) ----
    print("\n== permission probes ==", flush=True)
    probes = []
    for q, role, banned in PERMISSION_PROBES:
        status, exc, resp = call(args.api, "/ask", {"question": q, "role": role, "top_k": 4}, args.api_key)
        cits = resp.get("citations", [])
        outcome = ec.classify_outcome(status, exc, len(cits))
        retrieved_cols = [c.get("collection", "?") for c in cits]
        allow_cols = ROLE_COLLECTIONS.get(role, set())
        v = ec.leak_verdict(outcome, retrieved_cols, allow_cols, resp.get("answer", ""), banned)
        probes.append({
            "question": q, "role": role, "verdict": v["verdict"],
            "leaked_collections": v["leaked_ids"], "banned_hit": v["banned_hit"],
            "citations": [{"source": c.get("source", ""), "collection": c.get("collection", "?"),
                           "point_id": c.get("point_id", "")} for c in cits],
            "error": str(exc) if exc else None,
        })
        detail = (f"LEAK cols={v['leaked_ids']} banned={v['banned_hit']}" if v["verdict"] == ec.LEAK else v["verdict"])
        print(f"  role={role:10s} → {detail}", flush=True)

    with open("ask_eval_raw.json", "w", encoding="utf-8") as f:
        json.dump({"results": results, "probes": probes}, f, ensure_ascii=False, indent=1)

    # ---- summary (แยก verdict ชัด — ห้าม collapse) ----
    lat_ok = sorted(lat)
    p = lambda q: lat_ok[int(len(lat_ok) * q)] if lat_ok else 0
    has = [r for r in results if r["category"] == "has_answer" and r["outcome"] == ec.OK]
    noa = [r for r in results if r["category"] == "no_answer" and r["outcome"] == ec.OK]
    r_errs = [r for r in results if r["outcome"] == ec.ERROR]
    r_denied = [r for r in results if r["outcome"] == ec.DENIED]
    pv = Counter(pr["verdict"] for pr in probes)

    print("\n========== ASK EVAL SUMMARY ==========")
    print(f"total={len(results)}  OK={len(has) + len(noa)}  ERROR={len(r_errs)}  DENIED={len(r_denied)}")
    if has:
        hit = sum(1 for r in has if r["retrieval_hit"])
        cited = sum(1 for r in has if r["cited_any"])
        badref = sum(1 for r in has if not r["citation_valid"])
        print(f"has_answer OK ({len(has)}): retrieval-source-hit {hit}/{len(has)} = {hit / len(has):.0%}"
              f" | answer อ้าง [n] {cited}/{len(has)} | dangling-ref {badref}  (M2: hit != citation accuracy)")
    if noa:
        honest = sum(1 for r in noa if r["said_no_answer"])
        print(f"no_answer OK ({len(noa)}): honest 'ไม่พบ' {honest}/{len(noa)} (ที่เหลือ = hallucination risk)")
    print(f"permission probes: CLEAN={pv[ec.CLEAN]} LEAK={pv[ec.LEAK]} DENIED={pv[ec.DENIED]} "
          f"NO_RESULT={pv[ec.NO_RESULT]} ERROR={pv[ec.ERROR]}")
    print(f"latency: p50 {p(0.5):.1f}s | p95 {p(0.95):.1f}s")
    print("=======================================")

    # measurement พังหรือรั่ว → fail (ให้ CI จับ ไม่เขียวลวง)
    broken = pv[ec.LEAK] or pv[ec.ERROR] or len(r_errs)
    if broken:
        print(f"FAIL: LEAK={pv[ec.LEAK]} probe-ERROR={pv[ec.ERROR]} ask-ERROR={len(r_errs)}", flush=True)
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
