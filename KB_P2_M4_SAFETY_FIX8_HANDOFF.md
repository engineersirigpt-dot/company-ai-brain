# P2 — SQLite hardening รอบ 8 : canonical single-name + COMMIT outcome + row checksum + zero-length + atomic export

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX7_CODEX_REREVIEW_4444B1C.md` (SQLite direction **ACCEPT** ; FIX-THEN-GO — 3 blocker + 2 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py`, `p2_m4_ops.py` (wiring + rename), `test_p2_provenance.py`, `test_p2_m4_ops.py`

## แก้คำกล่าวอ้างผิดจากรอบ 7

รอบ 7 handoff อ้างว่า "SQLite ล็อก inode → hard-link/symlink alias ปลอดภัย" — **ผิด** ตามสเปก SQLite (rollback journal ตั้งชื่อตาม *pathname* `<name>-journal` ไม่ใช่ inode → เปิด db ผ่านหลาย link = journal คนละชื่อ = crash recovery undefined/corruption) รอบนี้เปลี่ยนจุดยืนเป็น **reject alias** ไม่ใช่รับรอง

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B1** | blocker | hard-link alias undefined ตามสเปก SQLite แต่ test เดิม "รับรอง" alias | `_resolve_db_path()`: **canonical single-name** (`realpath(abspath)`) + **reject symlink db และ `st_nlink != 1`** ; ทุก op (connect/read/write/export) ใช้ค่าเดียว ; ไม่ advertise alias |
| **B2** | blocker | `COMMIT` อยู่ใน `except` เดียวกับ validate/INSERT → COMMIT fail แบบ ack-lost/ retryable ไม่ถูกจำแนก ; `ProvenanceIndeterminate` ไม่เคยถูกใช้ | **แยก commit phase**: pre-commit fail (tx active) → rollback/uncommitted ; COMMIT fail → `_resolve_commit_outcome`: `in_transaction` True → uncommitted (retryable) ; ปิดแล้ว → verify row ผ่าน **fresh connection** → COMMITTED (ack หาย) / uncommitted / **`ProvenanceIndeterminate`** (verify ไม่ได้) |
| **B3** | blocker | `export_jsonl` เป็น in-place `O_TRUNC` rewrite ไม่มี receipt ; ไม่มีใครเรียก ; authority ยังชื่อ `.jsonl` (จริงเป็น SQLite binary) | export = **temp + fsync + `os.link` no-clobber + parent fsync + cleanup tmp ทุกกรณี** ; คืน **receipt** (source_db/schema_version/max_seq/row_count/jsonl_sha256/path) ; validate ทุก row ก่อน export ; **wire เข้า wrapper** (`_terminal` export `<out_dir>/<run_id>.provenance.jsonl` best-effort) ; rename param `provenance_log`→**`provenance_db`** + test paths `.db` |
| **M1** | major | `body_sha256` เขียนแต่ไม่ verify ตอนอ่าน → body ปลอมผ่าน SQL ได้ | **central row decoder** `_decode_row`: verify `body_sha256`, canonical body และ **column identity** (attempt_id/run_id/event) ทุกครั้ง ; ใช้ใน `_attempt_records`/`read_provenance`/export ; mismatch → ProvenanceError |
| **M2** | major | existing zero-length authority → connect ตีเป็น db ใหม่ → read คืน `[]` (สูญหลักฐานเงียบ) | `_connect`: existing 0-byte → **ProvenanceError (fail-closed)** ; ตั้ง+verify `PRAGMA user_version = SCHEMA_VERSION` ; schema/index หาย → fail-closed |

## behavior tests ที่เพิ่ม/แก้ (offline)

- **B1**: hard-link (`st_nlink=2`) → append **ทั้ง alias และ real** ถูก reject + read fail-closed ; unlink alias → ใช้ได้อีก ; symlink db → reject (skip ถ้า fs/สิทธิ์ไม่รองรับ)
- **B2**: (1) COMMIT fail + tx active → uncommitted (row ไม่ลง) ; (2) COMMIT applied + ack lost → verify row → committed (append สำเร็จ) ; (3) COMMIT ambiguous + verify ไม่ได้ → `ProvenanceIndeterminate`
- **B3**: export receipt (row_count/max_seq/jsonl_sha256/schema_version) + digest ตรงไฟล์ ; no-clobber (ทับไม่ได้ + ไม่มี tmp ค้าง) ; write ล้มกลางคัน → final ไม่ถูกสร้าง + ไม่มี tmp ค้าง ; wrapper terminal แนบ `provenance_export` + สร้าง `<run_id>.provenance.jsonl`
- **M1**: column tamper (attempt_id column != body) และ body tamper (body != sha256) → read ProvenanceError
- **M2**: existing 0-byte db → read/append fail-closed
- concurrent test **pre-seed** (กัน create-race ชน zero-length check) → 21 records ; lock contention/crash-safe คงเดิม

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 43   test_p2_m4_ops 28   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 806/806** (รอบ 7 = 793 → provenance +11, m4_ops +2)
- provenance suite **เสถียร 5/5 รันซ้ำ** (concurrency/alias/crash-safe/commit-injection)
- symlink-rejection test **skip บน Windows** (ไม่มีสิทธิ์สร้าง symlink) — ตรรกะ reject รันได้บน POSIX
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## หมายเหตุ threat model (ยกให้ adapter review)

- canonical `realpath` + reject symlink/hardlink ปิด alias-journal corruption ; แต่ **directory rename→replace ด้วย inode ใหม่ที่ path เดิม** ยังเป็น local-fs-tampering ที่ path string ไม่กัน — ถ้า threat model รวม local fs tampering ต้อง bind directory/file ID ในชั้น adapter
- `ProvenanceIndeterminate` ตอน commit-verify ไม่ได้ = conservative (row อาจ committed จริงแต่ยืนยันไม่ได้) — caller (`p2_m4_ops`) map เป็น `PROVENANCE_INDETERMINATE` ให้ operator ตรวจ

## ขอ Codex review (safety-pieces slice รอบ 9)

1. canonical single-name + alias rejection (B1) ครบทุก entry (connect/read/write/export) ไหม
2. COMMIT outcome resolution (B2) จำแนก committed/uncommitted/indeterminate ถูกทุก branch ไหม ; row decoder/checksum (M1) + zero-length/version fail-closed (M2) + atomic export/receipt/wiring (B3) ปิดครบไหม
3. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 9 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
