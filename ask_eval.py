"""
Baseline evaluation ของ POST /ask (end-to-end: retrieval + LLM + citation)

วัด 4 อย่างที่ eval.py เดิม (dense retrieval อย่างเดียว) ไม่ครอบคลุม:
  1. citation hit    — expected_source โผล่ใน citations ไหม (92 ข้อ has_answer)
  2. no-answer       — คำถามนอกคลังต้องตอบ "ไม่พบข้อมูล" ไม่แต่งเอง (8 ข้อ)
  3. permission leak — ถามเอกสาร level สูงด้วย role ที่ไม่มีสิทธิ์ ต้องไม่เห็น (probes)
  4. latency         — p50/p95 ต่อคำถาม

Usage (รันบน server ที่ API อยู่):
    python3 ask_eval.py [--api http://localhost:8002] [--limit N]
ผลลัพธ์: ask_eval_raw.json + สรุปทาง stdout (เอาไปใส่ eval_results.md)
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# probe: (คำถามที่เนื้อหาอยู่ใน collection หวงห้าม, role ที่ไม่มีสิทธิ์, คำที่ห้ามโผล่ใน citation)
PERMISSION_PROBES = [
    ("ขั้นตอนการเรียกคืนผลิตภัณฑ์จากลูกค้าทำอย่างไร", "logistics", ["852", "GP-580", "เรียกคืน"]),
    ("การซ้อม Mock Recall ต้องทำอะไรบ้าง",             "production", ["GP-580", "852"]),
    ("การอบรมพนักงานใหม่มีหัวข้ออะไรบ้าง",              "production", ["Training", "JD"]),
    ("ขั้นตอนการขายและเสนอราคางานทำอย่างไร",           "qc",         ["721", "722", "731"]),
    ("การจัดซื้อวัตถุดิบต้องขออนุมัติใคร",               "prepress",   ["741", "754"]),
]


def call_ask(api: str, question: str, role: str, top_k: int = 4, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        f"{api}/ask",
        json.dumps({"question": question, "role": role, "top_k": top_k}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def said_no_answer(answer: str) -> bool:
    return any(kw in answer for kw in ("ไม่พบข้อมูล", "ไม่มีข้อมูล", "ไม่พบเอกสาร", "ไม่สามารถตอบ"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8002")
    ap.add_argument("--eval-set", default="eval_set.json")
    ap.add_argument("--limit", type=int, default=0, help="รันแค่ N ข้อแรก (0 = ทั้งหมด)")
    args = ap.parse_args()

    with open(args.eval_set, encoding="utf-8") as f:
        items = json.load(f)
    if args.limit:
        items = items[: args.limit]

    results = []
    lat = []
    for i, item in enumerate(items):
        t0 = time.time()
        try:
            resp = call_ask(args.api, item["question"], "admin")
            err = None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            resp = {"answer": "", "citations": []}
            err = str(e)
        dt = time.time() - t0
        lat.append(dt)

        cits = [c["source"] for c in resp.get("citations", [])]
        exp = item.get("expected_source", "")
        answer = resp.get("answer", "")
        hit = any(exp in c or c in exp for c in cits) if exp else None
        no_ans = said_no_answer(answer)

        results.append({
            "i": i, "category": item["category"], "question": item["question"],
            "expected_source": exp, "citations": cits, "hit": hit,
            "said_no_answer": no_ans, "latency_s": round(dt, 1),
            "answer_head": answer[:200], "error": err,
        })
        tag = "HIT" if hit else ("no-ans" if no_ans else ("ERR" if err else "miss"))
        print(f"  [{i + 1}/{len(items)}] {item['category']:10s} {tag:7s} {dt:5.1f}s", flush=True)

    print("\n== permission probes ==", flush=True)
    probes = []
    for q, role, banned in PERMISSION_PROBES:
        try:
            resp = call_ask(args.api, q, role)
            err = None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            resp = {"answer": "", "citations": []}
            err = str(e)
        cits = [c["source"] for c in resp.get("citations", [])]
        leaked = [c for c in cits if any(b.lower() in c.lower() for b in banned)]
        probes.append({
            "question": q, "role": role, "citations": cits, "leaked": leaked,
            "said_no_answer": said_no_answer(resp.get("answer", "")), "error": err,
        })
        print(f"  role={role:10s} leak={'YES ' + str(leaked) if leaked else 'no'}", flush=True)

    with open("ask_eval_raw.json", "w", encoding="utf-8") as f:
        json.dump({"results": results, "probes": probes}, f, ensure_ascii=False, indent=1)

    # ---- summary ----
    has = [r for r in results if r["category"] == "has_answer" and not r["error"]]
    noa = [r for r in results if r["category"] == "no_answer" and not r["error"]]
    errs = [r for r in results if r["error"]]
    lat_ok = sorted(lat)
    p = lambda q: lat_ok[int(len(lat_ok) * q)] if lat_ok else 0

    print("\n========== ASK EVAL SUMMARY ==========")
    print(f"total={len(results)}  errors={len(errs)}")
    if has:
        hits = sum(1 for r in has if r["hit"])
        wrong_no = sum(1 for r in has if r["said_no_answer"])
        print(f"has_answer ({len(has)}): citation-hit {hits}/{len(has)} = {hits / len(has):.0%}"
              f" | ตอบ'ไม่พบ'ทั้งที่มีคำตอบ {wrong_no}")
    if noa:
        honest = sum(1 for r in noa if r["said_no_answer"])
        print(f"no_answer  ({len(noa)}): honest {honest}/{len(noa)} = {honest / len(noa):.0%}"
              f" (ที่เหลือ = hallucination risk)")
    leaks = [pr for pr in probes if pr["leaked"]]
    print(f"permission probes: leak {len(leaks)}/{len(probes)}")
    print(f"latency: p50 {p(0.5):.1f}s | p95 {p(0.95):.1f}s")
    print("=======================================")


if __name__ == "__main__":
    main()
