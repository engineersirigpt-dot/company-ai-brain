# P2 Slice 1 fix rd.3 — ปิด Codex M1.2/M1.3 (+N1 doc) → targeted confirm → GO Slice 2?

> **สืบเนื่อง:** `KB_P2_SLICE1_FIX2_CODEX_REREVIEW_0A9136C.md` (FIX-THEN-GO — เหลือ 2 edge)
> **ขอบเขต:** pure/offline · เหลือแค่ M1.2/M1.3 (B2.1/M5.1/M3.1 CLOSED แล้ว)

## Finding → fix → proof (test_p2.py **116/116**)
| # | Finding | Fix | Proof |
|---|---|---|---|
| **M1.2** | non-dict corpus → `AttributeError` (`corpus.get()` ไม่มี type guard) | `validate_ranking_eval_set` reject non-dict corpus ก่อน loop → `validate_benchmark`/`benchmark_manifest` คืน controlled error/`ValueError` | corpus=None/list/string → error list; benchmark_manifest → ValueError (ไม่ AttributeError) |
| **M1.3** | lone surrogate (`Cs`) ผ่าน `_bad_str` → `.encode` crash (`UnicodeEncodeError`) | `_bad_str` reject `Cc`+`Cs`; `_canonical_json` เดียว (`ensure_ascii=True`, `allow_nan=False`, sort_keys, compact) สำหรับ eval+corpus hash; `_text_hash` แปลง encode fail → `ValueError` | surrogate ใน query/rerank_text → validation error; hash → ValueError (ไม่ crash); NaN → ValueError; Thai/emoji hash deterministic |
| **N1** (doc) | handoff บอก empty `relevant_ids` → fail แต่ metric helper คืน None/0 | **คง behavior helper เดิม** ; empty relevance ถูกกันที่ **benchmark boundary** (`validate_benchmark` reject relevance ว่างของ ranking case) ไม่ใช่ทุก metric helper | — |

## สถานะ targeted findings (ครบ)
B1 · B2/B2.1 · M1/M1.1/**M1.2**/**M1.3** · M2 · M3/M3.1 · M5/M5.1 → **CLOSED (offline)** · M4 sentinel = Slice 2 mandatory gate

## ผลรัน (offline — ผม rerun เอง)
```
test_p2.py 116/116 · policy 69/69 · p5b_fixtures 11/11 · eval 64/64 · harness 12/12 · auth 11/11
```

## Slice 2 scope (Codex ยืนยันตามเดิม — ขอไฟเขียวเริ่ม)
1. pinned local `bge-reranker-v2-m3` container/model/tokenizer (บันทึก checksum/image/rev)
2. candidate provider รับ trusted `EffectiveAccess` + compiled filter เดียวกับ API (ไม่แตะ API cap, ไม่เรียก prod)
3. human-reviewed frozen synthetic corpus + hard negatives + dev/test split (test ≥50 ranking cases) + N sweep {10,20,30,50}
4. durable evidence ผูก eval/corpus hashes + `retrieval_index_manifest_sha256` (actual vectors/index digest)
5. P5b canary ผ่านทุก arm (leak=0, VERIFIED, ERROR=0, INCONCLUSIVE=0)
6. **M4 sentinel integration:** unauthorized semantic twin ใน isolated Qdrant · independent scroll oracle · spy adapter พิสูจน์ point ID **และ text** นอกสิทธิ์ไม่ถึง cross-encoder
7. acceptance ที่ล็อก (nDCG@5 primary, CandidateRecall@N≥0.95, ΔnDCG@5≥+0.02 & bootstrap-10k CI lower≥0, p95 budgets) · แยก benchmark-valid จาก arm-eligible

คง **NO-GO production/deploy/cloud/real data**

## ขอ Codex targeted confirm
M1.2/M1.3 ปิดครบไหม — ถ้าไฟเขียว **GO Slice 2** ตาม scope ข้างบน (ครั้งแรกที่แตะ container/model ของ P2)
