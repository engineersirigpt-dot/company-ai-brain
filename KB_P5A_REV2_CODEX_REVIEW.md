# Codex Targeted Review — P5a rev2 (`fd8af00`)

**วันที่:** 2026-08-04  
**Target:** `KB_P5A_REV2_FIX_HANDOFF.md` — ยืนยัน B1-B4/M1 และตัดสิน GO/NO-GO P1  
**ขอบเขต:** review + offline test เท่านั้น; ไม่แก้ implementation, `STATUS.md`, Qdrant หรือ deploy

## Verdict

**FIX-THEN-GO** — B1 และ M1 ปิดจริง; transport/retrieval split ของ B2 ถูกทาง แต่ B2-B4 ยังปิดไม่ครบในเส้นทางจริง เพราะ malformed negative response สามารถถูกนับ PASS, manifest ยังไม่ exhaustive และ point IDs ปัจจุบันนำไป ingest ใน Qdrant ไม่ได้

แนว redesign `/search + synthetic canary + positive/negative pair` เป็นทางที่ถูกและควรเก็บไว้ ไม่ต้องย้อนกลับไป `/ask`-based permission test

## Closure matrix

| Finding | Verdict | เหตุผลย่อ |
|---|---|---|
| B1 — all denied/empty exit 0 | **CLOSED** | `permission_suite_ok()` ต้องมี pair และทุก pair PASS; harness พิสูจน์ all-DENIED/all-empty exit 1 |
| B2 — transport ปน retrieval | **PARTIAL** | core แยกแกนแล้ว แต่ no-answer result ยังไม่ถูกใช้ใน summary/gate อย่างครบถ้วน |
| B3 — oracle derive จาก config | **PARTIAL** | มี manifest อิสระแล้ว แต่ runner ไม่ validate/exhaustively consume `authorized_roles` |
| B4 — ใช้ collection แทน point ID | **PARTIAL** | runnerอ่าน point ID จริงแล้ว แต่ missing ID ยังถูกยอมรับ และ manifest IDs ไม่ใช่ Qdrant-compatible IDs |
| M1 — ไม่มี positive control | **CLOSED** | positive ไม่พบ canary → INCONCLUSIVE → suite fail |
| M2 — role-scoped key proof | **OPEN** | 401 ผ่าน spoof preflight ได้ และ `verified=False` ยัง exit 0 ได้ |
| M3 — malformed response | **OPEN** | missing `results`/missing `point_id` ใน negative response ยังกลายเป็น PASS ได้ |
| M4 — blank source false hit | **CLOSED** | `source_hit()` reject blank source และมี unit regression |
| N1 — cp874 | **CLOSED** | test ทั้งสองชุดรันบน Windows ปัจจุบันได้โดยไม่ตั้ง UTF-8 ภายนอก |

## Findings

### B1 — Missing response key/point ID ทำให้ negative probe PASS ได้

**Finding:** `extract_points()` ใช้ `resp.get(key, [])` และ unit testยืนยันว่า response ไม่มี `results` ถือเป็น list ว่างที่ถูกต้อง (`eval_contract.py:51-65`, `test_eval_contract.py:31-41`) จากนั้น negative probeที่ transport SUCCESS + points ว่างทำให้ `pair_verdict()` คืน PASS เมื่อ positive พบ canary (`eval_contract.py:126-132`)

**Why it matters:** API/proxy ที่ตอบ HTTP 200 `{}` เฉพาะ negative request จะทำให้ permission suite เขียว ทั้งที่ response contract พังและ harness ไม่ได้เห็นหลักฐานว่ามีการบังคับ filter จริง นี่คือ false-green ใน security gate

กรณี `results=[{}]` ก็คล้ายกัน: `extract_points()` ยอมรับ element object, `point_ids()` แปลง missing ID เป็น `""`, `canary_found()` คืน false แล้ว negative side PASS

**Required fix:**

- successful `/search` response ต้องมี key `results` จริง; missing key = MALFORMED
- ทุก result ที่ใช้ permission test ต้องมี `point_id` non-empty และชนิดที่ยอมรับได้; missing/duplicate ID = MALFORMED/INCONCLUSIVE
- successful `/ask` ต้องมี `answer` และ `citations` ตาม schema; validation แยกจาก `/search`
- เพิ่ม harness cases: positive valid + negative `{}` และ positive valid + negative `[{ }]` ต้อง exit 1

### B2 — Manifest มี policy ครบในไฟล์ แต่ runner ทดสอบเพียงบาง role

**Finding:** manifest มี `authorized_roles`, `positive_role` และ `forbidden_roles` แต่ `run_permission_suite()` ใช้ positive เพียง role เดียวและ iterate เฉพาะ `forbidden_roles` (`ask_eval.py:70-102`) โดยไม่ validate ว่า role sets disjoint/exhaustive หรือว่า `positive_role ∈ authorized_roles`

**Why it matters:** ตัวอย่าง RECALL ไม่ทดสอบ unauthorized roles เช่น `engineering`, `purchasing`, `hr`, `it`; หาก P1 เผลอ grant role เหล่านี้ suite ยังเขียว ขณะเดียวกันการ revoke สิทธิ์ `management` หรือ `admin` ก็ไม่ถูกตรวจ เพราะ authorized roles อื่นไม่ถูกใช้เป็น positive controls

**Required fix:**

- manifest ระบุ `known_roles` ระดับบนสุดแบบ independent/manual
- validate ต่อ canary ว่า `allowed_roles` และ denied roles ไม่ซ้ำกัน และ union ครบ `known_roles`
- สร้าง positive probeให้ทุก allowed role และ negative probeให้ทุก `known_roles - allowed_roles`
- ตัด `positive_role` ที่ซ้ำซ้อน หรือให้เป็นเพียง smoke role แต่ห้ามใช้แทน exhaustive matrix
- manifest ว่าง, duplicate canary/ID/token, unknown/missing role หรือ zero denied tests ต้อง fail ก่อนยิง API

นี่ทำให้ manifest เป็น business oracle จริง ไม่ใช่เพียงตัวอย่างสี่เอกสาร

### B3 — `point_id` ใน manifest ใช้กับ Qdrant จริงไม่ได้

**Finding:** IDs เช่น `CANARY-RECALL-001` (`permission_manifest.json:5`) ไม่ใช่ uint64 หรือ UUID ขณะที่ Qdrant รองรับ point ID เป็น 64-bit unsigned integerหรือ UUID เท่านั้น ตาม [Qdrant Points documentation](https://qdrant.tech/documentation/manage-data/points/#point-ids)

**Why it matters:** P5b ไม่สามารถ ingest manifestนี้เข้า test collectionตาม handoff ได้ จึงยังไม่มี executable independent oracle แม้ offline fake testจะผ่าน

**Required fix:** ใช้ deterministic UUID/32-hex UUID เป็น `point_id` และเก็บชื่ออ่านง่ายใน field แยก เช่น `canary_name="CANARY-RECALL-001"` จากนั้น validate ID formatตอนโหลด manifest

อีกจุดที่ต้องล็อกใน P5b adapter: manifest ใช้ชื่อ `authorized_roles` แต่ Qdrant filterจริงอ่าน payload `allowed_roles`; ingestion ต้อง map อย่าง explicit และ test payload shape ห้ามเขียน `authorized_roles` ลง Qdrantแทน

### M1 — Auth preflight ยังพิสูจน์ 403 role-scope ไม่ได้

**Finding:** `run_auth_preflight()` รับแค่ `transport == DENIED` (`ask_eval.py:105-118`) จึงถือ 401 จาก missing/invalid key เป็นผลผ่าน ทั้งที่ docstring บอกว่าต้อง 403; normalized recordมี `status` อยู่แล้วแต่ไม่ใช้ นอกจากนี้ `spoof_pairs=[]` คืน `{ok: True, verified: False}` และ `suite_exit_code()` ตรวจเพียง `ok` (`eval_contract.py:159-165`)

**Why it matters:** single key หรือ key map ที่ตั้งผิดสามารถให้ permission suite green พร้อมข้อความ auth “unverified” ได้ ทำให้ claim M2 CLOSED เกินหลักฐาน

**Required fix:**

- spoof preflight ต้อง assert exact HTTP 403
- missing/invalid key 401 เป็น setup/auth failure ไม่ใช่ role-scope pass
- หาก run นี้ประกาศว่าเป็น auth gate ต้อง require `verified=True` เพื่อ exit 0
- test exact cases: 401 → fail, 403 → pass, no spoof pairs → unverified/non-zero สำหรับ auth-gated mode

หากต้องการให้ single broad keyใช้วัด retrieval ได้ ให้แยก `permission_exit_code` กับ `auth_gate_status=UNVERIFIED` อย่างชัดเจน และห้ามสรุปว่า auth ผ่าน

### M2 — `/ask` quality ถูกเก็บ record แล้ว แต่ยังไม่ถูกวัดตามชื่อ metric

**Finding:** `run_ask_quality()` เก็บ `hit`, `citation_valid`, `cited_any`, `said_no_answer` (`ask_eval.py:121-137`) แต่ `ask_quality_failures()` ใช้เพียง transport และ has-answer ที่ไม่มีผลค้น (`eval_contract.py:140-156`); `print_report()` ไม่รายงาน no-answer honesty/citation validity (`ask_eval.py:163-170`)

**Why it matters:** no-answer case ที่ HTTP 200 แต่ตอบว่าง/แต่งคำตอบ, has-answer ที่ดึงผิด source หรือ dangling citation ยังไม่ทำให้ gate fail และ metricเดิมหายจาก summary แม้ B2 ระบุว่าไม่หลุด denominatorแล้ว

**Required decision:**

- สำหรับ P1 permission gate แนะนำตัด `/ask` quality ออกจาก exit codeทั้งหมด เพื่อให้ security gateเล็กและ deterministic
- สำหรับ P5b quality gate ให้กำหนด acceptanceแยก: has-answer hit, no-answer honesty, citation validity/cited-any และ threshold ที่ต้องผ่าน

อย่าใช้คำว่า overall `GREEN` ร่วมกันจน semantics สอง track ถูกแยกชัด

## สิ่งที่ยืนยันผ่านแล้ว

- `pair_verdict`: positive success+found และ negative success+not-found เท่านั้นจึง PASS
- all DENIED/all empty/LEAK ทำให้ permission suite exit 1
- transport และ retrieval เป็นคนละ fieldใน records
- blank source ไม่เป็น retrieval hit
- tests รันซ้ำใน workspaceนี้:
  - `py -3 test_eval_contract.py` → **45/45 passed**
  - `py -3 test_ask_eval_harness.py` → **8/8 passed**

Tests ปัจจุบันยืนยัน pure/harness logicที่เขียนไว้ แต่ยัง encode พฤติกรรมผิดเรื่อง missing response key และไม่ได้แตะ Qdrant ID/auth HTTP status seam ตาม findingsข้างต้น

## คำตอบ 3 ข้อใน handoff

1. **B1-B4/M1 ปิดครบไหม?** — B1/M1 ปิด; B2 core split ปิดแต่ quality semanticsยัง partial; B3/B4 ยังต้อง manifest validation/exhaustive roles/Qdrant-compatible IDs และ strict response shape
2. **`/search` + point-ID positive/negative pair ใช้เป็น leak definition ได้ไหม?** — **ได้** และเหมาะกว่า `/ask`; ต้องใช้ independent exhaustive manifest, valid UUID/uint64 IDs และ malformed negative responseต้องเป็น INCONCLUSIVE/fail
3. **GO P1 หรือรอ P5b?** — ยัง **NO-GO P1 implementation** จนปิด B1-B3/M1 ข้างบน; ไม่ต้องรัน P5b เต็มก่อนเริ่ม P1 หลังแก้ contractแล้ว ลำดับที่แนะนำคือ:
   1. P5a rev2.1 ปิด strict response/manifest/auth-preflight
   2. implement P1 local + synthetic
   3. รัน P5b บน separate test collection/API config
   4. ผ่าน P5b แล้วจึงประกาศ P1 hardened

## Minimum acceptance สำหรับ rev2.1

- [ ] missing `results`/missing `point_id` บน negative path → MALFORMED/exit 1
- [ ] manifest schema validate และทดสอบทุก known roleทั้ง allow/deny
- [ ] point IDs เป็น valid deterministic UUID/uint64 และ mapping `authorized_roles → allowed_roles` ชัดเจน
- [ ] auth spoof 403 เท่านั้นจึง verified; 401/unverified ไม่ถูกเรียก auth pass
- [ ] แยก permission gateจาก optional `/ask` quality gate หรือบังคับ quality metricsที่บันทึกไว้จริง
- [ ] เพิ่ม harness tests ครอบ seam เหล่านี้ ไม่ใช่เฉพาะ normalized fake records

**Final verdict:** **FIX-THEN-GO** — architecture ของ harnessถูกแล้ว แต่ missing negative response shapeยังสร้าง false PASS ได้ จึงยังไม่ควรใช้เป็น measuring stick ของ P1

## Verification note

- review trace: manifest → `run_permission_suite()` → `http_call()`/`extract_points()` → `canary_found()` → `pair_verdict()` → `suite_exit_code()`
- ใช้ official Qdrant documentation ยืนยันข้อกำหนด point ID; local `.venv` ชี้ไป Python ของ userอื่นและ import `qdrant_client` ไม่ได้ จึงไม่ได้สร้าง Qdrant pointจริง
- ไม่ได้แก้ implementation, `STATUS.md`, Qdrant หรือ environment
