# P2 — ปิด Docker B1/B2/M1/M2 + adapter M3/M4 → review ก่อน CPU fetch+verify build

> **สืบเนื่อง:** `KB_P2_DOCKER_FIX_CODEX_REVIEW_71D957B.md` (FIX-THEN-BUILD + adapter FIX-BEFORE-EVIDENCE)
> **ทั้งหมด pure/offline** — ไม่ build/run/โหลด model · base-digest จริงยัง defer (resolve ตอน build ตาม review)

## Finding → fix → proof

| # | Finding | Fix | proof |
|---|---|---|---|
| **B1** | ไม่มี dockerignore → build context >12GB (raw corpus/.git/.venv/keys) | `Dockerfile.p2.dockerignore` **allowlist** (`*` แล้วเปิดเฉพาะ 7 ไฟล์ที่ Dockerfile COPY) — ไม่ blacklist รายโฟลเดอร์ | context เหลือเฉพาะ p2 build files |
| **B2** | `PY_BASE` floating tag ยัง build ได้ + ไม่ล็อก platform | default = **sentinel digest zeros ที่ pull ไม่ได้** (ไม่มี floating tag) ; **`p2_docker_build.py`** บังคับ `PY_BASE=python@sha256:<64hex>` จริง (reject sentinel/tag) + `--platform linux/amd64` + เขียน `p2_build_manifest.json` ; compose `platform: linux/amd64` | `validate_py_base` reject sentinel/tag/non-python/blank ; build_command ล็อก platform+iidfile |
| **M1** | `MODEL_COMMIT` guard ยอม blank ผ่าน (`if expect and …`) | แยก `None` (ไม่ตั้ง) ออกจาก empty — ตั้งมาแล้ว (แม้ ""/whitespace/malformed) ต้อง == pin เป๊ะ | blank/whitespace → SystemExit |
| **M2** | wheelhouse ยังไม่ reproducible lock | `pip download --only-binary=:all:` (fail ถ้ามี sdist) + บันทึก **wheel filename+sha256** (`/opt/wheelhouse.manifest.sha256`) + `pip freeze` (`/opt/wheelhouse.freeze.txt`) เป็น build evidence ; full `--require-hashes` เลื่อนไป CUDA image | (Docker build evidence — ตรวจตอน build) |
| **M3** | adapter `int(k)` truncate/overwrite N key เงียบ (`{10.5, "10"}`→`{10}`) | `_coerce_n_key`: รับเฉพาะ positive int (ไม่ใช่ bool) หรือ canonical decimal string round-trip exact ; reject float/whitespace/sign/exp/leading-zero + **normalized-key collision** | reject `10.5`/`"10.0"`/`" 10"`/`True`/`"010"`/`{10,"10"}` |
| **M4** | `test_p2_adapter` import p2_provider → ลาก qdrant_client, clean env รันไม่ได้ | แยก **pure section** (synthetic raw, imports = p2_runplan/adapter/p2_pin เท่านั้น) + **integration guarded** (try import p2_harness, skip ถ้าไม่มี qdrant_client) | pure import ผ่านแม้บล็อก qdrant_client |

## ผลรัน (offline — `rfqv` python + `PYTHONIOENCODING=utf-8`)
```
test_p2_pin 20/20 (รวม B2 validate_py_base + M1 blank/whitespace)
test_p2_adapter 22/22 (pure 21 + integration 1 ; pure รันได้โดยไม่มี qdrant_client)
test_p2 179 · test_p2_runplan 94 · provider 22 · harness 21 · policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```
พิสูจน์ isolation: `__import__` บล็อก `qdrant_client` แล้ว `import p2_runplan, p2_evidence_adapter, p2_pin` ยังผ่าน

## `<<FILL-AT-BUILD>>` (defer ตาม review — resolve ทันทีก่อน build บนเครื่อง target)
- `PY_BASE` = `python:3.11-slim` **linux/amd64 digest จริง** → ส่งผ่าน `p2_docker_build.py --py-base python@sha256:<digest>` → บันทึกใน `p2_build_manifest.json`
- `model_file_manifest_sha256` (p2_fetch_model) · `image_digest` (`docker image inspect '{{.Id}}'`/`--iidfile p2_image.id`)

## ขอ Codex review
1. B1 (context allowlist), B2 (sentinel base + wrapper fail-closed + platform), M1 (blank guard), M2 (wheel manifest/no-sdist) ปิดครบสำหรับ CPU fetch+verify build ไหม
2. adapter M3 (N-key) / M4 (test isolation) ปิดครบก่อนใช้ผลิต evidence จริงไหม
3. อนุมัติ **`python p2_docker_build.py --py-base python@sha256:<amd64 digest>` (fetch+verify+wheelhouse, ยังไม่ smoke)** ได้ไหม
4. หลัง build → ส่ง build log + context size + base digest+platform + wheel manifest + local image Id + resolved model SHA + model manifest กลับมา review ก่อน model-load smoke

**สถานะ gate:** model-load smoke / real M4 / N-sweep / decision benchmark = NO-GO จน review + Data Owner sign-off + validated M4/canary
