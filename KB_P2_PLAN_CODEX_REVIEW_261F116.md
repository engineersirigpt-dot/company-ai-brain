# Codex Review — P2 Reranker Plan (`261f116`)

**วันที่:** 2026-08-04  
**Target:** `KB_P2_PLAN.md`, commit `261f116`  
**Scope:** design/measurement contract ก่อนเขียน Slice 1  
**ไม่ได้แก้:** implementation, `STATUS.md`, Qdrant หรือ service ที่รันอยู่

## Verdict: **REVISE CONTRACT, THEN GO SLICE 1**

security direction ถูก: filter ก่อนสร้าง candidates, reranker เห็นเฉพาะ authorized points, permission suite แยกจาก quality และไม่แตะ production/cloud แต่ plan ยังรวมสองการทดลองคนละชนิดเข้าด้วยกัน และ label/metric contract ยังไม่พอตัดสินว่าโมเดลช่วยงานบริษัทจริงหรือไม่

หลังปรับตาม B1/B2 ด้านล่าง ให้เริ่ม Slice 1 ได้ทันที ไม่ต้องรอ deploy gate

## Intent / simpler design

เป้าหมาย P2 ควรแบ่งเป็นสองคำถามอิสระ:

1. **Ordering experiment:** เมื่อ dense retrieval หา authorized candidate pool เดิมมาแล้ว cross-encoder หรือ rank fusion จัดลำดับได้ดีกว่า dense score หรือไม่
2. **Candidate-generation experiment:** dense+sparse hybrid ช่วยนำ relevant point ที่ dense หาไม่เจอเข้ามาใน candidate pool หรือไม่

วิธีที่เล็กและวัดเหตุได้ชัดคือทำข้อ 1 ก่อน โดยมีเพียง `dense`, `rerank`, `fused_rrf` บน candidate IDs ชุดเดียว แล้วค่อยทำข้อ 2 เป็น slice แยกเมื่อ collection มี sparse vectors จริง

## Blockers ก่อนเขียน Slice 1

### B1 — arm ที่เรียก “hybrid” ยังไม่ใช่ hybrid retrieval

`KB_P2_PLAN.md:15,29` นิยาม hybrid เป็นการ fuse `dense_rank` กับ `rerank_rank` ของ candidate pool เดียวกัน นี่คือ **rank fusion/ensemble ordering** ไม่ใช่ dense+sparse hybrid retrieval

ปัจจุบัน `ingest.py:216-219` สร้าง Qdrant collection ด้วย dense `VectorParams` ชุดเดียว ไม่มี sparse vector/index ดังนั้น Slice 2 ตาม plan ปัจจุบันยังสร้าง hybrid arm จริงไม่ได้

**แก้ contract:**

- เปลี่ยนชื่อ arm ใน P2a เป็น `fused_rrf`
- P2a เปรียบเทียบ `dense_order` vs `cross_encoder_order` vs `fused_rrf_order` บน exact candidate-ID universe เดียวกัน
- แยก P2b hybrid retrieval เป็นงานภายหลัง: dense search + sparse search โดย **ทั้งสอง branch ใส่ compiled ACL filter ก่อน retrieval** → union/dedupe candidates → RRF หรือ cross-encoder rerank
- กติกา “candidate set เดียวกัน” ใช้กับการทดลอง ordering ภายใน P2a; ห้ามบังคับข้าม dense vs dense+sparse candidate-generation เพราะจุดประสงค์ของ hybrid คือเปลี่ยน candidate recall

หากไม่แยก จะสรุปผิดได้ว่า “hybrid ดีขึ้น” ทั้งที่ระบบไม่เคยใช้ sparse retrieval

### B2 — relevance label ต้องล็อกก่อนเขียน metrics

`eval_set.json` ปัจจุบันมี 100 cases แต่ทั้ง 100 ไม่มี `role` และไม่มี `relevant_point_ids`; ใช้เพียง `expected_source` จึงไม่พอวัด chunk ordering, multi-relevance, graded relevance หรือยืนยันว่า label อยู่ใน ACL ของผู้ถาม

`p5b` canary ห้ามใช้เป็น relevance anchor: query/token ถูกสร้างเพื่อพิสูจน์ permission presence/absence จึงง่ายและมี distribution คนละแบบกับคำถาม retrieval จริง

**ขั้นต่ำของ `p2_eval_set.json`:**

```json
{
  "query_id": "q-001",
  "query": "...",
  "role": "qc",
  "lang": "th",
  "category": "sibling-hard-negative",
  "split": "test",
  "relevance": {
    "<point-id-a>": 2,
    "<point-id-b>": 1
  },
  "relevant_sources": ["<stable-document-id>"],
  "label_status": "human-reviewed"
}
```

Validation ต้อง fail ก่อน eval เมื่อ query id ซ้ำ, role ไม่รู้จัก, point id/grade ผิด, relevant point ไม่มีใน frozen corpus หรือ relevant point ไม่ authorized สำหรับ role นั้น

label ต้องทำโดยไม่ดูผลแต่ละ arm ก่อน และ freeze dataset hash ก่อน benchmark เพื่อกันเลือก label/parameter ตามผล

## คำตอบ 5 ข้อ

### 1. Metric set

เพิ่ม nDCG และ candidate recall; ถอด `citation_accuracy` ออกจาก retrieval-only Slice 1/2

ชุดที่แนะนำ:

- `CandidateRecall@N` — metric บังคับ เพราะ reranker ช่วย point ที่ไม่อยู่ใน pool ไม่ได้
- `Hit@1/3/5`
- `MRR@5`
- `Recall@5`
- `nDCG@5` — ใช้ graded/multiple relevance
- document/source-level Hit และ nDCG แยกจาก point/chunk-level เพื่อไม่ให้หลาย chunk ของเอกสารเดียวทำคะแนนหลอก
- latency แยก `candidate_retrieval_ms`, `rerank_ms`, `total_ms`; รายงาน p50/p95 หลัง warm-up
- paired win/tie/loss ต่อ query และ paired bootstrap CI ของ primary-metric delta ใน Slice 2 เพื่อไม่ตัดสินจาก aggregate ที่ต่างเพียง noise

`citation_accuracy` ต้องรอ answer/citation generator เพราะ offline retrieval ไม่มี citation ให้ตรวจ หากต้องการ metric ตอนนี้ให้เรียก `source_hit`/`document_hit` ตามสิ่งที่วัดจริง

no-answer cases ต้องแยก suite: ranking ที่คืน top-k เสมอพิสูจน์ abstention ไม่ได้จนกว่าจะมี threshold/calibration contract

### 2. Fusion method

**ล็อก RRF เป็น primary/default ใน Slice 1; ยังไม่ทำ weighted-normalized เป็น decision arm**

เหตุผล: dense cosine กับ cross-encoder logits อยู่คนละ scale; per-query min-max normalization ไวต่อ outlier, equal-score/zero-range และ weight tuning ทำให้ overfit eval set ได้ RRF ใช้ rank อย่างเดียวจึง deterministic และอธิบายง่ายกว่า

Contract:

```text
rrf_score(id) = Σ 1 / (rrf_k + rank_i(id))
rank เป็น 1-based; default rrf_k=60
tie-break = dense_rank แล้ว point_id
```

ฟังก์ชันต้อง reject ranking ที่ ID ซ้ำ/หาย/เกิน candidate universe และผลลัพธ์ต้องเป็น permutation ของ input IDs แบบ exact set-equality

weighted-score เก็บเป็น follow-up หลังมี development split สำหรับ calibrate weight/normalization และ frozen test split สำหรับตัดสิน ห้าม tune บน test set

### 3. Execution order

**รับ pure offline harness ก่อน** และยังไม่ควรทำ shadow-in-live-API เป็นขั้นถัดไป

ลำดับที่อนุมัติ:

1. Slice 1 pure metrics/contracts/order/fusion ด้วย mock score function
2. Slice 2 isolated container + synthetic hard corpus + local cross-encoder
3. held-out redacted/approved company-like eval บน isolated snapshot เพื่อใช้ตัดสิน business quality
4. shadow-in-API เฉพาะหลัง production auth/audit/packaging gates พร้อม และต้องไม่ log unauthorized text/query โดยไม่ผ่าน policy

Slice 2 synthetic บอกได้ว่า pipeline/model adapter ทำงาน แต่ยังใช้ตัดสิน hardware หรือคุณภาพ corpus บริษัทไม่ได้เต็มที่

### 4. Eval-set labeling และ k

- ใช้ human-labeled synthetic set ที่มี hard negatives สำหรับ mechanics: sibling documents, Thai/English, table rows, duplicate-like titles, parent-child และ no-answer
- p5b canary คงเป็น permission regression suite เท่านั้น ห้ามปน relevance aggregate
- ก่อนข้อสรุปเชิงธุรกิจ ต้องมี held-out real/redacted/approved queries ที่ label จาก corpus จริงโดยผู้รู้เนื้อหา; synthetic-only ให้ประกาศผลเพียง “P2 mechanics PASS on synthetic”
- รายงาน output ที่ `k ∈ {1,3,5}` เพื่อเทียบทั้ง top answer, baseline Hit@3 เดิม และ candidate list 5; ตั้ง primary metric ล่วงหน้า เช่น `nDCG@5` พร้อม MRR@5

### 5. Candidate top-N

`N=30` ใช้เป็น provisional default ได้ แต่ห้ามล็อกจากการคาดเดาค่าเดียว

Slice 2 ให้ sweep `N ∈ {10,20,30,50}` บน candidate retrieval เดียว แล้วเลือก **N ต่ำสุดที่ CandidateRecall@N ถึงเกณฑ์ที่ประกาศล่วงหน้า** โดยดู rerank p95 latency และ memory ควบคู่ หาก relevant point ไม่อยู่ใน top-N ให้บันทึกเป็น candidate-generation miss ไม่ใช่ reranker miss

public `/search` ปัจจุบันจำกัด `top_k <= 10` (`app/main.py:283-284`) ดังนั้น offline N=30 ต้องใช้ internal candidate provider ที่เรียก Qdrant ด้วย compiled filter เดียวกัน ไม่ใช่เปลี่ยน API cap หรือเรียก production endpoint

## Slice 1 contract ที่อนุมัติ

### Candidate

```text
Candidate {
  point_id: non-empty unique str,
  document_id/source: stable str,
  dense_score: finite float,
  dense_rank: unique positive int,
  rerank_text: non-empty str
}
```

กำหนด `rerank_text` version แรกให้ deterministic เช่น `heading + child text` พร้อม token truncation ใน adapter; อย่าสลับ `text`/`parent_text` ตามความยาวระหว่าง arm เพราะทำให้ attribution ของผลไม่ชัด

### Required behavior/tests

- input candidates ไม่ถูก mutate
- score count ตรง candidate count; NaN/Inf/duplicate ID/missing ID → fail
- dense/rerank/fused outputs เป็น exact permutation ของ candidate IDs เดียวกัน
- stable deterministic tie-breaking
- empty input → empty output; one item → item เดิม
- instrumented `score_fn` บันทึก IDs/text ที่เห็น และ integration test ต้องใส่ unauthorized sentinel แล้วพิสูจน์ว่า sentinel ไม่เคยถึง score function
- permission failure/ERROR/INCONCLUSIVE ทำให้ quality report invalid; ห้ามรายงาน metric ต่อเหมือนผ่าน

## Acceptance ก่อนเลือก arm

ต้องประกาศเกณฑ์ก่อนรัน model จริง เช่น:

1. permission leak = 0 และ auth gate ผ่านทุก arm (hard gate)
2. candidate label coverage/ACL validation = 100%
3. rerank/fusion ต้องไม่ลด primary metric เทียบ dense เกิน tolerance ที่กำหนด และรายงาน CI/win-tie-loss
4. fused RRF ต้องชนะ rerank-only อย่างมีสาระจึงคุ้มเพิ่ม complexity; ถ้าไม่ชนะให้เลือก rerank-only
5. latency p95 ต้องอยู่ใน budget ที่บันทึกพร้อม hardware/model revision

## Handoff สั้นให้ Claude

แก้ `KB_P2_PLAN.md` เป็น rev 2 ก่อน code: เปลี่ยน hybrid arm เป็น `fused_rrf`, แยก dense+sparse hybrid เป็น P2b, ล็อก label schema + CandidateRecall@N/nDCG@5, ถอด citation accuracy จาก retrieval-only, ใช้ RRF เท่านั้นใน Slice 1, N=30 provisional พร้อม sweep 10/20/30/50 และระบุว่า synthetic เป็น mechanics evidence ไม่ใช่ business/hardware verdict เมื่อ rev 2 ปิดสอง blocker นี้แล้ว **GO เขียน Slice 1 pure ได้**
