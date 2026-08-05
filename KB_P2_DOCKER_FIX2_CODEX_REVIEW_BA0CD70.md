# Codex Targeted Re-review — P2 Docker fix 2 (`ba0cd70`)

วันที่รีวิว: 2026-08-05  
อ้างอิง: `KB_P2_DOCKER_FIX2_HANDOFF.md`, `ba0cd70`  
ขอบเขต: trace build wrapper → Dockerfile/context → image identity → documented smoke path และ runner adapter  
ข้อจำกัด: ไม่ build/run Docker, ไม่โหลดโมเดล, ไม่แตะ Qdrant/network, ไม่แก้โค้ดหรือ `STATUS.md`

## Intent และทางที่เล็กกว่า

เป้าหมายคือให้คำสั่งเดียวสร้าง CPU-compat image จาก base/model ที่ pin แล้ว พร้อม receipt ที่พิสูจน์ได้ว่า build สำเร็จและ image นั้นคือ image เดียวที่จะเข้าสู่ smoke

ทางที่เล็กกว่าคือแยก artifact สองชนิดชัดเจน:

- `build_request` — inputs ที่เขียนก่อน build (base/platform/model/source hashes)
- `build_receipt` — เขียนแบบ atomic **หลัง** Docker exit 0 และตรวจ image ID/tag/platform แล้วเท่านั้น

อย่าเรียกไฟล์ที่เขียนก่อน subprocess ว่า build manifest สำเร็จ เพราะมันทำให้ failure กับ success แยกไม่ออก

## Verdict

**FIX-THEN-BUILD** — B1/B2/M1/M2 เดิมด้าน context/base/model/wheelhouse ปิดในเส้นทางหลักแล้ว และ adapter M3 ปิดจริง แต่ wrapper ยังสร้างหลักฐานก่อนรู้ผล build และ image ที่ได้ไม่เชื่อมกับ compose smoke path

## Findings

### B1 — failed build ยังทิ้ง success-looking `p2_build_manifest.json` (blocker)

**Finding:** `p2_docker_build.py:57-64` เขียน manifest ก่อนเรียก `subprocess.call()` และคืน Docker exit code โดยไม่ finalize/rollback artifacts, ไม่อ่าน/validate `p2_image.id` และไม่บันทึก status

**Why it matters:** build ที่ fail สามารถเหลือ manifest ใหม่คู่กับ `p2_image.id` เก่าจากรอบก่อน หรือเหลือ manifest โดยไม่มี image แล้วถูกนำไปประกอบ RunPlan/evidence ผิดรอบได้ ชื่อคงที่ยังทำให้ rerun overwrite history

**Evidence:** mocked Docker ให้คืน exit `17` ผลคือ `main()` คืน 17 แต่ `p2_build_manifest.json` มีอยู่จริง, `p2_image.id` ไม่มี และ manifest ไม่มี field บอกว่า build ล้ม

**Suggested change:**

1. ใช้ run directory/temporary iid path ที่ unique และ reject/ล้างเฉพาะ artifact ของ run ปัจจุบันก่อน build
2. เขียน pre-build inputs เป็น `build_request.json` หรือ `.pending`
3. หลัง subprocess exit 0 เท่านั้น อ่าน iidfile แล้ว validate exact `sha256:<64hex>`
4. inspect image ให้ OS/Architecture ตรง `linux/amd64` และ tag ชี้ image ID เดียวกัน
5. เขียน `build_receipt.json` ไป temp แล้ว atomic rename โดยมี `status=SUCCEEDED`, return code, image ID, base digest, platform, model commit, git commit และ SHA256 ของ Dockerfile/requirements/build scripts
6. เมื่อ fail ห้ามมี success receipt; เก็บ failure log แยกได้แต่ต้องไม่ใช้ schema/ชื่อเดียวกับ evidence สำเร็จ

เพิ่ม unit test ด้วย fake subprocess สำหรับ success/fail/stale-iid/malformed-iid/inspect-mismatch ไม่ต้องใช้ Dockerจริง

### B2 — image จาก wrapper ไม่มี tag แต่ smoke path เรียก compose tag (blocker)

**Finding:** `p2_docker_build.py:35-38` ใช้ `docker build --iidfile ...` แต่ไม่มี `-t company-ai-brain/p2-reranker:pinned-cpu`; ขณะที่ `docker-compose.p2.yml:28` และคำสั่ง smoke บรรทัด 14 อ้าง tag นี้

**Why it matters:** build ผ่านแล้วจะได้ dangling image ที่มีแค่ iidfile ส่วน `docker compose run reranker ...` หา tag ไม่พบและอาจพยายาม build ใหม่จาก compose config ซึ่งไม่มี `PY_BASE` จริงและจะชน sentinel digest หรือใช้ artifact คนละตัวกับที่ review

**Suggested change:** เพิ่ม tag constant ใน wrapper (`--tag company-ai-brain/p2-reranker:pinned-cpu`) แล้วหลัง build inspect ว่า tag → exact iid; หรือเลิกใช้ compose smoke แล้วสั่ง `docker run --network none <validated-iid> ...` โดยแก้เอกสารให้เหลือ path เดียว ห้ามมีสอง path ที่อาจใช้คนละ image

### M1 — integration guard กลืน `ImportError` ทุกชนิด ไม่ใช่เฉพาะ optional Qdrant dependency (major)

**Finding:** `test_p2_adapter.py:112-119` ครอบ imports ทั้ง `policy`, `p2_reranker`, `p2_harness` ด้วย `except ImportError` แล้วเปลี่ยนทุกกรณีเป็น SKIP

**Why it matters:** ถ้า `p2_harness` มี typo/ลบ internal module/เกิด ImportError regression จริง test จะเขียวแบบ skip เหมือนกรณีไม่มี `qdrant_client`

**Suggested change:** ตรวจ optional dependency แบบเจาะจงก่อน (`importlib.util.find_spec("qdrant_client")`) แล้ว skip เฉพาะเมื่อ package นี้ไม่มี; ถ้ามี dependency แล้ว import `p2_harness` ล้ม ให้ test fail ตามปกติ เพิ่ม subprocess test ที่ทำให้ unrelated import ล้มแล้วต้องไม่ถูกนับเป็น skip

### M2 — post-build evidence extraction/verification ยังไม่มี authoritative path (major)

**Finding:** Dockerfile สร้าง `/opt/wheelhouse.manifest.sha256`, `/opt/wheelhouse.freeze.txt` และ `/opt/model_file_manifest.sha256` แต่ wrapperบันทึกเพียง base/platform/model inputs (`p2_docker_build.py:41-43`) และไม่ดึง/ผูกสาม artifact นี้กับ image ID

**Why it matters:** handoff ขอส่ง wheel/model manifests กลับมา review แต่ implementation ยังปล่อยให้คนดึงเองภายหลัง เสี่ยงหยิบจาก container/image คนละรอบ และ wheel manifestถูกสร้างแต่ไม่ `sha256sum -c` ก่อน install

**Suggested change:** กำหนด post-build receipt flow ก่อน buildจริง เช่น create container จาก **validated iid โดยไม่ start**, copy evidence filesออกมา, verify wheel manifest หรือเพิ่ม verify ใน Dockerfileก่อน install แล้ว hash/copy outputsเข้า run directory จากนั้น bind digest ของ outputsทั้งหมดใน success receipt การ extract นี้ยังไม่ใช่ model smokeและไม่ต้อง execute model

### N1 — build wrapper ผูกกับ caller working directory และ output ชื่อคงที่ (non-blocking แต่ควรปิดพร้อม B1)

**Finding:** `p2_docker_build.py:19,38,57` ใช้ relative paths (`Dockerfile.p2`, `.`, manifest/iid filenames) โดยไม่ anchor กับ directoryของ script

**Why it matters:** เรียก wrapperจาก directoryอื่นอาจส่ง contextผิดหรือเขียน receiptผิดที่ และ fixed filenamesชนกันเมื่อ rerun/concurrent

**Suggested change:** resolve repo rootจาก `Path(__file__).resolve().parent`, ส่ง `cwd=repo_root`, ใช้ run ID/output directory explicit และห้าม concurrent overwrite

## สิ่งที่ trace แล้วยืนยันว่าปิดจริง

- **Docker context B1 เดิม:** `Dockerfile.p2.dockerignore` ใช้ allowlist เฉพาะ Dockerfile/requirements/5 scriptsที่ COPY; raw corpus, `.git`, `.venv`, key artifactไม่อยู่ใน contextตาม design
- **Base B2 เดิม:** floating defaultถูกแทนด้วย unpullable sentinel; wrapperรับเฉพาะ `python@sha256:<64hex>` ที่ไม่ใช่ zerosและส่ง `--platform linux/amd64`
- **Model guard M1 เดิม:** environment absent ใช้ source pinได้ แต่ blank/whitespace/mismatch SystemExitก่อน fetch
- **Wheelhouse M2 เดิม:** download binary-only, runtime install `--no-index` จาก wheelhouseเดียว และสร้าง wheel hashes + freeze list; full hash lockยัง deferไป CUDA evidence imageตาม scope
- **Adapter M3:** `_coerce_n_key` reject float/noncanonical/bool/nonpositive และตรวจ normalized collisionก่อน assign; silent truncation/overwriteปิดแล้ว
- **Adapter approval boundary:** frozen casesยังเป็น authoritative และ adapterไม่สร้าง `approved`/decision

## Verification

รัน independently แบบ pure/offline:

- `test_p2_pin.py` — **20/20 PASS**
- `test_p2_adapter.py` — **21/21 PASS**, integration SKIPเพราะ environmentนี้ไม่มี `qdrant_client`
- `test_p2.py` — **179/179 PASS**
- `test_p2_runplan.py` — **94/94 PASS**
- mocked failed-build probe — ยืนยัน B1: Docker rc=17 แต่ manifestยังอยู่, iidไม่มี, commandไม่มี tag

ไม่ได้เรียก Docker/model/Qdrant/networkระหว่างรีวิว

## Go/No-Go

1. **Resolve base digest ตอนนี้: รอก่อน** — ปิด B1/B2 wrapper lifecycleก่อน แล้วค่อย resolveจาก trusted registryสำหรับ `python:3.11-slim` + `linux/amd64` พร้อมเก็บ raw resolution evidence ห้ามเดา SHA
2. **CPU fetch+verify Docker build: NO-GO** จน success receipt fail-closedและ image pathเชื่อมกับ smoke tag/iidเดียวกัน
3. หลังแก้ B1/B2: **GO build only** ได้; ยังคง NO-GO model smoke/real M4/N-sweep/decision benchmarkตาม gateเดิม
4. **Runner adapter:** M3ผ่าน; ปิด M1 test guardก่อนเรียก trackนี้ hardenedสำหรับ evidence

Verdict สั้น: **FIX-THEN-BUILD — ตัว Dockerfileพร้อมขึ้นมากแล้ว แต่ wrapperยังแยก failed buildออกจาก successful evidenceไม่ได้**
