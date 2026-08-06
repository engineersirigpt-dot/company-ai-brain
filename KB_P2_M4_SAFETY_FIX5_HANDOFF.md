# P2 — ปิด safety รอบ 5 : write-ahead intent + anomaly/​path/​payload load-bearing + private raw surface

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX4_CODEX_REREVIEW_F602329.md` (FIX-THEN-GO — 3 blocker + 3 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py`, `p2_m4_ops.py` (code) · `test_p2_provenance.py`, `test_p2_m4_ops.py` (tests)

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B3.2-P.1a** | blocker | poison เป็น best-effort — สร้าง marker **หลัง** storage ล้ม ; ถ้า marker เขียนไม่ได้ future process อ่าน ledger เป็น clean terminal ได้ | **เลิก poison-after-failure ทั้งกลไก → ใช้ write-ahead intent**: `_write_intent()` สร้าง durable marker `<log>.intent` แบบ **no-clobber (`O_CREAT│O_EXCL`) + fsync file + fsync parent** **ก่อน** mutation แรกของ ledger ; ลบด้วย `_clear_intent()` (fsync parent) เมื่อ outcome ยืนยันแล้ว **เท่านั้น** ; intent ค้าง = indeterminate โดย default |
| **B3.2-P.1b** | blocker | poison/intent check อยู่ **นอก lock** → writer B ผ่าน check แล้วเขียนต่อหลัง ledger ถูก mark ; reader check ครั้งเดียวนอก lock | `_check_intent()` เรียก **ใต้ writer lock** ก่อนทุก append ; `read_provenance()` **acquire lock เดียวกัน + `_check_intent` + อ่าน snapshot + release** → reader/writer เห็น health state ต้นทางเดียวกัน (ปิด TOCTOU) |
| **B3.2-P.2** | blocker | POSIX parent-directory fsync ล้ม ถูก `pass` เงียบทั้งที่เป็น durability boundary | `_fsync_parent()` **propagate `OSError`** ; เรียกใน commit boundary (log ใหม่) และใน `_clear_intent()` ; parent-fsync ล้ม → intent **ไม่ถูกลบ** → ledger INDETERMINATE (ไม่ report durable) ; กลืนเฉพาะ **post-commit close/release** (record durable แล้ว = cleanup warning จริง) |
| **M3.1** | major | `clock_anomaly=True` เป็น metadata เฉยๆ — `status`/`reconcile()` ยัง clean `PUBLISHED` | **anomaly load-bearing 2 ชั้น**: (1) ledger schema **reject `clock_anomaly` บน PUBLISHED** ; (2) `_terminal()` downgrade `PUBLISHED → DEGRADED`/`clock_anomaly` เมื่อ anomaly → `status` และ `reconcile()` = **DEGRADED** (gate ด้วย status พอ ไม่ต้องอ่าน flag) + ไม่แนบ evidence/receipt ; receipt-interval cross-check ใช้ **receipt ที่โหลดจากดิสก์** |
| **M3.2-B** | major | guard ตรวจแค่ `path` เป็น non-empty ; bundle นอก `out_dir` + evidence/receipt หาย ยัง PUBLISHED ; wrapper คืน payload จาก memory | **exact-path guard**: require `realpath(result["path"]) == realpath(out_dir)/<run_id>.bundle.json` + status/durability shape ครบ ไม่งั้น `FAILED/run_result_malformed` ; `_verify_published()` **คืน disk-loaded evidence+receipt** ; wrapper คืนเฉพาะค่า disk-loaded + ใช้ disk receipt ทำ interval cross-check |
| **M3.2-A** (follow-up) | major | `append_provenance()` เป็น public surface เขียน raw record (ปลอม binding) เข้า log เดียวกันได้ | rename `append_provenance → _append_raw` (**private, ไม่มี state machine**) ; public path มีแค่ `append_event()` ที่บังคับ `_validate_transition` (order + run_id + status + terminal schema) ; producer จริง = `p2_m4_ops` เท่านั้น |

## Bug ที่เจอ+ปิดเองระหว่างรอบนี้ (ก่อนส่ง Codex)

- **validation-reject ไม่ควรทิ้ง intent ค้าง**: ครั้งแรกวาง `_write_intent` ก่อน `validate_state` → เมื่อ transition ถูก reject (เช่น terminal-ก่อน-STARTED, STARTED ซ้ำ) intent จะค้างและ poison ledger ทั้งที่ **ยังไม่แตะ ledger เลย**
- **แก้:** `validate_state` เป็น pure read → ย้ายให้รัน **ก่อน** `_write_intent` ; transition ที่ถูก reject → ledger + intent ไม่ถูกแตะ → ได้ `ProvenanceError` สะอาด (retryable) ไม่ใช่ `ProvenanceIndeterminate`
- จับได้เพราะ `test_p2_provenance` ที่ทดสอบ bad-transition แล้ว log ตัวเดิมใช้ต่อไม่ได้ (สะท้อนของจริง)

## behavior tests (offline) ที่เพิ่ม/แก้

- **B3.2-P**: record-commit fsync ล้ม (intent fsync = call 1 ผ่าน, record fsync = call 2 ล้ม) + rollback ยืนยัน → **UNCOMMITTED** (reader เห็นแค่ STARTED, **intent เคลียร์**) → retry terminal ปิด attempt ได้
- **B3.2-P.1** (เขียนใหม่แทน poison เดิม): จำลอง `.intent` ค้าง (crash กลาง append) → `read_provenance`/`append_event` **fail-closed = `ProvenanceIndeterminate`** ; operator ลบ intent → อ่านได้ปกติ (attempt = INCOMPLETE)
- **M3.1**: terminal clock invalid → **`DEGRADED`/`clock_anomaly`** (ไม่ใช่ PUBLISHED) + `reconcile == DEGRADED` + ไม่แนบ evidence — ยืนยัน anomaly load-bearing ที่ทั้ง result และ ledger
- **M3.2-B**: (1) result ไม่มี path → `run_result_malformed` (เดิม) ; (2) **bundle valid แต่อยู่นอก `out_dir`** → `run_result_malformed` + ไม่ verify + ไม่แนบ evidence/receipt ; (3) path ตรง run_id แต่ไฟล์หาย → `verify_publish` (แก้ test เดิมให้ตั้งชื่อ `<run_id>.bundle.json` เพื่อผ่าน exact-path guard ก่อนถึง verify)
- **happy path**: evidence/receipt ที่คืน = **disk-loaded** (digest ตรง terminal binding)

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 33   test_p2_m4_ops 24   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 792/792** (เดิม 789 → +1 provenance repair, +2 m4_ops anomaly/outside-path)
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## write-ahead intent — invariant สรุป (ให้ Codex ตรวจ)

1. `_check_intent` (ใต้ lock) → intent ค้าง = `ProvenanceIndeterminate` เสมอ (reader + writer)
2. `validate_state` (pure read) รัน **ก่อน** `_write_intent` — transition reject = ledger/intent ไม่ถูกแตะ
3. `_write_intent` no-clobber + fsync file + fsync parent **ก่อน** truncate/append แรก
4. commit = record fsync (+ parent fsync ถ้า log ใหม่) สำเร็จ → `_clear_intent`
5. commit ล้ม → rollback (truncate+fsync) ยืนยัน → `_clear_intent` → raise (UNCOMMITTED, retry) ; rollback/parent ยืนยันไม่ได้ → intent ค้าง → `ProvenanceIndeterminate`
6. post-commit close/release ล้ม = กลืนเป็น warning (record durable แล้ว)

## ขอ Codex review (safety-pieces slice รอบ 6)

1. write-ahead intent (B3.2-P.1a/b/P.2) — survive crash ทุกจุด (ก่อน/ระหว่าง/หลัง intent, ก่อน/หลัง record fsync, ก่อน/หลัง parent fsync) แบบ fail-closed จริงไหม ; reader-under-lock ปิด TOCTOU ครบไหม
2. anomaly/path/payload load-bearing (M3.1, M3.2-B) — status/reconcile/returned payload bind ของจริงหมดไหม ; producer surface (M3.2-A) แคบพอไหม
3. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ได้ ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 6 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
