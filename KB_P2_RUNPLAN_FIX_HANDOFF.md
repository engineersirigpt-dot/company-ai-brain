# P2 — ปิด Codex B1/B2/B3/B4/M1/M2 (RunPlan fail-closed) → targeted re-review ก่อน Docker

> **สืบเนื่อง:** `KB_P2_RUNPLAN_CODEX_REVIEW_825A3B7.md` (FIX-THEN-GO — decision boundary ยัง fail-open)
> **ทั้งหมด pure/offline** — ไม่มี Docker/model/Qdrant ถูกแตะ · ยังไม่เลือก model commit · ยังไม่สร้าง Dockerfile.p2
> **decision benchmark ยัง NO-GO** จน Data Owner sign-off + validated real M4/canary (ตาม external gates เดิม)

## แนวทางที่ใช้ (ตามที่ review แนะนำ)
สร้าง **validated root run manifest + decision entry point เดียว** แล้วให้ N-sweep / quality / latency / M4 / canary
อ้าง `run_manifest_sha256` เดียวกัน — แทนการเพิ่ม decision helper แยกหลายชั้น

## Finding → fix → proof

| # | Finding (ตำแหน่งเดิม) | Fix | proof (negative test) |
|---|---|---|---|
| **B1** | `select_n()` รับ N นอก `{10,20,30,50}` และผลไม่ครบได้ (`p2_runplan.py:77-88`) | `select_n(dev_ev, run_manifest, expected_counts)` → `validate_dev_evidence`: split=`dev`, ผูก `run_manifest_sha256`, `raw_result_digest` sha256, **by_n keys == N_SET exact**, metric finite∈[0,1], `completed_queries/intents == expected` ; วนเลือกเฉพาะ `sorted(N_SET)` | N=1 reject · partial N reject · not-bound reject · split=test reject · NaN/string metric reject · completed!=expected reject |
| **B2** | paired analysis ตัด intent ที่ไม่ครบเงียบ ๆ (`p2_runplan.py:92-114`) | `validate_quality_evidence`: split=test, ผูก root, ทุก case มี arms `{dense,rerank,fused}` exact + nDCG@5 finite∈[0,1], query_id ไม่ซ้ำ, **intent/query count == expected** ; `per_intent_ndcg` เจอ `None` → raise ; `paired_deltas` `set(a)!=set(b)` → raise ; `paired_bootstrap` reject NaN delta / resamples<10000 / seed ไม่ใช่ int | intent count mismatch · arm ขาด · ndcg NaN · query_id ซ้ำ · not-bound · None→reject · NaN delta reject |
| **B3** | arm decision ไม่ gate latency/candidate/completeness ; `hn_ok({})` vacuous True (`p2_runplan.py:120-159`, `p2_eval.py:429-465`) | `decide_arm` hard-neg dict **ต้องไม่ว่าง + ครอบ required categories + finite** (ไม่ vacuous) ; **`decide_p2()` = entry point เดียว fail-closed** — คืน `NOT_DECISION_ELIGIBLE` ถ้าข้อใดขาด: selected N (dev,∈N_SET) · quality (intent/arm ครบ) · latency valid+within-budget สำหรับ arm≠dense · hard-neg ครบ gate categories · M4 PASS · canary PASS · sign-off | hard-neg `{}` → dense/ไม่ eligible · missing category → dense · latency over-budget(rerank) → not eligible · signoff หาย → not eligible + arm=None · **happy-path bundle ครบ → DECISION arm=rerank** |
| **B4** | evidence ประกอบข้าม model/image/run ได้ ; ไม่มี root manifest binding (`p2_runplan.py:36-63`, `p2_eval.py:359-465`) | root RunPlan บังคับ+hash: contract version, split/counts (dev+test intents/queries), thresholds, N set, seed/resamples, eval/corpus/index, **full model+tokenizer commit, model file-manifest, image digest, inference config** ; `validate_m4/canary_evidence(..., run_manifest_sha256)` cross-check exact ; canary bind `model_revision`+`image_digest` ; `decision_benchmark_manifest` cross-check m4/canary model+image เท่ากัน + `decide_p2` cross-check evidence vs root plan | model_commit abbreviated reject · image/config หาย reject · m4/canary run_manifest ไม่ตรง → error · m4 model_revision != root → not eligible · m4/canary คนละ run_id/image → not eligible |
| **M1** | model pin ยอม abbreviated SHA + ไม่ verify resolved snapshot (`p2_reranker.py:15-27,117-129`) | `_COMMIT` → **full 40/64 hex เท่านั้น** (reject 7-hex/branch/tag) ; loader `snapshot_download` แล้ว **assert `_resolved_commit(snap) == revision`** (basename ของ HF snapshot path = commit จริง) → raise ถ้าไม่ตรง ; metadata บันทึก resolved commit | validate_pin 7-hex reject · 40/64-hex ผ่าน (assert เต็มรูปเกิดจริงตอน container load) |
| **M2** | latency ประกอบจากคนละจำนวน sample ได้ (`p2_runplan.py:144-159`) | `validate_latency_evidence`: stage set exact `{candidate_retrieval,rerank,rrf,total}`, finite non-negative, **post-warmup count เท่ากันทุก stage == expected**, warmup non-negative exact int, `error_count`/`oom_count` exact 0, `raw_latency_digest` sha256 ก่อนคำนวณ within_budget | stage count ไม่เท่ากัน reject · error/oom!=0 reject · warmup<0 reject · stage ขาด reject · not-bound reject |

## ผลรัน (offline — host interpreter `rfqv` + `PYTHONIOENCODING=utf-8`)
```
test_p2         178/178   (+8 B4 root-manifest binding)
test_p2_runplan  72/72    (24 → 72 ; ครอบ negative acceptance ครบ)
test_p2_provider 22/22
test_p2_harness  21/21
test_policy      69/69 · test_eval_contract 64/64 · test_ask_eval_harness 12/12 · test_auth 11/11 · test_p5b_fixtures 11/11
```

## ตรงกับ acceptance ของ targeted re-review
1. **unknown/partial N set · test split · NaN/string metric · missing intent/arm · empty hard-neg → reject** ✔ (B1/B2/B3 negative tests)
2. **latency over budget / sample-error count ไม่ครบ → arm นั้นเลือกไม่ได้** ✔ (M2 + `decide_p2` budget gate สำหรับ arm≠dense)
3. **root manifest ขาด model/image/config/raw-result binding → hash/approve ไม่ได้** ✔ (B4 — `validate_run_plan` + evidence `raw_result_digest`/`raw_latency_digest` + run_manifest binding)
4. **full model commit ต้อง resolve ตรง snapshot ที่ bake** ✔ (M1 — regex full-commit + loader assert ; container-time)
5. **final decision entry point ไม่มีเส้นทางคืน arm verdict จาก partial bundle** ✔ (`decide_p2` ทุก branch ที่ไม่ครบ → `NOT_DECISION_ELIGIBLE, arm=None` ; DECISION เกิดเฉพาะ bundle ครบทุกด่าน)

## หมายเหตุ scope / gates ที่คงไว้
- **ไม่ได้แตะ Docker / model / Qdrant** ; ไม่ได้เลือก immutable model commit ; ไม่ได้สร้าง `Dockerfile.p2`
- `decide_p2` happy-path DECISION ใน test ใช้ **synthetic fixtures (label human-reviewed จำลอง + signoff จำลอง)** เพื่อพิสูจน์กลไกเท่านั้น — **ไม่ใช่** decision manifest จริง ; AI ไม่ได้สร้าง/กรอก human sign-off จริง และไม่ได้เปลี่ยน `label_status` ของ eval-set จริง (ยัง `ai-reviewed`)
- M1 loader assert (`_resolved_commit == revision`) พิสูจน์เต็มรูปได้เฉพาะตอนโหลด snapshot ใน container (offline ไม่มี torch/hf) — pure test ครอบ regex/validate_pin

## ขอ Codex re-review
1. B1/B2/B3/B4/M1/M2 ปิดครบใน pure boundary ไหม (โดยเฉพาะ `decide_p2` fail-closed + root manifest binding)
2. อนุมัติ **เลือก immutable model commit (full 40-hex) + สร้าง `Dockerfile.p2`/compose (pinned, ยังไม่รัน benchmark)** ได้ไหม
3. หลังนั้น → model-load smoke (assert resolved commit) → real M4 → N sweep (ผล UNAPPROVED จน sign-off/evidence ครบ)
