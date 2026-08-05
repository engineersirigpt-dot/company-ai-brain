# Codex Review — P2 pinned build (`22af2be`)

วันที่รีวิว: 2026-08-05  
ขอบเขต: ตรวจ `Dockerfile.p2`, `docker-compose.p2.yml`, pin/fetch/smoke scripts และ dependency contract เท่านั้น  
ข้อจำกัดรอบนี้: ไม่แก้โค้ด, ไม่แก้ `STATUS.md`, ไม่ build/run Docker, ไม่โหลดโมเดล, ไม่แตะ Qdrant

## Verdict

**FIX-THEN-BUILD** — ตัว model pin และ offline snapshot contract มาถูกทาง แต่ยังไม่อนุมัติ `docker build` แม้จะเป็น fetch+verify อย่างเดียว จนกว่าจะปิด B1/B2 และกำหนดหลักฐาน image identity ให้ชัด

**Runner adapter: GO เขียนต่อได้ตอนนี้** แบบ pure/offline และควรแยก commit จาก Docker fixes ไม่จำเป็นต้องรอ Docker review ปิด

## Findings

### B1 — base/CUDA/dependency contract ยังไม่ reproducible พอสำหรับ evidence image (blocker)

`Dockerfile.p2:17` ยังเป็น placeholder `python@sha256:<<FILL-AT-BUILD...>>` แต่ comment ระบุให้เลือก CUDA base สำหรับ 4x RTX 4090 ขณะที่ Dockerfile สมมติว่าฐานมี `python`/`pip` อยู่แล้ว (`Dockerfile.p2:29,42`) และ `requirements.p2.txt:3-4` ยังไม่ได้ล็อก PyTorch CUDA wheel/index จริง

นอกจากนี้ requirements ล็อกเฉพาะ direct dependencies 5 ตัว แต่ยังไม่ล็อก transitive dependencies, wheel hashes และ package index จึงยังสร้าง environment คนละชุดได้ในคนละเวลา แม้ model SHA จะเหมือนกัน และการ `pip install` สองรอบ (`Dockerfile.p2:29,42`) อาจ resolve คนละ artifact ได้

ผลกระทบ: image อาจ build ผ่านแต่เป็น CPU torch, CUDA/driver ไม่เข้ากัน หรือ dependency tree ไม่เหมือน evidence run ครั้งก่อน จึงยังเรียกว่า reproducible image ไม่ได้

ต้องปิดก่อน build:

1. เลือก runtime target ให้ชัดว่า build นี้เป็น CPU compatibility image หรือ CUDA/GPU evidence image
2. pin base เป็น digest จริงที่มี Python/pip ตาม contract; ถ้าใช้ CUDA base ต้องติดตั้ง Python อย่าง deterministic หรือใช้ PyTorch/CUDA runtime base ที่ pin digest
3. ล็อก CUDA wheel source และ dependency closure ด้วย lock/constraints + hashes หรือสร้าง wheelhouse ครั้งเดียวแล้วติดตั้ง runtime แบบ offline จาก wheelhouse
4. fetch stage ควรใช้ dependency ชุดเล็กเท่าที่จำเป็น; อย่าโหลด torch stack เต็มสองรอบโดยไม่มีเหตุผล

### B2 — `MODEL_COMMIT` build arg เป็น control ปลอม (blocker)

`Dockerfile.p2:18,22` และ `docker-compose.p2.yml:19` ทำให้ผู้รันเข้าใจว่า `--build-arg MODEL_COMMIT=...` เป็นตัวกำหนด snapshot แต่ `p2_fetch_model.py:26` fetch จาก `p2_pin.MODEL_COMMIT` เท่านั้น และไม่ได้อ่าน/เทียบ build arg เลย

ตัวอย่าง: ผู้รันส่ง SHA อื่นผ่าน build arg แต่ image ยัง fetch `953dc6f...` และ build ผ่าน ทำให้ command/manifest ที่บันทึกไว้อาจไม่ตรงกับสิ่งที่ถูก bake จริง

แนวแก้ที่แนะนำ: ให้ `p2_pin.py` เป็น single source of truth จริง แล้วลบ `MODEL_COMMIT` build arg ออกจาก Dockerfile/compose/คำสั่ง build ทั้งหมด หรือ export arg เป็น environment และ fail ก่อน network fetch ถ้าค่าไม่เท่ากับ `p2_pin.MODEL_COMMIT` ห้ามปล่อยให้มีสอง source ที่ไม่ cross-check กัน

### M1 — วิธีเก็บ `image_digest` ยังไม่แน่นอนสำหรับ local-only image (major)

`docker-compose.p2.yml:7` ใช้ `RepoDigests[0]` แต่ local image ที่เพิ่ง build และยังไม่ได้ push มักไม่มี RepoDigest ทำให้ได้ค่าว่างหรือ index error ขณะที่ RunPlan ต้องการ `sha256:<64hex>`

ต้องนิยามให้ชัดว่า evidence ใช้:

- local immutable image ID/config digest จาก `docker image inspect ... {{.Id}}` หรือ BuildKit `--iidfile`; หรือ
- registry content digest หลัง push ไป registry ที่ควบคุมได้

สำหรับรอบ local/offline แนะนำใช้ local image ID และบันทึก build command + pinned base digest + model manifest ประกอบ อย่าเรียก tag `:pinned` ว่า digest

### M2 — smoke ยังไม่ cross-check manifest ที่ bake และใช้ `assert` เป็น evidence gate (major)

fetch stage เขียน `/opt/model_file_manifest.sha256` และ runtime copy เข้ามา (`Dockerfile.p2:45`) แต่ `p2_model_smoke.py` คำนวณ metadata จาก snapshotแล้วพิมพ์ออกมาโดยไม่อ่าน/เทียบกับไฟล์ที่ bake ไว้ ดังนั้นไฟล์ manifest ที่ผิดชุดหรือถูกเปลี่ยนจะไม่ถูกจับ

อีกทั้ง checks หลักใช้ `assert` (`p2_model_smoke.py:24-28` และ `Dockerfile.p2:48-53`) ซึ่งถูกปิดได้ด้วย Python optimization (`-O`/`PYTHONOPTIMIZE`) ไม่ควรเป็น fail-closed evidence gate

ก่อน model-load smoke ให้:

1. อ่าน baked manifest แล้วเทียบ exact กับ `meta["file_manifest_sha256"]`
2. ใช้ explicit exception/SystemExit แทน `assert`
3. ระบุว่า smoke ใช้ CPU หรือ GPU; ถ้าเป็น GPU smoke ต้อง pin device/runtime และบันทึก CUDA/driver/device metadata ส่วน benchmark ยังเป็น gate แยก

### M3 — runtime install ยังพึ่ง network ทั้งที่มี fetch stage (major, รวมปิดกับ B1 ได้)

คำว่า multi-stage ออฟไลน์ครอบเฉพาะ container ตอน run แต่ runtime stage ยัง `pip install` จาก network ใหม่ (`Dockerfile.p2:42`) จึงไม่ได้พิสูจน์ว่า Python environment มาจาก artifact set เดียวกับที่ตรวจไว้ใน fetch stage

ทางที่เรียบง่ายกว่าคือ build/download wheelhouse ที่ pin+verify เพียงครั้งเดียว แล้ว `COPY --from=...` มาติดตั้งด้วย `--no-index --find-links`; runtime execution คง `network_mode: none` ตามเดิม

## สิ่งที่ยืนยันว่าถูกทิศทาง

- pin ใช้ full immutable commit `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` และ tokenizer ใช้ snapshot เดียวกัน
- fetch ใช้ `snapshot_download(..., revision=<full SHA>)`, ไม่มี fallback ไป `main`
- ตรวจ resolved snapshot commit, required files 6 รายการ, real safetensors size และ canonical file manifest
- runtime ตั้ง Hugging Face/Transformers offline และ `network_mode: none`; compose profile กันการรันโดยไม่ตั้งใจ
- pure/offline regression ผ่าน: `test_p2_pin.py` **7/7**, `test_p2_runplan.py` **94/94**, `test_p2.py` **179/179**

ผลเทสต์เหล่านี้ยืนยัน pin constant และ decision contract แต่ยังไม่ยืนยันว่า Docker dependency/CUDA/image-digest contract ถูกต้อง

## คำตอบ 2 คำถามจาก handoff

### 1. อนุมัติ Docker build (fetch+verify, ยังไม่ run) หรือไม่

**ยังไม่อนุมัติ — FIX-THEN-BUILD** เพราะ B1/B2 มีผลต่อ identity และ reproducibility ของ image โดยตรง หลังปิด B1/B2 พร้อมกำหนด local image ID ตาม M1 แล้ว จึง **GO build fetch+verify only** ได้

หลัง build ยังเป็น **NO-GO** สำหรับ model-load smoke, real M4, canary, N-sweep และ decision benchmark จนผ่าน gate ของแต่ละขั้นและ Data Owner sign-off ตาม RunPlan เดิม

### 2. runner adapter เขียนตอนนี้หรือรอ Docker

**เขียนตอนนี้ได้เลย** เพราะเป็น pure/offline consumer ของ contract ที่ปิดแล้วใน `9afe8bf` และไม่ขึ้นกับ base image/CUDA/model download

ขอบเขตที่อนุมัติ:

- แปลง raw output จาก `p2_harness` เป็น dev/quality/latency evidence ที่ bind กับ root RunPlan
- join ด้วย `query_id` กับ frozen cases และถือ `intent_id`, `role`, `challenge_tags` จาก frozen cases เป็น authoritative ห้ามเชื่อ metadata จาก harness/caller
- บังคับ exact query set, duplicate/missing/extra rejection, arm completeness และ finite metrics
- สร้าง raw/selection digest ผ่านฟังก์ชัน canonical ของ `p2_runplan` ไม่ทำ digest/schema ซ้ำเอง
- ใช้ fake candidate provider/scorer และ synthetic fixtures ใน unit tests เท่านั้น
- output ต้องไม่ตั้ง `approved=true` หรือประกาศ decision; public approval surface ยังคงเป็น `decide_p2()` เท่านั้น

ยังไม่อนุมัติให้ adapter แตะ Docker/model/Qdrant, เติม human sign-off, สร้าง M4/canary evidence ปลอม หรือรัน decision benchmark

## Gate สำหรับ targeted re-review รอบถัดไป

อนุมัติ Docker build เมื่อหลักฐานครบทั้ง 5 ข้อ:

1. base image เป็น immutable digest จริงและ runtime target CPU/CUDA ถูกระบุ
2. torch/CUDA + dependency closure มี source/lock/hash ที่ทำซ้ำได้ และ runtime install จาก artifact set เดียว
3. `MODEL_COMMIT` มี source เดียว หรือ build arg ถูก assert เท่ากับ `p2_pin.py` ก่อน fetch
4. local image identity capture ใช้งานได้จริงและ mapping เข้า RunPlan ชัดเจน
5. baked model manifest ถูก cross-check แบบ explicit fail-closed; ไม่พึ่ง `assert`

เมื่อปิดครบ: **GO Docker build = fetch+verify เท่านั้น** และให้ส่ง build log, local image ID, resolved model SHA และ model file manifest กลับมา review ก่อน model-load smoke
