# P2 M4a — real-run evidence (Docker + real bge-reranker + real Qdrant) — 2026-08-07

> **ครั้งแรกที่รันบน infra จริงบนเครื่อง** (Docker Desktop Linux engine) — **synthetic corpus เท่านั้น ไม่มีข้อมูลจริง**
> M4a synthetic mechanics = GO track (owner 2026-08-07) ; ไม่ต้อง Data Owner sign-off ; M4b/ข้อมูลจริง ยัง NO-GO

## Environment ที่ยืนยัน

| item | ค่าจริง |
|---|---|
| Docker | server 29.5.3 (linux engine) |
| reranker image (`plan.image_digest`) | `sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190` |
| qdrant image (`qdrant_image_ref`) | `qdrant/qdrant@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286` |
| baked model manifest (`/opt/model_file_manifest.sha256`) | `c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013` |
| torch (ใน image) | 2.3.1+cpu |

## 1. Real scorer — bge-reranker-v2-m3 ใน pinned container

รัน `load_pinned_cross_encoder(revision=PIN.MODEL_COMMIT)` ใน image (mount current source, baked model at `/opt/hf`):

```
kind                        pinned-cross-encoder
model_revision              953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e   (= PIN.MODEL_COMMIT)
tokenizer_revision          953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
model_file_manifest_sha256  c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013  (= baked)
dtype (current code)        float32       ← M1 canonical_dtype fix ทำงานบน torch จริง (torch.float32 → float32)
score('a cat sits on the mat', ['the cat is on the mat','the stock market fell today'])
     = [10.2299, -11.0317]  → relevant > irrelevant ✅ (reranker ทำงานถูก)
```

**ยืนยัน:** real model โหลดได้ + metadata ตรง pin + **M1 fix ถูกต้องบน torch จริง** + reranker rerank ถูก

## 2. Real Qdrant facade — `QdrantSession` ต่อ Qdrant container จริง

```
observed_target_identity  {'collection_id':'m4-real','endpoint':'http://localhost:6399'}  (get_collections ping)
recreate_collection       -> count 0
write_marker/read_marker  'm4-run-uuid-REAL' round-trip ✅
seed (UUID point ids)     -> count marker+3 = 4 ✅
```

**บั๊กที่เจอจากการรันจริง (offline/fake จับไม่ได้):** `QdrantSession.seed` เดิม `str(pid)` → Qdrant reject
`400 "value 0 is not a valid point ID, valid values are either an unsigned integer or a UUID"`
→ **แก้:** `_valid_qdrant_id` — point id ต้องเป็น **unsigned int หรือ UUID string** (M4 corpus/frozen ต้องใช้ id ชนิดนี้ให้ตรงกัน)

## 3. Real RBAC permission-leak proof — QdrantM4Provider + real Qdrant

seed A(qc,admin/ta), B(sales,admin/tb), **S(management/ts = sentinel, vector คล้าย A)** ; provider adapter จริง + `compile_retrieval_filter` + `to_qdrant_filter` query Qdrant จริง:

```
qc filtered texts:    ['ta']    (เห็นเฉพาะ A)
sales filtered texts: ['tb']    (เห็นเฉพาะ B)
PERMISSION-LEAK: sentinel 'ts' reached qc/sales? -> False    ← กรองก่อนถึง scorer จริง
RBAC correct: qc={ta}=True | sales={tb}=True
```

**ยืนยัน:** RBAC filter ทำงานบน **Qdrant จริง** (ไม่ใช่แค่ fake `matches_policy` model) — sentinel ที่คล้ายเชิง semantic ถูกกรองก่อน retrieval → **ปิดช่องว่าง "matches_policy เป็น model ไม่ใช่ Qdrant oracle"** ระดับ smoke (P5b conformance เต็มยังเป็นงานแยก)

## ยังเหลือ (full M4a real execution)

- `controller.execute` จริง: `docker run/exec` runner ภายใน pinned evaluator container บน `--internal` network เดียวกับ Qdrant → รับ bundle + executed image digest → `run_m4a_locked` verify (B1/B4 execution)
- point-id contract: synthetic corpus generator ต้องออก UUID/int ids + frozen ใช้ id เดียวกัน (ตาม `_valid_qdrant_id`)
- teardown จริง (network/volume/container) + IsolationProof จาก Docker inspect จริง

ทุก container ที่รันทดสอบ **teardown แล้ว** (ไม่มี m4qd ค้าง) ; ยังไม่แตะข้อมูลจริง
