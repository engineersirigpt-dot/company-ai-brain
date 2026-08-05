# P2 — M4 harness (pure/injectable) + public M4a gate + M4RunReceipt → review ก่อน run

> **สืบเนื่อง:** `KB_P2_M4_REAL_RUN_PLAN_CODEX_REREVIEW_2D491DC.md` (**GO M4 harness (pure/injectable)** ; NO-GO M4a real run)
> **ทั้งหมด pure/offline** — ไม่รัน Docker/Qdrant/model · M4a real run ยัง NO-GO จน harness review + negative controls ผ่าน

## สิ่งที่เพิ่ม (ตาม acceptance ของ Codex)

### 1. public M4a gate (trust anchor) — `p2_runplan.validate_m4_preflight_bundle(plan, frozen, evidence, receipt)`
ปลด N-sweep ได้ก็ต่อเมื่อ evidence+receipt ผูกกับ **validated RunPlan** จริง (ไม่เชื่อ digest/pin ที่ caller ส่งลอย ๆ):
1. `validate_run_plan(plan)` + recompute `root = run_manifest_sha256(plan)`
2. `validate_m4_frozen_manifest(frozen)` + digest/roles/categories **== RunPlan**
3. derive `M4RunRequest` จาก RunPlan ด้วย **helper เดียว** `m4_run_request(plan)` (reuse ทั้ง M4a + `decide_p2`) + freeze `run_id` จาก receipt (compare exact, ไม่ optional)
4. `validate_m4_run_receipt` (body) + recompute `m4_run_receipt_sha256(receipt)` == `evidence.run_receipt_sha256`
5. `validate_m4_run_evidence(..., require_stage=preflight-n50)` ด้วย eval/corpus/index จาก RunPlan
> full immutable commit มาจาก RunPlan validator (ไม่ใช้ 7-hex เป็น trust gate) · decision_eligible=False เสมอ · M4a เข้า `decide_p2` ไม่ได้

### 2. `M4RunReceipt` — body-validated (`p2_eval.validate_m4_run_receipt` + `m4_run_receipt_sha256`)
exact hash-only keys · `status=PASS` · `exit_code=0` (exact int) · `started/finished_utc` ISO+tz ·
`command_sha256`/`stdout_sha256`/`stderr_sha256`/`isolation_marker_sha256` · bind run_id/root/manifest/raw_evidence/pin/image/index **ชุดเดียวกัน** · **ไม่มี secret/raw log** (digest recompute จาก body)

### 3. `p2_m4_harness.py` — pure/injectable producer
- `SpyScorer` — wrap real cross-encoder, จับ call/score จริง → derive `model_call/input/score counts` + `all_scores_finite` (ไม่ self-stamp)
- `frozen_case` / `build_frozen_manifest` — seed จาก expected_visible_roles matrix (oracle อิสระ ไม่ reimplement policy)
- `component` / `build_case_record` — pair_components + pairs จาก (point_id, rerank_text) จริง + observed rank จากตำแหน่ง unfiltered
- `assemble_evidence` (recompute `raw_evidence_sha256` จาก body) + `assemble_receipt`
- seam inject ได้: `client` (fake Qdrant), `scorer` (Mock), timestamps → test offline ไม่ต้อง Docker/model

## ผลรัน (offline)
```
test_p2_m4_harness 12/12 (harness → validate_m4_preflight_bundle ผ่าน + trust-anchor + receipt body + digest)
test_p2_m4 39/39 · test_p2 166 · test_p2_runplan 95 · adapter 22 · dockerbuild 41 · pin 14 · provider 22 · harness 21 · policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```
**negative control (trust anchor):** เปลี่ยน evidence pin / frozen query hash / invalid RunPlan โดยคง RunPlan → **gate fail** ; receipt exit≠0 / tampered body / raw_evidence mismatch → **fail**

## ยังไม่ได้ทำ (รอ review รอบนี้ก่อน)
- **runner จริง** (`resolve_effective_access → provider → SpyScorer → PinnedCrossEncoder` บน isolated Qdrant) — เป็น real run = NO-GO
- receipt negative controls แบบ **atomic temp→rename / partial write / exception ก่อน-หลัง evidence write** ในตัว runner จริง (จะทำคู่กับ runner)

## ขอ Codex review
1. public M4a gate (`validate_m4_preflight_bundle`) anchor trusted inputs เข้ากับ validated RunPlan ครบไหม (โดยเฉพาะ frozen digest/roles/categories + M4RunRequest single-helper + run_id exact + receipt body)
2. `M4RunReceipt` schema/binding + harness builders (recompute digest จาก body) ปิด M1/M2 acceptance ไหม
3. หลังผ่าน → เขียน **runner จริง** (injectable) + isolation interlock + atomic evidence/receipt write แล้วขอ **GO M4a run** บน isolated Qdrant

**Gate:** M4a real run = NO-GO จน harness/runner review · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
