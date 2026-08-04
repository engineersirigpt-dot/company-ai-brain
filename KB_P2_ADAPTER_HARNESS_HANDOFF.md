# P2 — B3.1/B3.2/M1.1 fixes + cross-encoder adapter + harness scaffolding → review

> **สืบเนื่อง:** `KB_P2_SLICE2_INFRA_FIX_CODEX_REREVIEW_2364BB1.md` (GO adapter / FIX-THEN-GO evidence wiring)
> **ทำ 2 อย่าง:** (A) ปิด B3.1/B3.2/M1.1 (commit `85cc3cf`) · (B) เขียน adapter + harness scaffolding (GO'd, pure/offline)

## A. Fixes B3.1/B3.2/M1.1 (commit `85cc3cf`)
| # | fix | proof |
|---|---|---|
| **B3.1** | `validate_m4_evidence` + `validate_canary_evidence` (exact PASS ไม่ใช่ truthiness) — m4: status/interlock/oracle=PASS, sentinel_reached_model=False, unauthorized_in_model_inputs=0, model/tokenizer/image/index/run hashes ; canary: leak_count=0/auth=VERIFIED/arms {dense,rerank,fused}=PASS ; ผูก eval+corpus hash. `decision_benchmark_manifest` เรียก validators → **FAIL evidence สร้าง approved=True ไม่ได้** | m4 status=FAIL/sentinel=True/hash ผิด → error ; canary leak>0/UNVERIFIED/arm ขาด → error |
| **B3.2** | `allowed_label_status` param ; `artifact_manifest_unapproved` ยอม ai-reviewed (SMOKE_LABEL_STATUS) + `approved/decision_eligible=False` ; decision path เฉพาะ human-reviewed | smoke กับ ai-reviewed ผ่าน ; decision ปฏิเสธ ai-reviewed |
| **M1.1** | `validate_signoff` exact types — reviewer/data_owner_role non-blank str, git_commit hex(7-64), reviewed_at ISO-8601+tz, decision enum | bool/list/dict/no-tz → error |

## B. Cross-encoder adapter + harness (pure/offline)
- **`p2_reranker.py`** — `MockScorer` (deterministic, `revision="mock"` ห้ามใช้เป็น evidence) + `PinnedCrossEncoder`/`load_pinned_cross_encoder` (real `bge-reranker-v2-m3`, lazy torch, pin revision, บันทึก model/tokenizer/device — Slice 2 container)
- **`p2_harness.py`** — `rank_arms` (dense/rerank/fused บน **candidate universe เดียว**), `eval_query` (candidate_recall + hit@{1,3,5}/mrr@5/**nDCG@5**/recall@5), `aggregate` (mean/arm), `run_smoke` → **output `approved=False, decision_eligible=False`** (ไม่ wire decision manifest — NO-GO จน B3.1 confirm) + fail-closed skip (out-of-scope role → AuthError → skipped)

## ผลรัน (offline)
```
test_p2 161/161 · provider 22/22 · harness 15/15 · policy 69 · eval 64 · harness(perm) 12 · auth 11 · p5b 11
```
harness พิสูจน์: arm ต่างกันบน universe เดียว (dense A>B>C, mock rerank C>A>B), nDCG@5 rerank>dense เมื่อ grade-3 ขึ้นนำ, output unapproved

## ยังเหลือ = Slice 2 **run** (Docker + model) — mandatory
- `load_pinned_cross_encoder` โหลด model จริงใน container (offline นี้ torch ไม่มี → ใช้ MockScorer เทส logic)
- **real M4 integration:** independent raw-scroll oracle (ไม่ใช่ matcher ตัวเดียวกับ provider) + spy รอบ model จริง assert sentinel ID hashes ไม่ถึง → สร้าง `M4Evidence` ที่ validate ผ่าน
- N sweep {10,20,30,50} บน dev + paired bootstrap CI (Slice 2 decision) + P5b canary ทุก arm → `CanaryEvidence`
- durable evidence ผูก eval/corpus/index/model/run hashes
- **decision benchmark NO-GO** จน Data Owner sign-off + validated real M4 + validated canary PASS

## ขอ Codex review
1. B3.1/B3.2/M1.1 ปิดครบไหม (decision gate ปฏิเสธ FAIL evidence จริง)
2. adapter (`PinnedCrossEncoder`) + harness (`run_smoke` unapproved) ถูกทิศไหมก่อนต่อ real run
3. อนุมัติเปิด Docker รัน real M4 + N sweep (ผลติดป้าย UNAPPROVED จน sign-off/evidence ครบ) ได้เมื่อไร
