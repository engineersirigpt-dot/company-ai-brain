# P2 — เลือก model commit + Dockerfile.p2 pinned (WRITE-ONLY, ไม่ build/run) → review

> **สืบเนื่อง:** `KB_P2_RUNPLAN_FIX2_CODEX_REREVIEW_797CE36.md` (GO: เลือก commit full 40-hex + Dockerfile.p2 pinned)
> + `KB_P2_MODEL_COMMIT_CODEX_CROSSCHECK.md` (Codex GO pin `953dc6f…`)
> + FIX-BEFORE-RUN M1/M2/N1 ปิดแล้ว (`9afe8bf`)
> **ยังไม่ build / ไม่ run / ไม่โหลด model** — ทุกไฟล์เป็น artifact เขียนลง disk เท่านั้น

## Pin ที่เลือก
```
model_name       = BAAI/bge-reranker-v2-m3
model_commit     = 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e   (full 40-hex, HEAD ของ main)
tokenizer_commit = 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e   (snapshot เดียวกัน)
```
ที่มา: ดึงจาก HF API (`revision/main` + commit history) → Codex cross-check ยืนยัน (tree ณ SHA นี้มีไฟล์บังคับครบ 6)

## ไฟล์ที่เพิ่ม (pure/offline artifacts)
| ไฟล์ | บทบาท |
|---|---|
| `p2_pin.py` | single source of truth: `MODEL_COMMIT`/`TOKENIZER_COMMIT`/`REQUIRED_FILES`/`MIN_SAFETENSORS_BYTES` |
| `p2_fetch_model.py` | **build stage (network)**: `snapshot_download(revision=SHA)` → assert resolved==pinned → verify 6 files + safetensors เป็น blob จริง → เขียน canonical file-manifest sha256 |
| `p2_model_smoke.py` | **runtime (offline)**: load ผ่าน `load_pinned_cross_encoder` → assert resolved SHA + score finite (ขั้น manual แยก, **ไม่รันตอน build**) |
| `Dockerfile.p2` | multi-stage: fetch(network) + runtime(offline `HF_HUB_OFFLINE`) ; copy ทั้ง HF cache รักษา `snapshots/<SHA>` layout ; build-time verify snapshot resolve ได้ ; **ไม่รัน benchmark** |
| `docker-compose.p2.yml` | service reranker `network_mode: none` + `profiles:[manual]` (กัน `up` เผลอรัน) ; ไม่มี M4/N-sweep service |
| `requirements.p2.txt` | pin torch/transformers/hf_hub/safetensors/sentencepiece (baseline — ยืนยัน CUDA target ก่อน build) |
| `test_p2_pin.py` | 7/7 — pin เป็น full 40-hex + ผ่าน `validate_pin` + REQUIRED_FILES ครบ + 7-hex ย่อถูก reject |

## Codex gotchas (จาก cross-check) → ปิดตรงไหน
| # | gotcha | ที่ปิด |
|---|---|---|
| 1 | build network vs runtime offline | Dockerfile 2 stage: fetch (มี net) / runtime (`HF_HUB_OFFLINE=1`,`TRANSFORMERS_OFFLINE=1`) |
| 2 | คง `snapshots/<SHA>` layout ให้ `_resolved_commit` ผ่าน | ใช้ `HF_HOME` cache mode + `COPY --from=fetch /opt/hf /opt/hf` + build-time assert dir `snapshots/<SHA>` |
| 3 | symlink/blobs ครบ | copy ทั้ง `$HF_HOME` (snapshots+blobs) ไม่ flatten |
| 4 | real blob ไม่ใช่ pointer | `p2_fetch_model` เช็ค `model.safetensors` >= `MIN_SAFETENSORS_BYTES` |
| 5 | required files + canonical manifest | verify 6 ไฟล์ + `_snapshot_manifest_sha256` → เขียนไฟล์ให้ RunPlan |
| 6 | pin environment | `requirements.p2.txt` + `PY_BASE` เป็น digest ARG |
| 7 | image digest หลัง build | compose comment: `docker inspect` → `RunPlan.image_digest` (ไม่ใช้ tag) |
| 8 | ห้าม fallback main | `revision=SHA` ตรง ; pip/fetch ล้ม = build ล้ม |
| 9 | model-load smoke ยังจำเป็น | `p2_model_smoke.py` เป็นขั้น manual แยก (ไม่รันตอน build) |

## `<<FILL-AT-BUILD>>` (ค่าที่รู้ได้เฉพาะตอน build จริง — ไม่เดา)
- `PY_BASE` = python base image **digest** (เลือก CUDA base ให้ตรง 4x4090)
- `model_file_manifest_sha256` = output ของ `p2_fetch_model` จาก snapshot จริง → `RunPlan.model_file_manifest_sha256`
- `image_digest` = `docker inspect` หลัง build → `RunPlan.image_digest`
- torch/transformers versions = ยืนยันให้ตรง CUDA target

## ผลรัน (offline)
```
test_p2 179 · test_p2_runplan 94 · provider 22 · harness 21 · test_p2_pin 7 · policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```

## ขอ Codex review
1. Dockerfile.p2/compose/fetch/smoke ปิด gotchas 1–9 ครบไหม (โดยเฉพาะ cache layout #2/#3 + real blob #4 + no-fallback #8)
2. อนุมัติ **build image (fetch snapshot + verify, ยังไม่ run benchmark)** ได้ไหม — หรือมีอะไรต้องปิดก่อน `docker build`
3. หลัง build → บันทึก image_digest + file-manifest → model-load smoke (`p2_model_smoke`) = ปลดเป็น GO ได้เมื่อไหร่
4. runner adapter (harness rows → bound evidence, join frozen cases) — เขียนคู่รอบถัดไปหรือรอบนี้

**สถานะ gate ที่ยังคง:** real M4 / N-sweep / decision benchmark = NO-GO จน smoke ผ่าน + Data Owner sign-off + validated M4/canary
