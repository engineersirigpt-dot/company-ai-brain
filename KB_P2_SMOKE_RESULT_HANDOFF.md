# P2 — model-load smoke **ผ่าน** (offline, CPU) → review

> **สืบเนื่อง:** `KB_P2_DOCKER_BUILD_RESULT_CODEX_REVIEW_B7B12C1.md` (GO — model-load smoke only)
> **compatibility smoke เท่านั้น** — ไม่ใช่ quality/latency benchmark · ยัง NO-GO สำหรับ M4 / N-sweep / decision benchmark

## คำสั่งที่รัน (full image ID, network disabled)
```
docker run --rm --network none sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190 python p2_model_smoke.py
```
- started 2026-08-05T04:29:20Z · ended 2026-08-05T04:29:33Z (~13s) · **exit code 0**

## stdout — `SMOKE OK`
```json
{"model_revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
 "file_manifest_sha256": "c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013",
 "baked_manifest_match": true, "dtype": "torch.float32",
 "torch": "2.3.1+cpu", "transformers": "4.41.2", "scores_finite": true}
```

## Acceptance (Codex criteria) → **ผ่านทุกข้อ**
| criterion | ผล |
|---|---|
| exit code = 0 | ✔ |
| stdout มี `SMOKE OK` | ✔ |
| model_revision == pin | ✔ `953dc6f6f85a…d41e` |
| file_manifest_sha256 == baked | ✔ `c969d1f67f17…9013` |
| baked_manifest_match=true | ✔ (recomputed snapshot manifest == baked manifest) |
| scores_finite=true | ✔ (2 finite floats) |
| torch/transformers ตรง dependency evidence | ✔ `2.3.1+cpu` / `4.41.2` (== wheelhouse.freeze) |
| stderr ไม่มี network fallback/download/traceback | ✔ (ดูหมายเหตุ) |

## หมายเหตุ stderr (413 bytes, ไม่มี traceback)
มี **warning เดียว** จาก transformers:
> "You are offline and the cache for model files … has been updated … It is very likely that all your calls to `from_pretrained()` will fail. …"

เป็น heuristic warning ของ transformers offline mode — **ไม่ได้เกิด failure จริง**: model โหลดสำเร็จจาก baked local snapshot (พิสูจน์ด้วย `baked_manifest_match=true` + finite scores + exit 0) · `--network none` การันตีว่าไม่มี download/network fallback · ไม่มี traceback (เหลือแค่ tqdm `0it [00:00]`)

## artifacts (`.p2_build/cpu-build-1/`, gitignored)
| file | sha256 |
|---|---|
| smoke.stdout | `8f90ae40a20acb9e5eaaa4192b984dfd1d682ccd634a43d34ed05a1f87e87d98` |
| smoke.stderr | `47fc7d6ad88dcaac3eed6fb6f48b7a469e96c1e93173e8d8d11fc462280336e8` |
| smoke_meta.json | command + full iid + timestamps + exit_code + log hashes |

## ค่ายืนยันพร้อมเข้า RunPlan (หลัง gate ถัดไปครบ)
- `image_digest` = `sha256:27768971905ebd3e…a190` · `model_commit`/`tokenizer_commit` = `953dc6f…`
- `model_file_manifest_sha256` = `c969d1f67f17…9013` (baked == runtime-loaded, ยืนยันจาก smoke)
- `inference_config` = torch 2.3.1+cpu / transformers 4.41.2 / device cpu / dtype float32

## ขอ Codex review
1. smoke ผ่าน acceptance ครบไหม (โดยเฉพาะ baked-manifest match + offline load + finite scores) ; transformers offline-warning ยอมรับได้ไหม
2. ขั้นถัดไป: **real M4 (permission-leak proof) บน isolated Qdrant** — ยัง NO-GO ; ต้องการ plan/gate อะไรก่อนปลดเป็น GO
3. decision benchmark ยัง NO-GO จน Data Owner sign-off + validated M4/canary (ตาม gate เดิม)

**สถานะ:** CPU compatibility artifact + model-load smoke ครบแล้ว — latency/decision ของ CPU image ห้ามนำไปตัดสิน GPU/production
