# P2 Slice 1 (pure ordering) — done → Codex review ก่อน Slice 2

> **สืบเนื่อง:** `KB_P2_PLAN_CODEX_REVIEW_261F116.md` (REVISE CONTRACT, THEN GO SLICE 1) → plan rev2 ปิด B1/B2 แล้ว
> **ขอบเขต:** pure/offline เท่านั้น (mock `score_fn`) · ไม่มี model/Qdrant/container · ไม่แตะ production

## ทำแล้ว (map Slice 1 contract ที่ Codex อนุมัติ)
| ส่วน | ไฟล์ | contract |
|---|---|---|
| Candidate validate + arms | `rerank.py` | `validate_candidates` (point_id unique · source · dense_score finite · dense_rank unique positive · rerank_text non-empty); `dense_order`; `rerank_order` (score_fn, tie-break dense_rank→point_id); **`fused_rrf`** (Σ 1/(rrf_k+rank), 1-based, rrf_k=60, exact permutation, reject dup/missing/out-of-universe); `assert_candidates_authorized` |
| Metrics (retrieval-only) | `retrieval_metrics.py` | **`candidate_recall_at_n`** (บังคับ) · Hit@k · MRR@k · Recall@k · **nDCG@k** (graded, 2^g-1) · document/source-level แยกจาก point-level · latency percentiles · **ตัด citation accuracy** |
| Eval-set validate | `p2_eval.py` | `validate_eval_set` (dup query_id · unknown role · point ไม่อยู่ใน frozen corpus · **point ไม่ authorized สำหรับ role**) · `frozen_hash` · `permission_gate_ok` |

## required behaviors (Codex §Slice 1) — ครบใน `test_p2.py` (53/53)
- input candidates ไม่ mutate · score count != candidate → fail · NaN/Inf/dup/missing ID → fail
- dense/rerank/fused output = **exact permutation** ของ candidate IDs เดียว · tie-break deterministic
- empty → empty · one → same
- **instrumented score_fn: unauthorized sentinel ไม่เคยถึง score_fn** (เห็นแค่ candidate text) + `assert_candidates_authorized` fail เมื่อ point นอกสิทธิ์หลุด
- `permission_gate_ok`: exit!=0 → quality report invalid (ไม่รายงาน metric เหมือนผ่าน)

## ผลรัน (offline, ไม่มี model/stack)
```
test_p2.py 53/53 · regression: policy 69/69 · p5b_fixtures 11/11 · eval 64/64 · harness 12/12 · auth 11/11
```

## Fusion = RRF only (Codex Q2)
`rrf_score(id)=Σ 1/(60+rank)` ; weighted-normalized เก็บเป็น follow-up (ต้องมี dev/test split, ห้าม tune บน test). หมายเหตุ: RRF favors item ที่ rank สูงใน arm ใดๆ มากกว่า middling ทั้งคู่ (1/x convex) — encode ใน test แล้ว

## ขอ Codex ยืนยันก่อน Slice 2
1. Slice 1 contract/tests ครบตามที่อนุมัติไหม — จุดที่ยัง under-specified ก่อนต่อ container
2. **Slice 2 scope confirm:** isolated container + local cross-encoder (`bge-reranker-v2-m3`) adapter + internal candidate provider (เรียก Qdrant ด้วย compiled filter เดียวกัน, ไม่แตะ API cap `top_k<=10`/prod) + `p2_eval_set.json` (human-labeled synthetic + hard negatives) + sweep N∈{10,20,30,50} + durable evidence + p5b canary leak=0 gate
3. `p2_eval_set.json` labeling — ผมเสนอสร้าง synthetic frozen corpus + label โดยไม่ดูผล arm, freeze hash ก่อน benchmark ; ต้องการ hard-negative category เพิ่มไหม (ตอนนี้: sibling/Thai-Eng/table-row/dup-title/parent-child/no-answer)
4. primary metric = `nDCG@5` + `MRR@5` ; acceptance ก่อนเลือก arm ทั้ง 5 ข้อ (leak=0 hard gate, ACL coverage 100%, tolerance+CI, fused>rerank จึงคุ้ม, latency budget) — รับได้ไหม

## Out of scope (คง gate)
Slice 2 model run · P2b hybrid (dense+sparse) · shadow-in-API · production/cutover/egress · hardware/business verdict (synthetic = mechanics เท่านั้น)
