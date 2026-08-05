# P2 — ปิด wrapper lifecycle B1/B2/M1/M2/N1 → review ก่อน resolve base digest/build

> **สืบเนื่อง:** `KB_P2_DOCKER_FIX2_CODEX_REVIEW_BA0CD70.md` (FIX-THEN-BUILD — wrapper แยก failed จาก success ไม่ได้)
> **ทั้งหมด pure/offline** — ไม่ build/run/โหลด model · base-digest จริงยัง defer ตาม review

## Finding → fix → proof

| # | Finding | Fix | proof (fake subprocess, ไม่ใช้ Docker) |
|---|---|---|---|
| **B1** | failed build ทิ้ง success-looking `p2_build_manifest.json` | `p2_docker_build.run_build` เป็น **lifecycle เดียว**: `build_request.json` (PENDING) เขียนก่อน → runner → **`build_receipt.json` (SUCCEEDED) atomic เฉพาะเมื่อ rc==0 + iid valid + inspect ผ่าน** ; ล้ม/refused → `build_failure.json` (schema แยก) ; ล้าง artifact ของ run นี้ก่อนเริ่ม | FAILED rc17 → ไม่มี receipt (มี failure) · stale success receipt ถูกล้าง · malformed iid → rc3 · REFUSED → rc2 ไม่เรียก runner |
| **B2** | image ไม่มี tag แต่ smoke อ้าง compose tag | wrapper `--tag company-ai-brain/p2-reranker:pinned-cpu` + **inspect ยืนยัน os=linux/arch=amd64/tag→iid/Id==iidfile** ; smoke = **path เดียว** ผ่าน `image_id` ใน receipt (`docker run --network none $IID`) ไม่พึ่ง compose rebuild | build_command มี --tag/--platform/--iidfile · inspect arch/tag/Id mismatch → rc4 |
| **M1** | integration test กลืน ImportError ทุกชนิด | guard เจาะจง `importlib.util.find_spec("qdrant_client")` — ไม่มี qdrant → skip ; มีแล้ว `import p2_harness` ล้ม = **error จริง (ไม่ skip)** | subprocess probe: qdrant present + unrelated ImportError → non-zero |
| **M2** | wheelhouse ไม่ verify/ไม่ผูก evidence กับ image | Dockerfile `sha256sum -c` wheel manifest **ก่อน** install ; wrapper **extract evidence จาก validated iid** (docker create ไม่ start → cp → hash) แล้ว bind `evidence_sha256` ใน receipt | SUCCESS receipt มี evidence_sha256 + source_sha256 |
| **N1** | wrapper ผูก cwd + fixed filenames | path anchor `Path(__file__).resolve().parent` (`cwd=repo_root`) + **run directory แยกต่อ run** (`.p2_build/<uuid>`) กัน stale/concurrent | stale iid/receipt ถูกล้างก่อน build |

## ผลรัน (offline — `rfqv` python + `PYTHONIOENCODING=utf-8`)
```
test_p2_dockerbuild 25/25 (validate/inspect + lifecycle success/fail/refused/malformed-iid/inspect-mismatch/stale + M1 guard)
test_p2_pin 14/14 · test_p2_adapter 22/22 (integration guard เจาะจง qdrant) · test_p2 179 · test_p2_runplan 94
provider 22 · harness 21 · policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```
`.gitignore` เพิ่ม `.p2_build/`, `p2_image.id`, `wheels/` (runtime output ไม่ commit)

## receipt schema (evidence สำหรับ RunPlan / review)
`build_receipt.json` (status=SUCCEEDED เท่านั้น): `image_id` (→RunPlan.image_digest), `py_base_digest`, `platform`,
`model_commit`, `git_commit`, `source_sha256` (Dockerfile/requirements/scripts), `evidence_sha256`
(model_file_manifest + wheelhouse.manifest + wheelhouse.freeze)

## `<<FILL-AT-BUILD>>` (defer ตาม review — resolve หลัง review ผ่าน)
- `PY_BASE` = `python:3.11-slim` **linux/amd64 digest จริง** จาก trusted registry → `p2_docker_build.py --py-base python@sha256:<digest>`
- `model_file_manifest_sha256` / `image_digest` = จาก receipt หลัง build

## ขอ Codex review
1. wrapper lifecycle (request/receipt fail-closed + tag + inspect + evidence bind + path anchor) ปิด B1/B2/M2/N1 ครบไหม
2. adapter M1 test guard เจาะจง qdrant_client ปิดแล้วไหม
3. อนุมัติ **resolve `python:3.11-slim` amd64 digest + `docker build` (fetch+verify+wheelhouse, ยังไม่ smoke)** ได้ไหม
4. หลัง build → ส่ง `build_receipt.json` (image_id/source/evidence hashes) + build log + context size กลับมา review ก่อน model-load smoke

**สถานะ gate:** model-load smoke / real M4 / N-sweep / decision benchmark = NO-GO จน review + Data Owner sign-off + validated M4/canary
