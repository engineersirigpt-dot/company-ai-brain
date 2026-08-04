"""
Unit test ของ measurement contract (P5a) — pure logic, ไม่ต้องรัน Qdrant/model/API
พิสูจน์ว่า harness จะ "ไม่เขียวผิดเหตุผล" ตาม Codex B3/M2

    python test_eval_contract.py
"""
import sys
import eval_contract as ec

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))

# ── classify_outcome: แยก 4 สถานะ (B3) ──────────────────────────────────────
check("401 → DENIED", ec.classify_outcome(401, Exception(), 0) == ec.DENIED)
check("403 → DENIED", ec.classify_outcome(403, None, 0) == ec.DENIED)
check("timeout (no status) → ERROR", ec.classify_outcome(None, TimeoutError(), 0) == ec.ERROR)
check("500 → ERROR", ec.classify_outcome(500, Exception(), 0) == ec.ERROR)
check("200 + citations → OK", ec.classify_outcome(200, None, 3) == ec.OK)
check("200 + 0 citations → NO_RESULT", ec.classify_outcome(200, None, 0) == ec.NO_RESULT)

# ── citation integrity (M2): retrieval-hit ≠ citation-validity ───────────────
check("parse [1] [2][3] → [1,2,3]", ec.parse_citation_refs("ดู [1] และ [2][3] ประกอบ") == [1, 2, 3])
check("parse ว่าง → []", ec.parse_citation_refs("ตอบลอยไม่อ้างที่มา") == [])
ci = ec.citation_integrity("อ้างอิง [3]", 2)
check("citation [3] เกิน 2 citations → invalid (dangling ref)", ci["invalid_refs"] == [3] and not ci["valid"], ci)
ci2 = ec.citation_integrity("จาก [1] และ [2]", 2)
check("citation [1][2] / 2 → valid + cited_any", ci2["valid"] and ci2["cited_any"], ci2)
ci3 = ec.citation_integrity("ตอบโดยไม่อ้าง", 3)
check("answer ไม่อ้าง [n] → cited_any=False", not ci3["cited_any"], ci3)

# ── leak_verdict (B3): DENIED/ERROR/NO_RESULT ห้ามนับเป็น CLEAN ───────────────
check("DENIED (401) ไม่ใช่ CLEAN", ec.leak_verdict(ec.DENIED, [], [1, 2], "", ["x"])["verdict"] == ec.DENIED)
check("ERROR (timeout) ไม่ใช่ CLEAN", ec.leak_verdict(ec.ERROR, [], [1, 2], "", ["x"])["verdict"] == ec.ERROR)
check("NO_RESULT ไม่ใช่ CLEAN", ec.leak_verdict(ec.NO_RESULT, [], [1, 2], "", [])["verdict"] == ec.NO_RESULT)
v_leak = ec.leak_verdict(ec.OK, [1, 9], [1, 2], "", [])
check("OK + retrieved-id ⊄ allow-set → LEAK", v_leak["verdict"] == ec.LEAK and v_leak["leaked_ids"] == [9], v_leak)
check("OK + subset + no banned → CLEAN", ec.leak_verdict(ec.OK, [1, 2], [1, 2, 3], "answer", [])["verdict"] == ec.CLEAN)
v_canary = ec.leak_verdict(ec.OK, [1], [1, 2], "เนื้อหาลับ GP-580 หลุดมา", ["GP-580"])
check("OK + banned canary ใน answer → LEAK", v_canary["verdict"] == ec.LEAK and v_canary["banned_hit"] == ["GP-580"], v_canary)

# ── regression กับบั๊กเดิม: auth error เคยถูกนับเป็น 'ไม่รั่ว' ─────────────────
# เดิม: call error → resp={"citations":[]} → leaked=[] → นับ no-leak (ผิด)
# ใหม่: 401 → DENIED (ไม่ใช่ CLEAN) → suite ต้องไม่รายงานว่า "ไม่รั่ว"
old_would_pass = ec.leak_verdict(ec.DENIED, [], [1, 2], "", ["GP-580"])["verdict"] != ec.CLEAN
check("regression: auth-error probe ไม่ถูกนับเป็น CLEAN อีกต่อไป", old_would_pass)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
