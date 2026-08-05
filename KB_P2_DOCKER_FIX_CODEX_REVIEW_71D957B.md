# Codex Targeted Review — P2 Docker fix (`71d957b`)

วันที่รีวิว: 2026-08-05  
อ้างอิง: `KB_P2_DOCKER_FIX_HANDOFF.md`, `eb154e1`, `71d957b`, runner adapter `18ae84c`  
ขอบเขต: review เท่านั้น — ไม่แก้โค้ด/`STATUS.md`, ไม่ build/run Docker, ไม่โหลดโมเดล, ไม่แตะ Qdrant/network

## Intent และทางที่เล็กกว่า

เป้าหมายคือสร้าง CPU-compatibility image ที่ fetch โมเดลจาก immutable SHA, freeze dependency artifacts เป็น wheelhouse และติดตั้ง runtime แบบ offline เพื่อให้ตรวจ build identity ได้ก่อน model smoke

ทางที่เล็กและ fail-closed กว่า build arg หลายตัวคือ: ใช้ `p2_pin.py` เป็น model source เดียว, pin base digest ไว้ใน Dockerfile/build manifest ที่ commit ได้ และใช้ Dockerfile-specific allowlist context แทนส่งทั้ง repo เข้า builder

## Verdict

**FIX-THEN-BUILD** — B2/M2/M3 เดิมปิดได้ในเส้นทางหลัก แต่ยังมี blocker ใหม่ 2 ตัวที่ build path จริง: context ไม่มี ignore และ `PY_BASE` ไม่ได้ถูกบังคับเป็น digest/platform ตามที่ handoff อ้าง

Runner adapter: **scope ถูกทิศทาง แต่ FIX-BEFORE-EVIDENCE** สำหรับ N-key normalization และ test isolation; สองข้อนี้ไม่ block การแก้ Docker

## Findings

### B1 — Docker ส่งทั้ง repo เข้า build context รวม raw corpus/key artifact (blocker)

**Finding:** `docker-compose.p2.yml:20` ใช้ `context: .` แต่ไม่มี `.dockerignore` หรือ `Dockerfile.p2.dockerignore`

**Why it matters:** ก่อน `COPY` แบบเจาะจงใน Dockerfile จะทำงาน Docker/BuildKit ต้องรับ build context ก่อน ปัจจุบัน workspace มีประมาณ:

- `info/` **5.55 GB** — raw document corpus
- `.git/` **5.09 GB**
- `.venv/` **1.64 GB**
- `qdrant_storage/`, `parsed_output/`, `tmp/` และ `api_keys.p5b.json`

รวมมากกว่า 12 GB ทำให้ build ช้ามาก และถ้า builder ไม่ได้อยู่ local เครื่องเดียวกันจะเกิด data egress ของข้อมูลที่ไม่เกี่ยวกับ P2 แม้สุดท้ายไฟล์เหล่านั้นไม่ถูก `COPY` เข้า image

**Suggested change:** เพิ่ม `Dockerfile.p2.dockerignore` แบบ allowlist (`**` ก่อน แล้วเปิดเฉพาะ Dockerfile, requirements และ `p2_pin/p2_reranker/p2_fetch_model/p2_verify_snapshot/p2_model_smoke`) หรือย้าย build artifacts ไป context แยกที่มีเฉพาะไฟล์จำเป็น จากนั้นตรวจ `docker build --no-cache --progress=plain` ว่า context มีขนาดเล็กตามคาด ห้ามแก้ด้วย blacklist รายโฟลเดอร์เพราะไฟล์ใหม่จะหลุดเข้ามาอีก

### B2 — `PY_BASE` ยังไม่ fail-closed และยังไม่ล็อก platform (blocker)

**Finding:** `Dockerfile.p2:20-21` ระบุว่า evidence build ต้อง override digest แต่ default ยังเป็น `python:3.11-slim`; ถ้าผู้รันลืม `--build-arg` image จะ build ด้วย floating tag สำเร็จ ไม่มี code path ใดยืนยันว่าค่าอยู่ในรูป `python@sha256:<64hex>` นอกจากนี้ command/compose ยังไม่ล็อก `linux/amd64`

**Why it matters:** handoff อ้างว่า “PY_BASE digest override บังคับ” แต่ path จริงไม่บังคับ และ digest ของ tag อาจเป็น multi-architecture index ซึ่งเลือก child image ต่างกันตามเครื่อง ทำให้ base/wheels/image ID ต่างกันโดยไม่เห็นใน RunPlan

**Suggested change:** ก่อน resolve digest ให้ล็อก target เป็น `linux/amd64` ให้ชัด แล้วเลือกหนึ่งทาง:

1. แนะนำ: resolve digest แล้ว pin `FROM python@sha256:...` ใน source/build manifest โดยตรง ไม่เปิด floating default; หรือ
2. เอา default ออก + ใช้ build wrapper ที่ reject ค่าไม่ใช่ digest ก่อนสั่ง Docker พร้อมบันทึก platform และ base digest ลง build evidence

เพิ่ม test/lint ที่พิสูจน์ว่า build command ไม่มีทางสำเร็จด้วย tag ลอย ไม่ใช่อาศัย comment/manual discipline

### M1 — `MODEL_COMMIT` guard ยังยอม blank value ให้ผ่าน (major)

**Finding:** `p2_fetch_model.py:27-30` ใช้ `if expect and expect != PIN.MODEL_COMMIT` ดังนั้น `P2_EXPECT_COMMIT=""` ถูกมองเหมือนไม่มีค่าและ fetch จาก pin ต่อได้ ขณะที่ Dockerfileประกาศ environment variable นี้เสมอ

**Why it matters:** path ปกติที่ส่ง SHA ถูกต้องปิด B2 เดิมจริง แต่ malformed/blank build arg ยังไม่ fail-closed และ build command ที่บันทึกอาจไม่ตรงกับ contract

**Suggested change:** ถ้าจะเก็บ build arg ให้แยก `None` ออกจาก empty: เมื่อ variable มีอยู่ต้องเป็น full 40-hex และเท่ากับ `p2_pin.MODEL_COMMIT`; เพิ่ม test blank/whitespace/malformed หรือเอา build arg ออกทั้งหมดแล้วให้ `p2_pin.py` เป็น source เดียวจริง

### M2 — wheelhouse ปิด network install แต่ยังไม่ใช่ reproducible lock (major; ไม่ block CPU compatibility build หลัง B1/B2)

**Finding:** `Dockerfile.p2:41-42` resolve transitive dependencies สดด้วย `pip download --extra-index-url` และไม่มี hashes/manifest; `requirements.p2.txt` pin เฉพาะ direct dependencies

**Why it matters:** wheelhouse ทำให้ runtime install offline จาก artifact set เดียวกันจริง จึงปิด M3 เดิม แต่ build ซ้ำต่างวันยัง resolve transitive wheels คนละชุดได้ และคำว่า “freeze closure” ใช้ได้เฉพาะ image ที่เพิ่งสร้าง ไม่ใช่ reproducible rebuild

**Suggested change:** สำหรับ CPU compatibility build รอบนี้ให้บันทึก wheel filename+SHA256, `pip freeze`, base digest และ local image ID เป็น evidence พร้อมใช้ `pip download --only-binary=:all:` เพื่อ fail หากมี sdist สำหรับ CUDA/timed evidence image ค่อยยกระดับเป็น full `--require-hashes` lock ตาม gate เดิม

### M3 — runner adapter ทำ `int(k)` แล้ว truncate/overwrite N key เงียบ ๆ (major, fix before real evidence)

**Finding:** `p2_evidence_adapter.py:89-95` normalize key ด้วย `int(k)` โดยไม่ตรวจ canonical form หรือ collision

**Evidence:** probe `{10.5: {...0.1...}, "10": {...0.9...}}` ถูกแปลงเป็น `{10: {...0.9...}}` โดยไม่มี error — key `10.5` ถูก truncate และข้อมูลชุดแรกถูก overwrite

**Why it matters:** adapter อาจสร้าง dev evidence ที่ digest self-consistent แต่ไม่ได้แทน raw sweep จริง แล้ว validator ชั้นถัดไปไม่เห็นข้อมูลที่ถูกทิ้ง

**Suggested change:** รับเฉพาะ positive `int` ที่ไม่ใช่ bool หรือ canonical decimal string ที่แปลงกลับได้ exact; reject float/whitespace/sign/exponent และ reject normalized-key collision ก่อน assign เพิ่ม regression tests สำหรับ `10.5`, `"10.0"`, `" 10"`, `True`, และ `{10, "10"}`

### M4 — `test_p2_adapter.py` ไม่ pure/offline บน clean environment ตามที่อ้าง (major test isolation)

**Finding:** `test_p2_adapter.py:17` import `p2_provider` แต่ไม่ได้ใช้งาน และ import นี้ลาก `qdrant_client` เข้ามา

**Evidence:** independent run หยุดที่ `ModuleNotFoundError: qdrant_client` ก่อนแตะ adapter tests แม้ test ใช้ fake Qdrant/MockScorer ทั้งหมด

**Suggested change:** ลบ unused `p2_provider` import หรือแยก integration test ออก แล้วเพิ่ม N-key regressions จาก M3 ชุด pure ควรรันได้ด้วย dependency ขั้นต่ำของ adapterจริง

## Trace ที่ยืนยันว่าปิดแล้ว

- **Docker B2 main path:** `MODEL_COMMIT` → `P2_EXPECT_COMMIT` (`Dockerfile.p2:22-31`) → `_assert_expected_commit()` ก่อน import/fetch network (`p2_fetch_model.py:33-38`) ค่าที่ไม่ตรงถูก SystemExit
- **M2 smoke path:** loader ใช้ local snapshot + immutable revision → metadata recompute manifest → `p2_model_smoke.py:46-49` เทียบ baked manifest exact และใช้ SystemExit ไม่ใช้ assert
- **M3 runtime path:** prep download closure ไป `/wheels` → runtime copy wheelhouse → `pip install --no-index --find-links` (`Dockerfile.p2:40-55`) runtime install ไม่เรียก index
- **M1 image identity:** เปลี่ยนจาก `RepoDigests[0]` เป็น local image ID/config digest (`docker-compose.p2.yml:8-12`) เหมาะกับ local-only image
- **Runner approval boundary:** adapter join identity/tags จาก frozen cases และ output ไม่มี `approved`; `decide_p2()` ยังเป็น approval surface เดียว

## Verification

รัน independently ใน environment นี้:

- `test_p2_pin.py` — **12/12 PASS**
- `test_p2.py` — **179/179 PASS**
- `test_p2_runplan.py` — **94/94 PASS**
- `test_p2_adapter.py` — **ยังยืนยัน 15/15 ซ้ำไม่ได้** เพราะ unused import ทำให้ขาด `qdrant_client` ตาม M4 (ผล 15/15 ใน handoff ไม่ได้ถูกหักล้าง แต่ test isolation claim ยังไม่จริง)

ไม่มี Docker/model/Qdrant/network run ระหว่างรีวิว

## คำตอบ Go/No-Go

1. **Docker build ตอนนี้: NO-GO** — ปิด B1 build-context allowlist และ B2 base digest/platform fail-closed ก่อน
2. หลังปิด B1/B2: **GO CPU fetch+verify+wheelhouse build** ได้ โดยยังเป็น compatibility artifact ไม่ใช่ latency/decision evidence; ส่ง build log, context size, base digest+platform, wheel manifest, local image ID, resolved model SHA และ model manifest กลับมา review
3. **Model-load smoke / real M4 / N-sweep / decision benchmark: NO-GO** ตาม gate เดิม
4. **Runner adapter:** architecture/scope ผ่าน แต่ปิด M3/M4 ก่อนใช้ผลิต evidence จริง

## ควรหา `python:3.11-slim` digest ตอนนี้ไหม

**รอก่อน** — ต้องล็อก `linux/amd64` และรูปแบบ pin ใน source/build wrapperจาก B2 ให้จบก่อน มิฉะนั้นเสี่ยงเลือก manifest-list/platform identity ผิดหรือได้ digest ที่ไม่มี enforcement

หลัง targeted re-review ผ่าน ให้ resolve digest จาก registry/Docker tooling ทันทีก่อน build, บันทึก tag ณ เวลา resolve + index/platform digest + OS/architecture และ pin ค่านั้นใน artifact ที่ review/commit ได้ ไม่ควรเก็บไว้เป็นข้อความแชตหรือ CLI argumentอย่างเดียว
