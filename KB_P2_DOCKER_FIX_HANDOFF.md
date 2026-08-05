# P2 — ปิด Docker B1/B2/M1/M2/M3 (CPU-compat pinned build) → review ก่อน `docker build`

> **สืบเนื่อง:** `KB_P2_DOCKER_PIN_CODEX_REVIEW_22AF2BE.md` (FIX-THEN-BUILD)
> + runner adapter (GO) commit `18ae84c` · Docker pure B2/M2/M1 commit `eb154e1`
> **ยังไม่ build/run/โหลด model** — ทุกไฟล์เป็น artifact เขียนลง disk เท่านั้น
> **runtime target = CPU-compatibility image** (ผู้ใช้เลือก) — CUDA/GPU evidence image = งานแยกตอน timed latency run

## Finding → fix

| # | Finding | Fix |
|---|---|---|
| **B1** | base/CUDA/dependency ยัง reproducible ไม่พอ | ระบุ target = **CPU-compat** ชัดเจน ; `torch==2.3.1+cpu` (CPU wheel) ; `PY_BASE` = digest-pinned base (override ตอน build) ; **wheelhouse** freeze closure |
| **B2** | `MODEL_COMMIT` build arg เป็น control ปลอม | `p2_fetch_model._assert_expected_commit()` — `P2_EXPECT_COMMIT` (จาก build arg) ต้อง == `p2_pin.MODEL_COMMIT` มิฉะนั้น **SystemExit ก่อน fetch** ; p2_pin = single source |
| **M1** | เก็บ image_digest ด้วย `RepoDigests[0]` (ว่างสำหรับ local) | ใช้ `docker image inspect --format '{{.Id}}'` / `docker build --iidfile` → local image Id/config digest → `RunPlan.image_digest` |
| **M2** | smoke ใช้ `assert` + ไม่ cross-check baked manifest | `p2_model_smoke` อ่าน `/opt/model_file_manifest.sha256` แล้วเทียบ **exact** กับ file-manifest ของ snapshot ที่โหลดจริง ; ทุก gate เป็น **SystemExit** ; inline `assert` ใน Dockerfile → `p2_verify_snapshot.py` (SystemExit) |
| **M3** | runtime `pip install` ยังพึ่ง network | **wheelhouse**: prep stage `pip download` closure → `/wheels` ; runtime `pip install --no-index --find-links=/wheels` (ไม่มี network install) ; fetch stage ลงแค่ `huggingface_hub` (ไม่โหลด torch สองรอบ) |

## โครง Dockerfile.p2 (2 stage)
```
prep (network)    : pip install huggingface_hub → p2_fetch_model (snapshot@SHA + verify 6 files + real blob
                    + resolved==pinned + file-manifest) → pip download -r requirements → /wheels (wheelhouse)
runtime (offline) : pip install --no-index --find-links=/wheels → COPY HF cache (snapshots/<SHA>+blobs)
                    → p2_verify_snapshot.py (SystemExit fail-closed) → CMD print pin (ไม่รัน benchmark)
```

## แมปกับ Gate re-review (5 ข้อ)
1. base immutable digest + target CPU/CUDA ระบุ — **CPU-compat ระบุแล้ว** ; `PY_BASE` digest = override บังคับตอน build (เหมือน image_digest/manifest ที่เป็น build-time)
2. torch/dependency closure reproducible + install จาก artifact เดียว — **wheelhouse (M3)** ✔ (เสริม: `--require-hashes` lockfile ทำได้ก่อน evidence จริง)
3. MODEL_COMMIT single source / assert — **B2** ✔
4. local image identity capture — **M1** ✔
5. baked manifest cross-check fail-closed ไม่พึ่ง assert — **M2** ✔

## `<<FILL-AT-BUILD>>` (build-time เท่านั้น)
- `PY_BASE` digest (CPU-compat python:3.11-slim@sha256:…)
- `model_file_manifest_sha256` (output p2_fetch_model) · `image_digest` (docker image inspect .Id)
- (เสริม) hash-locked requirements

## Tests (offline)
```
test_p2_pin 12/12 (รวม B2 build-arg guard match/mismatch/absent, M2 verify fail-closed)
test_p2_adapter 15/15 · test_p2 179 · test_p2_runplan 94 · provider 22 · harness 21 · policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```

## ขอ Codex review
1. B1/B2/M1/M2/M3 ปิดครบสำหรับ **CPU-compat fetch+verify build** ไหม (โดยเฉพาะ wheelhouse offline install + build-arg guard + SystemExit gates)
2. อนุมัติ **`docker build` (prep fetch+verify + wheelhouse, ยังไม่รัน smoke/benchmark)** ได้ไหม — `PY_BASE` digest ให้ pin ตอน build
3. หลัง build → ส่ง build log + local image Id + resolved SHA + file-manifest กลับมา review ก่อน model-load smoke
4. runner adapter (`18ae84c`) — ยืนยัน scope ครบไหม (join frozen cases + ไม่ประกาศ approval)

**สถานะ gate:** model-load smoke / real M4 / N-sweep / decision benchmark = NO-GO จน review ผ่าน + Data Owner sign-off + validated M4/canary
