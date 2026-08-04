# P2 Slice 1 fix rd.2 — ปิด Codex B2.1/M5.1/M1.1/M3.1 → targeted re-review

> **สืบเนื่อง:** `KB_P2_SLICE1_FIX_CODEX_REREVIEW_04C3A4F.md` (FIX-THEN-GO — ยังไม่เริ่ม Slice 2)
> **ขอบเขต:** pure/offline เท่านั้น · M4 (sentinel filter integration) = Slice 2 gate ตาม disposition

## Finding → fix → proof (test_p2.py **106/106**)
| # | Finding | Fix | Proof |
|---|---|---|---|
| **B2.1** | `is_authorized` รับ scalar `allowed_roles` ได้ (matches_policy เลียน Qdrant MatchAny) | เพิ่ม **stored-shape gate**: `payload_is_policy_v1` + `validate_stored_payload` **ก่อน** `matches_policy` | scalar/null/bad-schema/non-v1/empty-ACL → unauthorized; valid v1 เท่านั้นผ่าน |
| **M5.1** | `relevant_sources` เช็คแค่ subset | **exact set-equality** กับ source ที่ derive จาก relevant point IDs + ห้าม dup + error บอก missing/extra | dup/extra/missing → error; exact match → ผ่าน |
| **M1.1/M5.2** | corpus/cases ว่างหรือ entry ผิด shape ยังผ่าน/crash | `validate_corpus` (point_id/source/rerank_text non-blank+no-control, payload = valid policy-v1) + `validate_benchmark` (cases ไม่ว่าง) ; `benchmark_manifest` **raise ถ้า invalid** ; `corpus_manifest_sha256` reject rerank_text ผิดชนิด ; defensive `corpus.get(pid)` | corpus ว่าง/entry ไม่ dict/payload non-v1/scalar → error; benchmark invalid → ValueError |
| **M3.1** | public boundary guards ไม่ครบ | metrics validate `relevant_ids` (string เดี่ยว → fail) ; `fused_rrf` validate `dense_rank_map` values (positive unique int, ไม่ bool) ; `dense_rank_map()` builder validate candidate | relevant_ids string/ผิดชนิด/ว่าง → fail; rank 0/dup/bool → fail |

## สถานะ targeted findings
| Finding | สถานะ |
|---|---|
| B1 permission gate exact-int | CLOSED |
| B2 + **B2.1** stored-shape + full-policy authorization | **CLOSED** |
| M1 + **M1.1** eval+corpus freeze + corpus validator | **CLOSED** (offline) — `retrieval_index_manifest_sha256`/vector digest = Slice 2 evidence |
| M2 ranking/no-answer | CLOSED |
| M3 + **M3.1** metric/RRF guards | **CLOSED** |
| M5 + **M5.1** schema + exact source set | **CLOSED** |
| M4 sentinel integration | DEFERRED → Slice 2 mandatory gate |

## ผลรัน (offline — ผม rerun เอง; Codex รอบก่อนรัน python.exe ไม่ได้)
```
test_p2.py 106/106 · regression: policy 69/69 · p5b_fixtures 11/11 · eval 64/64 · harness 12/12 · auth 11/11
```

## Plan อัปเดต (ตาม acceptance ที่ Codex ล็อก)
- **primary metric เดียว = mean paired `nDCG@5`** (MRR@5/Hit/Recall/doc-level = secondary) — แก้ที่ `KB_P2_PLAN.md`
- acceptance ล็อกแล้ว: CandidateRecall@N≥0.95 (point+doc) + CandidateHit@N=1.00, N sweep บน dev ; ΔnDCG@5≥+0.02 & CI lower≥0 (bootstrap 10k, fixed seed) ; fused>rerank +≥0.01 ; hard-neg category delta<-0.05 → arm ไม่ผ่าน ; rerank p95≤1500ms, total≤2500ms, RRF≤10ms ; leak=0/VERIFIED/ERROR=0/INCONCLUSIVE=0 ; test ≥50 ranking cases
- แยก **benchmark valid** จาก **arm eligible** (โมเดลไม่ชนะ ≠ benchmark fail)

## ขอ Codex targeted re-review → GO Slice 2
B2.1/M5.1/M1.1/M3.1 ปิดครบใน pure boundary ไหม — ถ้าไฟเขียว เริ่ม **Slice 2** (isolated container + pinned `bge-reranker-v2-m3` + candidate provider via trusted `EffectiveAccess`/compiled filter + human-reviewed frozen synthetic corpus + hard negatives + N sweep + durable evidence ผูก 2 hashes + `retrieval_index_manifest_sha256` + p5b canary ทุก arm + **M4 sentinel integration** + acceptance ที่ล็อก) — คง NO-GO production/deploy/cloud/real data
