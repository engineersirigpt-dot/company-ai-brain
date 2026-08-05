# P2 — ปิด M4 harness B1/B2/B3/M1/M2/M3 → review ก่อนเขียน runner จริง

> **สืบเนื่อง:** `KB_P2_M4_HARNESS_CODEX_REVIEW_FFAB5FA.md` (FIX-THEN-GO runner)
> **ทั้งหมด pure/offline** — ไม่รัน Docker/Qdrant/model · real-path runner ยัง NO-GO

## Finding → fix → proof

| # | probe ที่เจอ | Fix |
|---|---|---|
| **B1** ⭐ | model เห็น sentinel จริง แต่ evidence อ้าง authorized (spy trace แยกจาก model_input) | **boundary เดียว** `M4Scorer.score_candidates(query_vector, [(point_id, rerank_text)])` — guard sentinel/unauthorized **ก่อน**เรียก underlying (call=0) ; `build_case_record` derive `model_input/counts/finite` **จาก trace เท่านั้น** (ลบ caller `model_input`) ; spy ใหม่ต่อ case |
| **B2** | run_id วงกลม (expected สร้างจาก receipt ที่กำลังตรวจ) | gate ใช้ **`plan["run_id"]`** เป็น expected run_id ; evidence/receipt ต้องตรง RunPlan (ไม่ derive จาก output) |
| **B3** | malformed receipt (NaN) → gate **crash** | `validate_m4_run_receipt` structural ก่อน → short-circuit ก่อน hash ; `_safe_m4_receipt_digest` (NaN → None) |
| **M1** | finished < started ผ่าน ; command hash ambiguity ; `str()` coerce | receipt เช็ค `finished >= started` ; `command_sha256 = canonical(argv[str])` ; `_bytes_sha256` รับ bytes เท่านั้น |
| **M2** | builder self-stamp `isolated_interlock/oracle/sentinel_reached=PASS` | `assemble_evidence(..., verdicts)` — verdict มาจาก **validated interlock/oracle/spy** (ไม่ปั้น PASS เอง) |
| **M3** | `component(1,'x') == component('1','x')` ; vector `str()` | typed identity — `point_id` type-tag (int≠str) ; `rerank_text` str-only ; query vector = **canonical JSON ของ finite float** (allow_nan=False) |

## ผลรัน (offline)
```
test_p2_m4_harness 18/18 — harness→gate ผ่าน ; B1 sentinel→PermissionError + underlying call=0 ; run_id-from-plan ;
  NaN receipt no-crash ; finished<started ; argv ambiguity ; typed id ; verdict-not-self-stamp
test_p2_m4 39 · test_p2 166 · test_p2_runplan 95 · provider 22 · harness 21 · pin 14 · adapter 22 · dockerbuild 41 ·
  policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```
> หมายเหตุ: `test_p2_adapter` = **22/22** ในเครื่องนี้ (มี qdrant_client) ; บน clean env integration ถูก skip → 21/21 (pure ครอบ contract แล้ว)

## ยังไม่ได้ทำ (รอ review รอบนี้ก่อน — real-path)
- **runner จริง** — wire `resolve_effective_access → provider → M4Scorer.score_candidates → PinnedCrossEncoder` บน isolated Qdrant (unfiltered raw query + filtered provider ต่อ case, QueryProbe เดียว)
- **isolation interlock** object (fresh project/network/volume/collection UUID + marker) → verdict
- **independent oracle** object (direct scroll เทียบ frozen manifest) → verdict
- **atomic evidence/receipt write** (temp→rename) + failure controls (partial write / non-zero exit / exception ก่อน-หลัง write → ไม่มี PASS artifact)

## ขอ Codex review
1. `M4Scorer` boundary (guard ก่อน underlying + trace เป็นแหล่งเดียวของ model_input) ปิด B1 ครบไหม
2. run_id-from-plan (B2) · no-crash receipt (B3) · timestamp/argv/typed-id (M1/M3) · verdict-injection (M2) ปิดครบไหม
3. หลังผ่าน → เขียน real-path runner + interlock/oracle objects + atomic writer แล้วขอ **GO M4a run** บน isolated Qdrant

**Gate:** real-path runner = FIX-THEN-GO · M4a run = NO-GO จน runner review + atomic controls · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
