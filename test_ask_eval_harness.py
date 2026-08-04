"""
End-to-end harness test (P5a rev2) — inject call_fn, assert exit_code ของ suite
ปิด Codex B1: 'suite ต้องไม่ exit 0 เมื่อ deny/empty ทั้งชุด' + acceptance list (main/exit behavior)
รัน offline ล้วน (ไม่มี network/Qdrant/model) — ขับ run_suite ด้วย fake call_fn

    PYTHONUTF8=1 python test_ask_eval_harness.py
"""
import io
import json
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import eval_contract as ec
import ask_eval

with open("permission_manifest.json", encoding="utf-8") as f:
    MANIFEST = json.load(f)
CANARIES = MANIFEST["canaries"]

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))


def rec(transport, points=None, answer=""):
    return {"transport": transport, "status": 200, "points": points or [], "answer": answer, "error": None}


def _canary_for(query):
    for c in CANARIES:
        if c["probe_query"] == query:
            return c
    return None


# ── fake ของ "ระบบที่ถูกต้อง" — key scoped, filter ทำงาน ──────────────────────
def fake_correct(path, body, key_role):
    requested = body.get("role")
    # key ต้อง scope role: ขอ role นอก key (spoof) → 403 (admin key กว้าง)
    if requested and key_role != requested and key_role != "admin":
        return rec(ec.DENIED)
    if path == "/search":
        c = _canary_for(body["query"])
        if c and requested in c["authorized_roles"]:
            return rec(ec.SUCCESS, [{"point_id": c["point_id"], "content": c["canary_token"]}])
        return rec(ec.SUCCESS, [])           # ค้นเจอ endpoint แต่ filter ตัดออก (SUCCESS + empty)
    if path == "/ask":
        return rec(ec.SUCCESS, [{"point_id": "p", "source": body.get("_src", "doc-A.md")}], "จาก [1]")
    return rec(ec.SUCCESS, [])


ASK_HAS = [{"question": "q1", "category": "has_answer", "expected_source": "doc-A.md"}]

# 1) ระบบถูกต้อง + ask ดี → exit 0 (เส้นเดียวที่เขียวได้)
r = ask_eval.run_suite(fake_correct, MANIFEST, ASK_HAS, "admin", [])
check("ระบบถูกต้อง (pos เจอ, neg ไม่เจอ) + ask hit -> exit 0",
      r["exit_code"] == 0 and all(v == ec.PASS for v in r["verdicts"]), r["verdicts"])

# 2) B1: DENIED ทั้งชุด (เช่นไม่มี key ใน enforce) → exit 1 (ไม่เขียวลวง)
r = ask_eval.run_suite(lambda p, b, role: rec(ec.DENIED), MANIFEST, [], "admin", [])
check("B1: DENIED ทั้งชุด -> exit 1", r["exit_code"] == 1 and set(r["verdicts"]) == {ec.INCONCLUSIVE}, r["verdicts"])

# 3) B1+M1: NO_RESULT ทั้งชุด (SUCCESS+ว่าง) → pos หา canary ไม่เจอ → INCONCLUSIVE → exit 1
r = ask_eval.run_suite(lambda p, b, role: rec(ec.SUCCESS, []), MANIFEST, [], "admin", [])
check("B1+M1: NO_RESULT ทั้งชุด (ไม่มี positive control) -> exit 1",
      r["exit_code"] == 1 and set(r["verdicts"]) == {ec.INCONCLUSIVE}, r["verdicts"])

# 4) LEAK: ทุก role (รวม forbidden) ได้ canary point → exit 1
def fake_leak(path, body, key_role):
    c = _canary_for(body["query"]) if path == "/search" else None
    if c:
        return rec(ec.SUCCESS, [{"point_id": c["point_id"], "content": c["canary_token"]}])
    return rec(ec.SUCCESS, [])
r = ask_eval.run_suite(fake_leak, MANIFEST, [], "admin", [])
check("LEAK: forbidden role ได้ canary point -> exit 1 + มี LEAK",
      r["exit_code"] == 1 and ec.LEAK in r["verdicts"], r["verdicts"])

# 5) M3: /ask MALFORMED แม้ permission เขียว → exit 1 (ask hard-fail)
def fake_ask_malformed(path, body, key_role):
    if path == "/ask":
        return rec(ec.MALFORMED)
    return fake_correct(path, body, key_role)
r = ask_eval.run_suite(fake_ask_malformed, MANIFEST, ASK_HAS, "admin", [])
check("M3: permission เขียว แต่ /ask MALFORMED -> exit 1",
      r["exit_code"] == 1 and all(v == ec.PASS for v in r["verdicts"]), r)

# 6) B1: has_answer ได้ 200+ว่าง → exit 1
def fake_ask_empty(path, body, key_role):
    if path == "/ask":
        return rec(ec.SUCCESS, [], "ไม่พบข้อมูล")
    return fake_correct(path, body, key_role)
r = ask_eval.run_suite(fake_ask_empty, MANIFEST, ASK_HAS, "admin", [])
check("B1: has_answer ได้ 200+ว่าง -> exit 1", r["exit_code"] == 1)

# 7) M2: auth preflight — spoof ไม่โดน deny → exit 1
def fake_no_scope(path, body, key_role):
    return rec(ec.SUCCESS, [])   # ระบบไม่ enforce role-scope: spoof ก็ SUCCESS (ผิด)
r = ask_eval.run_suite(fake_no_scope, MANIFEST, [], "admin", [("qc", "sales")])
check("M2: spoof role ไม่โดน DENIED -> preflight fail -> exit 1",
      r["exit_code"] == 1 and r["preflight"]["ok"] is False, r["preflight"])

# 8) M2: spoof โดน DENIED ถูกต้อง → preflight ผ่าน (permission ต้องเขียวด้วย)
r = ask_eval.run_suite(fake_correct, MANIFEST, [], "admin", [("qc", "sales"), ("sales", "qc")])
check("M2: spoof โดน DENIED -> preflight ผ่าน + exit 0",
      r["exit_code"] == 0 and r["preflight"]["ok"] and r["preflight"]["verified"], r["preflight"])

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
