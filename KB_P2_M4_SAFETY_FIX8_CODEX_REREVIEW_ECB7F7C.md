# Codex targeted re-review — P2 M4 safety FIX8 (`ecb7f7c`)

วันที่รีวิว: 2026-08-06

ขอบเขต: `KB_P2_M4_SAFETY_FIX8_HANDOFF.md` และ diff `4444b1c..ecb7f7c` เฉพาะ canonical DB path, COMMIT outcome, row decoding, schema/version fail-closed, immutable JSONL export และ operational wiring

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้ source/tests/`STATUS.md`, ไม่แตะ Qdrant/Docker/model และไม่รัน M4a จริง

## Verdict

**FIX-THEN-GO — ยังไม่อนุมัติ Qdrant/Docker adapter slice**

ทิศทาง SQLite authority ยังถูกต้อง และของเดิมปิดจริงหลายจุด แต่ fault probes พบ 3 ช่องที่ทำให้คำว่า fail-closed/bound evidence ยังไม่จริง: existing SQLite file ถูก adopt/mutate เป็น ledger ได้, export receipt อาจอ้างคนละ snapshot กับ JSONL และชื่อ export ระดับ `run_id` ทำให้ retry attempt ใหม่ติด artifact เก่าพร้อมคืน clean terminal ได้

M4a real run ยังคง **NO-GO** ตาม gate เดิม

## Findings

### B1 — `_connect()` รับและแก้ไข SQLite DB ที่ไม่ใช่ provenance authority; schema verification ตรวจเพียงชื่อ object (blocker)

ตำแหน่ง: `p2_provenance.py:131-165`

`_connect()` เรียก `CREATE TABLE/INDEX IF NOT EXISTS` ก่อนตรวจ version/schema และเมื่อ `PRAGMA user_version=0` จะ stamp เป็น version 1 เสมอ ไม่ได้แยก “DB ใหม่ที่ฟังก์ชันนี้สร้าง” ออกจาก “ไฟล์ SQLite เดิมที่ caller ชี้มาผิด” ส่วน `_schema_ok()` เช็คเพียงว่ามี object ชื่อ `events`, `ux_started`, `ux_terminal`; ไม่ตรวจ columns, uniqueness หรือ partial-index predicate

fault probes ให้ผล:

```text
foreign SQLite DB + unrelated table + user_version=0
read_provenance() -> []
หลังอ่าน: user_version=1 และถูกเพิ่ม events/ux_started/ux_terminal

events table + indexes ชื่อถูก แต่ indexes เป็น non-unique/non-partial
_append_raw(STARTED เดิมซ้ำสองครั้ง) -> accepted, read ได้ 2 rows
```

ผลกระทบ: path misconfiguration หรือ schema ที่เสีย/ถูกแทนที่ถูกเปลี่ยนเป็น authority ที่ดู valid โดยเงียบ และ defense-in-depth ของ STARTED/terminal uniqueness หายโดย `_schema_ok()` ยังผ่าน จึงยังปิด M2 “missing/wrong schema fail-closed” ไม่ได้

แก้ขั้นต่ำ:

- แยก explicit initialization ออกจาก open-existing path; runtime open ต้องไม่สร้าง/แก้ schema ของไฟล์ที่มีอยู่แล้ว
- existing DB ที่ `user_version=0`, schema หาย หรือ schema ไม่ตรง exact contract ต้อง fail-closed; migration/initialization ต้องเป็นคำสั่งแยกที่ตั้งใจเรียก
- verify exact table columns/constraints และ index semantics (`unique`, columns, partial predicate) ไม่ใช่ชื่อ object อย่างเดียว
- เพิ่ม test foreign DB, same-name weak indexes และ first-create/concurrent-open โดยไม่ใช้ pre-seed กลบ initialization race

### B2 — JSONL body กับ `max_seq`/`row_count` ใน receipt มาจากคนละ DB snapshot (blocker)

ตำแหน่ง: `p2_provenance.py:372-416`

`export_jsonl()` อ่าน records ผ่าน `read_provenance()` แล้วปิด connection จากนั้น publish ไฟล์ ก่อนเปิด connection ใหม่ใน `_db_stats()` เพื่อสร้าง receipt หากมี append committed ระหว่างสองจุดนี้ ไฟล์กับ receipt จะไม่ตรงกัน

fault probe แทรก committed STARTED หนึ่งแถวก่อน `_db_stats()` ให้ผล:

```text
JSONL rows       = 1
receipt.row_count = 2
receipt.max_seq   = 2
JSONL digest      = valid สำหรับไฟล์ 1 row
```

ดังนั้น receipt ไม่ได้ bind “snapshot ที่ export” แม้ digest ของไฟล์จะถูกต้อง

แก้ขั้นต่ำ:

- เปิด read transaction/connection เดียว แล้วอ่าน `seq + row columns/body/digest + user_version` จาก snapshot เดียว
- derive `row_count` และ `max_seq` จาก rows ชุดที่นำไป serialize โดยตรง; ห้าม query `_db_stats()` หลัง publish
- เพิ่ม concurrent-append/fault-hook test ที่ยืนยันว่า receipt กับไฟล์ยังตรงกัน แม้ writer commit รอบ export

### B3 — export path ระดับ `run_id` freeze หลักฐานที่ attempt แรก; export failure เป็น return-only best-effort แต่ terminal ยัง clean (blocker)

ตำแหน่ง: `p2_m4_ops.py:141-163`; test ปัจจุบัน `test_p2_m4_ops.py:139-145`

wrapper export ทุก terminal ไป path เดียว `<run_id>.provenance.jsonl` ขณะที่ ledger รองรับหลาย `attempt_id` ต่อ run และ publisher เป็น no-clobber การ retry run เดิมด้วย attempt ใหม่จึงไม่สามารถ publish snapshot ใหม่ได้

fault probe จำลอง attempt แรก FAILED แล้ว attempt ที่สอง PUBLISHED ภายใต้ `run_id` เดิม:

```text
first export rows = 2
second export     = ProvenanceError (no-clobber)
JSONL เดิม        = [STARTED, FAILED]
SQLite ledger     = 4 rows รวม STARTED, PUBLISHED ของ attempt ใหม่
```

ใน operational path exception นี้ถูกกลืนเป็น `provenance_export_error` บน dict ที่คืนใน process เท่านั้น แต่ terminal ใน SQLite ยังเป็น `PUBLISHED` และไม่มี durable export receipt/error event หลัง restart จึงอาจเห็น clean PUBLISHED คู่กับ JSONL เก่าที่ไม่รู้จัก attempt ที่สำเร็จ

แก้ขั้นต่ำต้องเลือก contract ให้ชัดหนึ่งทาง:

1. ถ้า JSONL เป็น required evidence: ใช้ immutable path ที่ bind `run_id + attempt_id` (หรือ immutable export id), persist receipt/error เป็น durable record และ decision gate ต้องไม่ยอม clean evidence เมื่อ export ขาด/ชน/ไม่ยืนยัน durability
2. ถ้า SQLite เป็นหลักฐานเดียวและ JSONL เป็น diagnostic best-effort: ระบุและทดสอบให้ชัดว่า JSONL ไม่ใช่ส่วนหนึ่งของ clean-publish evidence contract แล้วห้าม downstream ใช้ไฟล์ `<run_id>.provenance.jsonl` เป็นหลักฐานตัดสิน

ไม่ควรใช้ run-level no-clobber file เดียวร่วมกับ export ทุก terminal เพราะสอง semantics ขัดกันโดยตรง

## Closures ที่ยืนยันแล้ว

- previous B1: canonical `realpath` + reject symlink DB / `st_nlink != 1` ถูกใช้ใน connect/read/write/export ตาม scope local filesystem เดิม
- previous B2: COMMIT ถูกแยกจาก pre-commit; active transaction, applied-but-ack-lost และ fresh-verification failure มีเส้นทางแยกกันจริง การที่ explicit `ROLLBACK` error ถูกกลืนควรมี regression test แต่ `in_transaction=True` + connection close ยังไม่ใช่ blocker ใหม่ใน threat model นี้
- previous M1: `_decode_row()` ตรวจ body SHA-256, canonical JSON และ `attempt_id/run_id/event` column identity ใน read/state/export path จริง
- previous M2 บางส่วน: existing zero-byte และ nonzero `user_version` ที่ไม่ตรงถูก reject แล้ว; ส่วน exact-schema fail-closed ยังเปิดตาม B1
- atomic file publisher แบบ temp → full write/fsync → hard-link no-clobber → parent fsync ปิด partial-final/overwrite ใน single-export path แล้ว; lifecycle/snapshot binding ยังเปิดตาม B2/B3
- rename `provenance_log` → `provenance_db` และ test paths `.db` ถูก wire แล้ว

## Verification

targeted suites ที่รันจริงใน review นี้:

```text
test_p2_provenance.py  43/43 PASS
test_p2_m4_ops.py      28/28 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                     152/152 PASS
```

เพิ่ม Codex fault probes ชั่วคราวสำหรับ foreign/weak schema, export snapshot race และ retry export collision; ลบ probe files หลังรันแล้ว ไม่ได้แก้ production/test code

ไม่ได้รันซ้ำ full 19-suite `806/806`; ตัวเลขนั้นคงเป็นหลักฐานจาก handoff ไม่ใช่ผลรันใหม่ของ review นี้

## Gate หลัง review

- SQLite provenance direction: **ACCEPT**
- canonical alias rejection / main COMMIT branches / row decoder / atomic no-clobber primitive: **CLOSED ตามขอบเขตด้านบน**
- exact schema authority + snapshot-bound receipt + retry-safe durable export contract: **OPEN — FIX-THEN-GO**
- Qdrant/Docker adapter coding: **NO-GO** จนปิด B1-B3 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน sign-off + M4b + validated canary

รอบถัดไปตรวจเฉพาะ 3 เรื่อง: exact initialize/open schema contract, same-snapshot export receipt และ attempt-safe durable export/result alignment ไม่ต้องทวน alias rejection, normal COMMIT/ack-loss, row decoder หรือ atomic no-clobber primitive อีก
