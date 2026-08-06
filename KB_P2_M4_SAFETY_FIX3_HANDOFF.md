# P2 — ปิด safety B3.1-R/B3.2-P/M3.1/M3.2 (order-aware reducer + fsync rollback + trusted clock + fail-closed content)

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX2_CODEX_REREVIEW_BF0E9B7.md` (FIX-THEN-GO Qdrant/docker — 2 blocker + 2 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B3.1-R** ⭐ | `reconcile()` นับจำนวนอย่างเดียว — ยอม terminal มาก่อน STARTED ถ้ามี STARTED ตามหลัง และไม่เทียบ run_id | **reducer เดียว `_reduce()` (order-sensitive)** consume record ตามลำดับ: state ว่าง→รับเฉพาะ STARTED · STARTED→terminal ครั้งเดียว (run_id ตรง + `status==event`) · terminal→รับเพิ่มไม่ได้ ; `reconcile` **และ** append validation เรียก reducer เดียวกัน |
| **B3.2-P** ⭐ | writer เขียน JSON+`\n` ก่อน `fsync` — ถ้า fsync ล้ม บรรทัดยังเห็นได้ (reader ถือ committed) แต่ caller ได้ error → API ขัด disk, retry ปิด attempt ไม่ได้ | **commit boundary = `fsync` สำเร็จ** : exception ก่อน commit → **rollback `ftruncate` กลับ `cut`** (record ที่ล้มหายไป reader เห็นแค่ STARTED) → retry terminal ปิด attempt ได้ ; exception หลัง commit (close/release) = record durable แล้ว (ไม่ใช่ uncommitted) |
| **M3.1** | `started_at`/`finished_at` เป็น string จาก caller — spoof/ย้อนเวลา/`finished<started` ได้ | wrapper รับ **trusted clock port** เรียก `clock.now_iso()` เองที่ STARTED/terminal + validate **ISO-8601+tz** + **monotonic** (clamp) ; clock ไม่น่าเชื่อ → FAILED/clock **ก่อน** STARTED/provision |
| **M3.2** | `_sha256_file` กลืน OSError→None แล้ว PUBLISHED ต่อได้ ; digest copy จาก in-memory (TOCTOU) | **PUBLISHED fail-closed**: `_verify_published` **reload final bundle จากดิสก์** → recompute artifact sha256 (64-hex) + **re-run `validate_m4_preflight_bundle` บน content ที่โหลด** + bind evidence_body/run_receipt จากดิสก์ ; read/parse/hash/validate ล้ม = **ไม่ PUBLISHED** (FAILED/verify_publish) |

## behavior tests (offline) ที่เพิ่ม
- **B3.1-R**: reconcile PUBLISHED-ก่อน-STARTED / run_id ไม่ตรง / `status!=event` → ProvenanceError ; append_event ใช้ reducer เดียวกัน
- **B3.2-P**: terminal fsync fail → exception + **rolled back** (reader เห็นแค่ STARTED, reconcile INCOMPLETE) → **retry terminal ปิด attempt ได้**
- **M3.1**: started/finished จาก trusted clock (monotonic) ; clock ให้ค่าไม่ใช่ ISO+tz → FAILED/clock + ไม่ provision + ไม่มี STARTED
- **M3.2**: `_verify_published(valid)` → artifact 64-hex + digests ; `(tampered)` → ValueError (public gate จับ) ; wrapper: final bundle invalid/หาย → **FAILED/verify_publish** (ไม่ clean PUBLISHED, ไม่แนบ evidence)

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_provenance 28/28   test_p2_m4_ops 20/20   test_p2_fs_probe 12/12   test_p2_m4_harness 47/47
test_p2_m4_runner 44/44  test_p2_atomic 25/25   test_p2_m4 59/59   test_p2_runplan 95/95   test_p2 166/166
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11  test_p2_provider 22  test_p2_harness 21
```
- **รวมเครื่องนี้ (19 suites): 783/783**
- **clean env (ไม่มี qdrant_client): 739/739**

## ขอ Codex review (safety-pieces slice รอบ 4)
1. order-aware reducer เดียว (B3.1-R) ปิด audit authority ครบไหม
2. fsync-rollback commit boundary (B3.2-P) — result กับ disk ไม่ขัดกัน + retry ปิด attempt ได้ ครบไหม
3. trusted clock (M3.1) + PUBLISHED disk re-verify (M3.2) พอไหม
4. หลังผ่าน → เขียน **Qdrant/docker adapter slice** ต่อ ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** safety-pieces review = **FIX-THEN-GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
