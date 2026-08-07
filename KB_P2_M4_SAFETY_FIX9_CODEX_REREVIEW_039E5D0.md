# Codex targeted re-review — P2 M4 safety FIX9 (`039e5d0`)

วันที่รีวิว: 2026-08-07

ขอบเขต: `KB_P2_M4_SAFETY_FIX9_HANDOFF.md` และ diff `ecb7f7c..039e5d0` เฉพาะ exact init/open schema contract, same-snapshot receipt และ attempt-safe diagnostic export

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้ source/tests/`STATUS.md`, ไม่แตะ Qdrant/Docker/model และไม่รัน M4a จริง

## Intent

ทำให้ SQLite เป็น operational provenance authority ที่เปิดไฟล์เดิมแบบ fail-closed โดยไม่เปลี่ยนไฟล์แปลกปลอม พร้อมสร้าง diagnostic JSONL ที่ receipt ผูก snapshot เดียวและ retry คนละ attempt ไม่ชนกัน

## Verdict

**FIX-THEN-GO — ยังไม่อนุมัติ Qdrant/Docker adapter slice**

B2 same-snapshot receipt ปิดจริง และการประกาศ JSONL เป็น diagnostic-only ทำให้ export failure ไม่ใช่ clean-decision hole แล้ว แต่ B1 ยังไม่เป็น verify-only/exact จริง 2 จุด และ B3 filename mapping ยังไม่ injective จึงยังไม่ควรให้ adapter ยึด contract นี้

M4a real run ยังคง **NO-GO** ตาม gate เดิม

## Simpler-alternative check

โครง `_try_create → _initialize / _open_existing` ใช้ต่อได้ แต่ไม่ควรให้ generic `_connect()` ทำทั้ง bootstrap, verification และ runtime mutation ในลำดับเดียว ทางเล็กกว่าและตรวจง่ายกว่าคือ:

- มี explicit `initialize_provenance_db()` หนึ่งครั้งก่อนเริ่ม run; runtime open เป็น verify-only จริง
- stamp/verify `PRAGMA application_id` เป็น file-format magic ร่วมกับ `user_version`
- valid existing DB ต้อง verify schema/journal mode ก่อนตั้ง runtime pragmas; foreign DB ต้องถูกปิดโดยไม่มี persistent mutation
- verify allowlist ของ behavior-changing schema objects โดยเฉพาะ trigger ไม่ใช่เช็คเฉพาะ columns กับ expected index names

วิธีนี้ลด bounded polling/init race surface และทำให้คำว่า authority file มี marker เฉพาะของมัน ไม่ต้องเดาจาก table shape อย่างเดียว

## Findings

### B1.1 — existing DB ถูกเปลี่ยน `journal_mode` ก่อน schema verification จึงไม่ใช่ verify-only (blocker)

ตำแหน่ง: `_apply_pragmas()` `p2_provenance.py:117-120`; `_connect()` `p2_provenance.py:206-223`; test ปัจจุบัน `test_p2_provenance.py:159-168`

เส้นทางจริงของ existing file คือ `_try_create=False → sqlite3.connect → _apply_pragmas → _open_existing` โดย `_apply_pragmas()` รัน `PRAGMA journal_mode=DELETE` ก่อนรู้ว่าไฟล์เป็น provenance DB หรือไม่ คำสั่งนี้เป็น persistent mutation ไม่ใช่ connection-only verification

fault probe สร้าง foreign SQLite DB ที่อยู่ใน WAL mode แล้วเรียก `read_provenance()`:

```text
schema verdict = ProvenanceError (ถูก reject)
journal_mode ก่อนเรียก = wal
journal_mode หลังถูก reject = delete
```

ดังนั้น test ปัจจุบันที่ตรวจเพียงว่าไม่มี `events/index` เพิ่มและ `user_version` ไม่ถูก stamp ยังเขียว ทั้งที่ foreign DB ถูกแก้จริงแล้ว

แก้ขั้นต่ำ:

- existing path: เปิด connection → ตั้งได้เฉพาะ non-persistent timeout → verify application id/version/schema/journal contract แบบ read-only → จึงค่อยตั้ง connection runtime options
- ห้ามใช้ `PRAGMA journal_mode=DELETE` เป็น auto-repair บน existing DB; initialize ให้เป็น DELETE ครั้งแรก แล้ว existing ที่ mode ผิดต้อง fail-closed หรือใช้ explicit migration
- เพิ่ม regression: foreign DB ใน WAL mode ต้องถูก reject และยังคง WAL หลังปิด connection

### B1.2 — `_verify_schema()` ยอม behavior-changing trigger; `append_event()` สามารถคืน success โดยไม่มี row (blocker)

ตำแหน่ง: `p2_provenance.py:140-151`, normal commit path `p2_provenance.py:374-409`

`_verify_schema()` ตรวจ exact column tuple และ SQL ของ index สองชื่อ แต่ไม่ตรวจ table DDL, extra indexes หรือ triggers บน `events` จึงยังไม่ใช่ exact behavioral schema

fault probe เริ่มจาก DB ที่โมดูลสร้างถูกต้อง แล้วเพิ่ม:

```sql
CREATE TRIGGER swallow_insert
BEFORE INSERT ON events
BEGIN SELECT RAISE(IGNORE); END;
```

ผลจริง:

```text
append_event("lost-0002") returned success
read_provenance() มีเพียง ["seed-0001"]
```

trigger ทำให้ INSERT ถูก ignore โดยไม่โยน error; COMMIT สำเร็จและ normal path ไม่มี post-commit row verification จึงรายงาน durable append ที่ไม่เคยเกิดขึ้น

แก้ขั้นต่ำ:

- verify exact table SQL/required objects และ reject trigger ทุกตัวบน `events`; ตรวจ `PRAGMA index_list/index_xinfo` ว่าไม่มี behavior-changing extra index/constraint ที่อยู่นอก allowlist
- เพิ่ม `application_id` เพื่อแยก provenance file ออกจาก DB ที่เพียงเลียน schema
- เพิ่ม regression trigger `RAISE(IGNORE)` โดย append ต้อง fail ก่อน transaction mutation
- defense-in-depth: พิจารณา verify exact inserted row หลัง normal COMMIT เช่นเดียวกับ ack-loss resolver หากต้นทุนยอมรับได้

### M1 — attempt filename sanitizer ไม่ injective; attempt ที่ valid สองตัวชน diagnostic path เดียวกัน (major)

ตำแหน่ง: attempt contract `p2_m4_ops.py:29-37`; filename mapping `p2_m4_ops.py:157-164`; test ปัจจุบัน `test_p2_m4_ops.py:147-156`

validator อนุญาตทั้ง `:` และ `_` แต่ filename sanitizer แปลง `:` เป็น `_` ดังนั้น:

```text
att:0001 -> att_0001
att_0001 -> att_0001
```

ทั้งสองเป็น valid `attempt_id` แต่ retry ตัวที่สองชน no-clobber path ของตัวแรก Test ใช้เฉพาะ hyphen จึงไม่เห็น collision

เพราะ JSONL ถูกลดเป็น diagnostic-only แล้ว finding นี้ไม่ทำให้ SQLite terminal หรือ decision evidence ผิด จึงเป็น major ไม่ใช่ blocker แต่คำว่า attempt-safe ยังกล่าวไม่ได้

แก้ขั้นต่ำ: derive filename ด้วย encoding ที่ one-to-one/collision-resistant เช่น SHA-256 ของ exact UTF-8 attempt ID (อาจใส่ safe prefix เพื่ออ่านง่าย) หรือจำกัด attempt contract ให้เป็น safe basename เดียวกับ filename และใช้ค่า exact; เพิ่ม colon-vs-underscore และ case-insensitive filesystem cases

## Closures ที่ยืนยันแล้ว

- **B2 CLOSED:** `_read_snapshot()` อ่าน rows, sequence และ `user_version` ใน read transaction เดียว; `row_count/max_seq/body` derive จาก rows ชุดเดียว และ `_db_stats` ถูกถอดแล้ว Fault hook หลัง freeze ไม่ทำให้ receipt/file drift
- **B3 contract decision ACCEPT:** SQLite ledger + validated runner bundle เป็น decision evidence; JSONL ถูกประกาศเป็น diagnostic-only ชัดเจน ดังนั้น export error ไม่ควรเปลี่ยน terminal ตาม contract นี้
- O_EXCL creator + bounded waiter ผ่าน normal 4×5 concurrent-create test โดยไม่ pre-seed
- previous alias rejection, COMMIT/ack-loss branches, row checksum/identity และ atomic no-clobber primitive ไม่พบ regression ใน targeted suites

## Verification

targeted suites ที่รันจริงใน review นี้:

```text
test_p2_provenance.py  46/46 PASS
test_p2_m4_ops.py      29/29 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                     156/156 PASS
```

เพิ่ม Codex fault probes ชั่วคราวสำหรับ foreign-WAL mutation, trigger-swallowed INSERT และ attempt filename collision; ลบ probe file หลังรันแล้ว ไม่ได้แก้ production/test code

ไม่ได้รันซ้ำ full 19-suite `810/810`; ตัวเลขนั้นคงเป็นหลักฐานจาก FIX9 handoff ไม่ใช่ผลรันใหม่ของ review นี้

## Gate หลัง review

- SQLite provenance direction: **ACCEPT**
- same-snapshot receipt และ diagnostic-only decision semantics: **CLOSED**
- verify-only open / exact behavioral schema / attempt-safe diagnostic filename: **OPEN — FIX-THEN-GO**
- Qdrant/Docker adapter coding: **NO-GO** จนปิด B1.1, B1.2, M1 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน sign-off + M4b + validated canary

รอบถัดไปตรวจเฉพาะ ordering ของ verify-before-persistent-pragmas, exact trigger/object allowlist และ injective attempt filename mapping ไม่ต้องทวน snapshot receipt, JSONL diagnostic decision หรือ main concurrent-create happy path อีก
