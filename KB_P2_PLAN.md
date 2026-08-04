# P2 — Reranker ordering experiment (rev 2, ปิด Codex B1/B2)

> **สืบเนื่อง:** `KB_P2_PLAN_CODEX_REVIEW_261F116.md` verdict **REVISE CONTRACT, THEN GO SLICE 1**
> **ขอบเขต:** local + synthetic · offline เท่านั้น · ไม่แตะ production/collection จริง · ไม่ egress cloud
> **สถานะหลักฐาน:** synthetic = **mechanics evidence เท่านั้น** — ยังใช้ตัดสิน business quality / hardware ไม่ได้

## แยกเป็นสองการทดลองอิสระ (Codex intent)
- **P2a — Ordering experiment (ทำก่อน):** candidate pool เดิม (dense retrieval + ACL filter) แล้วถาม
  *cross-encoder / rank fusion จัดลำดับดีกว่า dense score ไหม* — arm = `dense`, `rerank`, `fused_rrf` บน **candidate-ID universe เดียวกัน**
- **P2b — Candidate-generation experiment (ภายหลัง):** dense+sparse **hybrid retrieval** — *sparse ช่วยนำ relevant point ที่ dense หาไม่เจอเข้า pool ไหม* — เปลี่ยน candidate recall จึง**ไม่**บังคับ candidate set เดียว ; ต้องมี sparse vector/index ก่อน (`ingest.py` ตอนนี้มี dense `VectorParams` ชุดเดียว) ; ทั้ง dense+sparse branch ต้องใส่ compiled ACL filter **ก่อน** retrieval → union/dedupe → RRF/rerank

> **B1 ปิด:** เดิม "hybrid" คือ rank-fusion ของ pool เดียว = ensemble ordering ไม่ใช่ dense+sparse hybrid → เปลี่ยนชื่อเป็น `fused_rrf` และแยก P2b ออก (ไม่งั้นสรุปผิดว่า "hybrid ดีขึ้น" ทั้งที่ไม่เคยมี sparse)

## Guardrail (Codex 5 ข้อ — คงเดิม)
filter ก่อน candidates เสมอ (reranker เห็นเฉพาะ authorized point แม้ shadow) · candidate set เดียวใน P2a ordering · retrieval-quality metric แยกจาก permission suite (`TOP_K=10` ของ P5b ไม่นับเป็น quality) · p5b canary leak=0 = permission regression **เท่านั้น** · offline ไม่ egress

## Metrics (Codex Q1) — retrieval-only, ถอด citation accuracy
- **`CandidateRecall@N`** (บังคับ — reranker ช่วย point นอก pool ไม่ได้)
- `Hit@1/3/5` · `MRR@5` · `Recall@5` · **`nDCG@5`** (graded/multi-relevance)
- **แยก point/chunk-level ออกจาก document/source-level** Hit + nDCG (กันหลาย chunk ของเอกสารเดียวทำคะแนนหลอก)
- latency แยก `candidate_retrieval_ms` / `rerank_ms` / `total_ms` — p50/p95 หลัง warm-up
- Slice 2: paired **win/tie/loss** ต่อ query + paired **bootstrap CI** ของ primary-metric delta
- `citation_accuracy` ตัดออก (offline retrieval ไม่มี citation) → ใช้ `source_hit`/`document_hit` ; no-answer แยก suite (abstention ต้องมี threshold contract ก่อน)
- **primary metric เดียว = mean paired `nDCG@5`** ; `MRR@5`/Hit/Recall/document-level = key secondary/diagnostic ; รายงาน k ∈ {1,3,5}

## Fusion (Codex Q2) — RRF only ใน Slice 1
```
rrf_score(id) = Σ_i  1 / (rrf_k + rank_i(id))       # rank 1-based ; default rrf_k = 60
tie-break = dense_rank แล้ว point_id
```
ผลลัพธ์ต้องเป็น **exact permutation** ของ input IDs ; reject ranking ที่ ID ซ้ำ/หาย/เกิน candidate universe.
**weighted-normalized-score = follow-up** (dense cosine vs cross-encoder logits คนละ scale, min-max ไวต่อ outlier/overfit) — ต้องมี dev split calibrate + frozen test split ตัดสิน ; ห้าม tune บน test set

## Relevance label (Codex B2) — ล็อกก่อนเขียน metrics
`eval_set.json` เดิม (100 cases) ไม่มี `role`/`relevant_point_ids` ใช้แค่ `expected_source` → ไม่พอ. **p5b canary ห้ามเป็น relevance anchor** (distribution คนละแบบ). `p2_eval_set.json` ขั้นต่ำ:
```json
{ "query_id": "q-001", "query": "...", "role": "qc", "lang": "th",
  "category": "sibling-hard-negative", "split": "test",
  "relevance": { "<point-id-a>": 2, "<point-id-b>": 1 },
  "relevant_sources": ["<stable-document-id>"], "label_status": "human-reviewed" }
```
Validation **fail ก่อน eval** เมื่อ: query_id ซ้ำ · role ไม่รู้จัก · point_id/grade ผิด · relevant point ไม่อยู่ใน frozen corpus · **relevant point ไม่ authorized สำหรับ role นั้น**. label โดยไม่ดูผล arm ก่อน + **freeze dataset hash** ก่อน benchmark. hard negatives: sibling docs, Thai/Eng, table rows, dup-like titles, parent-child, no-answer.

## Candidate contract (Slice 1 — Codex approved)
```
Candidate { point_id: non-empty unique str · document_id/source: stable str ·
            dense_score: finite float · dense_rank: unique positive int · rerank_text: non-empty str }
```
`rerank_text` v1 deterministic = `heading + child text` + token-truncate ใน adapter ; **ห้ามสลับ text/parent_text ตามความยาวระหว่าง arm** (attribution ไม่ชัด)

## Slice
- **Slice 1 (offline, ทำทันที — GO):** `retrieval_metrics.py` + `rerank.py` (Candidate validate, `rerank_order`, `fused_rrf`) + `p2_eval.py` (eval-set validate) + `test_p2.py` ด้วย **mock `score_fn`**
  - required tests: input ไม่ mutate · score count == candidate count, NaN/Inf/dup/missing → fail · output = exact permutation · tie-break deterministic · empty→empty, one→same · **instrumented score_fn: unauthorized sentinel ต้องไม่เคยถึง score_fn** · permission fail/ERROR/INCONCLUSIVE → quality report invalid (ไม่รายงาน metric เหมือนผ่าน)
- **Slice 2 (container):** local cross-encoder adapter + internal candidate provider (เรียก Qdrant ด้วย compiled filter เดียวกัน, N=30, **ไม่แตะ API cap `top_k<=10` / ไม่เรียก prod endpoint**) + sweep `N∈{10,20,30,50}` เลือก N ต่ำสุดที่ `CandidateRecall@N` ถึงเกณฑ์ประกาศล่วงหน้า (ดู rerank p95 + memory) + durable evidence + p5b canary leak=0 gate
- **P2b (later):** dense+sparse hybrid — งานแยก
- **held-out real/redacted/approved eval:** ก่อนสรุป business/hardware — synthetic ประกาศได้แค่ "P2 mechanics PASS on synthetic"

## Acceptance ที่ Codex ล็อก (ประกาศก่อน model run) — แยก **benchmark valid** จาก **arm eligible**
> โมเดลไม่ชนะ ≠ benchmark fail. dataset: `test` ranking ≥ **50 cases** (น้อยกว่า = smoke) ; dev เลือก N, test รายงานครั้งเดียว ห้ามปรับ N/threshold จากผล test.

**A. Candidate pool / เลือก N** — sweep `N∈{10,20,30,50}` บน **dev**, เลือก N ต่ำสุดที่ผ่านพร้อมกัน:
- point-level `CandidateRecall@N ≥ 0.95` · document-level `CandidateRecall@N ≥ 0.95` · `CandidateHit@N = 1.00` ทุก case
- ไม่มี N ใดผ่าน/test หลุด → รายงาน mechanics ได้แต่ verdict = **candidate-generation limited → P2b** (ห้ามสรุป reranker ชนะ/แพ้)

**B. Improvement + paired CI** — paired bootstrap ระดับ query, **10,000 resamples**, fixed seed บันทึกก่อน run, 95% CI:
- `rerank` แทน `dense` เมื่อ mean `ΔnDCG@5 ≥ +0.02` และ CI lower bound `≥ 0.00`
- CI lower `≥ -0.01` แต่ไม่ถึงเกณฑ์ = non-inferior เท่านั้น → คง dense default · CI lower `< -0.01` = rerank ไม่ผ่าน
- `fused_rrf` แทน `rerank` เมื่อ `+≥0.01` และ CI lower `≥ 0.00` ; ไม่งั้นเลือก arm ที่ง่ายกว่า
- hard-negative category ใด mean delta `< -0.05` → arm นั้นไม่ผ่าน แม้ค่าเฉลี่ยรวมผ่าน

**C. Latency budget** (บันทึก CPU/GPU/RAM/CUDA/container digest/model+tokenizer rev/batch ; warm-up ≥10, samples ≥150, sync GPU):
- incremental rerank `p95 ≤ 1,500 ms/query` · candidate+rerank total `p95 ≤ 2,500 ms/query` · RRF overhead `p95 ≤ 10 ms/query` (N≤50) · error/OOM = 0
- CPU-only = คุณภาพใช้ได้ แต่ latency เป็น diagnostic ไม่ใช่ hardware verdict

**D. Benchmark-valid hard gates** (publish quality ได้เมื่อครบ):
- frozen validation ผ่าน + hashes/manifest ตรง + ไม่มี duplicate/unknown label
- permission gate leak=0, auth VERIFIED, ERROR=0, INCONCLUSIVE=0
- **M4 sentinel ไม่ถึง scorer** + exact authorized-set assertions ผ่านทุก arm
- arm output = exact candidate permutation (ไม่มี missing/extra/duplicate)
- evidence: commit · image/model/tokenizer/embedding rev · Qdrant/index manifest · config · seed · raw per-query metric/latency · aggregate CI

## Out of scope (คง deploy gate)
wire reranker เข้า live /search · shadow-in-API (เฉพาะหลัง prod auth/audit/packaging gate + ไม่ log unauthorized text) · production collection/cutover · cloud egress · P2b hybrid · hardware sizing verdict
