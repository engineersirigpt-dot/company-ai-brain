"""
Measurement contract สำหรับ ask_eval / permission tests (P5a — Codex review KB_NEXT_STEPS_CODEX_REVIEW B3/M2)

pure logic — ไม่พึ่ง network/Qdrant/model → unit-test ได้โดยไม่ต้องรัน stack
จุดที่ Codex ชี้ว่า harness เดิม "เขียวผิดเหตุผล" แล้วแก้ที่นี่:

- B3: auth/HTTP error ถูก catch เป็น `citations:[]` แล้วนับว่า "ไม่รั่ว"
      → แยกผลเป็น 4 สถานะชัด: OK / NO_RESULT / DENIED(401/403) / ERROR
      → DENIED/NO_RESULT/ERROR **ห้าม** collapse เป็น CLEAN (ไม่รั่ว)
- B3: leak เดิมเช็คแค่ keyword ใน source → เพิ่ม assert retrieved point-id ⊆ allow-set
- M2: citation 92% = retrieval hit ไม่ใช่ citation accuracy
      → parse `[n]` ที่ answer อ้างจริง แยก retrieval-hit / citation-reference-validity ออกจากกัน
"""
from __future__ import annotations
import re

# ---- outcome ของการเรียก API (ต้องแยกให้ชัด ห้าม collapse) ----
OK, NO_RESULT, DENIED, ERROR = "OK", "NO_RESULT", "DENIED", "ERROR"


def classify_outcome(status_code: int | None, exc: BaseException | None, n_citations: int) -> str:
    """
    แยกผลเรียก /ask|/search เป็น 4 สถานะ (B3):
      DENIED    — 401/403 (auth/authorization ปฏิเสธ) — เป็นผลที่ "ถูกต้อง" ของ probe หวงห้าม
      ERROR     — exception อื่น (timeout/connection) หรือ 4xx/5xx ที่ไม่ใช่ auth — inconclusive
      OK        — 200 + มี citations/results
      NO_RESULT — 200 + ว่าง (ถูก filter หมด หรือไม่มีข้อมูล)
    หมายเหตุ: DENIED/ERROR/NO_RESULT ห้ามนับเป็น "ไม่รั่ว" (ดู leak_verdict)
    """
    if status_code in (401, 403):
        return DENIED
    if exc is not None or (status_code is not None and status_code >= 400):
        return ERROR
    return OK if n_citations > 0 else NO_RESULT


# ---- citation integrity (M2) ----
_REF = re.compile(r"\[(\d+)\]")


def parse_citation_refs(answer: str) -> list[int]:
    """ดึงเลข [n] ที่ answer อ้างจริง (unique, เรียงจากน้อยไปมาก)"""
    return sorted({int(m) for m in _REF.findall(answer or "")})


def citation_integrity(answer: str, n_citations: int) -> dict:
    """
    M2: แยก 'retrieval-source-hit' ออกจาก 'citation-reference-validity'
      refs         — เลข [n] ที่ answer อ้าง
      invalid_refs — ref ที่อยู่นอกช่วง [1, n_citations]
      valid        — ทุก ref อยู่ในช่วง (ไม่มี dangling reference)
      cited_any    — answer อ้าง [n] อย่างน้อยหนึ่ง (ไม่ใช่ตอบลอยไม่อ้างที่มา)
    """
    refs = parse_citation_refs(answer)
    invalid = [r for r in refs if r < 1 or r > n_citations]
    return {"refs": refs, "invalid_refs": invalid, "valid": not invalid, "cited_any": bool(refs)}


# ---- permission leak verdict (B3) ----
CLEAN, LEAK = "CLEAN", "LEAK"


def leak_verdict(outcome: str, retrieved_ids, allow_ids, answer: str, banned) -> dict:
    """
    ตัดสิน permission probe แบบไม่ false-pass (B3):
      - ถ้า outcome ไม่ใช่ OK (DENIED/NO_RESULT/ERROR) → คืน outcome ตรงๆ
        **ห้าม** นับเป็น CLEAN (เพราะ error/deny ไม่ใช่หลักฐานว่า filter ทำงาน)
      - ถ้า OK → LEAK เมื่อ (retrieved_ids ⊄ allow_ids) หรือ (มี banned canary ใน answer)
                มิฉะนั้น CLEAN
    """
    if outcome != OK:
        return {"verdict": outcome, "leaked_ids": [], "banned_hit": []}
    allow = set(allow_ids)
    over = [i for i in retrieved_ids if i not in allow]
    banned_hit = [b for b in (banned or []) if b.lower() in (answer or "").lower()]
    verdict = LEAK if (over or banned_hit) else CLEAN
    return {"verdict": verdict, "leaked_ids": over, "banned_hit": banned_hit}
