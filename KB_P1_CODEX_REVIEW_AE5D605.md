# Codex review — P1 policy compiler / effective ACL (`ae5d605`)

**วันที่:** 2026-08-04  
**Target:** `KB_P1_IMPLEMENTATION_HANDOFF.md`  
**ขอบเขต:** review เท่านั้น — ไม่แก้ implementation, `STATUS.md`, Qdrant หรือ deploy

## Verdict

**FIX-THEN-GO P5b**

แกนหลักถูกทาง: `/search` และ `/ask` ผ่าน `authorize()` → `resolve_effective_access()` → `compile_retrieval_filter()` → `query_points(query_filter=...)` เส้นเดียวกัน และ admin ไม่มี no-filter bypass

แต่ยังมีหนึ่ง security blocker ใน lifecycle ของข้อมูล และหนึ่ง semantics decision ที่ต้องปิดก่อนใช้ P5b เป็นหลักฐานว่า P1 hardened:

1. เอกสารที่เคย ACTIVE แล้ว ingest รอบใหม่กลายเป็น QUARANTINED ยังทิ้ง point รุ่นเก่าให้ค้นเจอได้
2. ต้องนิยามให้ตรงกับ Qdrant ว่า `allowed_roles: "qc"` เป็น singleton ACL ที่ match ได้ หรือเป็น payload ผิด contract ที่ต้องกันตั้งแต่ write boundary; filter ปัจจุบันแยก scalar ออกจาก array ไม่ได้

ดังนั้นยัง **NO-GO** สำหรับคำว่า hardened/deploy/production corpus แต่หลังปิด B1 + decision D1 และเพิ่ม semantic probe ตาม M1 แล้ว จึง **GO P5b บน test collection แยก, `AUTH_MODE=enforce`, synthetic only**

## Findings

### B1 — Quarantine ไม่ revoke point รุ่นเก่า

`ingest.py:208-229` แยก point ที่ QUARANTINED ออกจาก batch ใหม่แล้วไม่ upsert ซึ่งป้องกันข้อมูลใหม่ได้ แต่ไม่แตะ point เดิมของ source เดียวกัน

fail path ที่เกิดได้จริง:

1. source เดิมมี point `policy_status=ACTIVE`, `allowed_roles=["sales", ...]`
2. mapping รอบใหม่เสีย, ACL ว่าง หรือ policy ถูก tighten จนต้อง quarantine
3. `store_in_qdrant()` skip batch ใหม่
4. point รุ่นเก่ายังคง ACTIVE และ `/search`/`/ask` ยัง retrieve ได้

กรณี policy เปลี่ยนจาก SALES เป็น admin-only ก็มีปัญหาเดียวกัน เพราะ ingestion เป็น upsert-only; chunk ID ที่หายหรือเปลี่ยนจะทิ้ง ACL รุ่นเก่าไว้ นี่ตรงกับ replacement-protocol backlog ที่ `STATUS.md:223-224` บันทึกอยู่แล้ว

**ต้องปิดก่อนใช้ P5b เป็นหลักฐาน hardened:** ทำ replacement/generation protocol ที่อย่างน้อยพิสูจน์สอง regression นี้บน test collection:

- ACTIVE → QUARANTINED: point เก่าต้องไม่ถูก standard retrieval เห็นอีก
- ACL กว้าง → ACL แคบ: role ที่ถูกถอนต้องเห็นศูนย์ point หลัง publish generation ใหม่

ถ้ายังไม่ทำ generation/staging เต็มรูป ให้ระบุ claim อย่างซื่อตรงว่า P1 รองรับเฉพาะ fresh insert และห้ามประกาศ hardened; ทางแนะนำคือปิด lifecycle ให้ถูกก่อน P5b เพราะ P5b ควรทดสอบระบบที่ตั้งใจจะรับรอง

### M1 — `matches_policy` ใกล้เคียง แต่ไม่ใช่ Qdrant oracle แบบ exact

ผลเทียบ semantics:

| Stored payload | Qdrant `MatchAny(any=["qc"])` | `matches_policy()` | ผล |
|---|---:|---:|---|
| `allowed_roles=["qc", "admin"]` | match | match | ตรง |
| `allowed_roles="qc"` | match | match | ตรง แต่ scalar ไม่ได้ fail-closed |
| `allowed_roles=null` | no match | no match | ตรง |
| field หาย | no match | no match | ตรง |
| `acl_schema_version=true` เทียบ `value=1` | no match เพราะ type ต่าง | match เพราะ Python `True == 1` | ไม่ตรง |

Qdrant ระบุว่า `Match Any` ใช้กับ stored scalar ได้ และถ้า stored value เป็น array จะผ่านเมื่ออย่างน้อยหนึ่งสมาชิก match; ส่วน `NULL` ใช้ match condition ไม่ได้ ต้องใช้ `IsNull` โดยเฉพาะ นอกจากนี้ Qdrant เปรียบเทียบตามชนิดข้อมูล ถ้า type ไม่ตรง condition ถือว่าไม่ผ่าน:

- https://qdrant.tech/documentation/search/filtering/#match-any
- https://qdrant.tech/documentation/search/filtering/#is-null
- https://qdrant.tech/documentation/concepts/payload/#payload-types

ความต่าง `true` กับ `1` ทำให้ fake matcher **over-match** เมื่อเทียบกับ Qdrant จึงไม่ใช่ fail-open ใน production แต่คำอธิบายว่าเป็น “exact executable semantics” ยังไม่จริง และ matrix oracle ที่ `test_policy.py:131-146` ใช้ Python equality/membership แบบเดียวกันจึงไม่ได้พิสูจน์ type edge อย่างอิสระ

**ก่อน P5b:**

- เพิ่ม table-driven semantic cases: scalar/list/null/missing, mixed-type array, `acl_schema_version=true`, `1.0`, `1`
- รันชุดเดียวกันกับ Qdrant test collection จริง แล้วเทียบผลกับ fake matcher
- เปลี่ยน strict equality ใน fake ให้ type-aware สำหรับ `MatchValue` (`bool` ต้องไม่เท่ากับ `int`)

### D1 — ต้องตัดสิน contract ของ scalar `allowed_roles`

ข้อกำหนดเดิมบอกว่า malformed ACL ต้องไม่มี principal ใดค้นเจอ แต่ Qdrant มอง keyword scalar และ keyword array เป็นรูปแบบที่ filter ได้ทั้งคู่ ดังนั้น payload v1 ที่มี:

```json
{"allowed_roles": "qc"}
```

จะถูก role `qc` retrieve หาก schema/version/status ที่เหลือผ่าน

ข้อเสนอสำหรับ PoC: **ยืนยันว่า payload canonical ที่ trusted writer สร้างต้องเป็น array เท่านั้น แต่ยอมรับว่า query filter ไม่สามารถพิสูจน์ array shape ได้เอง** แล้วบังคับดังนี้:

- raw mapping ที่ `allowed_roles` ไม่ใช่ `list[str]` ต้อง QUARANTINED ก่อนเขียน
- active payload ทุกจุดต้องสร้างผ่าน validator/write boundary เดียว
- P5b แยก test สองชนิด: malformed raw input ผ่าน ingestion ต้องถูก quarantine; direct malformed Qdrant payload ต้องบันทึกเป็น store-integrity violation ไม่ใช่อ้างว่า filter จะตรวจ shape ให้

หาก Business requirement ยืนยันว่า scalar ต้อง invisible แม้ถูกเขียนตรงเข้า Qdrant ต้องเปลี่ยน payload/filter representation เพราะ `MatchAny` ปัจจุบันบังคับ array-only ไม่ได้

### M2 — Quarantine gate ยังไม่ใช่ write boundary เดียวของระบบ

`ingest.py` ผ่าน resolver แล้ว แต่ยังมี Qdrant mutation path ที่ bypass gate:

- `ocr_reingest.py:110-121` delete แล้ว upsert `get_rbac()` โดยตรง ไม่มี schema/version/status
- `retag_rbac.py:65-78` set แค่ collection/level/roles
- `migrate_to_server.py:64-85` copy payload โดยไม่ validate

สองตัวแรกจะทำให้ point หายจากผลค้นภายใต้ filter v1 (availability failure) และ migration สามารถพา payload v1 ที่ malformed เข้า collection ปลายทางได้

**ไม่จำเป็นต้อง refactor ทั้งหมดก่อนเริ่ม P5b** ถ้า P5b ใช้ fresh collection และ writer ที่ allowlist ไว้เพียงตัวเดียว แต่ต้องทำหนึ่งในสองทางก่อนประกาศ hardened:

- refactor ทุก active writer ให้ผ่าน shared validated point builder; หรือ
- mark legacy tools ว่าใช้กับ P1 collection ไม่ได้และให้ fail-fast เมื่อพบ policy v1

### M3 — Quarantine เป็น stdout อย่างเดียวและรายงาน success แม้ไม่มี active point

`ingest.py:224-231` เก็บเพียง reason ใน console, ไม่มี source/hash/run ID/durable record และ `ingest.py:261-264` ยังพิมพ์ `[OK] Done!` หลัง `store_in_qdrant()` เสมอ การ quarantine ทั้งเอกสารจึงดูเหมือน ingest สำเร็จและไม่มี review queue ให้ admin ตามคำอธิบายใน contract

แยกจาก B1: quarantine store ไม่จำเป็นต่อการบล็อก retrieval ใน fresh insert แต่จำเป็นต่อ audit/retry/operation ก่อน deploy

ขั้นต่ำที่แนะนำ: durable manifest มี source, source hash, reason code, policy version, timestamp, run ID และ terminal outcome (`ACTIVE`/`QUARANTINED`); ห้าม success แบบกำกวม

### M4 — Resolver ยังยอมรับ mapping ผิดชนิดบางแบบเป็น ACTIVE

`policy.py:166-178` ใช้ `int(confidentiality_level)` จึงยอมรับ string, bool และตัดเศษ float; `validate_document_policy()` ตรวจ `collection_group` แค่ truthy และไม่ตรวจ range ของ level ทั้งที่ level เป็น egress signal

ตัวอย่างที่ปัจจุบันอาจ ACTIVE:

- `collection_group=123`
- `confidentiality_level=true`
- `confidentiality_level=2.9` ซึ่งถูกแปลงเป็น `2`

นี่ไม่ขยาย `allowed_roles` โดยตรง แต่ขัด contract “mapping ผิดชนิด → QUARANTINED” และจะอันตรายเมื่อ P4 ใช้ classification ตัดสิน cloud egress ควรเปลี่ยนเป็น strict type/range validation ก่อน P4 และควรเพิ่มเป็น P5b ingest canary หากปิดได้ในรอบนี้

### N1 — auth mode fail-closed อยู่ที่ FastAPI startup ไม่ได้อยู่ใน pure policy contract

`app/main.py:65-72` ปฏิเสธค่า `AUTH_MODE` แปลกถูกต้อง แต่ `policy.resolve_effective_access()` บังคับ key/scope เฉพาะเมื่อ `principal.auth_mode == "enforce"`; ถ้า pure module ถูกเรียกด้วย typo เช่น `"enfore"` จะไหลเหมือน warn/off

เส้นทาง FastAPI ปัจจุบันไม่รั่วเพราะ startup guard จึงไม่ block P5b แต่ควร validate enum ใน pure boundary ด้วยเพื่อให้คำว่า fail-closed ไม่ขึ้นกับ caller ทุกตัวทำถูก

## คำตอบตรง 4 ข้อ

1. **policy.py ถูกทิศและ request-time path ไม่พบ no-filter/admin bypass ใน enforce mode** แต่ document lifecycle, strict mapping validation และ invalid `auth_mode` ยังต้อง harden ตาม findings
2. **`matches_policy` ตรงกับ Qdrant สำหรับ scalar/list/null/missing ที่ถามมา แต่ไม่ exact ทุก JSON type**; scalar role match ทั้งสองฝั่ง ส่วน null/missing ไม่ match ต้องแก้ข้อความ overclaim และเพิ่ม real-Qdrant conformance test
3. **skip-upsert อย่างเดียวไม่พอ** เพราะไม่ revoke generation เก่าและไม่มี durable quarantine workflow; separate store ไม่ใช่สิ่งที่ทำให้ filter ปลอดภัย แต่จำเป็นต่อ operation/audit
4. **ยังไม่ไฟเขียว P5b ณ commit นี้: FIX-THEN-GO** ปิด B1, ตัดสิน D1, เพิ่ม M1 semantic probe ก่อน แล้วค่อยรัน P5b แบบ fresh isolated collection + `AUTH_MODE=enforce` + synthetic canaries

## Minimum acceptance สำหรับรอบแก้ก่อน P5b

- [ ] regression ACTIVE → QUARANTINED แล้ว role เดิมค้น point เก่าไม่เจอ
- [ ] regression ACL broad → narrow แล้ว role ที่ถูกถอนค้นไม่เจอ
- [ ] contract ระบุ scalar `allowed_roles` ให้ตรง Qdrant และ ingestion strict canonical array
- [ ] real-Qdrant semantic table ครบ scalar/list/null/missing/type mismatch
- [ ] P5b process start ด้วย `AUTH_MODE=enforce`; preflight no-key→401, out-of-scope→403, in-scope→200
- [ ] test collection/alias แยกจาก corpus/Voicebot จริง และ writer สำหรับ test ถูก allowlist ชัดเจน

เมื่อครบชุดนี้: **GO P5b local + synthetic**; หลัง P5b security verdict PASS + auth VERIFIED จึงค่อยประกาศ **P1 hardened ในขอบเขต PoC** ส่วน durable quarantine, legacy-writer closure, production backfill/atomic cutover ยังเป็น deploy gates

## Verification ที่ Codex ทำ

- trace `app/main.py`, `policy.py`, `ingest.py` และ Qdrant mutation paths ทั้ง repo
- `python test_policy.py` → **35/35 passed**
- `python test_auth.py` → **11/11 passed**; integration ส่วน `load_api_keys()` ถูก SKIP เพราะ environment ไม่มี `anthropic`
- `python test_eval_contract.py` → **64/64 passed**
- `python test_ask_eval_harness.py` → **11/11 passed**
- ไม่แก้ code, `STATUS.md`, Qdrant หรือ deployment
