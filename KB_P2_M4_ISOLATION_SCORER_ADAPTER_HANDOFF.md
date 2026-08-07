# P2 — M4 isolation/Docker + scorer adapter slice (injectable/offline) + M4a synthetic capstone

> **สืบเนื่อง:** `KB_P2_M4_QDRANT_ADAPTER_FIX_CODEX_REREVIEW_A2C0F9E.md` (GO/SHIP, safety v1 FROZEN, isolation/scorer slice = GO)
> **pure/offline ทั้งหมด** — fake driver/session/loader · **รัน M4a จริงบน docker/qdrant/model = ยัง NO-GO**
> ไฟล์ใหม่: `p2_m4_isolation.py`, `p2_m4_scorer.py`, `test_p2_m4_isolation.py`, `test_p2_m4_scorer.py`

## สิ่งที่สร้าง — ปิด ports.isolation + ports.scorer ครบ (M4 มี adapter จริงทั้ง 4 ตัวแล้ว)

| Adapter | ทำอะไร | กลไก |
|---|---|---|
| **`QdrantDockerIsolation`** (ports.isolation) | contract enforcer เหนือ **injectable driver** — provision → observe → marker → seed → teardown | บังคับ IsolationProof invariants แบบ fail-closed: handle 5 คีย์ (4 ids **distinct** + non-blank str), `observe_initial_count`/`observe_published_ports` = **int แท้**, `endpoint_is_production` = **bool แท้**, **refuse provision บน production**, teardown **idempotent** ; driver = fake (offline) / `DockerQdrantDriver` (real, docker network+volume+container + qdrant collection — reviewable seam, ยังไม่รัน) |
| **`p2_m4_scorer`** (ports.scorer) | pinned cross-encoder factory + **fail-closed plan-pin verifier** | reuse `p2_reranker.PinnedCrossEncoder` (มีอยู่แล้ว, injectable) ; `assert_scorer_matches_plan` = `HN.validate_scorer_metadata` เทียบ metadata กับ RunPlan pin (kind/model_revision/tokenizer_revision/model_file_manifest_sha256/inference_config/model_name) ; `build_m4_scorer(plan, loader=)` โหลด+verify ก่อนคืน (real loader = `load_pinned_cross_encoder` ; offline inject fake) |

## trust boundary / invariants ที่ปิด

- **isolation identity**: adapter validate handle (4 distinct ids) + IsolationProof ผ่าน `E.validate_m4_isolation_proof` (empty collection count 0, no published ports, non-production endpoint, marker write→read round-trip) ; provision บน production → abort + teardown
- **type exactness**: `observe_*` คืน int/bool แท้ (bool/float/numpy → IsolationError) — ตรง `_exact_zero_int` / `is not False` ของ runner
- **scorer pin**: metadata ต้องตรง RunPlan pin ก่อนใช้ (mock/wrong revision/wrong manifest → M4ScorerError) — กัน unpinned/mock model หลุดเข้า evidence
- **lifecycle guards**: ops ก่อน provision / หลัง teardown → IsolationError ; provision ซ้ำ → error ; teardown idempotent/partial-safe
- **real driver ยังไม่รัน**: `DockerQdrantDriver` เป็น reviewable seam (docker CLI ผ่าน injected `run` + qdrant client) — ไม่ถูก unit-test (ต้อง docker/qdrant จริง) ตาม convention `p2_docker_build`

## capstone — M4a synthetic run เต็ม (adapter จริง 4 ตัว) → PUBLISHED/PASS

`test_p2_m4_isolation.py` เสียบ **adapter จริงทั้ง 4** เข้า `RUN.run_m4a` (offline):
- `QdrantDockerIsolation(driver=FakeDriver)` + `QdrantM4Provider`/`QdrantM4Oracle`(client_factory) + `build_m4_scorer`(fake loader)
- ผล: **PUBLISHED + evidence PASS** + bundle ผ่าน public gate + IsolationProof/independent_oracle PASS + scorer เห็นเฉพาะ authorized query (ไม่มี sentinel ถึง model) + isolation lifecycle เต็ม (provision→observe→write<read<seed→teardown)
- **นี่คือ "ระบบสร้างผลลัพธ์จริง" ครั้งแรก** — pipeline M4a รันจบด้วย adapter code จริง (fake infra) ไม่ใช่ fake ports

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2_m4_isolation 22/22   test_p2_m4_scorer 7/7   test_p2_m4_qdrant 31/31   test_p2_m4_runner 44/44
test_p2_m4_ops 32/32   test_p2_m4 59/59   test_p2_m4_harness 47/47   test_p2 166/166   ... (22 suites)
```

- **รวมเครื่องนี้ (22 suites): 879/879** (เพิ่ม isolation 22 + scorer 7 ; ไม่มี regression)
- fake driver/session/loader เท่านั้น — **ไม่แตะ docker/qdrant/model จริง**

## ยัง NO-GO / ถัดไป (real M4a synthetic run)

- **รัน M4a synthetic บน docker/qdrant/model จริง = ยัง NO-GO** จน:
  1. review slice นี้ผ่าน (isolation/scorer provenance)
  2. `DockerQdrantDriver` real path review (docker isolation จริง: `--internal` network, no published ports, ephemeral volume/collection, marker ไม่ pollute retrieval, teardown จริง)
  3. `load_pinned_cross_encoder` โหลด bge-reranker จริง (model weights + pin verify)
- **M4b / N-sweep / ข้อมูลจริง = NO-GO** จน Data Owner sign-off (ดู `DATA_OWNER_SIGNOFF_PACK.md` — งานองค์กรขนาน)
- real-run marker: `DockerQdrantDriver` เขียน marker เป็น point `allowed_roles=[]` (deny-all → ไม่โผล่ provider) — ต้อง review ว่า approach นี้ปลอดภัยจริงกับ Qdrant

## ขอ Codex review (isolation/scorer adapter slice)

1. isolation contract enforcement — IsolationProof invariants (distinct ids / int-0 / False / marker round-trip / production refuse / teardown idempotent) ครบไหม ; `DockerQdrantDriver` real seam approach (network --internal, no publish, ephemeral, marker deny-all) โอเคไหมสำหรับ real-run slice
2. scorer pin verifier — fail-closed ตรง RunPlan pin ครบไหม ; reuse PinnedCrossEncoder เหมาะสมไหม
3. capstone M4a synthetic (adapter จริง 4 ตัว → PUBLISHED/PASS) — พอเป็นหลักฐาน mechanics ไหม
4. หลังผ่าน → เตรียม real M4a synthetic run (docker/qdrant/model จริง) เป็น slice ถัดไป

**Gate (ตาม owner decision 2026-08-07 / STATUS.md):** isolation/scorer review = **FIX-THEN-GO/GO** · real M4a synthetic run = NO-GO จน review + real driver/model slice · M4b/N-sweep/ข้อมูลจริง = NO-GO จน Data Owner sign-off (hash-bound) · production = NO-GO ; safety/provenance v1 = **FROZEN** (finding นอก 4 exceptions → backlog)
