"""
End-to-end harness test (P5a rev2.1) — inject call_fn ผ่าน normalize_response (ครอบ seam จริง)
assert exit_code ของ run_suite ตาม Codex acceptance รอบสอง

    python test_ask_eval_harness.py
"""
import io
import json
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import eval_contract as ec
import ask_eval
from ask_eval import normalize_response as norm

with open("permission_manifest.json", encoding="utf-8") as f:
    MANIFEST = json.load(f)
CANARIES = MANIFEST["canaries"]

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))


def _canary_for(query):
    for c in CANARIES:
        if c["probe_query"] == query:
            return c
    return None


def _denied(status):   # จำลอง HTTPError ที่ status นั้น
    return norm("/search", status, Exception("http"), {})


# ── fake ของ "ระบบที่ถูกต้อง": key scoped (spoof->403), filter ทำงาน ──────────
def fake_correct(path, body, key_role):
    requested = body.get("role")
    if requested and key_role != requested and key_role != "admin":
        return _denied(403)                       # spoof role นอก key -> 403
    if path == "/search":
        c = _canary_for(body["query"])
        if c and requested in c["authorized_roles"]:
            return norm("/search", 200, None,
                        {"results": [{"point_id": c["point_id"], "content": c["canary_token"]}]})
        return norm("/search", 200, None, {"results": []})     # filter ตัดออก (valid empty)
    if path == "/ask":
        return norm("/ask", 200, None,
                    {"answer": "จาก [1]", "citations": [{"point_id": "p", "source": "doc-A.md"}]})
    return norm(path, 200, None, {"results": []})


SPOOF_OK = [("qc", "sales"), ("sales", "qc")]        # จะได้ 403 -> VERIFIED
ASK_HAS = [{"question": "q1", "category": "has_answer", "expected_source": "doc-A.md"}]

# 1) ระบบถูกต้อง + auth VERIFIED -> exit 0 (เส้นเดียวที่เขียว)
r = ask_eval.run_suite(fake_correct, MANIFEST, [], "admin", SPOOF_OK)
check("ระบบถูกต้อง (exhaustive roles) + spoof 403 -> exit 0",
      r["exit_code"] == 0 and set(r["verdicts"]) == {ec.PASS} and r["auth_status"] == ec.VERIFIED, r["auth_status"])

# 2) B1: DENIED ทั้งชุด -> exit 1
r = ask_eval.run_suite(lambda p, b, role: _denied(403), MANIFEST, [], "admin", SPOOF_OK)
check("DENIED ทั้งชุด -> exit 1", r["exit_code"] == 1 and set(r["verdicts"]) == {ec.INCONCLUSIVE})

# 3) B1+M1: NO_RESULT ทั้งชุด -> exit 1
r = ask_eval.run_suite(lambda p, b, role: norm("/search", 200, None, {"results": []}), MANIFEST, [], "admin", SPOOF_OK)
check("NO_RESULT ทั้งชุด (ไม่มี positive control) -> exit 1",
      r["exit_code"] == 1 and set(r["verdicts"]) == {ec.INCONCLUSIVE})

# 4) LEAK: ทุก role ได้ canary -> exit 1
def fake_leak(path, body, key_role):
    if path == "/search":
        c = _canary_for(body["query"])
        if c:
            return norm("/search", 200, None,
                        {"results": [{"point_id": c["point_id"], "content": c["canary_token"]}]})
    return norm("/search", 200, None, {"results": []})
r = ask_eval.run_suite(fake_leak, MANIFEST, [], "admin", SPOOF_OK)
check("LEAK: forbidden role ได้ canary -> exit 1 + มี LEAK",
      r["exit_code"] == 1 and ec.LEAK in r["verdicts"])

# 5) B1/M3 seam: positive valid + negative 200 `{}` (ไม่มี key results) -> exit 1
def fake_neg_empty(path, body, key_role):
    requested = body.get("role")
    if path == "/search":
        c = _canary_for(body["query"])
        if c and requested in c["authorized_roles"]:
            return norm("/search", 200, None,
                        {"results": [{"point_id": c["point_id"], "content": c["canary_token"]}]})
        return norm("/search", 200, None, {})            # <-- ไม่มี 'results' -> MALFORMED
    return fake_correct(path, body, key_role)
r = ask_eval.run_suite(fake_neg_empty, MANIFEST, [], "admin", SPOOF_OK)
check("seam: negative 200 {} -> MALFORMED -> exit 1 (ปิด false-green)",
      r["exit_code"] == 1 and set(r["verdicts"]) == {ec.INCONCLUSIVE})

# 6) B1/M3 seam: negative `[{}]` (result ไม่มี point_id) -> exit 1
def fake_neg_noid(path, body, key_role):
    requested = body.get("role")
    if path == "/search":
        c = _canary_for(body["query"])
        if c and requested in c["authorized_roles"]:
            return norm("/search", 200, None,
                        {"results": [{"point_id": c["point_id"], "content": c["canary_token"]}]})
        return norm("/search", 200, None, {"results": [{}]})   # <-- ไม่มี point_id -> MALFORMED
    return fake_correct(path, body, key_role)
r = ask_eval.run_suite(fake_neg_noid, MANIFEST, [], "admin", SPOOF_OK)
check("seam: negative [{}] ไม่มี point_id -> MALFORMED -> exit 1",
      r["exit_code"] == 1 and set(r["verdicts"]) == {ec.INCONCLUSIVE})

# 7) M1 auth: spoof ได้ 401 (ไม่ใช่ 403) -> FAILED -> exit 1 แม้ permission PASS
r = ask_eval.run_suite(fake_correct, MANIFEST, [], "admin", [("qc", "sales")], require_auth=True)
_r401 = ask_eval.run_suite(lambda p, b, role: (fake_correct(p, b, role) if b.get("role") == role or role == "admin" else _denied(401)),
                           MANIFEST, [], "admin", [("qc", "sales")])
check("auth: spoof 401 (setup fail) -> FAILED -> exit 1",
      _r401["auth_status"] == ec.FAILED and _r401["exit_code"] == 1, _r401["auth_status"])

# 8) M1 auth: ไม่มี spoof pair -> UNVERIFIED -> exit 1 (auth-gated) แต่ retrieval-only -> exit 0
r_unv = ask_eval.run_suite(fake_correct, MANIFEST, [], "admin", [])
check("auth: ไม่มี spoof -> UNVERIFIED -> exit 1 (auth-gated)",
      r_unv["auth_status"] == ec.UNVERIFIED and r_unv["exit_code"] == 1)
r_ro = ask_eval.run_suite(fake_correct, MANIFEST, [], "admin", [], require_auth=False)
check("retrieval-only: permission PASS + UNVERIFIED -> exit 0",
      r_ro["exit_code"] == 0 and r_ro["auth_status"] == ec.UNVERIFIED)

# 9) B2/B3: manifest invalid -> fail ก่อนยิง API (exit 1)
bad_manifest = {"known_roles": ["admin", "qc"],
                "canaries": [{"canary_name": "X", "point_id": "NOT-A-UUID",
                              "canary_token": "T", "authorized_roles": ["admin", "qc"]}]}
r_bad = ask_eval.run_suite(fake_correct, bad_manifest, [], "admin", SPOOF_OK)
check("manifest invalid (non-uuid + zero-denied) -> exit 1 ก่อนยิง API",
      r_bad["exit_code"] == 1 and r_bad["manifest_errs"], r_bad.get("manifest_errs"))

# 10) M2 two-track: /ask MALFORMED **ไม่** ทำ security fail (แยกแทร็ค) — permission ยัง PASS/exit 0
def fake_ask_bad(path, body, key_role):
    if path == "/ask":
        return norm("/ask", 200, None, {})       # /ask malformed
    return fake_correct(path, body, key_role)
r_q = ask_eval.run_suite(fake_ask_bad, MANIFEST, ASK_HAS, "admin", SPOOF_OK)
check("two-track: /ask malformed ไม่กระทบ security exit (ยัง 0) แต่ quality_gate fail",
      r_q["exit_code"] == 0 and r_q["quality"]["ok"] is False, (r_q["exit_code"], r_q["quality"]["ok"]))

# 11) spoof matrix: ≥1 forbidden spoof ต่อ **ทุก** key (ไม่ใช่แค่ 2 ตัวแรก)
sp = ask_eval.build_spoof_pairs(["qc", "sales", "admin"], ["admin", "qc", "sales", "hr", "it"])
check("spoof matrix: ครบทุก key + spoof role != key role",
      len(sp) == 3 and {p[0] for p in sp} == {"qc", "sales", "admin"} and all(a != b for a, b in sp), sp)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
