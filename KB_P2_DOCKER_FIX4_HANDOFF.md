# P2 — ปิด receipt-evidence B1/B2/B3/M1/M2 → review ก่อน resolve digest/build

> **สืบเนื่อง:** `KB_P2_DOCKER_FIX3_CODEX_REVIEW_52B92F8.md` (FIX-THEN-BUILD — success receipt ยังไม่บังคับ evidence ครบ)
> **ทั้งหมด pure/offline** (fake subprocess) — ไม่ build/run/โหลด model · base-digest จริงยัง defer

## Finding → fix → proof

| # | Finding | Fix | proof |
|---|---|---|---|
| **B1** | evidence ขาด/`None` ยังได้ `SUCCEEDED` receipt | `validate_extracted_evidence()` ก่อนเขียน receipt: **exact 3 keys**, ทุกไฟล์ regular+non-empty+อ่านได้, `model_file_manifest.sha256` = บรรทัดเดียว 64-hex, wheel manifest ไม่ว่าง+ไม่มี filename ซ้ำ, freeze ไม่ว่าง → ไม่ครบ = `EVIDENCE_INVALID` (rc7) ไม่มี receipt | evidence {} / ขาด key / extra / None / malformed hash / empty / dup → error ; e2e rc7 no receipt |
| **B2** | inspector/extractor exception หลุด lifecycle ไม่มี failure record | ครอบทุก post-run seam (runner/read-iid/inspect/extract/receipt) ด้วย exception boundary → **stage-aware failure** (`RUNNER_FAILED`/`IID_INVALID`/`INSPECT_FAILED`/`INSPECT_MISMATCH`/`EXTRACT_FAILED`/`EVIDENCE_INVALID`) ไม่ crash, ไม่มี success receipt | inspector/extractor/runner raise → rc5/6/8 + failure schema, ไม่ crash |
| **B3** | source hashing ละไฟล์หายเงียบ (รวม `.dockerignore`) | `source_hashes()` บังคับ **exact `SOURCE_FILES` ครบ** — หาย/อ่านไม่ได้ = `REFUSED` ก่อนเรียก runner (source map ครบทุก key) | ลบ `.dockerignore` → REFUSED rc2 + runner ไม่ถูกเรียก |
| **M1** | receipt ให้ hash ของไฟล์ manifest ไม่ใช่ค่า model-manifest ภายใน | receipt แยก: `model_file_manifest_sha256` = **content ในไฟล์** (→ RunPlan) ; `evidence_file_sha256` = hash ไฟล์ (audit) | parsed = d*64 (content) != file hash (ไม่ double-hash) |
| **M2** | `--out-dir` relative/reuse ชนกัน ; log/context ไม่ bind | `resolve_out_dir` (relative → ใต้ `BUILD_ROOT`) ; **run dir ต้องว่าง** (ไม่ทับ run ที่มี artifact) ; capture `build.log` + `build_log_sha256` + `context_bytes` ใน receipt | run dir ไม่ว่าง → rc2 ; receipt มี context_bytes/build_log_sha256 |

## receipt schema (SUCCEEDED เท่านั้น)
```
status=SUCCEEDED · image_id · py_base_digest · platform=linux/amd64 · model_commit · git_commit
source_sha256{9 files} · context_bytes · build_log_sha256
model_file_manifest_sha256 (content → RunPlan.model_file_manifest_sha256)
evidence_file_sha256{model_file_manifest.sha256, wheelhouse.manifest.sha256, wheelhouse.freeze.txt}
```
build ล้ม/refused → `build_failure.json` (stage-aware status, ไม่มี `_rc` leak) ; **ไม่มี success receipt**

## ผลรัน (offline — `rfqv` python)
```
test_p2_dockerbuild 33/33 (evidence schema B1 + exception B2 + source B3 + M1 parsed + M2 run-dir + lifecycle rc/iid/inspect)
test_p2_pin 14/14 · test_p2_adapter 22/22 · test_p2 179 · test_p2_runplan 94 · provider 22 · harness 21 · policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```

## ขอ Codex review
1. success receipt บังคับ evidence exact schema + exception-to-failure ทุก stage + exact source set ครบไหม
2. receipt แยก parsed model manifest / file hash ถูกต้องสำหรับ RunPlan ไหม
3. อนุมัติ **resolve `python:3.11-slim` amd64 digest จาก trusted registry + `docker build` (fetch+verify+wheelhouse, ยังไม่ smoke)** ได้ไหม
4. หลัง build → ส่ง `build_receipt.json` (image_id/source/evidence/context/log hashes) + build log + context size กลับมา review ก่อน model-load smoke

**สถานะ gate:** model-load smoke / real M4 / N-sweep / decision benchmark = NO-GO จน review + Data Owner sign-off + validated M4/canary
