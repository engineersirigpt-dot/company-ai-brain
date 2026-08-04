# P2 — Reranker offline/shadow + hybrid arm (plan → Codex review)

> **สืบเนื่อง:** Codex GO P2 (`KB_P5B_FINAL_CLOSURE_CODEX_REVIEW_D120935.md` §Decision) หลังปิด P1 PoC track (`68d4d08`)
> **บทบาท Codex:** review plan/จัดลำดับ/ชี้ความเสี่ยง ก่อนลงมือ (เหมือนทุกเฟส)
> **ขอบเขต:** local + synthetic · offline/shadow เท่านั้น · **ไม่แตะ production/collection จริง · ไม่ส่ง context ไป cloud**

## เป้าหมาย
วัดว่า reranker (`bge-reranker-v2-m3`) และ hybrid ทำ **retrieval quality** ดีกว่า dense-only จริงไหม — เพื่อ *ตัดสินใจ* (ยังไม่ deploy) ว่าจะ wire reranker เข้า stack ตาม PoC goal "วัดผลก่อนตัดสินใจ hardware"

## สถาปัตยกรรม (ผูก guardrail Codex 5 ข้อ)
```
query → auth → effective role → compiled Qdrant filter → candidates top-N (N=30)   ← [G1] filter ก่อน candidates เสมอ
   ├─ ARM dense  : top-k จาก vector score เดิม
   ├─ ARM rerank : bge-reranker-v2-m3(query, candidate.text) → re-order → top-k
   └─ ARM hybrid : fuse(dense_rank, rerank_rank) → top-k                              ← [G2] candidate set เดียวกันทุก arm
วัดแต่ละ arm เทียบ ground-truth relevance: Hit@k · MRR · citation-accuracy · latency  ← [G3] แยกจาก permission suite
permission canaries (p5b) leak=0 คงเดิมทุก arm                                        ← [G4] regression gate
offline harness เท่านั้น — reranker ไม่เห็น point นอกสิทธิ์แม้ shadow log             ← [G1/G5]
```
- **[G1]** reranker รับเฉพาะ candidates ที่ผ่าน `authorized_points()`/filter แล้ว — ไม่มีทางเห็น point นอกสิทธิ์
- **[G2]** ทั้ง 3 arm rerank/fuse บน candidate set ก้อนเดียว (top-N หลัง filter) → permission behavior เหมือนกันเป๊ะ
- **[G3]** retrieval-quality metric อยู่คนละ suite กับ permission ; **`TOP_K=10` ของ P5b ไม่ถูกนับเป็น quality result**
- **[G4]** รัน p5b permission canary (leak=0) เป็น gate ของทุก arm ก่อนรับผล quality
- **[G5]** offline/shadow: ไม่มี live /search change, ไม่มี cloud egress (rerank เป็น local cross-encoder)

## Slice (เสนอทำทีละก้อน — offline ก่อน)
**Slice 1 (offline, ทำได้ทันที ไม่ต้อง model):**
- `retrieval_metrics.py` (pure): `hit_at_k`, `mrr`, `citation_accuracy`, `latency_percentiles` — unit-test
- `rerank.py` (pure ordering): candidate contract `{point_id, dense_score, text}`; `rerank_order(cands, scores)`; `hybrid_fuse(cands, rerank_scores, method)` รองรับ **RRF** + **weighted-normalized**; cross-encoder อยู่หลัง interface `score_fn(query, texts)->list[float]` (inject mock ตอน test / โมเดลจริงตอน Slice 2)
- test: metric ถูก, fusion ordering ถูก, และ **ทุก arm ใช้ candidate set เดียว** (encode G2 เป็น test)

**Slice 2 (ต้องมี model/container):**
- reranker adapter (cross-encoder, torch) รันใน P2 container
- offline eval harness: รัน 3 arm บน synthetic labeled eval set → metric ต่อ arm + ตารางเทียบ + รัน p5b canary leak=0 gate → durable evidence (เหมือน `evidence/run2`)
- `p2_eval_set.json`: synthetic corpus + relevance label (point_id ที่ relevant ต่อ query)

## อยากให้ Codex ช่วยตัดสิน/ชี้
1. **metric set** — Hit@k + MRR + citation-accuracy + latency พอไหม / เพิ่ม nDCG@k ไหม
2. **hybrid fusion** — RRF (reciprocal rank fusion) หรือ weighted-normalized-score สำหรับ PoC (หรือทำทั้งคู่แล้วเทียบ)
3. **reranker execution** — เสนอ **pure offline harness ก่อน** (ไม่มี live risk) แล้วค่อย shadow-in-API ทีหลัง ; รับได้ไหม
4. **eval-set labeling** — synthetic corpus + hand-label relevance หรือใช้ p5b canary เป็น relevance anchor ; top-N candidate ควรเท่าไร (เสนอ N=30, k=5)
5. **candidate top-N** — filter คืน top-N ก่อน rerank ; N ใหญ่ไป = latency, เล็กไป = reranker ช่วยน้อย — ค่าเริ่มที่เหมาะกับ corpus PoC

## Out of scope P2 (คง deploy gate)
wire reranker เข้า live /search · production collection/cutover · cloud egress · shadow logging บน production · hardware sizing decision (P2 แค่ *วัด* เพื่อ *เสนอ*)
