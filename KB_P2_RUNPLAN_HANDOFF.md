# P2 — ปิด Codex B1/B2/B3/M1/M2 ครบ (pure) → review ก่อนเปิด Docker

> **สืบเนื่อง:** `KB_P2_ADAPTER_HARNESS_CODEX_REVIEW_F885403.md` (FIX-THEN-GO Docker/model run)
> **ทั้งหมด pure/offline** — ไม่มี Docker/model/Qdrant ถูกแตะ · decision benchmark ยัง NO-GO จน sign-off + real M4/canary

## Finding → fix → proof
| # | Finding | Fix | proof |
|---|---|---|---|
| **B1** | `PinnedCrossEncoder` ยังไม่ pinned + metadata คลาดเคลื่อน | `validate_pin` (model allowlist + revision **immutable commit SHA**, reject main/branch, positive int) ; loader offline (HF_HUB_OFFLINE) + record model/tokenizer commit/**file-manifest sha256**/dtype/versions ; metadata รายงานค่าจริง ; logits float32 | validate_pin main/allowlist/params (test_p2_harness) |
| **B2** | evidence ยอมค่าไม่ใช่ hash/int + ไม่ bind M4↔canary run | exact-zero int, sha256 64-hex, image_digest sha256:<hex>, commit hex ; **sentinel id disjoint จาก model inputs** ; canary arm_error_counts=0 + expected==actual ; decision manifest cross-check run_id+index | B2 tests (test_p2) |
| **B3** | retrieval failure กลายเป็น skipped | `run_ranking` **zero-skip** (fail-on-error, n_completed==n_expected) แยก `run_diagnostic` (allow_errors, status INCOMPLETE, non-evidence) | run_ranking raise/COMPLETE, diagnostic INCOMPLETE |
| **M1** | analysis contract ยังไม่มี | **`p2_runplan.py`**: RunPlan immutable + `run_manifest_sha256` ; `select_n` (dev, N ต่ำสุดที่ CandidateRecall point+doc≥0.95 + Hit=1.0) ; `paired_bootstrap` 10k **group ตาม intent_id** ; `decide_arm` (Δ≥0.02 & CI≥0, fused +≥0.01, hard-neg floor -0.05) ; `latency_summary` (แยก stage, ตัด warm-up, p50/p95, budget) | test_p2_runplan 24/24 |
| **M2** | timestamp ตรวจแค่ regex | `validate_signoff` reviewed_at ใช้ `datetime.fromisoformat` + tzinfo (99:99 ไม่ผ่าน) | M2 test |

## ผลรัน (offline)
```
test_p2 170/170 · provider 22/22 · harness 21/21 · runplan 24/24 · policy 69 · eval 64 · harness(perm) 12 · auth 11 · p5b 11
```

## Slice 2 run (Docker + model) — เตรียมพร้อมหมดแล้ว เหลือแค่ infra
1. เลือก **immutable model commit SHA** ของ `BAAI/bge-reranker-v2-m3` → ใส่ใน RunPlan + build Dockerfile.p2 (bake snapshot, `local_files_only`)
2. model-load compatibility smoke (หลัง B1 confirm)
3. seed isolated Qdrant + real M4 (independent scroll oracle + spy → `M4Evidence` ที่ validate ผ่าน) + P5b canary ทุก arm → `CanaryEvidence`
4. รัน N sweep {10,20,30,50} บน **dev** → select_n → freeze N → test run → paired bootstrap → decide_arm + latency
5. **decision benchmark**: `decision_benchmark_manifest` (human-reviewed labels + Data Owner sign-off hash + validated M4 + validated canary) — NO-GO จนครบ

## ขอ Codex review
1. B1/B2/B3/M1/M2 ปิดครบใน pure boundary ไหม (โดยเฉพาะ evidence binding + run_ranking zero-skip + RunPlan/decide_arm)
2. อนุมัติ **เลือก immutable model commit + สร้าง Dockerfile.p2/compose (pinned) โดยยังไม่รัน benchmark** ได้ไหม
3. หลังนั้น → model-load smoke → real M4 → N sweep (ผล UNAPPROVED จน sign-off/evidence ครบ)
