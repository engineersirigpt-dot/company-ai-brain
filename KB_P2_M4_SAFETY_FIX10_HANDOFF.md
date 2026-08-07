# P2 — SQLite hardening รอบ 10 : verify-before-persistent-pragma + exact behavioral schema + injective export filename

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX9_CODEX_REREVIEW_039E5D0.md` (SQLite direction **ACCEPT** ; B2/B3 CLOSED ; FIX-THEN-GO — 2 blocker + 1 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py`, `p2_m4_ops.py`, `test_p2_provenance.py`, `test_p2_m4_ops.py`

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B1.1** | blocker | `_apply_pragmas` รัน `PRAGMA journal_mode=DELETE` บน existing file **ก่อน** verify → foreign WAL db ถูก convert เป็น delete (persistent mutation) จึงไม่ใช่ verify-only | แยก `_conn_pragmas` (busy_timeout non-persistent, **ก่อน** verify) ออกจาก `_runtime_pragmas` (synchronous per-connection, **หลัง** verify) ; existing path = `_verify_open` **อ่าน** `journal_mode`/`application_id` (ไม่ set) → ต้องเป็น `delete` + app_id ตรง ไม่งั้น fail-closed ; `journal_mode=DELETE` set เฉพาะตอน `_initialize` (ไฟล์ใหม่) ไม่ใช่ auto-repair |
| **B1.2** | blocker | `_verify_schema` ตรวจแค่ column tuple + index 2 ชื่อ ไม่ตรวจ table DDL/extra index/**trigger** → `CREATE TRIGGER ... RAISE(IGNORE)` ทำ INSERT ถูก swallow แต่ `append_event` คืน success (ไม่มี row) | `_verify_schema` = **exact behavioral**: table DDL ตรงเป๊ะ + column tuple + index set = allowlist (unique/partial/predicate) + **reject index นอก allowlist และ trigger ทุกตัวบน events** + `application_id` magic (`'PROV'`) แยก provenance file ; **defense-in-depth**: post-COMMIT `_row_exists` ยืนยัน row จริง (กัน silent-drop ที่หลุด verify) |
| **M1** | major | filename sanitizer แปลง `:`→`_` ไม่ injective → `att:0001` กับ `att_0001` ชน diagnostic path เดียว (no-clobber) | `_provenance_export_path` = readable prefix (lossy) + **sha256 ของ exact UTF-8 attempt_id** (`<run_id>.<safe>.<hash40>.provenance.jsonl`) — injective/collision-resistant, colon/underscore/case ไม่ชน |

## behavior tests (offline) ที่เพิ่ม/แก้

- **B1.1**: existing db ถูก flip เป็น WAL → **reject + ยังเป็น WAL** หลังปิด connection (ไม่ถูก convert เป็น delete)
- **B1.2**: (1) schema ถูกหมดแต่ `application_id` ไม่ตรง → reject ; (2) extra index นอก allowlist → reject ; (3) `TRIGGER RAISE(IGNORE)` → **append fail-closed ก่อน mutation** + ledger มีแค่ seed (raw count) ; (4) defense-in-depth: monkeypatch ให้ trigger หลุด `_verify_schema` → post-commit row verify ยังจับ (COMMIT ok แต่ row หาย)
- **M1**: `att:0000001` vs `att_0000001` (valid ทั้งคู่) → export path ต่างกัน (injective) + deterministic
- คงเดิม: foreign-db/weak-index/zero-length fail-closed, alias rejection, COMMIT outcome, row decoder, snapshot receipt, retry-safe export

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 51   test_p2_m4_ops 31   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 817/817** (รอบ 9 = 810 → provenance +5, m4_ops +2)
- provenance suite **เสถียร 6/6 รันซ้ำ**
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## contract สรุป (verify-only open)

- **existing provenance file เปิดแบบ verify-only จริง**: ตั้งได้แค่ busy_timeout ก่อน verify → อ่าน `application_id='PROV'` + `journal_mode=delete` + exact schema (table DDL/columns/index-allowlist/no-trigger) + version → ผ่านค่อยตั้ง synchronous ; foreign/tampered = fail-closed **โดยไม่ mutate ไฟล์**
- **init = ไฟล์ที่เราสร้างเอง (O_EXCL) เท่านั้น** — stamp `application_id`+`user_version`+schema ใต้ write lock
- append durable ต่อ event + **post-commit row verification** (สองชั้นกับ ack-loss resolver)

## หมายเหตุ threat model (ยกให้ adapter review)

- verify-only + application_id + exact schema + reject trigger/alias ปิด foreign-adopt/silent-drop/journal-alias ; **directory rename→replace (inode ใหม่ที่ path เดิม)** ยังเป็น local-fs-tampering ที่ path string ไม่กัน — ยกให้ adapter ตัดสินว่าต้อง bind directory/file ID เพิ่มไหม

## ขอ Codex review (safety-pieces slice รอบ 11)

1. verify-before-persistent-pragma (B1.1) + exact behavioral schema/trigger allowlist + application_id (B1.2) + injective export filename (M1) ปิดครบไหม
2. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 11 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
