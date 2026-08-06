# P2 — M4a safety pieces (fs capability probe + operational provenance + exception-authority wrapper + leaf id-hash)

> **สืบเนื่อง:** `KB_P2_M4_RUNNER_FIX3_CODEX_REREVIEW_505B8F2.md` (GO real adapters + 4 non-blocking constraints)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model ; **รัน M4a จริง = ยัง NO-GO** จน adapter provenance review + Data Owner sign-off
> **ขอบเขตรอบนี้:** ทำ **safety pieces (Codex constraint 1–4) ให้ครบ+เทสต์ offline** ก่อน ; Qdrant/docker adapter wiring = slice ถัดไป

## Codex constraint → ส่งมอบ → proof

| # | constraint | ส่งมอบ |
|---|---|---|
| **1** | startup capability probe บน **output filesystem จริง** ก่อน provision/model (hard-link no-clobber, cleanup, durability mode) | **`p2_fs_probe.probe_output_fs`** — สร้าง+link+relink บน out_dir จริง : hard-link ไม่รองรับ/ไม่ no-clobber/cleanup ล้ม → `CapabilityError` ก่อน provision ; คืน `{hardlink_no_clobber, cleanup_ok, durability_mode, out_dir}` |
| **2** | wrapper ต้องถือ exception เป็น authority — `CleanupUnconfirmed`/`DurabilityUnconfirmed` **ห้ามตีความว่า clean success** เพราะเจอ bundle.json | **`p2_m4_ops.run_m4a_operational`** — probe → run → map exception เป็น status : Cleanup/Durability → **DEGRADED** (จาก exception ไม่ใช่จากการเจอไฟล์) ; RunnerError/PublishRefused/อื่น → FAILED ; สำเร็จ → PUBLISHED |
| **3** | persist durability mode + cleanup/durability exception ลง provenance ที่ **อยู่รอดข้าม process** (return dict ไม่พอ) | **`p2_provenance`** — append-only JSONL (O_APPEND + fsync) ; wrapper บันทึกทุกผล (PUBLISHED/DEGRADED/FAILED + phase + error_type + durability_mode + path) |
| **4** | typed-id hash → **leaf helper เดียว** ให้ evaluator+harness เรียกร่วม ลด drift | **`p2_eval.typed_id_sha256`** (int→'i:' str→'s:') ; `p2_eval._role_id_sha256` และ `p2_m4_harness._id_hash` เรียกตัวเดียวกัน ; test ยืนยัน `HN._id_hash == E.typed_id_sha256 == E._role_id_sha256` |

## exception → operational status (constraint 2)
```
probe_output_fs CapabilityError → FAILED (phase fs_probe)   ← ไม่ provision/model
run_m4a RunnerError/PublishRefused/PermissionError/... → FAILED (phase run)   ← ไม่มี PASS artifact
run_m4a CleanupUnconfirmed/DurabilityUnconfirmed → **DEGRADED** (phase publish)   ← artifact ปรากฏแต่ไม่ clean
run_m4a สำเร็จ → PUBLISHED (durability_mode ใน result)
ทุกกรณี → append_provenance (ข้าม process)
```

## negative/behavior tests (offline)
- **fs_probe**: happy → hardlink_no_clobber+cleanup+durability ; `os.link` ไม่รองรับ → CapabilityError ; link ไม่ raise FileExistsError → CapabilityError ; ไม่ทิ้ง probe dir ค้าง
- **provenance**: append 2 + read ตามลำดับ ; dir สร้างเอง ; อ่านซ้ำ (จำลอง process ใหม่) ครบ ; record ไม่ใช่ dict → TypeError
- **ops**: PUBLISHED+durability+evidence/receipt+provenance ; interlock ผิด → FAILED phase run + ไม่มี artifact + provenance error_type=RunnerError ; durability fail → **DEGRADED** (artifact ปรากฏจริงแต่ไม่ report PUBLISHED) ; CapabilityError → FAILED phase fs_probe + **ไม่ provision/model** (`isolation.calls==[]`, `scorer.queries==[]`)
- **constraint 4**: `HN._id_hash == E.typed_id_sha256 == E._role_id_sha256` (str) และ typed int≠str

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_fs_probe 7/7   test_p2_provenance 6/6   test_p2_m4_ops 10/10   test_p2_m4_harness 47/47
test_p2_m4_runner 44/44   test_p2_atomic 25/25   test_p2_m4 59/59   test_p2_runplan 95/95   test_p2 166/166
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11  test_p2_provider 22  test_p2_harness 21
```
- **รวมเครื่องนี้ (19 suites): 746/746**
- **clean env (ไม่มี qdrant_client): 702/702**
- โมดูลใหม่ (`p2_fs_probe`/`p2_provenance`/`p2_m4_ops`) import แค่ stdlib + p2_atomic/runner (clean-importable, ไม่ดึง qdrant_client/torch)

## ยังไม่ได้ทำ (Qdrant/docker adapter slice ถัดไป — ยัง NO-GO ที่จะ *รัน*)
- **IsolationController จริง** (isolated project/network/volume/collection บน Qdrant + docker network inspector), **FilteredProvider จริง** (wrap `p2_provider.build_candidates` บน isolated client), **OracleReader จริง** (client แยก direct scroll), **PinnedCrossEncoder** wire, CLI (argv/exit จริง)
- adapter จะเรียก `run_m4a_operational` (probe+provenance+exception-authority มีแล้ว) เป็น entry

## ขอ Codex review (safety-pieces slice)
1. capability probe (constraint 1) ครอบ guarantee ของ publisher ครบไหม — เหลือ capability ไหนที่ต้อง probe ก่อน run จริง
2. exception-authority mapping (constraint 2) + provenance ข้าม process (constraint 3) พอไหม — status/field ไหนควรเพิ่ม
3. leaf id-hash (constraint 4) ปิด drift ครบไหม
4. หลังผ่าน → เขียน **Qdrant/docker adapter slice** ต่อ ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** safety-pieces review = **FIX-THEN-GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
