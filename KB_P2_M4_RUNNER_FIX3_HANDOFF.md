# P2 — ปิด runner B2.1/M3.1/M3.2 (frozen role-identity recompute + durability/cleanup honesty)

> **สืบเนื่อง:** `KB_P2_M4_RUNNER_FIX2_CODEX_REREVIEW_83A4EC0.md` (FIX-THEN-GO real adapters — 1 blocker + 2 major)
> **pure/injectable เท่านั้น** — รัน M4a บน Qdrant/model จริง = **NO-GO** จน adapter-review + Data Owner sign-off

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B2.1** ⭐ | frozen validator ตรวจ `role_identity_sha256` แค่ว่าเป็น hash (ไม่ recompute == hash(effective_role)) และยอม embedded `m4_case_manifest_sha256` (ไม่อยู่ใน digest body) → 2 manifest ผ่าน preflight แล้วล้มหลัง seed/model | `validate_m4_frozen_manifest` เพิ่ม **recompute `role_identity_sha256 == _role_id_sha256(effective_role)`** (typed-id `'s:'+role` ตรง harness `_id_hash`) ; **ตัด `m4_case_manifest_sha256` ออกจาก `_M4_FROZEN_KEYS`** (embedded field → unknown-field error, ตัด two-source ambiguity) ; fixtures ย้าย role_identity → typed hash |
| **M3.1** | `_fsync_dir` เหมารวม directory-open OSError ทุกชนิดเป็น `unsupported` → POSIX EACCES/EIO/... ถูกลดเป็น Windows limitation, publisher คืน success | POSIX → `os.open`/`os.fsync` OSError **propagate ทั้งหมด** (→ `DurabilityUnconfirmed`) ; non-POSIX → `unsupported` แบบ **explicit `os.name`** (ไม่ใช่ catch-all) ; **persist `durability` mode ใน runner result** (durable/atomic-visibility-only) ไม่ใช่แค่ docstring |
| **M3.2** | `_unlink` กลืน temp-unlink OSError ทั้ง success/collision path → คืน PUBLISHED พร้อม hidden hard-link ค้าง หรือ losing body ค้างหลัง collision | `_unlink` คืน exception (ไม่กลืน) ; **success path** unlink fail → `CleanupUnconfirmed(final_path)` (ห้ามคืน PUBLISHED เงียบ) ; **collision path** unlink fail → คง primary `PublishRefused` + `add_note` (temp อาจค้าง) |

## negative tests (offline) ที่เพิ่ม
- **B2.1**: role_identity ≠ typed hash → frozen error + `RunnerError` **ก่อน provision** (`isolation.calls==[]`, `scorer.queries==[]`) ; embedded `m4_case_manifest_sha256` → unknown-field → `RunnerError` ก่อน provision
- **M3.1**: `durability_mode` ตาม platform ; POSIX `os.open` fail และ `os.fsync` fail → propagate OSError (ทั้งคู่); parent fsync fail หลัง publish → `DurabilityUnconfirmed` + final ปรากฏ ; happy result มี `durability`
- **M3.2**: winner temp-unlink fail → `CleanupUnconfirmed` + final ปรากฏ ; collision + temp-unlink fail → `PublishRefused` + `__notes__` (primary ไม่ถูกกลบ)

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_m4_runner 44/44   test_p2_atomic 25/25   test_p2_m4 59/59   test_p2_m4_harness 46/46
test_p2_runplan 95/95     test_p2 166/166        test_p2_provider 22/22   test_p2_harness 21/21
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11
```
- **รวมเครื่องนี้ (16 suites): 722/722**
- **clean env (ไม่มี qdrant_client): 678/678** (core 606 + runner 44 + atomic 25 − adapter integration 1)

## รับทราบ (platform note ของ `os.link` — เป็นงาน adapter/runbook ไม่ใช่ code fix รอบนี้)
`os.link` เป็น atomic create-if-absent เมื่อ filesystem รองรับ hard link (temp/final อยู่ dir เดียวกัน = fs เดียวกัน ✓) แต่ FAT/exFAT/บาง FUSE/บาง network fs อาจไม่รองรับ → จะ fail **ก่อนสร้าง final** (ไม่ fail-open) แต่เสีย run ทั้งรอบตอน publish หลัง teardown
→ **real adapter/runbook จะเพิ่ม startup capability probe บน output filesystem + บันทึก filesystem/durability mode ใน run provenance** (จะรวมใน adapter slice)

## ขอ Codex review (runner slice รอบ 4)
1. frozen role-identity recompute + reject embedded digest (B2.1) ปิด fail-before-mutate สำหรับ frozen ครบไหม — เหลือ field ไหนใน frozen/evidence ที่ validate หลัง seed อีก
2. durability platform-explicit + cleanup surfacing (M3.1/M3.2) พอไหม
3. หลังผ่าน → **GO เขียน real adapters** (พร้อม fs capability probe) ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** runner/atomic review = **FIX-THEN-GO** · real adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
