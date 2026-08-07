# Codex review — P2 M4 isolation/scorer adapter @ `afe62a7`

**Verdict: FIX-THEN-GO**

รับรองได้เฉพาะ **offline/injectable mechanics slice**: test seam ทำงานตาม contract และ capstone พิสูจน์ orchestration flow ได้ แต่ **ยังไม่ GO real M4a synthetic run** เพราะ concrete driver/scorer path ยังมี 3 blocker ที่เข้า freeze exceptions โดยตรง (แตะ production, false PASS, cleanup กระทบรอบถัดไป), 1 concrete-path blocker และ 1 runtime mismatch ที่ทำให้ model จริงผ่าน gate ไม่ได้

ไม่ได้เสนอเปิด hardening loop ใหม่ของ safety/provenance v1; findings ด้านล่างจำกัดอยู่ที่ slice ใหม่และ 4 exceptions ที่ `STATUS.md` อนุญาตเท่านั้น

## Intent / simpler path

เป้าหมายที่ถูกต้องคือ: สร้าง Qdrant ชั่วคราวซึ่งพิสูจน์ว่าไม่ใช่ production, ให้ provider/oracle/scorer ใช้ target เดียวกัน, รัน synthetic M4a และ publish PASS เฉพาะเมื่อ cleanup ยืนยันแล้ว

ทางที่เรียบง่ายและลด trust seam กว่าโครงปัจจุบันคือให้ **pinned evaluator/scorer container หนึ่งตัว** รันอยู่ใน Docker internal network เดียวกับ Qdrant แล้วใช้ concrete `QdrantSession` ที่ห่อ standard `QdrantClient`; host controller มีหน้าที่ create/inspect/cleanup และรับ artifact หลัง runner จบเท่านั้น วิธีนี้แก้ host-DNS problem, ผูก runtime image ได้จริง และไม่ต้องให้ `client_factory`/metadata self-attest หลายชั้น

## Findings

### B1 — Qdrant ถูก mutate ก่อนพิสูจน์ target และ production guard เป็น self-report

**ตำแหน่ง:** `p2_m4_isolation.py:145-168`

`DockerQdrantDriver.provision()` สร้าง `_client` แล้วเรียก `recreate_collection()` ที่บรรทัด 156 ทันที จากนั้น `QdrantDockerIsolation.provision()` จึงค่อยเรียก `endpoint_is_production()` หลัง driver คืน handle แล้ว (`p2_m4_isolation.py:46-59`) ดังนั้น guard ไม่ใช่ fail-before-mutate

ยิ่งกว่านั้น `endpoint_is_production()` เช็กเพียงว่า string ที่ driver สร้างเองอยู่ใน caller-supplied set หรือไม่; default set ว่าง และไม่มีการยืนยันว่า client ที่ `client_factory()` คืนมาชี้ container/endpoint นั้นจริง หาก factory misbind ไป production การสร้าง collection/marker/seed จะไปแตะ production แต่ guard ยังคืน `False`

**Fault probe:** injected client ถูกเรียก `recreate_collection(...)` แล้ว driver รายงาน `endpoint_is_production=False` โดยไม่มี target observation ใด ๆ

**ต้องปิดก่อน real run:** แยก infra provision ออกจาก Qdrant mutation; สร้าง session จาก exact observed endpoint และตรวจ transport-derived target/container identity ก่อน Qdrant write แรก ห้ามใช้ generated URL หรือ empty denylist เป็นหลักฐานว่าไม่ใช่ production

### B2 — cleanup failure ถูกกลืน ทำให้ runner publish PASS ทั้งที่ resource อาจค้าง

**ตำแหน่ง:** `p2_m4_isolation.py:179-187`, consumer ที่ `p2_m4_runner.py:205-220`

`DockerQdrantDriver.teardown()` catch แล้วทิ้ง exception ของ container, volume และ network ทุกตัว จึงคืน success เสมอ แต่ runner อาศัย exception จาก `iso.teardown()` เป็นเงื่อนไขห้าม publish ผลคือ Docker cleanup ล้มทั้งสามคำสั่งก็ยังผ่านไปสร้าง `PUBLISHED/PASS` ได้ และ resource เก่าอาจรบกวน run ถัดไป

**Fault probe:** บังคับให้ `docker rm`, `volume rm`, `network rm` ทั้งหมด raise; `DockerQdrantDriver.teardown()` ยัง `RETURNED_CLEAN` หลังกลืน error 3 ตัว

**ต้องปิดก่อน real run:** พยายาม cleanup ทุก resource แต่สะสม error, verify ว่าทรัพยากรหายจริง แล้ว raise aggregate/cleanup-unconfirmed หากยืนยันไม่ได้; "not found" หลังลบซ้ำรับเป็น idempotent success ได้ แต่ error อื่นห้ามกลืน

### B3 — class ที่เรียก `DockerQdrantDriver` ยังไม่ใช่ real executable path

**ตำแหน่ง:** `p2_m4_isolation.py:135-177`

มีสองปัญหาที่ทำให้ next-step real run ใช้ class นี้ตรง ๆ ไม่ได้:

1. container อยู่บน network `--internal` และไม่ publish port แต่ `client_factory(endpoint)` รันใน Python process ปัจจุบันโดยใช้ hostname `m4qd-<token>`; code ไม่ได้ attach process/evaluator container เข้า network นั้น ดังนั้น host runner resolve/connect target นี้ไม่ได้
2. installed `QdrantClient` ไม่มี `upsert_marker`, `read_marker`, `seed`; `recreate_collection` ต้องใช้ `vectors_config` และ `count()` คืน `CountResult` ไม่ใช่ int ตรง ๆ ปัจจุบัน class จึงต้องการ facade ที่ยังไม่มีและไม่มี test ของ real API shape

**ต้องปิดก่อน real run:** สร้าง concrete session/facade ด้วย standard Qdrant operations (`VectorParams`, `upsert`, `retrieve`, `.count`) และทำให้ evaluator/scorer process อยู่บน exact internal network; pin Qdrant image ด้วย immutable digest แทน default `qdrant/qdrant:latest`

### B4 — fake scorer สามารถออก public-valid `PUBLISHED/PASS` และ image digest ยังไม่ได้ observe จาก runtime

**ตำแหน่ง:** `p2_m4_scorer.py:30-56`, `p2_m4_harness.py:62-85`, `test_p2_m4_isolation.py:129-175`, `p2_m4_runner.py:191-217`

`assert_scorer_matches_plan()` ตรวจ metadata ที่ scorer รายงานเองเท่านั้น Test capstone ใช้ `PinnedScorer` ปลอมที่คืน metadata ตาม plan แล้วได้ bundle ซึ่ง public validator รับและมี `scorer_kind=pinned-cross-encoder`, `status=PASS` ขณะ `image_digest` ถูกคัดจาก RunPlan ไม่ได้สังเกตจาก runtime จริง

การใช้ fake เป็น unit test ถูกต้อง แต่ artifact รูปเดียวกับ real evidence ทำให้มีเส้นทาง false PASS ซึ่งตรง freeze exception #3

**ต้องปิดก่อน real run:** แยก mechanics artifact ให้เป็น non-evidence/test-only ที่ public M4 gate ปฏิเสธ หรือให้ operational launcher เป็นผู้สร้าง concrete scorer เอง, observe runtime image digest + loaded model manifest แล้ว bind ค่าดังกล่าวกับ receipt; real evidence path ห้ามรับ arbitrary loader/ports ที่ self-report ว่า pinned

### M1 — real scorer metadata ชน RunPlan dtype แบบ exact

**ตำแหน่ง:** `p2_reranker.py:84-91,146-150`, `p2_m4_scorer.py:44-56`

RunPlan/tests ใช้ `dtype="float32"` แต่ real loader บันทึก `str(model.dtype)` ซึ่ง smoke เดิมให้ค่า `"torch.float32"`; `validate_scorer_metadata()` เทียบ `inference_config` ทั้ง dict แบบ exact จึง reject scorer จริงก่อน provision

**Fault probe:** สร้าง `PinnedCrossEncoder` ด้วย metadata แบบ real (`torch.float32`) เทียบ plan (`float32`) ได้ `M4ScorerError: inference_config != M4RunRequest`

**แก้:** กำหนด canonical dtype representation จุดเดียวทั้ง RunPlan, loader และ smoke receipt (เช่น normalize `torch.float32` → `float32`) แล้วเพิ่ม regression test ที่ใช้ metadata shape จาก real smoke ไม่ใช่ fake ซึ่ง copy `IC` จาก plan

## Verification

- `test_p2_m4_isolation.py` — **22/22 PASS**
- `test_p2_m4_scorer.py` — **7/7 PASS**
- `test_p2_m4_runner.py` — **44/44 PASS**
- inspected installed Qdrant client signatures: standard methodsตาม B3; custom marker/seed methodsไม่มี
- offline fault probes ยืนยัน B1, B2 และ M1 ตามรายละเอียดด้านบน
- ไม่รัน Docker, Qdrant หรือ model จริง; ไม่แก้ source/tests/`STATUS.md`

## Gate

- **offline/injectable mechanics:** ACCEPTED
- **real M4a synthetic preparation/run:** **NO-GO จน B1-B4 + M1 ปิดและ targeted re-review ผ่าน**
- **M4b / N-sweep / ข้อมูลจริง:** NO-GO ตาม Data Owner gate เดิม
- **safety/provenance v1:** ยัง FROZEN; review นี้ไม่เปิดประเด็นนอก exceptions
