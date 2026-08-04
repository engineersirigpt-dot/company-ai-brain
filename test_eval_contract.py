"""
Unit test ของ pure decision core (P5a rev2.1) — ไม่ต้องรัน Qdrant/model/API
พิสูจน์ contract ที่ปิด Codex FIX-THEN-GO รอบสอง (strict shape, exhaustive roles,
UUID, auth 403-only, two-track)

    python test_eval_contract.py   (test ห่อ stdout UTF-8 เอง — รันใต้ cp874 ได้)
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import eval_contract as ec

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn):
    try:
        fn(); return False
    except ValueError:
        return True

# ── classify_transport (B2) ────────────────────────────────────────────────────
check("401 -> DENIED", ec.classify_transport(401, Exception()) == ec.DENIED)
check("403 -> DENIED", ec.classify_transport(403, None) == ec.DENIED)
check("timeout (status None) -> ERROR", ec.classify_transport(None, TimeoutError()) == ec.ERROR)
check("500 -> ERROR", ec.classify_transport(500, Exception()) == ec.ERROR)
check("429 -> ERROR", ec.classify_transport(429, Exception()) == ec.ERROR)
check("200 -> SUCCESS", ec.classify_transport(200, None) == ec.SUCCESS)
check("malformed -> MALFORMED", ec.classify_transport(200, None, malformed=True) == ec.MALFORMED)

# ── strict shape (B1/M3): missing key/point_id = MALFORMED ─────────────────────
check("resp ไม่ใช่ dict -> ValueError", raises(lambda: ec.extract_points([1], "results")))
check("**ไม่มี key 'results'** -> ValueError (ปิด false-green 200 {})",
      raises(lambda: ec.extract_points({}, "results")))
check("key ไม่ใช่ list -> ValueError", raises(lambda: ec.extract_points({"results": "x"}, "results")))
check("element ไม่ใช่ object -> ValueError", raises(lambda: ec.extract_points({"results": [1]}, "results")))
check("results:[] (key มี, ว่าง) -> ผ่าน (negative ที่ถูก filter)",
      ec.extract_points({"results": []}, "results", require_point_id=True) == [])
check("result ไม่มี point_id -> ValueError (require_point_id)",
      raises(lambda: ec.extract_points({"results": [{"source": "x"}]}, "results", require_point_id=True)))
check("point_id ซ้ำ -> ValueError",
      raises(lambda: ec.extract_points({"results": [{"point_id": "a"}, {"point_id": "a"}]}, "results", True)))
check("validate_search: {} -> ValueError", raises(lambda: ec.validate_search_response({})))
check("validate_search: [{}] (ไม่มี point_id) -> ValueError",
      raises(lambda: ec.validate_search_response({"results": [{}]})))
check("validate_ask: ไม่มี answer -> ValueError",
      raises(lambda: ec.validate_ask_response({"citations": []})))
check("validate_ask: ครบ -> ผ่าน",
      ec.validate_ask_response({"answer": "x", "citations": []}) == [])

# ── retrieval / source_hit (B2/M4) ─────────────────────────────────────────────
check("0 -> NO_RESULTS", ec.retrieval_outcome(0) == ec.NO_RESULTS)
check("3 -> HAS_RESULTS", ec.retrieval_outcome(3) == ec.HAS_RESULTS)
check("blank source ไม่เป็น hit", ec.source_hit("doc-A.md", ["", ""]) is False)
check("expected โผล่ใน source -> hit", ec.source_hit("doc-A.md", ["p/doc-A.md"]) is True)
check("ไม่มี expected -> None", ec.source_hit("", ["x"]) is None)

# ── canary_found (B4: point_id หลัก) ───────────────────────────────────────────
check("เจอด้วย exact point_id",
      ec.canary_found([{"point_id": "u1"}], "u1", "TOK", [""]) is True)
check("ไม่เจอ -> False", ec.canary_found([{"point_id": "other"}], "u1", "TOK", ["x"]) is False)
check("เจอด้วย token ใน content", ec.canary_found([{"point_id": "x"}], "u1", "TOK", ["มี TOK"]) is True)

# ── is_uuid (B3) ───────────────────────────────────────────────────────────────
check("uuid ถูก format", ec.is_uuid("ed8e586a-602e-5eca-8873-f4fc345867d5") is True)
check("CANARY-RECALL-001 ไม่ใช่ uuid", ec.is_uuid("CANARY-RECALL-001") is False)

# ── citation integrity (M2 เดิม) ──────────────────────────────────────────────
check("parse [1][2][3]", ec.parse_citation_refs("[1] [2][3]") == [1, 2, 3])
check("citation [3]/2 -> invalid", ec.citation_integrity("[3]", 2)["invalid_refs"] == [3])
check("ไม่อ้าง -> cited_any False", ec.citation_integrity("ตอบลอย", 3)["cited_any"] is False)

# ── canary_verdict: exhaustive (B1/M1/B2) ──────────────────────────────────────
S = ec.SUCCESS
P = lambda t, f, b=None: {"transport": t, "found": f, "banned_hit": b or []}
check("ทุก pos เจอ + ทุก neg ไม่เจอ -> PASS",
      ec.canary_verdict([P(S, True), P(S, True)], [P(S, False), P(S, False)]) == ec.PASS)
check("neg ใดเจอ -> LEAK", ec.canary_verdict([P(S, True)], [P(S, False), P(S, True)]) == ec.LEAK)
check("neg banned_hit -> LEAK", ec.canary_verdict([P(S, True)], [P(S, False, ["TOK"])]) == ec.LEAK)
check("pos ใดไม่เจอ -> INCONCLUSIVE (M1)",
      ec.canary_verdict([P(S, True), P(S, False)], [P(S, False)]) == ec.INCONCLUSIVE)
check("pos DENIED -> INCONCLUSIVE (B1)", ec.canary_verdict([P(ec.DENIED, False)], [P(S, False)]) == ec.INCONCLUSIVE)
check("neg MALFORMED -> INCONCLUSIVE (B1: {} negative ไม่ PASS)",
      ec.canary_verdict([P(S, True)], [P(ec.MALFORMED, False)]) == ec.INCONCLUSIVE)
check("positives ว่าง -> INCONCLUSIVE", ec.canary_verdict([], [P(S, False)]) == ec.INCONCLUSIVE)
check("negatives ว่าง -> INCONCLUSIVE", ec.canary_verdict([P(S, True)], []) == ec.INCONCLUSIVE)
check("pair_verdict wrapper", ec.pair_verdict(P(S, True), P(S, False)) == ec.PASS)

# ── permission_ok ──────────────────────────────────────────────────────────────
check("ว่าง -> ไม่ ok", ec.permission_ok([]) is False)
check("ทุก PASS -> ok", ec.permission_ok([ec.PASS, ec.PASS]) is True)
check("มี INCONCLUSIVE -> ไม่ ok", ec.permission_ok([ec.PASS, ec.INCONCLUSIVE]) is False)

# ── auth_gate_status (M1 auth: 403-only) ───────────────────────────────────────
check("ทุก spoof 403 -> VERIFIED", ec.auth_gate_status([{"status": 403}, {"status": 403}]) == ec.VERIFIED)
check("spoof 401 -> FAILED (401 ไม่ใช่ pass)", ec.auth_gate_status([{"status": 401}]) == ec.FAILED)
check("spoof 200 -> FAILED", ec.auth_gate_status([{"status": 200}]) == ec.FAILED)
check("ไม่มี spoof -> UNVERIFIED", ec.auth_gate_status([]) == ec.UNVERIFIED)

# ── security_exit_code: permission + auth ──────────────────────────────────────
check("PASS + VERIFIED -> 0", ec.security_exit_code([ec.PASS], ec.VERIFIED) == 0)
check("PASS + UNVERIFIED (auth-gated) -> 1", ec.security_exit_code([ec.PASS], ec.UNVERIFIED) == 1)
check("PASS + FAILED -> 1", ec.security_exit_code([ec.PASS], ec.FAILED) == 1)
check("PASS + UNVERIFIED + retrieval-only -> 0",
      ec.security_exit_code([ec.PASS], ec.UNVERIFIED, require_auth=False) == 0)
check("LEAK + VERIFIED -> 1", ec.security_exit_code([ec.PASS, ec.LEAK], ec.VERIFIED) == 1)
check("all-INCONCLUSIVE -> 1", ec.security_exit_code([ec.INCONCLUSIVE], ec.VERIFIED) == 1)
check("ไม่มี pair -> 1", ec.security_exit_code([], ec.VERIFIED) == 1)

# ── validate_manifest (B2/B3) ──────────────────────────────────────────────────
GOOD = {"known_roles": ["admin", "qc", "sales"],
        "canaries": [{"canary_name": "C1", "point_id": "ed8e586a-602e-5eca-8873-f4fc345867d5",
                      "canary_token": "T1", "authorized_roles": ["qc", "admin"]}]}
check("manifest ดี -> ไม่มี error", ec.validate_manifest(GOOD) == [])
def _bad(mut):
    import copy
    m = copy.deepcopy(GOOD); mut(m); return ec.validate_manifest(m)
check("point_id ไม่ใช่ uuid -> error",
      any("UUID" in e for e in _bad(lambda m: m["canaries"][0].__setitem__("point_id", "CANARY-1"))))
check("zero denied (ทุก role authorized) -> error",
      any("denied" in e for e in _bad(lambda m: m["canaries"][0].__setitem__("authorized_roles", ["admin", "qc", "sales"]))))
check("authorized นอก known -> error",
      any("known_roles" in e for e in _bad(lambda m: m["canaries"][0].__setitem__("authorized_roles", ["ghost"]))))
check("known_roles ว่าง -> error", ec.validate_manifest({"known_roles": [], "canaries": []}) != [])

# ── ask_quality track (M2 — แยกจาก security) ───────────────────────────────────
ar = lambda cat, t, ret=None, hit=None, cv=True, ca=True, no=False: {
    "category": cat, "transport": t, "retrieval": ret, "hit": hit,
    "citation_valid": cv, "cited_any": ca, "said_no_answer": no}
rep = ec.ask_quality_report([ar("has_answer", S, ec.HAS_RESULTS, True), ar("no_answer", S, ec.NO_RESULTS, no=True)])
check("quality_report นับ has_answer hit", rep["has_answer_hit"] == 1 and rep["has_answer_n"] == 1)
check("quality_report นับ no_answer honest", rep["no_answer_honest"] == 1 and rep["no_answer_n"] == 1)
qg_ok = ec.quality_gate([ar("has_answer", S, ec.HAS_RESULTS, True), ar("no_answer", S, ec.NO_RESULTS, no=True)])
check("quality_gate ผ่านเมื่อ hit เต็ม+honest เต็ม", qg_ok["ok"] is True, qg_ok)
qg_bad = ec.quality_gate([ar("has_answer", S, ec.NO_RESULTS, False), ar("no_answer", S, ec.SUCCESS, no=False)])
check("quality_gate fail เมื่อ hit ต่ำ/no_answer แต่งคำตอบ", qg_bad["ok"] is False, qg_bad)
check("quality_gate fail เมื่อ transport พัง",
      ec.quality_gate([ar("has_answer", ec.MALFORMED)])["ok"] is False)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
