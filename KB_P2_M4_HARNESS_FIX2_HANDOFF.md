# P2 — ปิด M4 harness B1/B2/M1/M2 (round 2) → review ก่อนเขียน runner จริง

> **สืบเนื่อง:** `KB_P2_M4_HARNESS_FIX_CODEX_REREVIEW_60FE55F.md` (FIX-THEN-GO runner — 3 ช่องสำคัญ)
> **ทั้งหมด pure/offline** — ไม่รัน Docker/Qdrant/model · real-path runner ยัง NO-GO

## Finding → fix → proof

| # | ช่องที่ Codex เจอ (probe) | Fix |
|---|---|---|
| **B1** ⭐ | `assemble_evidence` ปิดท้ายด้วย `ev.update(run_meta)` ไม่มี allowlist → caller ส่ง proof FAIL แล้วใส่ค่า PASS ทับผ่าน `run_meta` ได้ (ทับ verdict/`per_case`/`raw_evidence_sha256`/`schema_version`) → public gate ผ่านผิด | **exact allowlist** `_RUN_META_KEYS` — key นอก allowlist (รวม `status`/`per_case`/`raw_*`) → **raise** ; ลำดับเขียน = metadata ก่อน แล้ว **verdict/protected เขียนทับทีหลัง** (run_meta แตะ verdict ไม่ได้) ; verdict มาจาก `build_verdicts(isolation, oracle, case_count, traced_count)` (derive จาก validated proof, ไม่รับ dict ลอย) |
| **B2** | cross-encoder ได้ query คงที่ `"m4"` ทุก case ; query จริงไม่อยู่ใน evidence | boundary ใหม่ `score_case(query_text, query_vector, candidates, authorized_pairs, scorer)` ส่ง **query text จริงของ case** เข้า underlying ; `query_text_sha256` เข้า frozen case + per_case + `_M4_CASE_KEYS`/`_M4_FCASE_KEYS` ; validator เทียบ **exact กับ frozen QueryProbe** (`_m4_case_errors` + `validate_m4_frozen_manifest`) |
| **M1** | `components/pairs/scores/calls/sentinel_reached` เป็น public mutable → หลัง model เห็น sentinel แก้ trace เป็น authorized ย้อนหลังได้ → evidence ผ่าน | `score_case` = **one-shot** คืน **frozen `CaseTrace` (NamedTuple, immutable)** ; validate output count + finite **ก่อน seal** ; `build_case_record` consume `CaseTrace` เท่านั้น (dict → `TypeError`) — แก้ trace หลัง finalize ไม่ได้ |
| **M2** | `_vec_hash()` เกิด **หลัง** `_s.score()` → malformed/NaN vector แตะ model ก่อน harness fail | `score_case` validate **query_text → query_vector → candidates → authorized** ครบ **ก่อน delegate** ; sentinel/unauthorized guard ก่อนเรียก underlying (call = 0) |

## execution source เดียว (หลัง fix)

```
score_case(query_text, query_vector, candidates, authorized_pairs, scorer)
  1. validate query_text (non-empty str) · query_vector (finite floats) · candidates · authorized  ← ก่อน delegate ทั้งหมด (M2)
  2. guard: pair ที่ไม่อยู่ใน authorized → PermissionError ก่อนเรียก underlying (B1/sentinel)
  3. scorer.score(query_text จริง, [candidate texts])   ← ไม่ใช่ 'm4' (B2)
  4. validate score count + finite → seal → คืน CaseTrace (immutable, call_count=1)  (M1)
build_case_record(trace=CaseTrace)  → model_input/counts/finite มาจาก trace เท่านั้น
assemble_evidence(per_case, run_meta[allowlist], verdicts=build_verdicts(...))  → verdict เขียนทับ run_meta เสมอ (B1)
```

## negative tests ที่เพิ่ม (pure)

- **B1**: `run_meta={**RUN_META,"status":"PASS"}` → **raise** ; `run_meta` ทับ `per_case` → **raise** ; proof `isolation=FAIL` → evidence `status=FAIL` → **gate fail**
- **B2**: fake scorer ได้ query ของ case จริง (`_rec.queries == [QT1]`, ไม่ใช่ `'m4'`) ; เปลี่ยน `query_text_sha256` (คง vector) → **gate fail** ; format ผิด → error
- **M1**: `CaseTrace` แก้ `pairs` ไม่ได้ (`AttributeError`) ; `build_case_record(trace=dict)` → `TypeError`
- **M2**: query vector NaN → `ValueError` **ก่อน delegate** + underlying call = 0 ; sentinel → `PermissionError` + call = 0

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch ครบ)
```
test_p2_m4_harness 20/20   test_p2_m4 41/41   test_p2_runplan 95/95   test_p2 166/166
test_p2_provider 22/22     test_p2_harness 21/21   test_p2_pin 14/14   test_p2_adapter 22/22
test_p2_dockerbuild 41/41  test_policy 69/69   test_eval_contract 64/64   test_ask_eval_harness 12/12
test_auth 11/11            test_p5b_fixtures 11/11        รวม 609 checks ผ่านหมด
```

## ยังไม่ได้ทำ (รอ review รอบนี้ก่อน — real-path)
- **runner จริง** — wire `resolve_effective_access → provider → score_case → PinnedCrossEncoder` บน isolated Qdrant (unfiltered raw query + filtered provider ต่อ case, QueryProbe เดียว/case)
- **IsolationProof** object (fresh project/network/volume/collection UUID + marker) → feed `build_verdicts(isolation=...)`
- **OracleProof** object (direct scroll เทียบ frozen manifest แบบ independent) → feed `build_verdicts(oracle=...)`
- **atomic evidence/receipt write** (temp→rename) + failure controls (partial write / non-zero exit / exception → ไม่มี PASS artifact)

## ขอ Codex review
1. `assemble_evidence` allowlist + verdict-เขียนทับ-run_meta (B1) ปิด false-PASS ครบไหม — มี key path ไหนที่ run_meta ยังทับ verdict/protected ได้อีก
2. `score_case` boundary — query text จริงเข้า underlying + validate ก่อน delegate + one-shot `CaseTrace` immutable (B2/M2/M1) ปิดครบไหม
3. `build_verdicts` derive จาก isolation/oracle/traced_count — contract นี้พอสำหรับ real runner ไหม หรือควรบังคับ proof object ตั้งแต่ตอนนี้
4. หลังผ่าน → เขียน real-path runner + IsolationProof/OracleProof + atomic writer แล้วขอ **GO M4a run** บน isolated Qdrant

**Gate:** real-path runner = FIX-THEN-GO หลัง harness re-review ผ่าน · M4a run = NO-GO จน runner/interlock/oracle/atomic review · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน Data Owner sign-off + M4b + validated canary
