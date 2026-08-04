# Codex re-review — P1 fixes before P5b (`809cb60`)

**วันที่:** 2026-08-04  
**Target:** `KB_P1_FIX_BEFORE_P5B_HANDOFF.md`  
**ขอบเขต:** review/offline verification เท่านั้น — ยังไม่ start Docker, ไม่แตะ Qdrant, corpus หรือ deploy

## Verdict

**GO P5b — local + synthetic only, โดยต้องทำ isolation interlock ก่อน infra call แรก**

**NO-GO** สำหรับคำว่า P1 hardened, production backfill, live filter cutover หรือ deploy จนกว่า P5b real-Qdrant จะ PASS และ deploy gates ที่ระบุไว้จะถูกปิด

fix รอบนี้ปิดเหตุผลเดิมที่ห้ามเริ่ม P5b ได้พอ:

- B1: serial replace-by-source ถอน point เก่าก่อน publish ชุดใหม่ จึงไม่ทิ้ง ACL เก่าในเส้นทาง single-writer
- D1: canonical ACL เป็น array-only และ scalar ถูก quarantine ที่ trusted write boundary อย่างตรงกับข้อจำกัด Qdrant
- M1: fake matcher type-aware แล้ว และเลิกอ้างว่าเป็น exact oracle
- M4/N1: strict mapping และ invalid auth mode fail-closed ตาม contract

สิ่งที่ยังไม่จบเป็นคนละชั้น: real-Qdrant conformance คือเนื้องาน P5b เอง; atomic generation/concurrency, production cutover และ quarantine review workflow เป็น deploy track

## คำตอบ 4 ข้อ

### 1. B1 replace-by-source พอหรือไม่

**พอสำหรับ P5b แบบ isolated + serial + single writer** และปิด invariant “หลัง replacement สำเร็จ ห้ามเหลือ ACL เก่า” ในขอบเขตนั้น

trace จริงคือ `ingest.store_in_qdrant()` → `plan_source_replacement()` → delete ทุก point ที่ `source` ตรง → สร้างเฉพาะ point ACTIVE → upsert (`ingest.py:197-265`)

แต่ยังไม่ใช่ generation/atomic replacement:

- delete สำเร็จแล้วการสร้าง `PointStruct`, upsert หรือ manifest ล้ม → เอกสารหาย
- ingest source เดียวกันพร้อมกันสอง process อาจ interleave เป็น delete(A) → delete(B) → upsert(B) → upsert(A) และเกิด generation ผสม
- test B1 ปัจจุบันใช้ `LifecycleQdrant.apply()` จำลอง algorithm (`test_policy.py`) ไม่ได้เรียก `store_in_qdrant()` ตัวจริง จึงยังไม่พิสูจน์ Qdrant filter/delete call และ ordering จริง

นี่เป็น availability/concurrency gap ไม่ใช่การคง ACL เก่าใน serial success path จึงไม่ block การเริ่ม P5b แต่ P5b ต้องประกาศ single-writer และเพิ่ม real-store lifecycle case; ห้ามยกระดับ claim เป็น production hardened

### 2. type-aware `matches_policy` พอปิด M1 หรือไม่

**ปิด M1 ในระดับ offline model แล้ว**

`_type_exact_eq()` ทำให้ bool/int/float ไม่เท่ากันแบบ Python และ `matches_policy()` ระบุชัดว่า scalar/list ได้, null/missing ไม่ได้ พร้อมเปลี่ยน claim เป็น conservative model (`policy.py:135-174`)

การให้ real-Qdrant conformance เป็น P5b ถูกลำดับ เพราะนั่นคือสิ่งที่ P5b มีไว้พิสูจน์ ไม่ควรจำลอง Qdrant เพิ่มอีกชั้นแล้วเรียกว่า oracle

อ้างอิง behavior จาก Qdrant ทางการ: [Match Any](https://qdrant.tech/documentation/search/filtering/#match-any), [Is Null](https://qdrant.tech/documentation/search/filtering/#is-null) และ [payload type matching](https://qdrant.tech/documentation/concepts/payload/#payload-types)

ข้อกำหนดสำหรับ conformance run: ยิง Qdrant filter โดยตรงด้วย `scroll`/`count` บน points ที่รู้ ID ไม่ควรใช้ vector similarity/top-k เป็นตัวพิสูจน์ filter semantics เพราะ retrieval miss อาจทำให้ผลลบทั้งที่ filter ถูก

### 3. D1 scalar → quarantine ที่ write boundary รับได้หรือไม่

**รับได้ และเป็น contract ที่เหมาะกับ PoC**

Qdrant `MatchAny` รองรับ stored keyword scalar และ array ดังนั้น query filter พิสูจน์ array shape ไม่ได้ การบังคับ `list[str]` ที่ resolver แล้วให้ active writer ผ่าน boundary เดียวเป็นตำแหน่งที่ถูกต้อง (`policy.py:198-212`)

P5b ต้องแยกความหมายให้ชัด:

- malformed raw mapping ผ่าน trusted ingestion → ต้อง QUARANTINED
- malformed payload ที่ test inject เข้า Qdrant โดยตรง → ใช้พิสูจน์ native Qdrant semantics/store-integrity risk ไม่ใช่คาดว่า ingestion validator เคยเห็นมัน

### 4. GO P5b ได้หรือไม่

**GO** โดย P5b ต้องสร้าง test harness/stack ที่ fail-closed ต่อ target ก่อนยิง infra จริง ตาม gate ด้านล่าง

## Blocker ก่อน infra call แรกของ P5b

### P5B-B1 — target ปัจจุบันยัง default ไปชื่อ/volume เดิม

แม้แผนระบุ fresh collection แต่โค้ดปัจจุบันยังมี default ที่ชนของเดิมได้:

- `docker-compose.yml:9,19` ใช้ volume `qdrant_data` และ `COLLECTION_NAME=company_docs`
- `ingest.py:26-27,208-225` ใช้ local `./qdrant_storage` และ collection `company_docs` แบบ hard-coded
- API เปลี่ยน collection ผ่าน env ได้ แต่ ingestion CLI ยังเปลี่ยน target ผ่าน env/argument ไม่ได้

ดังนั้นห้ามรัน compose/ingest เดิมตรง ๆ แล้วหวังว่าเป็น test collection

**minimum interlock ก่อน call แรก:** 

- ใช้ dedicated P5b Qdrant container/volume หรือ compose project แยก; ห้าม mount volume ที่มี corpus เดิม
- collection ต้องเป็นชื่อ unique เช่น `company_docs_p5b_<run_id>` และ API + seeder ต้องรับชื่อเดียวกันจาก explicit config
- seeder ต้อง refuse ชื่อ `company_docs` และ refuse target ที่ไม่ว่าง เว้นแต่มี explicit test-run marker ตรงกัน
- bind API/test ports แยกจาก service ที่ใช้งานอยู่
- cleanup ได้เฉพาะ exact collection/container/volume ที่ run นี้สร้าง; ห้ามใช้ wildcard

ทางที่เล็กและปลอดภัยกว่าการดัด compose หลักคือทำ `docker-compose.p5b.yml`/override เฉพาะ test พร้อม volume และ port ใหม่ แล้วให้ P5b harness เป็นผู้สร้าง canary โดยตรง ไม่ใช้ `ingest.py` CLI ที่ยัง hard-code target

## Mandatory acceptance ภายใน P5b

### A. Real-Qdrant filter conformance

ใช้ compiled filter เดียวกับ API และ direct Qdrant `scroll`/`count` ตรวจอย่างน้อย:

- `allowed_roles` list ตรง role → match
- scalar role → native Qdrant match (แต่ถือเป็น store-integrity violation)
- null/missing/unknown role → no match
- stale `acl_schema_version` → no match
- `acl_schema_version=true` และ `1.0` เทียบ integer `1` → no match
- `policy_status=QUARANTINED` → no match แม้ admin

### B. Actual writer lifecycle

ต้องเรียก write path จริงกับ Qdrant test collection ไม่ใช่จำลอง list:

- ACTIVE → QUARANTINED แล้ว role เดิมเห็นศูนย์ point
- broad ACL → narrow ACL แล้ว revoked role เห็นศูนย์; retained role ยังเห็น generation ใหม่
- assert delete/upsert ใช้ exact test collection และ exact source
- run นี้เป็น single-writer; concurrency/atomicity บันทึกเป็น deploy gate

เพื่อทำ test นี้โดยไม่ monkeypatch global แนะนำให้ `store_in_qdrant()` รับ `collection_name`, `rbac_lookup` และ `manifest_path` แบบ explicit dependency; default production-like values ต้องไม่ถูกใช้ใน P5b

### C. API auth + permission canaries

- API start ด้วย `AUTH_MODE=enforce` และ registry synthetic เท่านั้น
- no key → 401
- key ถูก + in-scope role → 200
- key ถูก + out-of-scope role → exact 403
- `UNCLASSIFIED` → admin-only
- missing ACL/stale schema/quarantine → ไม่มี standard role รวม admin retrieve ได้ตาม contract
- permission manifest ต้องมี positive ทุก allowed role และ negative ทุก denied role

`ask_eval.py:237-241` ยังสร้าง spoof pair จาก key สองตัวแรกเท่านั้น รอบ P5b ต้องสร้างอย่างน้อยหนึ่ง forbidden-role spoof ต่อ **ทุก role-scoped key** ตาม acceptance ที่ล็อกไว้ก่อนหน้า มิฉะนั้น auth VERIFIED ยังไม่ครอบ registry ทั้งชุด

Security gate ใช้ `/search` ได้ทั้งหมด จึงไม่จำเป็นต้องตั้ง Claude key หรือส่งข้อมูลออก Cloud ใน P5b รอบนี้

## Findings ที่ยังเป็น deploy/follow-up — ไม่ block การเริ่ม P5b

### M2 ยังไม่ควร mark ว่า legacy writer “closed” ทั้งระบบ

- `ocr_reingest.py` ตรวจเพียง 50 point แรก; mixed collection ที่ policy-v1 อยู่นอก sample ยังผ่าน
- guard ใน OCR กลืน exception ที่ไม่ใช่ `RuntimeError`; transient scroll failure จึง fail-open แล้วอาจไปเขียนต่อ
- `migrate_to_server.py` validate ทีละ batch หลังเริ่ม upload; malformed ใน batch หลังทำให้ปลายทางถูกเขียนบางส่วนแล้ว

P5b ไม่เรียก tools เหล่านี้จึงไม่ block run แต่ต้องคงเป็น deploy gate หรือแก้เป็น full/filter-based scan + fail-closed exception ก่อนเรียกว่า active-writer surface hardened

### M3 manifest ยังเป็น audit artifact ขั้นต้น ไม่ใช่ durable review workflow

`source_sha256` ใน `policy.py:322` เป็น hash ของชื่อ source ไม่ใช่ hash ของ content และ manifest สร้างหนึ่ง row ต่อ chunk แม้ docstring บอกต่อ source; `run_id` ละเอียดเพียงวินาทีและไฟล์ถูกเขียนหลัง Qdrant mutation

เพียงพอให้ offline run ไม่รายงาน quarantine เป็น success กำกวม แต่ยังใช้พิสูจน์ exact document generation/recovery ไม่ได้ จึงต้องคง durable quarantine workflow เป็น deploy gate ตาม handoff

### M4 stored-payload validator ยังมี type hole เล็ก

`validate_document_policy()` ใช้ `policy.acl_schema_version != 1`; Python ทำให้ `True == 1` ดังนั้น `validate_stored_payload()` อาจรับ schema version แบบ bool แม้ Qdrant filter จริงจะไม่ match ควรตรวจ `type(...) is int` เพื่อให้ migration validator สอดคล้องกับ M1

นี่ทำให้ point หายจาก retrieval ไม่ใช่ permission leak และ P5b direct conformance จะเห็นพฤติกรรมจริง จึงไม่ block การเริ่ม P5b แต่ควรแก้ก่อนใช้ migration tool

## Verification

- trace `policy.py` → `ingest.store_in_qdrant()` → Qdrant delete/upsert boundary
- trace legacy mutation paths: `ocr_reingest.py`, `retag_rbac.py`, `migrate_to_server.py`
- trace P5b harness/auth path: `ask_eval.py` → `/search` → `authorized_points()`
- `python test_policy.py` → **62/62 passed**
- `python test_auth.py` → **11/11 passed**; `load_api_keys()` integration ถูก SKIP เพราะ environment ไม่มี `anthropic`
- `python test_eval_contract.py` → **64/64 passed**
- `python test_ask_eval_harness.py` → **11/11 passed**
- `py_compile` ทั้ง 6 module → PASS
- ยังไม่ได้ start Docker/Qdrant และไม่ได้แก้ `STATUS.md` หรือ production state

## Final handoff

**GO P5b implementation/run** หลังทำ P5B-B1 target interlock ก่อน call แรก โดยใช้ dedicated test stack + unique collection + synthetic data + `AUTH_MODE=enforce`

ผลที่อนุญาตหลัง P5b:

- real conformance + lifecycle + permission + exhaustive auth ผ่านทั้งหมด → ประกาศ **P1 hardened เฉพาะ PoC local/synthetic/single-writer** ได้
- ผลใดเป็น LEAK/ERROR/INCONCLUSIVE หรือ auth ไม่ VERIFIED → FAIL และห้ามขยับไป deploy

production staging/backfill/atomic alias cutover, concurrent writer fencing, durable quarantine workflow และ legacy-writer closure ยังคงเป็น deploy gates ไม่ได้ถูก GO จาก review นี้
