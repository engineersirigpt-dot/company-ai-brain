# P2 — CPU fetch/verify build **สำเร็จ** → review ก่อน model-load smoke

> **สืบเนื่อง:** `KB_P2_DOCKER_FIX4_CODEX_REVIEW_E826F13.md` (GO — resolve digest + CPU build only)
> + base digest resolution: `KB_P2_BASE_DIGEST_RESOLUTION.md`
> **build เท่านั้น** — ยังไม่ execute image / model-load smoke / M4 / N-sweep / decision benchmark (NO-GO ตาม gate เดิม)

## คำสั่งที่รัน (ผ่าน wrapper เท่านั้น)
```
docker buildx imagetools inspect python:3.11-slim         # resolve amd64 digest (trusted registry)
python p2_docker_build.py --py-base python@sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553 --out-dir cpu-build-1
→ exit 0 · SUCCEEDED
```

## ผล gate (`validate_receipt` = Codex pass criteria) → **ผ่านทุกข้อ**
```
build_receipt.json (status=SUCCEEDED, ไม่มี build_failure.json)
  image_id                   = sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190
  platform                   = linux/amd64
  py_base_digest             = python@sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553
  model_commit               = 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
  model_file_manifest_sha256 = c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013   (parsed content → RunPlan)
  build_log_sha256           = 728c6e9c4250c303363fd80af873011dba525d4c41ce8c50c9a2d3cbba141b3b
  declared_context_bytes     = 24467
  source_sha256              = 9 keys (exact)
  evidence_file_sha256       = 3 keys (model_file_manifest / wheelhouse.manifest / wheelhouse.freeze), ทุกค่า 64-hex
  git_commit                 = 7d68886cbb6db6909b8a23994246212234af547a   (full 40-hex)
  git_dirty                  = true   (working tree มี KB docs/tmp/ untracked ตอน build — source_sha256 ผูก build inputs จริงแล้ว)
```

## build log ยืนยัน (Codex gate item: context เล็ก + base/model fetch ตรง pin)
```
#5  load metadata for docker.io/library/python@sha256:78b39ef14d8e...4553      ← base pin ตรง
#8  transferring context: 18.95kB                                              ← dockerignore allowlist ทำงาน (ไม่ส่ง 12GB)
#13 {"resolved_commit":"953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
     "model_file_manifest_sha256":"c969d1f6...","safetensors_bytes":2271071852} ← model fetch @pin + real blob 2.27GB
#17 sha256sum -c /opt/wheelhouse.manifest.sha256 → certifi/charset/filelock/.../torch: OK  ← wheel integrity ก่อน install
#22 offline snapshot OK 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e               ← runtime verify (SystemExit gate) ผ่าน
#23 naming to docker.io/company-ai-brain/p2-reranker:pinned-cpu               ← tag
docker image inspect ...:pinned-cpu → Id=sha256:27768971905e... Os=linux Arch=amd64  ← tag→iid ตรง receipt
```

## dependency closure (baked, CPU) — `wheelhouse.freeze.txt`
```
torch==2.3.1+cpu · transformers==4.41.2 · tokenizers==0.19.1 · safetensors==0.4.3 · sentencepiece==0.2.0
huggingface-hub==0.23.4 · numpy==2.4.6 · sympy==1.14.0 · networkx==3.6.1 · regex==2026.7.19 · requests==2.34.2 ...
```

## artifacts (บนดิสก์ — `.p2_build/cpu-build-1/`, gitignored)
`build_receipt.json` · `build_request.json` · `build.log` (34KB) · `evidence/{model_file_manifest.sha256, wheelhouse.manifest.sha256, wheelhouse.freeze.txt}` · `p2_image.id`
raw base resolution: `KB_P2_BASE_DIGEST_RESOLUTION.md` (index/platform digest ยืนยัน hash แล้ว)

## ค่าที่พร้อมเข้า RunPlan (หลัง review)
- `image_digest` = `sha256:27768971905ebd3e...a190` (local image Id/config digest)
- `model_file_manifest_sha256` = `c969d1f67f17...9013`
- `model_commit`/`tokenizer_commit` = `953dc6f6f85a...d41e` · `inference_config` = torch/transformers ตาม freeze

## ขอ Codex review (ก่อน model-load smoke)
1. receipt/evidence/build-log ครบพอยืนยัน CPU compatibility artifact ตาม gate ไหม (image identity + source + evidence + base/model pin)
2. อนุมัติ **model-load smoke** (`docker run --rm --network none sha256:27768971905e... python p2_model_smoke.py`) ได้ไหม — พิสูจน์ resolved SHA + baked-manifest cross-check + score finite (ยังไม่ใช่ benchmark)
3. real M4 / N-sweep / decision benchmark = ยัง NO-GO ตาม gate เดิม (ต้อง sign-off + validated M4/canary)

**git_dirty=true note:** build inputs ผูกด้วย `source_sha256` (9 ไฟล์ exact) — dirty มาจาก KB handoff docs + `tmp/` ที่ยัง untracked ไม่ใช่การแก้ source ระหว่าง build
