"""
Measurement contract — permission-leakage (security) / ask-quality tracks
P5a rev2.1 — ปิด Codex FIX-THEN-GO รอบสอง (strict response shape, exhaustive roles,
Qdrant-compatible IDs, auth 403-only, สองแทร็คแยกกัน)

pure logic ล้วน — ทุก decision ที่ตัด exit code อยู่ในไฟล์นี้ → unit/harness-test ได้โดยไม่ต้องรัน stack

รอบนี้แก้:
- B1/M3: missing 'results' key หรือ result ที่ไม่มี point_id (non-empty) = MALFORMED → INCONCLUSIVE/fail
          (เดิม resp.get(key, []) ทำให้ 200 `{}` เป็น list ว่างที่ 'ถูกต้อง' แล้ว negative PASS ได้)
- B2: manifest ต้อง exhaustive — ทุก known_role ถูกทดสอบทั้งฝั่ง allow (positive) และ deny (negative)
- B3: point_id เป็น UUID (Qdrant รับ uint64/UUID เท่านั้น); canary_name เก็บชื่ออ่านง่ายแยก
- M1(auth): spoof preflight ต้องได้ **exact 403**; 401 = setup failure ไม่ใช่ pass;
            no spoof pair = UNVERIFIED (auth-gated run ต้อง VERIFIED จึง exit 0)
- M2(quality): /ask quality เป็นแทร็คแยก — ไม่ปนใน security exit code (แต่ยังรายงาน metric ครบ)
"""
from __future__ import annotations
import re

# ── transport outcome (ชั้น HTTP/auth) ─────────────────────────────────────────
SUCCESS, DENIED, ERROR, MALFORMED = "SUCCESS", "DENIED", "ERROR", "MALFORMED"
# ── retrieval outcome ──────────────────────────────────────────────────────────
HAS_RESULTS, NO_RESULTS = "HAS_RESULTS", "NO_RESULTS"
# ── canary verdict ─────────────────────────────────────────────────────────────
PASS, LEAK, INCONCLUSIVE = "PASS", "LEAK", "INCONCLUSIVE"
# ── auth gate status ───────────────────────────────────────────────────────────
VERIFIED, UNVERIFIED, FAILED = "VERIFIED", "UNVERIFIED", "FAILED"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_uuid(s: str) -> bool:
    return bool(_UUID.match(str(s)))


# ── transport classification (B2) ──────────────────────────────────────────────
def classify_transport(status_code: int | None, exc: BaseException | None,
                       malformed: bool = False) -> str:
    if malformed:
        return MALFORMED
    if status_code in (401, 403):
        return DENIED
    if exc is not None or status_code is None:
        return ERROR
    if 200 <= status_code < 300:
        return SUCCESS
    return ERROR


# ── strict response validation (B1/M3) ─────────────────────────────────────────
def extract_points(resp, key: str, require_point_id: bool = False) -> list[dict]:
    """
    validate shape — raise ValueError (→ caller ตั้ง MALFORMED) เมื่อ:
      - resp ไม่ใช่ object
      - **ไม่มี key** (เดิมยอมเป็น [] = ช่องโหว่ false-green)
      - key ไม่ใช่ list / element ไม่ใช่ object
      - require_point_id: มี result ที่ point_id ว่าง หรือ point_id ซ้ำ
    ไม่คืน/ไม่ log raw body (อาจมีข้อมูลลับ)
    """
    if not isinstance(resp, dict):
        raise ValueError("response ไม่ใช่ JSON object")
    if key not in resp:
        raise ValueError(f"ไม่มี key '{key}' ใน response")
    pts = resp[key]
    if not isinstance(pts, list):
        raise ValueError(f"'{key}' ไม่ใช่ list")
    seen = set()
    for p in pts:
        if not isinstance(p, dict):
            raise ValueError(f"element ใน '{key}' ไม่ใช่ object")
        if require_point_id:
            pid = str(p.get("point_id", "")).strip()
            if not pid:
                raise ValueError("result ไม่มี point_id (security-critical)")
            if pid in seen:
                raise ValueError(f"point_id ซ้ำ: {pid}")
            seen.add(pid)
    return pts


def validate_search_response(resp) -> list[dict]:
    """/search สำเร็จ ต้องมี 'results' และทุก result มี point_id non-empty ไม่ซ้ำ (B1/B4)"""
    return extract_points(resp, "results", require_point_id=True)


def validate_ask_response(resp) -> list[dict]:
    """/ask สำเร็จ ต้องมี 'answer' (str) + 'citations' (list) — validate แยกจาก /search"""
    cits = extract_points(resp, "citations", require_point_id=False)
    if not isinstance(resp.get("answer", None), str):
        raise ValueError("/ask ไม่มี 'answer' เป็น string")
    return cits


def retrieval_outcome(n_points: int) -> str:
    return HAS_RESULTS if n_points > 0 else NO_RESULTS


def point_ids(points: list[dict]) -> list[str]:
    return [str(p.get("point_id", "")) for p in points]


def source_hit(expected_source: str, sources: list[str]):
    """M4: blank source ไม่นับเป็น hit"""
    if not expected_source:
        return None
    return any(bool(s) and (expected_source in s or s in expected_source) for s in sources)


def canary_found(points: list[dict], canary_point_id: str,
                 canary_token: str, texts: list[str]) -> bool:
    if canary_point_id and canary_point_id in point_ids(points):
        return True
    if canary_token:
        tok = canary_token.lower()
        return any(tok in (t or "").lower() for t in texts)
    return False


# ── citation integrity (M2 เดิม) ──────────────────────────────────────────────
_REF = re.compile(r"\[(\d+)\]")


def parse_citation_refs(answer: str) -> list[int]:
    return sorted({int(m) for m in _REF.findall(answer or "")})


def citation_integrity(answer: str, n_citations: int) -> dict:
    refs = parse_citation_refs(answer)
    invalid = [r for r in refs if r < 1 or r > n_citations]
    return {"refs": refs, "invalid_refs": invalid, "valid": not invalid, "cited_any": bool(refs)}


# ── canary verdict (B1/M1 + exhaustive B2) ─────────────────────────────────────
def canary_verdict(positives: list[dict], negatives: list[dict]) -> str:
    """
    ตัดสิน canary หนึ่งตัวจาก **ทุก** authorized role (positives) และ **ทุก** denied role (negatives):
      probe dict = {"transport": str, "found": bool, "banned_hit": list}
      LEAK         — มี negative ใด ๆ เจอ point/token ต้องห้าม
      INCONCLUSIVE — positives หรือ negatives ว่าง / มี transport ไม่ SUCCESS / มี authorized role
                     ใดหา canary ไม่เจอ (ระบบพิสูจน์ไม่ได้ว่า filter ทำงาน — B1/M1)
      PASS         — ทุก authorized เจอ, ทุก denied ไม่เจอ, transport SUCCESS หมด
    """
    if not positives or not negatives:
        return INCONCLUSIVE
    if any(p.get("transport") != SUCCESS for p in positives + negatives):
        return INCONCLUSIVE
    if not all(p.get("found") for p in positives):
        return INCONCLUSIVE
    if any(n.get("found") or n.get("banned_hit") for n in negatives):
        return LEAK
    return PASS


def pair_verdict(pos: dict, neg: dict) -> str:
    """คู่เดียว (backward-compat / smoke) = canary_verdict([pos], [neg])"""
    return canary_verdict([pos], [neg])


def permission_ok(verdicts: list[str]) -> bool:
    """เขียวได้เฉพาะเมื่อมี verdict อย่างน้อยหนึ่ง และทุกตัว == PASS"""
    return bool(verdicts) and all(v == PASS for v in verdicts)


# ── auth gate (M1 auth: 403-only) ──────────────────────────────────────────────
def auth_gate_status(spoof_results: list[dict]) -> str:
    """
    spoof_results = [{"status": int|None}, ...] จากการใช้ key ของ role หนึ่งขอ role นอก scope
      VERIFIED   — มี spoof อย่างน้อยหนึ่ง และ **ทุกตัวได้ exact 403** (role-scope บังคับจริง)
      FAILED     — มี spoof ที่ไม่ได้ 403 (รวม 401/200/อื่น — 401 = setup fail ไม่ใช่ pass)
      UNVERIFIED — ไม่มี spoof pair (พิสูจน์ role-scope ไม่ได้)
    """
    if not spoof_results:
        return UNVERIFIED
    return VERIFIED if all(r.get("status") == 403 for r in spoof_results) else FAILED


def security_exit_code(verdicts: list[str], auth_status: str,
                       require_auth: bool = True) -> int:
    """
    security gate (permission leak + role-scope) — **ไม่รวม /ask quality**:
      0 เฉพาะเมื่อ permission ทุก pair PASS และ (ถ้า require_auth) auth == VERIFIED
    require_auth=False = retrieval-only mode (auth ประกาศ UNVERIFIED ชัด ๆ ไม่ block)
    """
    if not permission_ok(verdicts):
        return 1
    if require_auth and auth_status != VERIFIED:
        return 1
    return 0


# ── ask-quality track (แยกจาก security — M2) ───────────────────────────────────
def ask_quality_report(records: list[dict]) -> dict:
    """สรุป metric ตามชื่อจริง (บังคับให้รายงานครบ ไม่หายจาก summary)"""
    has = [r for r in records if r.get("category") == "has_answer" and r.get("transport") == SUCCESS]
    noa = [r for r in records if r.get("category") == "no_answer" and r.get("transport") == SUCCESS]
    return {
        "n": len(records),
        "transport_bad": [i for i, r in enumerate(records)
                          if r.get("transport") in (ERROR, MALFORMED, DENIED)],
        "has_answer_n": len(has),
        "has_answer_hit": sum(1 for r in has if r.get("hit")),
        "has_answer_empty": sum(1 for r in has if r.get("retrieval") == NO_RESULTS),
        "dangling_citation": sum(1 for r in has if not r.get("citation_valid")),
        "cited_any": sum(1 for r in has if r.get("cited_any")),
        "no_answer_n": len(noa),
        "no_answer_honest": sum(1 for r in noa if r.get("said_no_answer")),
    }


def quality_gate(records: list[dict], min_hit_rate: float = 0.7,
                 min_honesty_rate: float = 1.0) -> dict:
    """
    quality gate แยกสำหรับ P5b (ไม่ผูกกับ security exit code):
      fail ถ้า transport พัง, has_answer hit-rate < min, no_answer honesty < min,
      หรือมี dangling citation
    """
    rep = ask_quality_report(records)
    reasons = []
    if rep["transport_bad"]:
        reasons.append(f"transport พัง {len(rep['transport_bad'])} เคส")
    if rep["dangling_citation"]:
        reasons.append(f"dangling citation {rep['dangling_citation']} เคส")
    if rep["has_answer_n"]:
        hit_rate = rep["has_answer_hit"] / rep["has_answer_n"]
        if hit_rate < min_hit_rate:
            reasons.append(f"has_answer hit-rate {hit_rate:.0%} < {min_hit_rate:.0%}")
    if rep["no_answer_n"]:
        honesty = rep["no_answer_honest"] / rep["no_answer_n"]
        if honesty < min_honesty_rate:
            reasons.append(f"no_answer honesty {honesty:.0%} < {min_honesty_rate:.0%}")
    return {"ok": not reasons, "reasons": reasons, "report": rep}


# ── manifest validation (B2/B3 — fail ก่อนยิง API) ─────────────────────────────
def validate_manifest(manifest: dict) -> list[str]:
    """
    ตรวจ manifest ให้เป็น business oracle จริง — คืน list ของ error (ว่าง = ผ่าน):
      - known_roles ครบ ไม่ซ้ำ
      - มี canary ≥1 ; point_id/canary_name/canary_token ไม่ซ้ำทั้ง manifest ; point_id เป็น UUID
      - แต่ละ canary: authorized_roles ⊆ known_roles, ไม่ว่าง ; denied = known - authorized ต้องไม่ว่าง
        (canary ที่ทุก role อ่านได้ = ไม่ใช่ permission test)
    """
    errs = []
    known = manifest.get("known_roles")
    if not isinstance(known, list) or not known:
        return ["known_roles หายหรือว่าง"]
    if len(set(known)) != len(known):
        errs.append("known_roles มีซ้ำ")
    known_set = set(known)

    canaries = manifest.get("canaries")
    if not isinstance(canaries, list) or not canaries:
        return errs + ["canaries หายหรือว่าง"]

    seen_id, seen_name, seen_tok = set(), set(), set()
    for c in canaries:
        name = c.get("canary_name", "?")
        pid, tok = c.get("point_id", ""), c.get("canary_token", "")
        if not is_uuid(pid):
            errs.append(f"{name}: point_id ไม่ใช่ UUID ({pid})")
        for val, seen, label in ((pid, seen_id, "point_id"), (name, seen_name, "canary_name"),
                                 (tok, seen_tok, "canary_token")):
            if val in seen:
                errs.append(f"{name}: {label} ซ้ำ ({val})")
            seen.add(val)
        auth = c.get("authorized_roles", [])
        if not auth:
            errs.append(f"{name}: authorized_roles ว่าง")
        extra = set(auth) - known_set
        if extra:
            errs.append(f"{name}: authorized_roles นอก known_roles: {sorted(extra)}")
        denied = known_set - set(auth)
        if not denied:
            errs.append(f"{name}: ไม่มี denied role (ทุก role อ่านได้ = ไม่ใช่ permission test)")
    return errs
