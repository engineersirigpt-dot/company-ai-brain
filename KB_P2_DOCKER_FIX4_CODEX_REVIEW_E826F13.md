# Codex Targeted Re-review — P2 Docker fix 4 (`e826f13`)

วันที่รีวิว: 2026-08-05  
อ้างอิง: `KB_P2_DOCKER_FIX4_HANDOFF.md`, `e826f13`  
ขอบเขต: exact source preflight → build lifecycle → image inspect → exact evidence extraction → receipt และ adapter guard  
ข้อจำกัดรอบรีวิว: ไม่ resolve registry digest, ไม่เรียก Docker/model/Qdrant/network, ไม่แก้โค้ดหรือ `STATUS.md`

## Verdict

**GO — resolve digest + CPU fetch/verify build only**

Blocker B1/B2/B3 และ M1 เดิมปิดแล้วใน actual `main()` path: success receiptเกิดได้ต่อเมื่อ sourceครบ, Docker rc=0, iid/tag/platformตรง, evidenceสามไฟล์ครบ/อ่านได้ และ model manifestถูก parseเป็นค่า RunPlanจริง

ยัง **NO-GO** สำหรับ model-load smoke, real M4, N-sweep และ decision benchmarkตาม gateเดิม

## Trace ที่ยืนยัน

### 1. Exact source/context preflight

`main()` → `run_build()` → `source_hashes()` (`p2_docker_build.py:194-200`)

- `source_hashes():75-87` เดินครบ `SOURCE_FILES` และคืน errorเมื่อไฟล์หาย/ไม่ใช่ regular file/อ่านไม่ได้
- errorถูกเปลี่ยนเป็น `REFUSED` ก่อนเรียก runner
- `Dockerfile.p2.dockerignore` อยู่ใน exact source set จึงปิด regressionส่งทั้ง corpusเข้า build context

### 2. Build/image identity

`run_build()` → runner → iid validate → image inspect (`203-224`)

- runner exception/non-zeroไม่สร้าง receipt
- iidต้องเป็น `sha256:<64hex>`
- inspectต้องยืนยัน `linux/amd64`, exact image ID และ tagเดียวกับ build
- documented smokeใช้ iidจาก receiptโดยตรง ไม่ trigger compose rebuild

### 3. Evidence exact schema

validated iid → `_docker_extract_evidence()` → `validate_extracted_evidence()` (`226-238`)

- exact filename setต้องเท่ากับ evidenceสามไฟล์ ไม่มี missing/extra
- ทุกไฟล์ต้อง non-empty/อ่านได้
- `model_file_manifest.sha256` contentต้องเป็น 64-hexและถูกเก็บเป็น `model_file_manifest_sha256`
- file hashesถูกแยกเป็น `evidence_file_sha256`; ไม่มี double-hash ambiguity
- validation error/exceptionสร้าง stage-aware failureและไม่มี success receipt

### 4. Receipt lifecycle

- receiptถูก atomic-writeหลังทุก gateผ่าน (`233-241`)
- status `SUCCEEDED`, image ID, source hashes, context bytes, build log hash, parsed model manifest และ evidence file hashesอยู่ใน artifactเดียว
- non-empty run directoryถูกปฏิเสธ จึงไม่ทับ evidenceรอบก่อน

### 5. Runner adapter

- N-key normalization/collisionยัง fail-closed
- integration sectionตรวจ `qdrant_client`แบบเจาะจง; ถ้า dependencyมีแล้ว import regressionอื่นจะ fail ไม่ถูกเปลี่ยนเป็น skip
- approval surfaceยังอยู่ที่ `decide_p2()` เท่านั้น

## Verification

รัน independently แบบ pure/offline:

- `test_p2_dockerbuild.py` — **33/33 PASS**
- `test_p2_pin.py` — **14/14 PASS**
- `test_p2_adapter.py` — **21/21 PASS**, integration SKIPเพราะ environmentนี้ไม่มี `qdrant_client`
- `test_p2.py` — **179/179 PASS**
- `test_p2_runplan.py` — **94/94 PASS**

ไม่ได้เรียก Docker/model/Qdrant/networkระหว่าง review

## Non-blocking hardening

สิ่งเหล่านี้ไม่ block CPU compatibility buildรอบนี้ แต่ควรปิดก่อนเรียก wrapper trackว่า production-grade:

1. `build_log_sha256` สามารถเป็น `null`ได้เมื่อใช้ injected runnerที่ไม่สร้าง log (`p2_docker_build.py:210,236`); real `main()` ใช้ `_runner_with_log()` จึงสร้างไฟล์จริง รอบ post-build reviewต้อง reject receiptทันทีหากค่าไม่ใช่ 64-hex และควรเพิ่ม validator/testถาวร
2. wheel manifest parserตรวจ non-empty/duplicate filenameแต่ยังไม่ตรวจว่าแต่ละแถวเป็น `<64hex> <wheel>`; Dockerfileมี `sha256sum -c` ก่อน installอยู่แล้ว จึงไม่ block build แต่ receipt validatorควรเข้มให้เท่ากันภายหลัง
3. `context_bytes` เป็นผลรวม allowlistที่คาด ไม่ใช่ byte countจาก BuildKitจริง ให้เทียบกับบรรทัด `transferring context` ใน `build.log` และเรียก fieldนี้ว่า declared context bytesหากนำไปใช้อัตโนมัติ
4. `git_commit` อาจว่างได้ถ้า git commandล้ม แต่ exact source hashesยัง bind build inputs รอบ post-buildต้องตรวจ commitเป็น full SHAและบันทึก dirty-stateแยก

## ขั้น build ที่อนุมัติ

1. Resolve `python:3.11-slim` จาก trusted registryสำหรับ `linux/amd64` เท่านั้น และเก็บ raw resolution outputพร้อม tag/index digest/platform digest ห้ามเดาหรือรับ SHAจาก LLMล้วน
2. รันคำสั่งผ่าน wrapperเท่านั้น:

   `python p2_docker_build.py --py-base python@sha256:<verified-digest>`

3. ถือว่า buildผ่าน gateนี้ต่อเมื่อ:

   - process exit 0
   - มี `build_receipt.json` status `SUCCEEDED` และไม่มี `build_failure.json`
   - `image_id`, `build_log_sha256`, `model_file_manifest_sha256` เป็น digestถูก format
   - `source_sha256` มี exact 9 keys
   - `evidence_file_sha256` มี exact 3 keysและทุกค่าเป็น 64-hex
   - build logยืนยัน contextเล็กและ base/model fetchตรง pin

4. ส่ง raw digest-resolution evidence, run directoryทั้งชุดและ build logกลับมา targeted reviewก่อน model-load smoke

## Gate หลัง build

- **GO ตอนนี้:** resolve trusted amd64 digest + Docker buildแบบ fetch/verify/wheelhouse
- **ยัง NO-GO:** execute image/model smoke, real M4, Qdrant/model benchmark, N-sweep, decision benchmark และการใช้ Data Owner sign-offจริง

Verdict สั้น: **GO BUILD-ONLY — receiptผูก source, imageและ evidenceครบพอสำหรับสร้าง CPU compatibility artifactแล้ว**
