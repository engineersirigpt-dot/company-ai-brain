# P2 — ปิด durable-root + observed proof B1/B2/B3/M1 (round 4) → review ก่อนเขียน runner จริง

> **สืบเนื่อง:** `KB_P2_M4_HARNESS_FIX3_CODEX_REREVIEW_2BE538A.md` (FIX-THEN-GO runner)
> **ทั้งหมด pure/offline** — ไม่รัน Docker/Qdrant/model · real-path runner ยัง NO-GO

## ประเด็นหลัก (Codex): "receipt ยัง bind แค่ per_case → สลับ IsolationProof resource identity หลังรัน แล้ว receipt เดิมยังผ่าน ; proof objects ยังเป็น digest ของ expected input ไม่ใช่ runtime observation ตามชื่อ"

## Finding → fix → proof

| # | ช่องที่ Codex เจอ | Fix |
|---|---|---|
| **B1** ⭐ | `raw_evidence_sha256` hash แค่ `case_records` ; receipt ไม่ commit isolation/oracle proof/scorer pin → สลับทั้ง 4 UUID ใน IsolationProof + recompute inner digest โดย **ไม่แตะ receipt** แล้ว gate ผ่าน | **`evidence_body_sha256`** = canonical hash ของ **ทุก top-level field ยกเว้น `run_receipt_sha256`** (durable root) ; receipt commit ค่านี้ ; public gate: recompute evidence body → เทียบ receipt → recompute receipt digest → เทียบ evidence ref (**two-way**) ; `raw_evidence_sha256` ยังเป็น per-case digest แต่ไม่ใช่ root |
| **B2** | `OracleProof` = digest ของ `frozen` ชุดเดียวกับ validator → gate เทียบ expected กับค่าที่ derive จาก expected เอง แล้วประกาศ `independent_oracle=PASS` ; schema ไม่มีที่รับ observed body | `OracleProof` รับ **observed_visibility จาก independent direct-scroll**: `[{case_id_sha256, observed_authorized_pairs, observed_sentinel_pairs}]` + `collection_id` + `observation_sha256=recompute` ; validator เทียบ observed **== frozen exact** (authorized/sentinel ต่อ case + case set) + `collection_id == isolation.collection_id` ; **builder สร้าง PASS จาก frozen อย่างเดียวไม่ได้** |
| **B3** | `IsolationProof` เป็น identity manifest — ตรวจแค่ 4 ค่า distinct ; `1,2,3,4` แทน UUID ก็ผ่าน | `IsolationProof` รับ **interlock observation**: `initial_point_count==0` (ว่างก่อน seed) · `network_published_ports==0` (internal/no publish) · `endpoint_is_production==False` · `marker_written_sha256==marker_readback_sha256` (write→read กลับ target เดียวกัน) — ทั้งหมดอยู่ใน evidence_body (B1) |
| **M1** | `PinnedCrossEncoder.metadata()` ยังใช้ contract เก่า (`file_manifest_sha256`, ไม่มี `kind`/`inference_config`) → wire real class เข้า harness ไม่ได้ | `metadata()` เป็น **superset** — เพิ่ม `kind`/`model_name`/`model_file_manifest_sha256`/`inference_config` (ตรง M4RunRequest) + คง key เก่าให้ model smoke ; เพิ่ม **positive/negative contract test** กับ `validate_scorer_metadata` (revision ผิด → raise) |

## durable chain (หลัง fix)

```
evidence_body_sha256 = sha256(canonical( evidence ทุก field ยกเว้น run_receipt_sha256 ))
                       └─ ครอบ scorer pin · isolation_proof · oracle_proof · per_case · verdict · run metadata
receipt.evidence_body_sha256 == evidence.evidence_body_sha256      (durable full-bundle root)
evidence.run_receipt_sha256  == sha256(canonical(receipt body))    (ผูกกลับ)
→ แก้ top-level ใด ๆ หลังรัน โดยไม่ออก receipt ใหม่ = mismatch (post-run proof swap ปิด)
IsolationProof/OracleProof = runtime observation (ไม่ใช่ digest ของ expected) — runner ป้อน observed body จริง
```

## negative tests (pure) ที่เพิ่ม
- **B1**: สลับ IsolationProof resource id หลังรัน (receipt เดิม) → gate fail · recompute evidence_body แต่ไม่ออก receipt ใหม่ → `evidence_body_sha256` mismatch · `evidence_body_sha256` ไม่ตรง top-level body → error
- **B2**: `observed_authorized/sentinel != frozen` → oracle invalid · observed case ไม่ครอบ frozen → invalid · `observed_visibility` ว่าง → invalid · frozen_manifest/index ผิด → error · isolation/oracle `collection_id` ไม่ตรง → error
- **B3**: `initial_point_count!=0` · `network_published_ports!=0` · `endpoint_is_production=True` · marker readback != written · resource id ไม่ distinct → isolation invalid
- **M1**: `PinnedCrossEncoder.metadata()` ผ่าน `validate_scorer_metadata` (real contract) · revision ผิด → raise

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch ครบ)
```
test_p2_m4_harness 41/41   test_p2_m4 56/56   test_p2_runplan 95/95   test_p2 166/166
test_p2_provider 22/22     test_p2_harness 21/21   test_p2_pin 14/14   test_p2_adapter 22/22
test_p2_dockerbuild 41/41  test_policy 69/69   test_eval_contract 64/64   test_ask_eval_harness 12/12
test_auth 11/11            test_p5b_fixtures 11/11        รวม 655 checks ผ่านหมด
```
> schema ยังเป็น `p2-m4-v5` (ยังไม่มี durable v5 artifact จริง — เติม field ก่อน M4a ตามที่ Codex ระบุ)

## ยังไม่ได้ทำ (รอ review รอบนี้ก่อน — real-path)
- **runner จริง** — wire `resolve_effective_access → provider → run_case(PinnedCrossEncoder จริง)` บน isolated Qdrant + **ป้อน observed body จริง** เข้า `build_isolation_proof` (interlock: create isolated resources → assert empty/no-publish/non-prod → write+read marker) และ `build_oracle_proof` (**direct scroll แยกจาก filtered provider** → observed visibility ต่อ case)
- **atomic evidence/receipt write** (temp→rename ; artifact root เดียว = evidence_body) + failure controls (partial write / non-zero exit / exception → ไม่มี PASS artifact)

## ขอ Codex review
1. `evidence_body_sha256` durable root + two-way receipt binding ปิด post-run proof swap ครบไหม (มี field top-level ใดหลุดจาก body coverage ไหม)
2. `OracleProof` observed_visibility == frozen exact + `collection_id` binding — contract พอบังคับให้ runner ต้องอ่านจริงไหม
3. `IsolationProof` interlock observation (count/ports/prod/marker-readback) — พอไหม หรือควรเพิ่ม assertion ใด
4. หลังผ่าน → เขียน real-path runner (ป้อน observed body จริง) + atomic writer แล้วขอ **GO M4a run**

**Gate:** real-path runner = FIX-THEN-GO หลัง targeted re-review ผ่าน · M4a run = NO-GO จน runner + real interlock/oracle observation + atomic-write review ผ่าน · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน Data Owner sign-off + M4b + validated canary
