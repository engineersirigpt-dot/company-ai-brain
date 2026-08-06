# P2 — ปิด runner B1-B2/M1-M3 (atomic no-clobber + frozen/case preflight + shared run_id + observability)

> **สืบเนื่อง:** `KB_P2_M4_RUNNER_FIX_CODEX_REREVIEW_3D30B15.md` (FIX-THEN-GO real adapters — 2 blocker + 3 major)
> **pure/injectable เท่านั้น** — รัน M4a บน Qdrant/model จริง = **NO-GO** จน adapter-review + Data Owner sign-off

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B1** ⭐ | publisher ใช้ `exists → os.replace` = ไม่ atomic no-clobber ; concurrent writers ทั้งคู่ถึง replace ได้ (loser ไม่ได้ controlled refuse ; POSIX replace overwrite) | เปลี่ยนเป็น **`os.link(temp, final)`** (create-if-absent atomic) → `FileExistsError` = `PublishRefused` ; ลบ early-exists check (os.link เป็น immutability control ตัวเดียว) ; **barrier test 2-thread** → PUBLISHED 1 + PublishRefused 1 พอดี, winner content, ไม่มี temp ค้าง |
| **B2** ⭐ | frozen digest/roles/categories + case id/role/query validate **หลัง seed** → case ผิดตัวหลังทำให้ model ถูกเรียกบางส่วน | เพิ่ม `_preflight_frozen_cases()` **ก่อน provision/scorer**: `validate_m4_frozen_manifest==[]` + frozen digest==RunPlan + roles/categories==RunPlan + case set **exact** (ไม่ dup/extra/missing) + ทุก id/role/query_text/query_vector hash ตรง frozen → mismatch = `RunnerError` ก่อน provision (isolation/scorer ไม่ถูกแตะ) |
| **M1** | RunPlan กับ publisher ใช้ run_id contract คนละชุด (reserved ผ่าน RunPlan → seed/model ก่อน publisher refuse) ; `.match()` ยอม trailing newline | **validator เดียว** `p2_atomic.is_safe_run_id` (`fullmatch` + reserved + no control/newline) แชร์ทั้ง `validate_run_plan` และ publisher → reserved/newline ถูก reject **ก่อน provision** |
| **M2** | `_safe_teardown` กลืน cleanup failure หมด → operator ไม่รู้ว่า resource อาจค้าง | `_safe_teardown` คืน teardown exception (ไม่ raise) ; failure path `_note(original, "cleanup ล้ม: ... resource อาจค้าง")` ผ่าน `add_note` — **คง primary cause เดิม** + surface teardown fail |
| **M3** | `_fsync_dir` กลืน fsync error ทุก OS แต่ docstring อ้าง crash durability | POSIX genuine fsync fail → **raise** ; publisher แปลงเป็น `DurabilityUnconfirmed` (final ปรากฏแล้วแต่ durability ไม่ยืนยัน) ; Windows (เปิด dir fd ไม่ได้) = `unsupported`/atomic-visibility only (ระบุชัด) + fault-injection test |

## negative tests (offline) ที่เพิ่ม (ปิด test-gap ที่ Codex ชี้)
- **B1**: 2-thread concurrent publish → exactly-one winner + one `PublishRefused` (ไม่มี uncontrolled error), winner file อยู่, ไม่มี temp ค้าง
- **B2**: case แรก/ท้าย ผิด, case set เกิน/ขาด/ซ้ำ, frozen digest ≠ RunPlan → `RunnerError` **ก่อน provision** (`isolation.calls==[]`, `scorer.queries==[]`)
- **M1**: reserved (`CON`) + trailing newline (`run-1\n`) ใน plan → `RunnerError` ก่อน provision ; publisher reject `\n`/`\r`/`\x00`/reserved
- **M2**: work-fail+teardown-fail → primary=`RunnerError` + `__notes__` มี "cleanup" ; provision-fail+teardown-fail → primary=`RuntimeError(provision)` + note ; ทั้งสองไม่มี artifact
- **M3**: `_fsync_dir` ปกติไม่ raise (durable/unsupported) ; parent fsync fail หลัง publish → `DurabilityUnconfirmed` + final ปรากฏแล้ว

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_m4_runner 41/41   test_p2_atomic 20/20   test_p2_m4_harness 46/46   test_p2_m4 56/56
test_p2_runplan 95/95     test_p2 166/166        test_p2_provider 22/22     test_p2_harness 21/21
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11
```
- **รวมเครื่องนี้ (16 suites): 711/711**
- **clean env (ไม่มี qdrant_client): 667/667** (core 606 + runner 41 + atomic 20)

> `p2_runplan` import `is_safe_run_id` จาก `p2_atomic` (stdlib-only ไม่มี cycle) — clean-importable

## ขอ Codex review (runner slice รอบ 3)
1. atomic no-clobber (os.link) + concurrent barrier test ปิด B1 ครบไหม — มี platform/fs ไหนที่ os.link ไม่ atomic
2. frozen/case preflight (B2) ครอบ fail-before-mutate ครบไหม — เหลือ input ไหนที่ validate หลัง seed อีก
3. shared run_id contract + cleanup observability + durability semantics (M1/M2/M3) พอไหม
4. หลังผ่าน → **GO เขียน real adapters** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** runner/atomic review = **FIX-THEN-GO** · real adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
