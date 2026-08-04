"""
Measurement contract สำหรับ permission-leakage / ask-quality harness
P5a rev2 — ปิด Codex FIX-THEN-GO (B1-B4, M1-M4)

pure logic ล้วน — ไม่พึ่ง network/Qdrant/model → unit-test + harness-test ได้โดยไม่ต้องรัน stack
ทุก decision ที่ตัด exit code ของ suite อยู่ในไฟล์นี้ เพื่อให้ test assert ได้ตรง

สิ่งที่ปิดจากรีวิว:
- B1: suite ต้อง **ไม่** exit 0 เมื่อทุก request โดน DENIED/NO_RESULT — เพราะ deny/empty ไม่ใช่
      หลักฐานว่า filter ทำงาน. เขียวได้เฉพาะเมื่อมี positive control ที่ 'เจอ canary' คู่กับ
      negative control ที่ 'ไม่เจอ canary' (ดู pair_verdict / permission_suite_ok)
- B2: แยก transport_outcome (SUCCESS/DENIED/ERROR/MALFORMED) ออกจาก retrieval_outcome
      (HAS_RESULTS/NO_RESULTS) — no-answer case ไม่ถูกตัดหายจาก denominator อีก
- B3: expected policy มาจาก manifest อิสระ (ไม่ derive จาก rbac_config) — ดู permission_manifest.json
- B4: leak ตัดด้วย **point_id** จริง (collection เป็น diagnostic เฉย ๆ)
- M1: positive control เป็น requirement — pair ที่ positive ไม่เจอ canary = INCONCLUSIVE (fail)
- M3: malformed/partial JSON / ผิด shape → MALFORMED (structured, non-zero)
- M4: blank source ไม่นับเป็น retrieval hit
"""
from __future__ import annotations
import re

# ── transport outcome (ชั้น HTTP/auth) ─────────────────────────────────────────
SUCCESS, DENIED, ERROR, MALFORMED = "SUCCESS", "DENIED", "ERROR", "MALFORMED"
# ── retrieval outcome (ชั้นผลค้น — มีความหมายเฉพาะเมื่อ transport == SUCCESS) ────
HAS_RESULTS, NO_RESULTS = "HAS_RESULTS", "NO_RESULTS"
# ── pair / probe verdict ───────────────────────────────────────────────────────
PASS, LEAK, INCONCLUSIVE = "PASS", "LEAK", "INCONCLUSIVE"


def classify_transport(status_code: int | None, exc: BaseException | None,
                       malformed: bool = False) -> str:
    """
    แยกผลชั้น transport (B2) — ไม่ยุ่งกับจำนวนผลลัพธ์:
      MALFORMED — body ไม่ใช่ JSON/ผิด shape (M3)
      DENIED    — 401/403 (auth/authorization ปฏิเสธ) — **ไม่ใช่** หลักฐานว่า filter ทำงาน
      ERROR     — exception (timeout/conn) / status None / 4xx-5xx อื่น — inconclusive
      SUCCESS   — 2xx และ body well-formed
    """
    if malformed:
        return MALFORMED
    if status_code in (401, 403):
        return DENIED
    if exc is not None or status_code is None:
        return ERROR
    if 200 <= status_code < 300:
        return SUCCESS
    return ERROR


def extract_points(resp, key: str) -> list[dict]:
    """
    validate shape ของ response ก่อนใช้ (M3) — raise ValueError ถ้า malformed
      key = 'results' (/search) หรือ 'citations' (/ask)
    ไม่คืน/ไม่ log raw body (อาจมีข้อมูลลับ) — ให้ caller จับ ValueError → MALFORMED
    """
    if not isinstance(resp, dict):
        raise ValueError("response ไม่ใช่ JSON object")
    pts = resp.get(key, [])
    if not isinstance(pts, list):
        raise ValueError(f"'{key}' ไม่ใช่ list")
    for p in pts:
        if not isinstance(p, dict):
            raise ValueError(f"element ใน '{key}' ไม่ใช่ object")
    return pts


def retrieval_outcome(n_points: int) -> str:
    return HAS_RESULTS if n_points > 0 else NO_RESULTS


def point_ids(points: list[dict]) -> list[str]:
    return [str(p.get("point_id", "")) for p in points]


def source_hit(expected_source: str, sources: list[str]):
    """
    M4: blank source ไม่นับเป็น hit — 's in expected' ตอน s=='' เคยเป็น true ปลอม
    คืน None ถ้าไม่มี expected (ไม่ประเมิน), True/False ถ้ามี
    """
    if not expected_source:
        return None
    return any(bool(s) and (expected_source in s or s in expected_source) for s in sources)


def canary_found(points: list[dict], canary_point_id: str,
                 canary_token: str, texts: list[str]) -> bool:
    """เจอ canary ไหม — โดย exact point_id (หลัก) หรือ token โผล่ใน content (สำรอง)"""
    if canary_point_id and canary_point_id in point_ids(points):
        return True
    if canary_token:
        tok = canary_token.lower()
        return any(tok in (t or "").lower() for t in texts)
    return False


# ── citation integrity (M2 เดิม — retrieval-hit แยกจาก [n]-reference validity) ──
_REF = re.compile(r"\[(\d+)\]")


def parse_citation_refs(answer: str) -> list[int]:
    return sorted({int(m) for m in _REF.findall(answer or "")})


def citation_integrity(answer: str, n_citations: int) -> dict:
    refs = parse_citation_refs(answer)
    invalid = [r for r in refs if r < 1 or r > n_citations]
    return {"refs": refs, "invalid_refs": invalid, "valid": not invalid, "cited_any": bool(refs)}


# ── permission pair verdict (หัวใจของ B1/M1) ───────────────────────────────────
def pair_verdict(pos: dict, neg: dict) -> str:
    """
    ตัดสิน canary หนึ่งตัวจากผลสองด้าน (ต้องคู่กันเสมอ — M1):
      pos = role ที่ **มีสิทธิ์** ค้น canary  → คาดว่า SUCCESS + found
      neg = role ที่ **ไม่มีสิทธิ์** ค้น canary → คาดว่า SUCCESS + not found + ไม่มี banned

    return:
      LEAK         — neg เจอ point/token ต้องห้าม (รั่วจริง)
      INCONCLUSIVE — สรุปไม่ได้: transport ไม่ SUCCESS ด้านใดด้านหนึ่ง หรือ pos หา canary ไม่เจอ
                     (deny/error/empty ไม่พิสูจน์ว่า filter ทำงาน — B1)
      PASS         — pos เจอ, neg ไม่เจอ, ทั้งคู่ transport SUCCESS

    dict keys: pos/neg = {"transport": str, "found": bool, "banned_hit": list}
    """
    if pos.get("transport") != SUCCESS or neg.get("transport") != SUCCESS:
        return INCONCLUSIVE
    if not pos.get("found"):
        return INCONCLUSIVE
    if neg.get("found") or neg.get("banned_hit"):
        return LEAK
    return PASS


def permission_suite_ok(pair_verdicts: list[str]) -> bool:
    """เขียวได้เฉพาะเมื่อมี pair อย่างน้อยหนึ่ง และ **ทุก** pair == PASS (B1)"""
    return bool(pair_verdicts) and all(v == PASS for v in pair_verdicts)


# ── ask-quality gate (แยกจาก permission — B2) ──────────────────────────────────
def ask_quality_failures(records: list[dict]) -> list[str]:
    """
    hard-fail ของฝั่ง /ask (ไม่รวม permission):
      - transport ERROR/MALFORMED/DENIED = inconclusive/พัง (key ไม่ scope → DENIED ก็ fail)
      - has_answer แต่ retrieval ว่าง (200+0) = quality failure (B1)
    NO_RESULTS ของ no_answer case = ผ่านตาม contract (ไม่ fail)
    record keys: category, transport, retrieval
    """
    fails = []
    for i, r in enumerate(records):
        t = r.get("transport")
        if t in (ERROR, MALFORMED, DENIED):
            fails.append(f"ask[{i}] {r.get('category')}: transport={t}")
        elif r.get("category") == "has_answer" and r.get("retrieval") == NO_RESULTS:
            fails.append(f"ask[{i}] has_answer: retrieval ว่าง (200+0)")
    return fails


def suite_exit_code(pair_verdicts: list[str], ask_records: list[dict],
                    auth_preflight_ok: bool = True) -> int:
    """0 เฉพาะเมื่อ: permission suite ผ่าน + ask ไม่มี hard-fail + auth preflight ผ่าน"""
    ok = (permission_suite_ok(pair_verdicts)
          and not ask_quality_failures(ask_records)
          and auth_preflight_ok)
    return 0 if ok else 1
