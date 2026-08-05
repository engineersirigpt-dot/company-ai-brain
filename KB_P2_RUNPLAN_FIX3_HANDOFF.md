# P2 — ปิด FIX-BEFORE-RUN M1/M2 + N1 (quality↔frozen join + threshold domain) → re-review

> **สืบเนื่อง:** `KB_P2_RUNPLAN_FIX2_CODEX_REREVIEW_797CE36.md`
> (GO pinned-build-prep · **FIX-BEFORE-RUN**: quality สวม query/intent นอก frozen set ได้ + threshold รับค่าหลุด domain)
> **ทั้งหมด pure/offline** — ไม่แตะ Docker/model/Qdrant · ยังไม่เลือก model commit · ยังไม่สร้าง Dockerfile.p2
> B1/B2/B3/B4 + M1(N-keys) + model full-commit = **CLOSED** จากรอบก่อน (ยืนยันใน re-review)

## Finding → fix → proof

| # | Finding (re-review) | Fix | proof (negative test) |
|---|---|---|---|
| **M1** | quality evidence สวมผลจาก query/intent/tag คนละชุดกับ frozen eval set แล้วได้ DECISION (recompute raw digest ให้ self-consistent ได้) | `decide_p2` **join quality rows กับ frozen test cases ด้วย `query_id`** (`_resolve_quality_rows`): บังคับ **exact query-id set** = frozen test cases + `intent_id`/`role`/`challenge_tags` ต้องตรง case เดิม ; analysis (paired/hard-neg) ใช้ **identity/tags จาก frozen cases** ไม่ใช่จาก evidence | fabricated query IDs (digest self-consistent) → not eligible · changed intent_id/role/challenge_tags → not eligible · unknown/extra qid → not eligible · missing query (49 rows) → not eligible |
| **M2** | threshold รับค่าหลุด domain (`candidate_recall=0.0001`, `ci_lower_min/floors=-999`) | lock **CandidateRecall=0.95 / CandidateHit=1.0 exact** (frozen P2 acceptance) ; delta/fused_min ∈[0,1] ; ci_lower_min ∈[-1,1] ; noninferior/hardneg floor ∈[-1,0] + `noninferior_floor <= ci_lower_min` ; latency budget positive + เรียง `rrf <= rerank <= total` | candidate_recall 0.0001 → error · candidate_hit≠1.0 → error · ci/floor=-999 → error · latency เรียงผิด → error · (`min_delta=0.99` ในโดเมน → ยังผ่าน) |
| **N1** | `p2_harness` docstring อ้าง `decision_benchmark_manifest` ที่ถูกลบแล้ว | เปลี่ยนเป็น: harness output = **unapproved/raw** ; ต้อง join กับ frozen cases → bound evidence → `p2_runplan.decide_p2()` เท่านั้น | (comment เท่านั้น) |

## ผลรัน (offline — `rfqv` python + `PYTHONIOENCODING=utf-8`)
```
test_p2         179/179   test_p2_runplan  94/94   provider 22/22   harness 21/21
policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```

## ตรงกับ acceptance ก่อนเปิด container run
1. quality query-id set ตรง frozen test cases exact + intent/role/tags เปลี่ยนไม่ได้ ✔ (`_resolve_quality_rows`)
2. hard-negative categories derive จาก tags ของ **frozen cases** (join แล้ว) ไม่ใช่ tags ที่ runner ส่งเอง ✔
3. threshold ทุกตัวมี domain/relationship (lock target + bounded floors/CI + ordered latency) ✔
4. harness/runner output = unapproved จนผ่าน `decide_p2` ✔ (docstring แก้แล้ว)

## GO items (จาก re-review) — สถานะ
- **เลือก immutable model commit (full 40-hex)** : GO — แต่ต้องได้ commit SHA จริงของ `BAAI/bge-reranker-v2-m3` (ออนไลน์/HF) ; ยังไม่เลือกในรอบ pure นี้ (ไม่ fabricate SHA)
- **Dockerfile.p2/compose (pinned, ไม่ build/run)** : GO — ยังไม่เขียนในรอบนี้ (แยกเป็นงานถัดไปหลัง confirm SHA source)
- **runner adapter (join harness rows → bound evidence)** : GO NOW — consumer-side (`decide_p2` join) ปิดแล้ว ; producer-side adapter เขียนคู่กับ Docker/run prep

## ขอ Codex review
1. M1/M2/N1 ปิดครบใน pure boundary ไหม (โดยเฉพาะ quality↔frozen-cases join + threshold domain lock)
2. ยืนยัน GO เดิม (เลือก model commit full 40-hex + Dockerfile.p2 pinned ยังไม่รัน) ยังคงอยู่
3. model-load smoke / real M4 / N-sweep = FIX-BEFORE-RUN ปิดแล้วหรือยัง (ควรปลดเป็น GO-after-adapter ไหม)
