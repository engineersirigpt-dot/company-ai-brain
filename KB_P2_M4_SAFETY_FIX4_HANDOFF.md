# P2 — ปิด safety B3.2-P.1/P.2 + M3.1/M3.2-A/M3.2-B (3-state commit + ledger schema + unified clock)

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX3_CODEX_REREVIEW_56AD337.md` (FIX-THEN-GO Qdrant/docker — 2 blocker + 3 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B3.2-P.1** ⭐ | rollback fsync/truncate error ถูกกลืน → ถ้า rollback ล้มด้วย record ยังโผล่ (newline) แต่ caller ได้ error | **commit/rollback outcome 3 สถานะ**: fsync สำเร็จ = COMMITTED · exception ก่อน commit + **rollback ยืนยันได้ (truncate+fsync)** = UNCOMMITTED (retry ได้) · rollback **ยืนยันไม่ได้** = **`ProvenanceIndeterminate` + poison marker** → `read`/`append` fail-closed จน operator repair |
| **B3.2-P.2** ⭐ | exception หลัง durable commit (close/`_release`) ถูก report เป็น append failure ทั้งที่ record durable | หลัง `committed=True` → cleanup (close/parent-fsync/release) ล้ม = **กลืนเป็น warning** (record durable, append ไม่ report fail) ; pre-commit close ล้ม = fail จริง |
| **M3.2-A** ⭐ | reducer ตรวจแค่ order/run/status — append_event ยอม PUBLISHED ขั้นต่ำที่ไม่มี binding → reconcile ให้ clean | reducer/append **enforce event-specific schema ที่ ledger boundary**: PUBLISHED ต้องมี `artifact_sha256`/`evidence_body_sha256`/`run_receipt_sha256` (64-hex) + capability + path + finished_at(ISO+tz) ; STARTED/DEGRADED/FAILED มี schema แยก |
| **M3.1** | terminal clock error/regression ถูกซ่อน (finished=started เงียบ) ; wrapper/runner คนละ clock | wrapper ใช้ **`ports.clock` (authority เดียวกับ runner receipt)** ; terminal clock invalid/regression → clamp + **`clock_anomaly=True` explicit** (ไม่ clean PUBLISHED เงียบ) ; PUBLISHED **cross-check receipt interval** ⊆ [started_at, finished_at] |
| **M3.2-B** | result ไม่มี `path` → `try` โยน KeyError, `except` อ่าน `result["path"]` ซ้ำ → crash, attempt ค้าง INCOMPLETE | **validate runner-result shape** ก่อนใช้ + เก็บ sanitized `path` ตัวเดียว (error handler ไม่ dereference untrusted result ซ้ำ) ; malformed → FAILED/run_result_malformed (ปิด attempt) |

## behavior tests (offline) ที่เพิ่ม
- **B3.2-P.1**: commit fsync fail + rollback confirmed → UNCOMMITTED (reader เห็นแค่ STARTED) → retry ปิด attempt ได้ ; commit+rollback fsync ล้ม → `ProvenanceIndeterminate` + poison marker → read/append fail-closed
- **B3.2-P.2**: post-commit release fail → record ยัง durable (reconcile PUBLISHED)
- **M3.2-A**: PUBLISHED ไม่มี binding → ProvenanceError ที่ append + reconcile
- **M3.1**: started/finished จาก `ports.clock` (monotonic, ไม่มี anomaly) ; STARTED clock invalid → FAILED/clock ไม่ provision ; **terminal clock invalid → PUBLISHED + `clock_anomaly=True`** (explicit)
- **M3.2-B**: result ไม่มี path → FAILED/run_result_malformed (normalize ไม่ crash)

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_provenance 32/32   test_p2_m4_ops 22/22   test_p2_fs_probe 12/12   test_p2_m4_harness 47/47
test_p2_m4_runner 44/44  test_p2_atomic 25/25   test_p2_m4 59/59   test_p2_runplan 95/95   test_p2 166/166
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11  test_p2_provider 22  test_p2_harness 21
```
- **รวมเครื่องนี้ (19 suites): 789/789**
- **clean env (ไม่มี qdrant_client): 745/745**

## ขอ Codex review (safety-pieces slice รอบ 5)
1. 3-state commit/rollback outcome + poison fail-closed (B3.2-P.1/P.2) — result กับ disk ไม่ขัดกันทุก failure mode ไหม
2. ledger terminal schema enforcement (M3.2-A) + unified clock/anomaly (M3.1) + result-shape guard (M3.2-B) พอไหม
3. หลังผ่าน → เขียน **Qdrant/docker adapter slice** ต่อ ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** safety-pieces review = **FIX-THEN-GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
