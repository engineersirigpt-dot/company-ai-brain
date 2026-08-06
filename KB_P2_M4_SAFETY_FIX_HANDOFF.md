# P2 — ปิด safety-pieces B1-B3/M1-M2 (probe durability primitive + event ledger + sanitized provenance)

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_CODEX_REVIEW_0E04EB7.md` (FIX-THEN-GO Qdrant/docker — 3 blocker + 2 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B1** ⭐ | probe fsync แค่ไฟล์ + คืน `durability_mode` จาก `os.name` (label) แต่ **ไม่เคยเรียก dir primitive จริง** → POSIX fs ที่ dir fsync ไม่ได้ probe PASS แล้วล้มหลัง model | probe เรียก **`AT._fsync_dir(out_dir)` (primitive เดียวกับ publisher)** หลัง link/unlink ; POSIX dir error → `CapabilityError` ; `durability_mode` มาจากผลจริง (durable/atomic-visibility-only) |
| **B2** ⭐ | probe แปลงเป็น CapabilityError แค่ os.link/unlink บางช่วง — makedirs/mkdtemp/open/fsync/cleanup raw OSError หลุด wrapper (ไม่มี provenance) ; `rmtree(ignore_errors=True)` กลืน cleanup failure | probe **normalize OSError ทุกชนิด → CapabilityError** (chained) ; cleanup ไม่ ignore เงียบ (success path cleanup fail → CapabilityError ระบุ path) ; wrapper **defensive catch `except Exception` รอบ probe** → FAILED/fs_probe |
| **B3** ⭐ | provenance เขียนหลัง run เท่านั้น — ตายกลางทางไม่มีร่องรอย ; terminal append fail หลัง publish = exception หลุดไม่มี status | **event ledger**: append `STARTED` (attempt_id) **ก่อน run** ; STARTED เขียนไม่ได้ = abort ก่อน provision/model ; terminal (PUBLISHED/DEGRADED/FAILED) attempt_id เดียวกัน ; terminal เขียนไม่ได้ = **PROVENANCE_UNCONFIRMED** (ไม่ clean success) ; `reconcile`: STARTED ไม่มี terminal = **INCOMPLETE** |
| **M1** | JSONL writer สมมติ `os.write` ครบ (short write → partial line → read JSONDecodeError) ; ไม่มี lock/allow_nan/parent-fsync | full-write loop (ตรวจ byte ครบ ; ไม่คืบ = ProvenanceError) ; **single-writer O_EXCL lock** (bounded retry → ProvenanceLocked) ; canonical JSON `allow_nan=False` + record size limit ; fsync parent เมื่อสร้าง log ใหม่ (POSIX) ; read **recover truncated tail** / interior corrupt → ProvenanceError |
| **M2** | wrapper persist `repr(e)` ดิบ → adapter exception ที่มี URL cred/token/query/payload รั่วลง ledger ถาวร | provenance เก็บเฉพาะ **sanitized** (attempt_id/run_id/event/phase/**error_type**/durability_mode/path) — **ไม่เก็บ raw exception text** เลย |

## behavior tests (offline) ที่เพิ่ม
- **B1**: `AT._fsync_dir` fail → CapabilityError (ไม่หลุดไป publisher)
- **B2**: file fsync fail / makedirs PermissionError / rmtree fail (success path) → CapabilityError ; wrapper: CapabilityError → FAILED/fs_probe ไม่ provision/model
- **B3**: ledger STARTED→PUBLISHED/FAILED/DEGRADED + reconcile ; STARTED-append fail → abort ก่อน run (`isolation.calls==[]`) ; terminal-append fail → PROVENANCE_UNCONFIRMED (ไม่แนบ evidence) ; STARTED-only → INCOMPLETE
- **M1**: NaN → ProvenanceError · oversize → ProvenanceError · partial write (loop เขียนครบ) · os.write คืน 0 → ProvenanceError · lock ถูกถือ → ProvenanceLocked · truncated tail → drop · interior corrupt → ProvenanceError · 4×5 concurrent writers → 20 records ครบ
- **M2**: exception มี `TOP-SECRET`/`Bearer` → FAILED (error_type=RuntimeError) + **log ไม่มี secret**

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_fs_probe 11/11   test_p2_provenance 15/15   test_p2_m4_ops 11/11   test_p2_m4_harness 47/47
test_p2_m4_runner 44/44  test_p2_atomic 25/25   test_p2_m4 59/59   test_p2_runplan 95/95   test_p2 166/166
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11  test_p2_provider 22  test_p2_harness 21
```
- **รวมเครื่องนี้ (19 suites): 760/760**
- **clean env (ไม่มี qdrant_client): 716/716**

## ขอ Codex review (safety-pieces slice รอบ 2)
1. probe เรียก dir primitive จริง + normalize error ครบ (B1/B2) ปิด fail-before-model ครบไหม
2. STARTED→terminal ledger + PROVENANCE_UNCONFIRMED + reconcile (B3) พอเป็น durable audit trail ไหม
3. single-writer lock + full-write + tail recovery (M1) + sanitized provenance (M2) พอไหม — writer model (single vs concurrent) เหมาะกับ PoC ไหม
4. หลังผ่าน → เขียน **Qdrant/docker adapter slice** ต่อ ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** safety-pieces review = **FIX-THEN-GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
