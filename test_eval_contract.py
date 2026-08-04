"""
Unit test ของ pure decision core (P5a rev2) — ไม่ต้องรัน Qdrant/model/API
พิสูจน์ contract ที่ปิด Codex B1-B4/M1-M4

    PYTHONUTF8=1 python test_eval_contract.py   (ต้อง UTF-8 — ดู N1)
"""
import io
import sys

# N1: บังคับ stdout UTF-8 ในตัว test เอง ให้รันได้ทุกเครื่อง (ไม่ต้องพึ่ง env ภายนอก)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import eval_contract as ec

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))

# ── classify_transport: แยกชั้น transport (B2) ─────────────────────────────────
check("401 -> DENIED", ec.classify_transport(401, Exception()) == ec.DENIED)
check("403 -> DENIED", ec.classify_transport(403, None) == ec.DENIED)
check("timeout (status None) -> ERROR", ec.classify_transport(None, TimeoutError()) == ec.ERROR)
check("500 -> ERROR", ec.classify_transport(500, Exception()) == ec.ERROR)
check("429 -> ERROR", ec.classify_transport(429, Exception()) == ec.ERROR)
check("status None ไม่มี exc -> ERROR", ec.classify_transport(None, None) == ec.ERROR)
check("200 -> SUCCESS", ec.classify_transport(200, None) == ec.SUCCESS)
check("malformed flag -> MALFORMED", ec.classify_transport(200, None, malformed=True) == ec.MALFORMED)

# ── extract_points: validate shape (M3) ───────────────────────────────────────
check("extract list ปกติ", ec.extract_points({"results": [{"point_id": "a"}]}, "results") == [{"point_id": "a"}])
check("extract ไม่มี key -> []", ec.extract_points({"answer": "x"}, "citations") == [])
def raises(fn):
    try:
        fn(); return False
    except ValueError:
        return True
check("resp ไม่ใช่ dict -> ValueError", raises(lambda: ec.extract_points([1, 2], "results")))
check("key ไม่ใช่ list -> ValueError", raises(lambda: ec.extract_points({"results": "x"}, "results")))
check("element ไม่ใช่ object -> ValueError", raises(lambda: ec.extract_points({"results": [1]}, "results")))

# ── retrieval_outcome (B2) ─────────────────────────────────────────────────────
check("0 points -> NO_RESULTS", ec.retrieval_outcome(0) == ec.NO_RESULTS)
check("3 points -> HAS_RESULTS", ec.retrieval_outcome(3) == ec.HAS_RESULTS)

# ── source_hit: blank source ไม่เป็น hit (M4) ──────────────────────────────────
check("blank source ไม่เป็น hit", ec.source_hit("doc-A.md", ["", ""]) is False)
check("expected โผล่ใน source -> hit", ec.source_hit("doc-A.md", ["path/doc-A.md"]) is True)
check("ไม่มี expected -> None (ไม่ประเมิน)", ec.source_hit("", ["x"]) is None)

# ── canary_found: point_id หลัก / token สำรอง (B4) ─────────────────────────────
pts = [{"point_id": "CANARY-1", "content": "เนื้อหา"}]
check("เจอด้วย exact point_id", ec.canary_found(pts, "CANARY-1", "TOK", ["เนื้อหา"]) is True)
check("ไม่เจอ point_id/token -> False", ec.canary_found([{"point_id": "OTHER"}], "CANARY-1", "TOK", ["x"]) is False)
check("เจอด้วย token ใน content", ec.canary_found([{"point_id": "X"}], "CANARY-1", "TOK", ["มี TOK ปน"]) is True)

# ── citation integrity (M2 เดิม) ──────────────────────────────────────────────
check("parse [1] [2][3] -> [1,2,3]", ec.parse_citation_refs("ดู [1] และ [2][3]") == [1, 2, 3])
ci = ec.citation_integrity("อ้าง [3]", 2)
check("citation [3] เกิน 2 -> invalid", ci["invalid_refs"] == [3] and not ci["valid"], ci)
check("ไม่อ้าง [n] -> cited_any=False", ec.citation_integrity("ตอบลอย", 3)["cited_any"] is False)

# ── pair_verdict: หัวใจ B1/M1 ──────────────────────────────────────────────────
S = ec.SUCCESS
mk = lambda t, f, b=None: {"transport": t, "found": f, "banned_hit": b or []}
check("pos เจอ + neg ไม่เจอ -> PASS", ec.pair_verdict(mk(S, True), mk(S, False)) == ec.PASS)
check("neg เจอ canary -> LEAK", ec.pair_verdict(mk(S, True), mk(S, True)) == ec.LEAK)
check("neg banned_hit -> LEAK", ec.pair_verdict(mk(S, True), mk(S, False, ["TOK"])) == ec.LEAK)
check("pos ไม่เจอ (ระบบหา canary ไม่เจอ) -> INCONCLUSIVE (M1)",
      ec.pair_verdict(mk(S, False), mk(S, False)) == ec.INCONCLUSIVE)
check("pos DENIED -> INCONCLUSIVE (B1: deny ไม่ใช่ pass)",
      ec.pair_verdict(mk(ec.DENIED, False), mk(S, False)) == ec.INCONCLUSIVE)
check("neg ERROR -> INCONCLUSIVE", ec.pair_verdict(mk(S, True), mk(ec.ERROR, False)) == ec.INCONCLUSIVE)

# ── permission_suite_ok ────────────────────────────────────────────────────────
check("suite ว่าง -> ไม่ ok (เขียวจากศูนย์ pair ไม่ได้)", ec.permission_suite_ok([]) is False)
check("ทุก PASS -> ok", ec.permission_suite_ok([ec.PASS, ec.PASS]) is True)
check("มี INCONCLUSIVE -> ไม่ ok", ec.permission_suite_ok([ec.PASS, ec.INCONCLUSIVE]) is False)
check("มี LEAK -> ไม่ ok", ec.permission_suite_ok([ec.PASS, ec.LEAK]) is False)

# ── ask_quality_failures (B2) ──────────────────────────────────────────────────
ar = lambda cat, t, ret=None: {"category": cat, "transport": t, "retrieval": ret}
check("ask DENIED -> fail", len(ec.ask_quality_failures([ar("has_answer", ec.DENIED)])) == 1)
check("ask MALFORMED -> fail", len(ec.ask_quality_failures([ar("no_answer", ec.MALFORMED)])) == 1)
check("has_answer + NO_RESULTS -> fail (B1)",
      len(ec.ask_quality_failures([ar("has_answer", ec.SUCCESS, ec.NO_RESULTS)])) == 1)
check("no_answer + NO_RESULTS -> ผ่าน (ตาม contract)",
      ec.ask_quality_failures([ar("no_answer", ec.SUCCESS, ec.NO_RESULTS)]) == [])
check("has_answer + HAS_RESULTS -> ผ่าน",
      ec.ask_quality_failures([ar("has_answer", ec.SUCCESS, ec.HAS_RESULTS)]) == [])

# ── suite_exit_code: การประกอบร่างสุดท้าย ─────────────────────────────────────
ok_ask = [ar("has_answer", ec.SUCCESS, ec.HAS_RESULTS)]
check("ทุก PASS + ask ดี + preflight ok -> exit 0", ec.suite_exit_code([ec.PASS], ok_ask, True) == 0)
check("all-INCONCLUSIVE (deny ทั้งชุด) -> exit 1 (B1)",
      ec.suite_exit_code([ec.INCONCLUSIVE, ec.INCONCLUSIVE], ok_ask, True) == 1)
check("มี LEAK -> exit 1", ec.suite_exit_code([ec.PASS, ec.LEAK], ok_ask, True) == 1)
check("permission ผ่าน แต่ ask fail -> exit 1",
      ec.suite_exit_code([ec.PASS], [ar("has_answer", ec.SUCCESS, ec.NO_RESULTS)], True) == 1)
check("permission+ask ผ่าน แต่ auth preflight fail -> exit 1",
      ec.suite_exit_code([ec.PASS], ok_ask, False) == 1)
check("ไม่มี pair เลย (แม้ ask ดี) -> exit 1", ec.suite_exit_code([], ok_ask, True) == 1)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
