# ENQ Extraction — Error Taxonomy (Codex F1)

สัญญา error ระหว่าง **DB functions (007)** → **transport mapper (`enq_api/main.py`)** → **worker/client**
กำหนด **ก่อน** เขียน extraction endpoints เพื่อให้ worker แยก retry / re-claim / fix-result ได้ถูกประเภท
โดย **ไม่ parse `message_primary`** (กันข้อมูล ENQ จริงหลุด) — ใช้ SQLSTATE ที่เชื่อถือได้เท่านั้น

## หลักการ

- DB ยิง **custom SQLSTATE (`RF*`)** สำหรับ business error ที่ SQLSTATE มาตรฐานกำกวม
  (เดิม `apply` ใช้ `23514` ทั้ง *state conflict* และ *invalid result*, ใช้ `23503` ทั้ง *run ไม่พบ* และ *ref ใน result ไม่พบ* → op เดียวกันแยกไม่ได้)
- mapper เป็น **operation-aware**: `_http_for_pg(op, sqlstate)` — op ∈ `draft|begin|claim|apply|fail`
- ค่าที่ client เห็น = **stable public code** เท่านั้น (ไม่มี DB message); server log = request_id, op, sqlstate, schema identifier

## SQLSTATE → HTTP → stable code

| SQLSTATE | ความหมาย | ยิงจาก (007) | HTTP | stable code |
|---|---|---|---:|---|
| `RFS01` | wrong status / lease-token·actor·service ไม่ตรง / lease หมดอายุ | claim, fail, apply | **409** | `state_conflict` |
| `RFN01` | `run_id` ไม่พบ | claim, fail, apply | **404** | `run_not_found` |
| `RFR01` | invalid provider result: evidence completeness/derivation/inference/clarification หรือ ref ใน result resolve ไม่ได้ | apply (+ `_resolve_subject`) | **422** | `invalid_extraction_result` |
| `23505` | idempotency key ซ้ำด้วย payload/actor ต่าง | begin/claim/apply/fail | **409** | `idempotency_conflict` |
| `23503` | begin: source/provider/attestation/approval input ไม่พบ | begin | **422** | `invalid_request` |
| `22023` (+ class `22`, `23502`, `23514` ที่เหลือ) | invalid input value (purpose, schema_version, input_sha256, JSON null, typed cast…) | ทุก op | **422** | `invalid_payload` (draft) / `invalid_request` (begin·claim·fail) / `invalid_extraction_result` (apply) |
| `54000` | payload/collection เกิน limit | begin/apply | **413** | `payload_too_large` |
| `57014` `55P03` `40001` `40P01`, class `08` | canceled/timeout, lock, deadlock, serialize, connection | ทุก op | **503** | `database_unavailable` (worker retry ได้) |
| อื่น ๆ | unexpected | — | **500** | `internal_error` |

> `55P03` เป็น class 55 แต่ **transient** — mapper เช็ค transient ก่อน class-based branch

## ทำไมสำคัญต่อ worker

- **409 `state_conflict`** → lease หลุด/ถูก reclaim → **อย่า retry ด้วย token เดิม**; re-claim ใหม่ถ้ายังอยู่ใน window
- **404 `run_not_found`** → run ผิด/ถูกลบ → หยุด ไม่ retry
- **422 `invalid_extraction_result`** → provider result ผิด → **fix result / re-run provider**, ไม่ใช่ retry เฉย ๆ
- **409 `idempotency_conflict`** → key ซ้ำ payload ต่าง → bug ฝั่ง caller
- **503** → transient → **retry ได้** (backoff)

การแยก 409 (state) ↔ 422 (result) ↔ 404 (not-found) คือแกนหลัก; ความละเอียด state-vs-lease (RFS01 ครอบทั้งคู่)
เป็น refinement ภายหลัง — guard ปัจจุบันรวม status+lease อยู่ก้อนเดียว

## ยืนยันด้วยเทสต์

- **DB (`050`)**: es12/13/22/24 → `RFR01`, es15/16/31 → `RFS01`, es29/30 → `RFN01`, es6 → `23503` (begin); harness T16/T17 → `RFS01`
- **mapper (`enq_api/test_api.py`)**: `_http_for_pg(op, code)` ครบทุกแถว + ยืนยัน `apply` RFS01(409) ≠ RFR01(422)
