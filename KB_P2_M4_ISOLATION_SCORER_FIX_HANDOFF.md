# P2 — isolation/scorer adapter FIX รอบ 1 : dtype canonical + cleanup-unconfirmed + fail-before-mutate + real facade

> **สืบเนื่อง:** `KB_P2_M4_ISOLATION_SCORER_ADAPTER_CODEX_REVIEW_AFE62A7.md` (offline mechanics ACCEPTED ; real M4a = FIX-THEN-GO — B1-B4 + M1)
> **pure/offline ทั้งหมด** — fake driver/session/loader/run · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_reranker.py`, `p2_m4_isolation.py`, `test_p2_m4_isolation.py`, `test_p2_m4_scorer.py`

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix + สถานะ test |
|---|---|---|---|
| **M1** | runtime mismatch | real loader บันทึก `str(model.dtype)`=`"torch.float32"` แต่ plan ใช้ `"float32"` → `validate_scorer_metadata` reject scorer จริงก่อน provision | **`p2_reranker.canonical_dtype()`** (จุดเดียว) — `torch.float32`→`float32` ใช้ที่ `load_pinned_cross_encoder` ; **✅ tested**: real-shaped `PinnedCrossEncoder` (metadata จริง ไม่ copy IC) ผ่าน plan pin ; dtype ไม่ canonical → reject |
| **B2** | blocker (freeze#4 cleanup) | `DockerQdrantDriver.teardown` กลืน exception ทุกตัว → คืน clean เสมอ → runner publish PASS ทั้งที่ resource ค้าง | **cleanup accumulation**: สะสม error, `"not found"/"no such"` = idempotent OK, อื่น ๆ → **`CleanupUnconfirmed`** ; **✅ tested**: docker error → CleanupUnconfirmed ; not-found → clean ; **adapter propagate** driver cleanup failure (ไม่กลืน → runner เห็น) |
| **B1** | blocker (freeze#2 production) | driver `recreate_collection()` **ก่อน** production guard (guard รันหลัง driver คืน handle) + guard เป็น self-report/empty-denylist | **fail-before-mutate**: provision infra → build session → **verify transport-derived `observed_target_identity` + endpoint_is_production ก่อน `recreate_collection` (write แรก)** ; target/production ไม่ตรง → abort ก่อน mutate ; **✅ tested**: identity ไม่ตรง → abort + recreate **ไม่ถูกเรียก** ; identity ตรง → recreate หลัง verify |
| **B3** | blocker (concrete path) | `DockerQdrantDriver` ใช้ client method ที่ไม่มีจริง (`upsert_marker`/`seed`), `count()` คืน CountResult, image `:latest` | **`QdrantSession` facade** เหนือ standard `QdrantClient` (`recreate_collection(VectorParams)`, `count().count→int`, marker ผ่าน `upsert/retrieve` point deny-all, seed ผ่าน upsert) ; **require image pin `@sha256:`** (reject `:latest`) ; **✅ tested (guard)**: `:latest` → IsolationError ; facade real API = reviewable seam (untested — real-run slice) |
| **B4** | blocker (freeze#3 false-PASS) | fake scorer ออก public-valid PASS ; `image_digest` copy จาก plan ไม่ observe runtime | **M4a synthetic = `decision_eligible=False` เสมอ** (runner:196 — mechanics เท่านั้น, decision path ต้อง `decision_eligible=True` + real data + Data Owner) → **ใช้เป็น decision ไม่ได้** ; **✅ tested**: capstone assert `decision_eligible is False` ; real-path: `observed_image_digest()` (docker inspect runtime) ให้ launcher bind แทน plan-declared + launcher สร้าง scorer เองด้วย `build_m4_scorer` (real loader) ไม่รับ arbitrary self-report — **real-run slice** |

## ผลรัน (offline)

- **22 suites: 888→ (isolation 29 + scorer 10)** — รวม **889/889** ; ไม่มี regression จาก dtype/driver change (test_p2_pin/dockerbuild/reranker ผ่านครบ)
- fake driver/session/loader/run เท่านั้น — **ไม่แตะ docker/qdrant/model จริง**
- DockerQdrantDriver: logic (image-pin guard / fail-before-mutate ordering / cleanup accumulation) offline-testable ; real API (QdrantSession/docker) = reviewable seam ยังไม่รัน

## ยัง NO-GO — real M4a synthetic run (slice ถัดไป, ต้อง review + รันจริง)

- `QdrantSession` real API (VectorParams/upsert/retrieve/.count) + evaluator/scorer process บน **exact --internal network** เดียวกับ Qdrant (แก้ host-DNS: host resolve `m4qd-<token>` ไม่ได้ → ต้องรัน controller ใน network เดียว หรือ publish แบบ controlled)
- pinned Qdrant image ด้วย immutable digest จริง (ไม่ใช่ค่า test)
- `load_pinned_cross_encoder` โหลด bge-reranker จริง (torch/model) + observe runtime image_digest bind receipt
- production guard: ต้องมี transport-derived proof ว่า target ไม่ใช่ production (ephemeral self-provisioned + internal network) ไม่ใช่แค่ denylist

## ขอ Codex re-review (targeted — B1-B4 + M1)

1. M1 dtype canonical + B2 cleanup-unconfirmed (สะสม + not-found idempotent + adapter propagate) ปิดครบไหม
2. B1 fail-before-mutate (transport identity verify ก่อน recreate) + B3 facade/image-pin + B4 decision_eligible=False bound + observed_image_digest design — พอเป็นทิศทาง real-run ที่ถูกไหม
3. real M4a synthetic run = slice ถัดไป (QdrantSession real + internal-network topology + real model) — ยัง NO-GO

**Gate (owner decision 2026-08-07):** isolation/scorer re-review = **FIX-THEN-GO/GO** · real M4a synthetic run = NO-GO จน re-review + real facade/model slice · M4b/N-sweep/ข้อมูลจริง = NO-GO จน Data Owner sign-off · safety/provenance v1 = **FROZEN**
