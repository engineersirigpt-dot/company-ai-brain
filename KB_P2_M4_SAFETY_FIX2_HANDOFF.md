# P2 — ปิด safety B3.1-B3.2/M1.1-M1.2/M3 (crash-safe ledger + attempt state machine + content binding)

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX_CODEX_REREVIEW_8066F0E.md` (FIX-THEN-GO Qdrant/docker — 3 blocker + 2 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B3.1** ⭐ | caller คุม `attempt_id` (None เขียนได้แล้ว reconcile ข้าม) ; state machine ยอม terminal ไม่มี STARTED / attempt reuse / last-write-wins | wrapper **สร้าง attempt_id เอง (crypto-random `secrets.token_hex`)** หรือ validate token (reject None/blank/control/oversize) ; `append_event` **state machine ใต้ lock**: STARTED create-once+first, terminal ครั้งเดียว+ตาม STARTED, run_id ต้องตรง → ผิด = `ProvenanceError` ; `reconcile` strict (duplicate/terminal-without-STARTED → error) |
| **B3.2** ⭐ | "tail recovery" แค่ drop ตอน read แต่ writer ไม่ตัด tail พังก่อน append → terminal ใหม่ต่อท้ายเศษ = หาย ; valid JSON ไม่มี `\n` ถูกนับ committed | **newline = commit marker** : tail ไม่ลงท้าย `\n` = uncommitted เสมอ ; writer อ่าน+`ftruncate` ตัด uncommitted tail **ใต้ lock ก่อน append** + fsync ; interior corrupt (committed) = `ProvenanceError` |
| **M1.1** ⭐ | O_EXCL sentinel lock ค้างถาวรเมื่อ process ตาย ; release failure ถูกกลืน | เปลี่ยนเป็น **OS advisory lock** (`fcntl.flock` POSIX / `msvcrt.locking` Windows, non-blocking + bounded retry) — **OS ปล่อยเองเมื่อ fd/process ตาย** ; **subprocess crash test**: child ถือ lock → parent ProvenanceLocked → kill child → parent acquire ได้ (ไม่ลบ lock manual) |
| **M1.2** | probe cleanup fail บน **failure path** ถูกซ่อน — caller ไม่รู้ `.fsprobe.*` ค้าง | คง primary `CapabilityError` + **`add_note()` แนบ cleanup exception + exact probe path** (failure path) ; success path cleanup fail = raise เหมือนเดิม |
| **M3** | terminal ไม่ bind immutable run/capability/artifact — path อย่างเดียวไม่ใช่ content binding | **STARTED bind** `run_manifest_sha256`/`m4_case_manifest_sha256`/`model_revision`/`image_digest`/out_dir realpath (หลัง pure validate) ; **terminal bind** capability summary + `artifact_sha256`(recompute)/`evidence_body_sha256`/`run_receipt_sha256` ; `started_at`/`finished_at` แยกจาก trusted clock |

> เพิ่มเติม: os.open ใช้ `O_BINARY` (Windows) กัน CRLF ทำ byte offset เพี้ยน + robust full-read

## behavior tests (offline) ที่เพิ่ม
- **B3.1**: attempt_id=None → generate ; invalid → ValueError ; duplicate STARTED / terminal-without-STARTED / duplicate terminal / run_id ไม่ตรง / attempt reuse → ProvenanceError ; reconcile strict
- **B3.2**: valid JSON ไม่มี newline → drop ; partial tail → append terminal → ตัด tail + terminal reconcile ได้ ; interior corrupt → ProvenanceError
- **M1.1**: **subprocess** child acquire → parent ProvenanceLocked → kill → parent acquire ได้ ; concurrent 4×5 writers → 20 records
- **M1.2**: failure-path cleanup fail → CapabilityError + note มี path
- **M3**: STARTED มี run_manifest/m4_manifest/model/image/out_dir/started_at ; terminal มี capability + artifact/evidence/receipt digest + finished_at แยก

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_fs_probe 12/12   test_p2_provenance 21/21   test_p2_m4_ops 14/14   test_p2_m4_harness 47/47
test_p2_m4_runner 44/44  test_p2_atomic 25/25   test_p2_m4 59/59   test_p2_runplan 95/95   test_p2 166/166
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11  test_p2_provider 22  test_p2_harness 21
```
- **รวมเครื่องนี้ (19 suites): 770/770**
- **clean env (ไม่มี qdrant_client): 726/726**

## ขอ Codex review (safety-pieces slice รอบ 3)
1. attempt state machine + strict reconcile (B3.1) ปิด audit-authority ครบไหม
2. newline-commit + tail-truncate-before-append (B3.2) + OS crash-safe lock (M1.1) ปิด crash recovery ครบไหม
3. probe cleanup observability (M1.2) + immutable content binding (M3) พอไหม
4. หลังผ่าน → เขียน **Qdrant/docker adapter slice** ต่อ ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** safety-pieces review = **FIX-THEN-GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
