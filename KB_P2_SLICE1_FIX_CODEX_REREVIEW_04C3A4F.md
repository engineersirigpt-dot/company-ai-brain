# Codex Targeted Re-review — P2 Slice 1 fixes

**Commit reviewed:** `04c3a4f`  
**Input:** `KB_P2_SLICE1_FIX_HANDOFF.md`  
**Scope:** เฉพาะ B1/B2/M1/M2/M3/M5 และ go/no-go สำหรับ Slice 2; ไม่ทบทวน P1/P5b  
**Verdict:** **FIX-THEN-GO — ยังไม่เริ่ม Slice 2**

แกน ordering/metrics ดีขึ้นชัดเจน และ **B1, M2 ปิดแล้ว** แต่ label/corpus boundary ยังรับ policy payload ผิดรูปได้หนึ่งทาง และ frozen evidence ยังไม่บังคับข้อมูลที่นำไปวัดให้ครบ จึงควรปิด pure/offline อีกรอบเล็กก่อนเสียเวลา build container/model

## Findings

### B2.1 — `is_authorized()` ยังรับ scalar `allowed_roles` ซึ่งผิด stored-policy contract

**ตำแหน่ง:** `p2_eval.py:28-32`, `policy.py:152-174`, `policy.py:295-308`

`is_authorized()` เรียก `matches_policy()` อย่างเดียว ขณะที่ matcher จงใจเลียนแบบ Qdrant `MatchAny`: payload แบบ `allowed_roles: "qc"` จึง match role `qc` ได้ แม้ policy-v1 กำหนดให้ field นี้ต้องเป็น list และ write boundary ต้อง quarantine ข้อมูลดังกล่าว

ผลคือ label validator ยังสามารถรับ point ที่ระบบไม่ควรยอมรับเป็น active corpus ได้ ทำให้คำกล่าวว่า B2 ปิดแล้วไม่จริงครบถ้วน

**Required fix:** frozen corpus ต้องผ่านทั้ง stored-shape validation และ query-time policy:

```python
if not P.payload_is_policy_v1(payload):
    return False
valid, _ = P.validate_stored_payload(payload)
return valid and P.matches_policy(
    payload,
    P.compile_retrieval_filter(_effective_access(role)),
)
```

เพิ่ม regression อย่างน้อย: scalar `allowed_roles`, non-list/null, bool/float schema, marker ไม่ครบ, unknown role, empty ACL ต้อง unauthorized; policy-v1 ที่ valid เท่านั้นจึงผ่าน

### M5.1 — `relevant_sources` ตรวจเพียง subset จึงยังใส่ source ปลอม/ซ้ำได้

**ตำแหน่ง:** `p2_eval.py:90-111`

ตอนนี้ตรวจเพียงว่า source ของ relevant point แต่ละตัว “อยู่ใน” `relevant_sources` ดังนั้น label เช่น relevant point มาจาก `D1` แต่ประกาศ `relevant_sources=["D1", "D999"]` ยังผ่าน ทั้งที่ document/source-level metric จะใช้ ground truth ที่เกินจริง

**Required fix:**

- ห้าม source ซ้ำ
- derive source set จาก relevant point IDs ใน corpus
- บังคับ **exact set-equality** กับ `relevant_sources`
- error ต้องบอกทั้ง missing และ extra sources

### M1.1/M5.2 — freeze hash มีแล้ว แต่ corpus/case ว่างหรือ corpus entry ผิด shape ยังผ่านหรือ crash ทีหลัง

**ตำแหน่ง:** `p2_eval.py:46-55`, `p2_eval.py:107-111`, `p2_eval.py:120-137`

- `cases=[]` คืน error ว่าง ทำให้ benchmark ศูนย์คำถามดูเหมือน contract ผ่าน
- ไม่มี validator ระดับ corpus สำหรับ point id, `source`, `rerank_text`, `payload`
- code สมมติว่า `corpus[pid]` เป็น dict; malformed entry อาจ `AttributeError`
- `corpus_manifest_sha256()` ยอม text ว่างผ่าน `or ""` และอาจ crash เมื่อ text เป็นชนิดอื่น

**Required fix:** เพิ่ม frozen-corpus validator และให้ runner/manifest fail ก่อนคำนวณเมื่อ cases/corpus ว่างหรือ entry ผิด shape โดยบังคับอย่างน้อย:

- point id/source/rerank_text เป็น non-blank string ไม่มี control character
- payload เป็น policy-v1 ที่ `validate_stored_payload()` ผ่าน
- point id ไม่ซ้ำโดย construction และ corpus ไม่ว่าง
- ranking cases ไม่ว่าง
- `benchmark_manifest()` สร้างได้หลัง validation ผ่านเท่านั้น หรือรับ validated object ที่แบ่ง type ชัด

**Slice 2 evidence เพิ่มเติม:** hash สองตัวปัจจุบันยังไม่ผูก actual vector/index ที่กำหนด candidate ranking ให้บันทึก `retrieval_index_manifest_sha256` (หรือ digest actual stored vectors) พร้อม Qdrant collection UUID/point count, embedding model+revision, vector/index params และ run marker ด้วย มิฉะนั้น text/payload เดิมแต่ vector เปลี่ยนจะได้ corpus hash เดิม

### M3.1 — public helper guards ยังไม่ปิดครบตาม claim

**ตำแหน่ง:** `rerank.py:81-115`, `retrieval_metrics.py:48-81`

- `fused_rrf()` เช็ค key coverage ของ `dense_rank_map` แต่ไม่เช็คค่า rank ว่าเป็น positive unique int
- `dense_rank_map()` ไม่ validate candidate contract เอง
- Hit/MRR/Recall/CandidateRecall validate ranked IDs แต่ไม่ validate `relevant_ids`; string เดี่ยวจะถูก `set()` เป็นชุดตัวอักษร

นี่ไม่ใช่ช่อง permission leak แต่ทำให้ evidence ผิดความหมายจาก caller/config typo ได้

**Required fix:** validate `dense_rank_map` values และ relevant IDs ที่ public boundary พร้อม regression สำหรับ bool/float/0/negative/duplicate rank และ relevant IDs ผิดชนิด/ว่าง/ซ้ำตาม semantics ที่เลือก

### M4 — disposition ถูกต้อง: ต้องปิดใน Slice 2 integration

การไม่ทำ sentinel integration ใน pure Slice 1 ถูกต้อง แต่ Slice 2 จะผ่านไม่ได้จนกว่าจะพิสูจน์ครบ:

- unauthorized semantic twin อยู่จริงใน isolated Qdrant
- candidate provider รับ trusted `EffectiveAccess` และ compile filter เดียวกับ API
- independent scroll oracle ยืนยัน authorized set
- spy cross-encoder ไม่เคยเห็น unauthorized ID **และ text**
- dense/rerank/fused เป็น exact permutation/subset ตาม contract ของ authorized candidate pool
- P5b canary ผ่านทุก arm โดย `leak=0`, auth VERIFIED และไม่มี ERROR/INCONCLUSIVE

## สถานะ targeted findings เดิม

| Finding | สถานะ |
|---|---|
| B1 permission gate exact-int | **CLOSED** |
| B2 full-policy label authorization | **OPEN — B2.1 scalar/malformed policy** |
| M1 eval+corpus freeze | **PARTIAL — hashes มีแล้ว; validation/vector evidence ยังไม่ครบ** |
| M2 ranking/no-answer separation | **CLOSED** |
| M3 metric/RRF guards | **PARTIAL — boundary gaps ด้านบน** |
| M5 schema/source consistency | **PARTIAL — ต้อง exact source set + corpus shape** |
| M4 sentinel integration | **DEFERRED CORRECTLY; mandatory Slice 2 gate** |

## Acceptance ที่ล็อกก่อน model run

ต้องแยกสอง verdict: **benchmark valid** กับ **model arm eligible** โมเดลไม่ชนะไม่ควรทำให้ benchmark ถูกตีว่า fail

### 1. Dataset และ split

- `test` ranking cases อย่างน้อย **50 cases**; น้อยกว่านี้เรียกได้เพียง engineering smoke ไม่ใช้เลือก arm
- dev ใช้เลือก `N`; test ใช้รายงานครั้งเดียว ห้ามปรับ N/threshold จากผล test
- primary metric เดียว: **mean paired `nDCG@5`**
- `MRR@5`, Hit/Recall และ document-level metrics เป็น secondary/diagnostic
- แก้ `KB_P2_PLAN.md:24` ให้เลิกระบุ nDCG และ MRR เป็นสอง primary ก่อน freeze contract

### 2. Candidate pool และการเลือก N

Sweep `N={10,20,30,50}` บน dev แล้วเลือก N ต่ำสุดที่ผ่านพร้อมกัน:

- macro point-level `CandidateRecall@N >= 0.95`
- macro document-level `CandidateRecall@N >= 0.95`
- `CandidateHit@N = 1.00` ทุก ranking case (อย่างน้อยหนึ่ง relevant point อยู่ใน pool)

นำ N ที่เลือกไปใช้กับ test โดยไม่เปลี่ยน หากไม่มี N ใดผ่าน หรือ test หลุด target: benchmark mechanics ยังรายงานได้ แต่ **ห้ามสรุปว่า reranker ชนะ/แพ้ retrieval ทั้งระบบ**; verdict คือ candidate-generation limited และส่งต่อ P2b

### 3. Improvement และ paired CI

ใช้ paired bootstrap ที่ระดับ query, **10,000 resamples**, fixed seed ที่บันทึกก่อน run, 95% percentile CI:

- rerank มีสิทธิ์แทน dense เมื่อ mean `ΔnDCG@5 >= +0.02` และ CI lower bound `>= 0.00`
- ถ้า CI lower bound `>= -0.01` แต่ไม่ถึงเกณฑ์ด้านบน: ถือว่า non-inferior เท่านั้น; คง dense เป็น default เพราะยังไม่คุ้ม complexity
- ถ้า CI lower bound `< -0.01`: rerank arm ไม่ผ่าน
- fused RRF มีสิทธิ์แทน rerank เมื่อเพิ่มอีก `>= +0.01` และ CI lower bound `>= 0.00`; มิฉะนั้นเลือก arm ที่ง่ายกว่า
- hard-negative category ใดมี mean delta `< -0.05` ให้ arm นั้นไม่ผ่าน แม้ค่าเฉลี่ยรวมผ่าน

### 4. Latency budget

บันทึก CPU/GPU/RAM/CUDA/container digest/model+tokenizer revision/batch size ก่อนรัน ใช้ warm-up อย่างน้อย 10 calls และ measured samples อย่างน้อย 150 (เช่น 3 repeats × 50 test cases), synchronize GPU ก่อนจับเวลา:

- incremental rerank `p95 <= 1,500 ms/query`
- candidate retrieval + rerank total `p95 <= 2,500 ms/query`
- RRF compute overhead `p95 <= 10 ms/query` ที่ N ไม่เกิน 50
- error/OOM = 0

ถ้ารัน CPU-only ผลคุณภาพยังใช้ได้ แต่ latency เป็น diagnostic เท่านั้น ไม่ถือเป็น hardware/deploy verdict

### 5. Benchmark-valid hard gates

ผล quality publish ได้เมื่อทั้งหมดเป็นจริงเท่านั้น:

- frozen validation ผ่าน, hashes/manifest ตรง และไม่มี duplicate/unknown label
- permission gate: leak=0, auth VERIFIED, ERROR=0, INCONCLUSIVE=0
- M4 sentinel ไม่ถึง scorer และ exact authorized-set assertions ผ่านทุก arm
- arm output เป็น exact candidate permutation, ไม่มี missing/extra/duplicate
- evidence บันทึก commit, image/model/tokenizer/embedding revisions, Qdrant/index manifest, config, seed, raw per-query metrics/latencies และ aggregate CI

## Go / No-Go

**NO-GO Slice 2 ณ commit `04c3a4f`.** ปิด B2.1, M5.1, M1.1/M5.2 และ M3.1 ใน pure code/tests ก่อน แล้วส่ง targeted re-review อีกครั้ง ไม่ต้องแตะ container/model ระหว่างแก้

เมื่อจุดเหล่านี้ผ่าน ให้ **GO Slice 2 แบบ isolated/local/synthetic** ตาม scope ที่เสนอ: pinned `bge-reranker-v2-m3`, internal candidate provider ผ่าน trusted EffectiveAccess/compiled filter, human-reviewed frozen synthetic corpus + hard negatives, N sweep, durable evidence, P5b canaries และ M4 sentinel integration โดยยังคง **NO-GO production/deploy/cloud/real company data**

## Verification note

รีวิวนี้ตรวจ static code/diff ที่ commit `04c3a4f`; ไม่แก้โค้ดหรือ `STATUS.md` และไม่ได้ยืนยันตัวเลข `80/80` ซ้ำ เพราะ `python.exe` ใน environment นี้ถูกระบบปฏิเสธการเข้าถึง ตัวเลข suite ใน handoff จึงถือเป็นผลที่ผู้ implement รายงาน ไม่ใช่ผล rerun โดย Codex
