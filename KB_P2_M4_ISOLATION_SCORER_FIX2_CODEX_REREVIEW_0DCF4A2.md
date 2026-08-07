# Codex targeted re-review — isolation/scorer FIX2 @ `0dcf4a2`

**Verdict: FIX-THEN-GO**

Targeted result:

- **B1 driver identity core: CLOSED** — endpoint มาจาก Docker-inspected network membership/IP ก่อนสร้าง session และก่อน `recreate_collection()`
- **B1/B4 launcher integration: OPEN** — scorer image digest ถูกใช้เป็น Qdrant image และรูปแบบ digest ขัดกับ RunPlan contract
- **B4 runtime binding: OPEN** — inspect container ที่ caller ระบุยังไม่พิสูจน์ว่า scorer ถูกโหลด/รันใน container นั้น

ไม่มี finding นอก B1/B4 และไม่เปิด safety/provenance hardening loop ใหม่

## Simpler path

แทน helper แยกสามตัว ให้สร้าง real entry point เดียว `run_m4a_locked(...)` ที่รับ **Qdrant image ref แยกจาก scorer image digest**, สร้าง/ระบุ evaluator container จากตัว controller เอง, รัน scorer+M4 runner ภายใน container นั้น และเป็นผู้ประกอบ ports/เรียก `run_m4a` เอง วิธีนี้ทำให้ attestation เป็น load-bearing และตัดช่อง caller จับคู่ container A กับ scorer process B

## Findings

### B1/B4 — RunPlan ที่ valid ใช้ launcher ไม่ได้ และ scorer image ถูกส่งไปเปิดเป็น Qdrant

**ตำแหน่ง:** `p2_m4_launch.py:44-55`, `p2_m4_isolation.py:145-163`, contract ที่ `p2_eval.py:54-55` / `p2_runplan.py:163-164`

RunPlan กำหนด `image_digest` เป็น local scorer/evaluator image ID รูป `sha256:<64hex>` แต่ `build_locked_isolation()` บังคับให้ค่าเดียวกันมี `@sha256:` แล้วส่งเข้า `DockerQdrantDriver.image_digest`

ผลมีสองชั้น:

1. RunPlan ที่ผ่าน `validate_run_plan()` (`sha256:...`) ถูก `build_locked_isolation()` ปฏิเสธ
2. ค่าแบบ `company-ai-brain/p2-reranker@sha256:...` ที่ launcher test ใช้ไม่ผ่าน RunPlan validator และหาก bypass จะถูก `docker run` เป็น **Qdrant container image** ทั้งที่เป็น reranker/scorer image

**Fault probe:** `E._is_image_digest("sha256:<64hex>") == True` แต่ launcher raise `LaunchError`; ส่วน repo-ref ผ่าน launcherแต่ `E._is_image_digest(...) == False` และ `iso._driver._image` เท่ากับ `company-ai-brain/p2-reranker@sha256:...`

**แก้:** แยกสองค่าอย่างชัดเจน:

- `plan.image_digest` = scorer/evaluator runtime image ID `sha256:<64hex>` ตาม contract เดิม
- `qdrant_image_ref` = immutable Qdrant repository ref `qdrant/qdrant@sha256:<64hex>` จาก trusted infra config/run manifest

`build_locked_isolation()` ต้องรับ `qdrant_image_ref` แยกและห้ามนำ `plan.image_digest` ไป launch Qdrant เพิ่ม test ด้วย **full valid RunPlan** ไม่ใช่ mini-plan ที่ใช้ digest คนละ schema

### B4 — inspected container ไม่ได้ผูกกับ process/scorer ที่สร้าง evidence

**ตำแหน่ง:** `p2_m4_launch.py:21-43`

`attest_runtime_image()` inspect ชื่อ `scorer_container` ที่ caller ส่งมา แต่ `build_attested_scorer()` โหลด scorer ใน Python process ปัจจุบัน ไม่มีหลักฐานว่า process นี้อยู่ใน container ที่ inspect หรือ model call เกิดใน container นั้น Caller จึงสามารถชี้ไป container อื่นที่มี digest ถูก แล้วโหลด scorer จาก host/สภาพแวดล้อมอื่นได้

นอกจากนี้ `_loader` ยังเป็น callable parameter ของ public function และ test พิสูจน์เองว่าสามารถคืน object ใดก็ได้หลัง inspect ผ่าน ขณะโมดูลยังไม่มี entry point ที่นำ attested scorer + locked isolation ไปเรียก `run_m4a` แบบบังคับเส้นเดียว จึงยังไม่ปิด false scorer/image attribution

**Fault probe:** inspect seam คืน digest ถูกของ container ชื่อ `unrelated-correct-image` และ `_loader=lambda _: object()`; `build_attested_scorer()` รับและคืน fake objectสำเร็จ

**แก้:** real entry ต้องทำอย่างใดอย่างหนึ่ง:

- รัน launcher/scorer/runner **ภายใน evaluator container ที่ inspect** และ derive current container identity เอง; หรือ
- controller เป็นผู้ `docker run/exec` fixed command ภายใน exact pinned container แล้วรับ bundleกลับมา

real entry ห้ามรับ `_loader`/`scorer_container` จาก caller และต้องเรียก concrete loader → `run_m4a` → publish ใน flow เดียว เพิ่ม negative test ว่า inspect container ที่ถูก digestแต่ไม่ใช่ execution containerไม่สามารถ publish ได้ Test-only injectorแยกชื่อ/module ได้ แต่ห้ามอยู่ใน real callable boundary

## Confirmed part of B1

`p2_m4_isolation.py:157-173` ตรวจ Docker `NetworkSettings.Networks`, abort ก่อนสร้าง sessionเมื่อ container ไม่อยู่บน network ที่สร้าง และ derive endpoint จาก inspected IP ก่อน Qdrant mutation Test negative/happy ครอบ ordering นี้แล้ว จึงไม่ต้อง re-openส่วน driver identity อีก เว้น real Docker run หักล้างสมมติฐาน

## Verification

- `test_p2_m4_isolation.py` — **29/29 PASS**
- `test_p2_m4_launch.py` — **8/8 PASS**
- `test_p2_runplan.py` — **95/95 PASS**
- targeted total — **132/132 PASS**
- offline contract probes ยืนยัน digest-schema collision, wrong Qdrant image selection และ unrelated-container/fake-loader bypass
- ไม่รัน Docker, external Qdrant หรือ modelจริง; ไม่แก้ source/tests/`STATUS.md`

## Gate

- **B1 Docker-inspect identity core:** CLOSED
- **B1/B4 launcher integration + runtime binding:** FIX
- **real M4a synthetic execution:** **NO-GO จนสอง finding ข้างต้นปิดและ targeted confirm ผ่าน**
- **M4b/ข้อมูลจริง:** NO-GO ตาม Data Owner gate เดิม

