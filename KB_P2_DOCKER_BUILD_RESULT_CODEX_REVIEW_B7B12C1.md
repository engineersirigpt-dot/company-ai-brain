# Codex Review — P2 CPU build result (`b7b12c1`, image `277689…a190`)

วันที่รีวิว: 2026-08-05  
Build source commit: `7d68886cbb6db6909b8a23994246212234af547a`  
Artifact: `.p2_build/cpu-build-1/`  
ขอบเขต: read-only verification ของ receipt/source/evidence/log/local image; ไม่ execute containerหรือโหลด model

## Verdict

**GO — model-load smoke only**

CPU fetch/verify buildผ่าน gateที่กำหนด: receiptถูกต้อง, source/evidence/log hashesตรงไฟล์จริง, local image identity/tag/platformตรง receipt และ build logยืนยัน base/model/wheel/offline gatesครบ

ยัง **NO-GO** สำหรับ real M4, N-sweep, decision benchmark และการนำผล latencyของ CPU imageไปตัดสิน GPU/production

## หลักฐานที่ตรวจยืนยัน

### Receipt lifecycle

- `build_receipt.json`: `status=SUCCEEDED`, `return_code=0`
- ไม่มี `build_failure.json`
- `validate_receipt(receipt)` คืน `[]`
- iid file, receiptและ Docker daemonตรงกัน:
  `sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190`

### Read-only Docker inspect

ตรวจ imageจาก daemonโดยไม่ execute container:

- `Id` ตรง receipt
- tag = `company-ai-brain/p2-reranker:pinned-cpu`
- OS/architecture = `linux/amd64`
- environmentระบุ `PYTHON_VERSION=3.11.15`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`
- default commandไม่ใช่ benchmark

### Hash binding

คำนวณ SHA256ใหม่จากไฟล์บนดิสก์:

- source hashes **9/9 ตรง receipt**
- evidence hashes **3/3 ตรง receipt**
- `build.log` hashตรง receipt
- model manifest contentตรง receipt:
  `c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013`
- build source 9 ไฟล์ไม่มี diffจาก commit `7d68886…` ถึง HEADปัจจุบัน

`git_dirty=true` จึงไม่ใช่ clean-tree build แต่ไม่ทำให้ artifactนี้กำกวม เพราะ dirty filesไม่ได้อยู่ใน exact source setและ source 9/9ถูก bindด้วย hashesแล้ว ปัจจุบัน `git status` เหลือเพียง `tmp/`

### Build log

- baseใช้ exact platform digest `python@sha256:78b39e…4553`
- Docker contextจริงเพียง **18.95 kB**; allowlistทำงาน ไม่ส่ง corpusหลาย GB
- model resolveเป็น exact commit `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`
- `model.safetensors` = **2,271,071,852 bytes**
- wheel manifest **24 rows**, ทุก wheelผ่าน `sha256sum -c`
- runtime freeze **24 packages**
- offline snapshot verificationผ่านก่อน image finalize
- warningsใน logมีเพียง pip-as-root warningตามปกติของ container build ไม่พบ build error

### Base digest provenance

`KB_P2_BASE_DIGEST_RESOLUTION.md` บันทึก index digestและ `linux/amd64` child digestแยกกัน พร้อมวิธี recomputeจาก raw registry JSON จึงสอดคล้องกับ digestที่ buildใช้

ข้อปรับปรุงแบบไม่ block: raw index JSONยังเก็บนอก run directory รอบถัดไปควร copyเข้า artifact directoryและ hashใน receiptเพื่อให้ provenance bundle self-contained

## Model-load smoke ที่อนุมัติ

ใช้ **full image ID จาก receiptเท่านั้น** ห้ามใช้ค่าตัดด้วย `…` และไม่ใช้ tag/compose rebuild:

```powershell
docker run --rm --network none sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190 python p2_model_smoke.py
```

ขอบเขตของ smokeนี้มีเพียง:

1. imageทำงานได้แบบ network disabled
2. model/tokenizerโหลดจาก local snapshotได้
3. resolved model/tokenizer commitตรง pin
4. recomputed snapshot manifestตรง baked manifest
5. scoringตัวอย่างคืน float finiteครบสองค่า

นี่เป็น compatibility smoke ไม่ใช่ quality/latency benchmark

## Acceptance หลัง smoke

ถือว่า smokeผ่านเมื่อ:

- process exit code = 0
- stdoutมี `SMOKE OK`
- `model_revision` = `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`
- `file_manifest_sha256` = `c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013`
- `baked_manifest_match=true`
- `scores_finite=true`
- torch/transformers versionsตรง dependency evidence
- stderrไม่มี network fallback/model downloadหรือ traceback

ให้เก็บ full command, full iid, stdout/stderr, exit code, start/end timestamp และ SHA256ของ smoke logไว้ใต้ run directoryเดียวกัน แล้วส่งกลับมา targeted review

## Gate หลังจากนี้

- **GO ตอนนี้:** model-load smokeคำสั่งข้างต้นเท่านั้น
- **NO-GO:** real M4, Qdrant/model integration, N-sweep, latency benchmark, decision benchmark และการประกาศเลือก arm
- Data Owner sign-off + validated M4/canaryยังเป็น gateก่อน decision benchmarkตามเดิม

Verdict สั้น: **GO SMOKE-ONLY — build artifactและ receiptผูกกันครบแล้ว; ขั้นถัดไปคือพิสูจน์ว่าโมเดลโหลด offlineและให้ finite scoresได้จริง**
