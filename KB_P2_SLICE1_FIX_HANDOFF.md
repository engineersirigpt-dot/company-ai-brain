# P2 Slice 1 fix — ปิด Codex B1/B2/M1/M2/M3/M5 → targeted re-review

> **สืบเนื่อง:** `KB_P2_SLICE1_CODEX_REVIEW_B9B377F.md` verdict **FIX-THEN-GO SLICE 2**
> **ขอบเขต:** pure/offline เท่านั้น (ไม่แตะ infra) · M4 (unauthorized-sentinel filter integration) = Slice 2 acceptance ตามที่ Codex ระบุ

## Finding → fix → proof (test_p2.py 80/80)
| # | Finding | Fix | Proof |
|---|---|---|---|
| **B1** (fail-open) | `permission_gate_ok(False)` → True (`False==0`) | **type-strict**: `type(exit_code) is not int → ValueError`; รับเฉพาะ exact int | `0→True · 1/-1→False · False/True/0.0/None/"0"→ValueError` |
| **B2** | label authorization เช็คแค่ `role in allowed_roles` | ใช้ **P1 policy path เดียวกับ retrieval**: `is_authorized = matches_policy(payload, compile_retrieval_filter(role))` — ไม่ reimplement; corpus ต้องมี full policy payload | stale/QUARANTINED/wrong-version → ไม่ authorized แม้ role ใน allowed_roles · admin ไม่มี bypass · unknown role reject · validate จับ stale relevant point |
| **M1** | freeze แค่ cases ไม่ครอบ corpus | dual hash: `eval_set_sha256` + **`corpus_manifest_sha256`** (point_id/source/`sha256(rerank_text)`/full payload/`rerank_text_version`) + `benchmark_manifest` (+ contract version) | corpus hash เปลี่ยนเมื่อ rerank_text/ACL เปลี่ยน |
| **M2** | plan มี no-answer แต่ validator บังคับ relevance ไม่ว่าง | ranking dataset = `case_type="ranking"` เท่านั้น, relevance ไม่ว่าง; no-answer → error (ย้าย abstention suite แยก) | case_type != ranking → error |
| **M3** | metric/RRF ยอม input ผิดความหมาย | guards: `n/k` positive int · ranked/candidate id unique non-empty · grade ∈ {1,2,3} · nDCG ∈ [0,1] · `rrf_k` positive int · `dense_rank_map` ครบ universe · latency finite non-negative | k<1/1.0, dup id, bad grade, rrf_k=0, dense_rank_map ไม่ครบ, latency ติดลบ → fail |
| **M5** | schema ไม่ครอบ fields | required-field/type/control-char check + split/label_status allowlist + **source consistency** (relevant point.source ∈ relevant_sources) + dup query_id | field หาย/split ผิด/label_status/source ไม่ตรง/control char/dup → error |

## ยังไม่ทำ (Slice 2 integration — Codex M4, mandatory acceptance)
unauthorized-sentinel ต้องพิสูจน์ที่ **candidate-provider จริง**: seed unauthorized twin ใน isolated Qdrant · provider รับ trusted `EffectiveAccess` (ไม่รับ raw role) + compiled filter เดียวกับ API · independent scroll oracle · spy adapter ยืนยัน unauthorized ID/text ไม่ถึง cross-encoder · dense/rerank/fused ID set == authorized pool

## ผลรัน (offline)
```
test_p2.py 80/80 · regression: policy 69/69 · p5b_fixtures 11/11 · eval 64/64 · harness 12/12 · auth 11/11
```

## ปรับตามคำตอบ Codex
- **primary metric = `nDCG@5` เดียว** ; `MRR@5` = key secondary (ไม่เปิดทางเลือกผู้ชนะจากสอง primary)
- fusion RRF only ; weighted = follow-up
- no-answer → abstention suite แยก (ยังไม่มี threshold contract)

## ขอ Codex targeted re-review
1. B1/B2/M1/M2/M3/M5 ปิดครบใน pure boundary ไหม — จุดที่ยัง fail-open/under-specified ก่อน GO Slice 2
2. Slice 2 scope (isolated container + pinned `bge-reranker-v2-m3` + candidate provider via `EffectiveAccess`/compiled filter + human-labeled synthetic frozen corpus + hard negatives ที่เพิ่ม + sweep N + durable evidence ผูก 2 hashes + p5b canary ไหลผ่าน provider ทุก arm + M4 sentinel integration) — confirm ก่อนแตะ model
3. acceptance ต้องใส่ตัวเลขจริง (CandidateRecall@N target/วิธีเลือก N · min nDCG@5 improvement/allowed degradation · CI lower-bound rule · rerank/fusion p95 budget/hardware) — ขอ Codex ช่วย propose ตัวเลข PoC หรือให้ผมเสนอมาให้ review
