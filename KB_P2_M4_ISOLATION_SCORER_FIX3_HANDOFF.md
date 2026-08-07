# P2 — isolation/scorer FIX รอบ 3 : แยก Qdrant image + single load-bearing `run_m4a_locked`

> **สืบเนื่อง:** `KB_P2_M4_ISOLATION_SCORER_FIX2_CODEX_REREVIEW_0DCF4A2.md` (B1 driver identity core CLOSED ; launcher B1/B4 OPEN)
> **pure/offline** — inject controller/docker_run seam · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์: `p2_m4_isolation.py` (rename param), `p2_m4_launch.py` (rewrite → single flow), `test_p2_m4_launch.py`, `test_p2_m4_isolation.py`

## Finding → fix → proof

| # | ช่องเดิม | Fix + test |
|---|---|---|
| **B1/B4 digest schema** | `plan.image_digest` = scorer image `sha256:<64hex>` แต่ launcher บังคับ `@sha256:` แล้วส่งเป็น **Qdrant image** → valid RunPlan ถูก reject + scorer image ถูกเปิดเป็น Qdrant | **แยกสองค่า**: `plan.image_digest` = scorer/evaluator `sha256:<64hex>` (contract เดิม) · `qdrant_image_ref` = `<repo>@sha256:<64hex>` (infra config, **ไม่ใช่จาก plan**) ; `DockerQdrantDriver` param `image_digest`→**`qdrant_image`** (Qdrant image ที่ docker run) ; **✅ tested**: qdrant_image_ref = scorer digest → LaunchError (schema แยก) ; full valid RunPlan |
| **B4 load-bearing** | attest inspect caller-named container แต่ scorer โหลดใน process ปัจจุบัน (ไม่มีหลักฐานรันใน container นั้น) ; `_loader`/`scorer_container` เป็น caller param | **single flow `run_m4a_locked(*, plan, ..., qdrant_image_ref, controller)`**: controller รัน scorer+runner **ภายใน pinned evaluator container** คืน `{executed_image_digest, bundle}` → launcher **verify `executed_image_digest == plan.image_digest`** (พิสูจน์ scorer รันใน pinned image) + validate bundle ผ่าน public gate ; real entry **ไม่รับ** loader/scorer_container จาก caller ; ลบ 3 helpers เดิม ; **✅ tested**: executed ≠ pin → LaunchError ; bundle ไม่ผ่าน gate → LaunchError ; controller ไม่มี .execute → LaunchError |

## สอดคล้อง Codex "simpler path"

`run_m4a_locked` เป็น entry เดียวที่ **controller เป็นผู้รันและรายงาน executed image เอง** — caller จับคู่ "container A ถูก digest" กับ "scorer process B ที่อื่น" ไม่ได้ (attestation load-bearing) ; injectable controller = **test เท่านั้น** (real = docker run/exec)

## ผลรัน (offline)

- **23 suites: 895/895** (launch rewrite 6 ; isolation 29 ; ไม่มี regression)
- offline-testable: input/schema validation, executed-image attestation, bundle gate — ผ่าน injected fake controller ที่รัน `run_m4a` (adapter จริง 4 ตัว + fake infra)
- **`controller.execute` ตัวจริง (docker run/exec runner ใน pinned container) = untested seam** — พิสูจน์เต็มต้องรันจริง

## ⚠️ ถึงขอบเขต offline แล้ว — real M4a synthetic execution = slice ถัดไป (ต้องรัน docker/qdrant/model จริง)

finding ที่เหลือทั้งหมด (controller รัน scorer ใน pinned container จริง, endpoint จาก Docker inspect จริง, model digest จาก runtime) **พิสูจน์ offline ไม่ได้** — ต้อง:
- build p2-reranker image (immutable digest) + Qdrant container (`qdrant_image_ref`) บน `--internal` network
- controller `docker run/exec` runner ภายใน pinned evaluator container → รับ bundle + executed image
- โหลด bge-reranker จริง (torch/model) — ผลรันจริงครั้งแรก

## ขอ Codex review — **design sign-off ของ `run_m4a_locked` contract**

1. แยก qdrant_image_ref/plan.image_digest + attestation load-bearing (executed == pin) + single flow ปิด B1/B4 launcher เชิง design ไหม
2. `controller.execute` contract (`{executed_image_digest, bundle}` จากการรันใน pinned container จริง) พอไหมสำหรับ real controller
3. หลังผ่าน design → **real M4a synthetic execution** (build image + controller docker run/exec จริง) = slice ที่ต้องรันจริง

**Gate:** run_m4a_locked design review = FIX-THEN-GO/GO · real execution = NO-GO จน design ผ่าน + real controller slice + รันจริง · M4b/ข้อมูลจริง = NO-GO จน Data Owner sign-off · safety/provenance v1 = FROZEN
