# Codex Targeted Re-review — P2 Docker fix 3 (`52b92f8`)

วันที่รีวิว: 2026-08-05  
อ้างอิง: `KB_P2_DOCKER_FIX3_HANDOFF.md`, `52b92f8`  
ขอบเขต: build request → runner → iid/tag inspect → evidence extraction → atomic receipt และ adapter guard  
ข้อจำกัด: ไม่ resolve registry digest, ไม่เรียก Docker/model/Qdrant/network, ไม่แก้โค้ดหรือ `STATUS.md`

## Intent และทางที่เล็กกว่า

เป้าหมายคือให้ `build_receipt.json` มีความหมายเดียว: Docker build สำเร็จ, image identity/platform/tag ถูกต้อง และ evidence บังคับครบจาก image เดียวกัน

ทางที่เล็กที่สุดคือให้ `run_build()` มี validator เดียวหลัง extract ซึ่งรับเฉพาะ evidence exact schema แล้วค่อยเขียน receipt หาก step ใด raise/คืนค่าผิด ให้แปลงเป็น `build_failure.json` และห้ามมี receipt

## Verdict

**FIX-THEN-BUILD** — lifecycle rc/iid/tag หลักปิดแล้ว แต่ receipt ยังออก `SUCCEEDED` เมื่อ evidence ขาด/เป็น `None` และ exception จาก inspect/extract ยังหลุดออกนอก lifecycleโดยไม่มี failure record

## Findings

### B1 — incomplete evidence ยังสร้าง `SUCCEEDED` receipt ได้ (blocker)

**Finding:** `p2_docker_build.py:144-146` รับผล `extractor()` ทุก dictโดยไม่มี exact-key/type/value validation แล้วเขียน receiptทันที ขณะที่ `_docker_extract_evidence():167-173` ตั้งค่าของไฟล์ที่ `docker cp` ล้มเป็น `None`

**Why it matters:** image ที่ขาด model manifest, wheel manifest หรือ freeze listยังถูกประกาศสำเร็จได้ ทำให้คำว่า “receipt fail-closed + evidence bindingครบ” ไม่จริง

**Evidence:** mocked extractorคืน `{"model_file_manifest.sha256": None}` ผลคือ `run_build()` คืน `0` และสร้าง receipt `status=SUCCEEDED` ทั้งที่ขาด evidenceอีกสองไฟล์และค่าที่มีเป็น `None` ชุดทดสอบปัจจุบันยอมรับ extractorที่มีเพียง keyเดียว (`test_p2_dockerbuild.py:76-80`) จึงเขียวผิด contract

**Suggested change:** เพิ่ม `validate_extracted_evidence()` ก่อนเขียน receipt:

- exact keysต้องเท่ากับ basenameของ `EVIDENCE_FILES` ทั้งสาม ไม่มี missing/extra
- ทุกไฟล์ต้องมีอยู่จริง เป็น regular file, non-empty และ file SHA256 เป็น lowercase 64-hex
- `model_file_manifest.sha256` contentต้องเป็นหนึ่งบรรทัด 64-hex
- wheel manifestต้องไม่ว่างและไม่มี duplicate filename; freezeต้องไม่ว่าง
- validation fail → controlled failure code + `build_failure.json`, ไม่มี receipt

เพิ่ม tests: extractor `{}`, missing key, extra key, `None`, malformed hash, empty file และ valid exact set

### B2 — inspector/extractor exceptions หลุด lifecycleและไม่สร้าง failure record (blocker)

**Finding:** `p2_docker_build.py:139-146` ไม่ครอบ `inspector()` หรือ `extractor()` ด้วย exception boundary; real seamsสามารถ raiseจาก Docker command, JSON decode, filesystem หรือ hash I/O

**Why it matters:** buildอาจสำเร็จแต่ post-build verificationขัดข้อง แล้ว wrapper crashพร้อม `build_request=PENDING` โดยไม่มี receiptและไม่มี `build_failure.json` ผู้ปฏิบัติงานแยกไม่ออกว่าล้มขั้นไหนและอาจหยิบ tag/imageไปใช้ด้วยมือ

**Evidence:** mocked inspector raise `RuntimeError("inspect down")` → exceptionออกจาก `run_build()`, ไม่มี failure file และไม่มี receipt

**Suggested change:** ครอบทุก post-run seam (read iid, inspect, extract, validate evidence, receipt write) เป็น stage-aware controlled failure เช่น `INSPECT_FAILED`, `EXTRACT_FAILED`, `EVIDENCE_INVALID`, พร้อม reasonแบบไม่ใส่ข้อมูลลับ และรับประกันด้วย `finally` ว่าไม่มี success receiptเมื่อ stageใดล้ม เพิ่ม exception testsสำหรับ inspector/extractor/json/file I/O

### B3 — source binding silently omits missing contract files (blocker)

**Finding:** `source_hashes():68-69` ใช้ comprehensionพร้อม `if (root / name).is_file()` จึงละไฟล์ที่หายแทนที่จะ fail

**Why it matters:** โดยเฉพาะถ้า `Dockerfile.p2.dockerignore` หาย Dockerยัง buildได้แต่ contextกลับไปเป็นทั้ง repo >12 GB และ receiptยัง `SUCCEEDED` พร้อม source mapที่แค่ไม่มี keyนี้ นี่เปิด data-egress regressionเดิมกลับมา

**Evidence:** probe rootที่มีเพียง `Dockerfile.p2` คืน dictหนึ่ง keyโดยไม่ error

**Suggested change:** บังคับ exact setของ `SOURCE_FILES`; missing/non-file/unreadableต้อง REFUSED ก่อนเรียก runner และ source hash mapใน request/receiptต้องมี keyครบทุกตัว เพิ่ม testลบ `Dockerfile.p2.dockerignore` แล้ว runnerต้องไม่ถูกเรียก

### M1 — receiptยังไม่ให้ค่า RunPlan model manifestโดยตรง (major)

**Finding:** `_docker_extract_evidence()` เก็บ SHA256ของไฟล์ `model_file_manifest.sha256` แล้ว receiptใส่ภายใต้ `evidence_sha256` แต่ RunPlanต้องการ **ค่าภายในไฟล์** ซึ่งเป็น SHA256ของ model snapshot (`model_file_manifest_sha256`)

**Why it matters:** สองค่านี้เป็นคนละค่า—ค่าปัจจุบันเป็น hashของไฟล์ข้อความที่บรรจุ hashอีกชั้น หากนำ `evidence_sha256["model_file_manifest.sha256"]` ไปใส่ RunPlanจะเกิด double-hashและไม่ตรง metadataจาก model smoke

**Suggested change:** receiptควรแยกชัด:

- `model_file_manifest_sha256`: parsed/validated contentของไฟล์
- `evidence_file_sha256`: hashของ evidence filesทั้งสามเพื่อ audit

เพิ่ม testยืนยัน parsed model manifestเท่าค่าที่ RunPlanคาดและไม่เท่ากับ file hashโดยบังเอิญ

### M2 — run artifacts/logยังไม่ bindครบและ explicit `--out-dir` ยังชนกันได้ (major)

**Finding:** default run directoryเป็น UUIDและ anchorถูกต้อง แต่ `--out-dir` รับ relative/reused path (`p2_docker_build.py:189,197`) และ `run_build()` ลบ artifactsเดิมใน pathนั้น (`111-114`) โดยไม่ lock/reject non-empty; build log/context sizeก็ไม่ได้ถูก captureหรือ hashใน receipt

**Why it matters:** operatorสองคนหรือ rerun pathเดิมสามารถลบ/ทับ evidenceกัน และ handoffขอ build log/context sizeแต่ wrapperยังไม่สร้าง durable artifactให้ review

**Suggested change:** resolve relative outputใต้ `BUILD_ROOT`, require new/empty run directoryพร้อม atomic creation/lock, capture plain build logเป็นไฟล์และ bind hash+context-size evidenceใน receipt ห้าม overwrite runที่มี receiptแล้ว

## สิ่งที่ยืนยันว่าปิดแล้ว

- build failure rc≠0ไม่สร้าง success receipt; stale iid/receiptใน run pathถูกล้าง
- iid strict `sha256:<64hex>`; inspectตรวจ image ID, `linux/amd64` และ tagตรงกัน
- wrapperใส่ tag และ documented smokeใช้ validated iidโดยตรง จึงปิด compose rebuild ambiguity
- Dockerfile verify wheel hashesก่อน offline installและ runtimeใช้ wheelhouseเดียว
- context allowlist, base sentinel/digest validator, model expected-commit guardยังอยู่ครบ
- adapter N-key truncation/collisionปิดแล้ว
- adapter integration skipเฉพาะเมื่อ `qdrant_client`ไม่พบ; เมื่อ dependencyมีแล้ว unrelated ImportErrorไม่ถูกกลืนตาม code path

## Verification

รัน independently แบบ pure/offline:

- `test_p2_dockerbuild.py` — **25/25 PASS**
- `test_p2_pin.py` — **14/14 PASS**
- `test_p2_adapter.py` — **21/21 PASS**, integration SKIPเพราะ environmentนี้ไม่มี `qdrant_client`
- `test_p2.py` — **179/179 PASS**
- `test_p2_runplan.py` — **94/94 PASS**
- targeted probesยืนยัน: incomplete/`None` evidenceยังได้ receipt rc0; inspector exceptionไม่มี failure file; missing sourcesถูก omitเงียบ

ไม่มี Docker/model/Qdrant/network callระหว่าง review

## Go/No-Go

1. **Resolve `python:3.11-slim` amd64 digest: รอก่อน** จนปิด B1-B3 เพื่อไม่ให้ digestที่หาแล้วผลัก workflowเข้า buildทั้งที่ receiptยังรับหลักฐานไม่ครบ
2. **CPU fetch+verify Docker build: NO-GO** จน evidence exact schema + exception-to-failure + exact source setผ่าน tests
3. หลังปิด B1-B3: GO resolve digestจาก trusted registryและ GO build-onlyได้; ส่ง success receipt + evidence files + build log/context sizeกลับมา review
4. Model-load smoke/real M4/N-sweep/decision benchmarkยัง NO-GOตาม gateเดิม

Verdict สั้น: **FIX-THEN-BUILD — image identityปิดแล้ว แต่ success receiptยังไม่บังคับว่าหลักฐานจาก imageครบและอ่านได้จริง**
