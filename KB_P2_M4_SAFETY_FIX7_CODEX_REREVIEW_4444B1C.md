# Codex targeted re-review — P2 M4 safety FIX7 (`4444b1c`)

วันที่รีวิว: 2026-08-06

ขอบเขต: `KB_P2_M4_SAFETY_FIX7_HANDOFF.md` และ diff `6721721..4444b1c` เฉพาะ SQLite authority, transaction/state machine, alias/crash behavior และ JSONL evidence export

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้โค้ดหรือ `STATUS.md`, ไม่เขียน Qdrant/Docker adapters และไม่รัน M4a จริง

## Intent

แทน custom JSONL WAL ด้วย SQLite transaction authority โดยคง state machine และสร้าง JSONL เป็น immutable evidence artifact ภายหลัง

## Verdict

**FIX-THEN-GO — SQLite direction ถูก แต่ Qdrant/Docker adapter wiring ยัง NO-GO**

การ REWORK ไป SQLite ลด crash-recovery surface ได้จริง แต่ implementation ปัจจุบันรับรอง hard-link alias ตรงข้ามข้อกำหนดของ SQLite, ไม่แยก ambiguous COMMIT outcome และ `export_jsonl` ยังไม่ใช่ immutable/bound evidence artifact จึงยังประกาศ B1/B2/M1 ปิดเชิงโครงสร้างไม่ได้

## Simpler-alternative check

ไม่ควรย้อนกลับไป JSONL WAL แล้ว ทางเล็กสุดคือคง SQLite แต่ลด contract ให้ชัด:

- SQLite database ต้องมี **canonical path เดียว**, ห้าม symlink/hard-link alias
- transaction API ต้องแยก pre-commit failure ออกจาก unknown post-COMMIT outcome
- JSONL เป็น derived immutable snapshot พร้อม receipt/digest ไม่ใช่ชื่อใหม่ของ authority file

ไม่ต้องสร้าง recovery engine เองอีก แต่ต้องใช้ SQLite ภายในข้อจำกัดที่ SQLite รับรองจริง

## Findings

### B1 — hard-link test รับรองพฤติกรรมที่ SQLite ระบุว่า undefined และอาจทำ crash recovery ผิด journal (blocker)

ตำแหน่ง: claim `p2_provenance.py:10`; implementation `_connect()` ที่ `p2_provenance.py:78-99`; test `test_p2_provenance.py:122-140`

handoff สรุปว่า SQLite “ล็อก inode” จึง hard-link/symlink alias ปลอดภัย แต่ rollback journal ถูกตั้งชื่อตาม **database pathname** (`<name>-journal`) ไม่ใช่ inode เมื่อ database inode เดียวกันถูกเปิดผ่านสอง hard-link names ทั้งสอง connection จะใช้ auxiliary journal คนละ path แม้ normal concurrent writes ใน test จะ serialize ได้

เอกสาร SQLite ระบุชัดว่าการเปิด database เดียวผ่านหลาย hard/soft links ใช้ rollback journals/WAL คนละชื่อ ทำให้ process อื่นหา journal หลัง crash ไม่พบ และ behavior เป็น undefined/อาจเสียหาย:

- [SQLite — How To Corrupt An SQLite Database File, §2.6 Multiple links](https://www.sqlite.org/howtocorrupt.html#multiple_links_to_the_same_file)
- [SQLite — File Locking and Concurrency, journal alias warning](https://www.sqlite.org/lockingv3.html)

test ปัจจุบันเขียน 5+5 rows แล้วปิด connection ตามปกติ จึงไม่มี hot journal เหลือและไม่พิสูจน์ crash recovery ผ่าน alias ตาม claim

แก้ขั้นต่ำ:

- canonicalize database path ครั้งเดียวด้วย `realpath(abspath(...))`
- reject database file ที่เป็น symlink และ reject `st_nlink != 1`; ห้าม advertise alias support
- ทุก caller/adapter ต้องใช้ canonical path เดียวตลอดอายุ run
- เปลี่ยน test จาก “alias เขียนพร้อมกันต้องผ่าน” เป็น “hard-link alias ต้องถูก reject ก่อน connect/write”

### B2 — COMMIT exception ไม่ถูกจำแนก outcome; caller อาจเห็น fail แต่ row committed แล้ว (blocker)

ตำแหน่ง: `p2_provenance.py:177-207` โดยเฉพาะ `p2_provenance.py:199-205`; wrapper consumer `p2_m4_ops.py:146-156`

`COMMIT` อยู่ใน `except BaseException` เดียวกับ validation/INSERT เมื่อ COMMIT โยน exception code พยายาม `ROLLBACK`, กลืน rollback error แล้ว re-raise exception เดิม โดยไม่ตรวจ `conn.in_transaction` และไม่ verify exact row ผ่าน connection ใหม่ `ProvenanceIndeterminate` ที่ประกาศไว้จึงไม่เคยถูกใช้ในจุดที่ outcome อาจไม่ทราบ

fault-injection ให้ COMMIT สำเร็จแล้วจำลอง acknowledgement loss ได้ผล:

```text
append outcome = OperationalError
next reconcile = FAILED
```

ผลกระทบ: `p2_m4_ops` map exception ทั่วไปเป็น `PROVENANCE_UNCONFIRMED` แต่ process ถัดไปเห็น terminal ปกติ เกิด result-alignment mismatch แบบเดิมแม้ storage engine atomic

แก้ขั้นต่ำ:

- แยก phase `BEGIN/validate/INSERT` กับ `COMMIT`
- pre-commit failure ที่ transaction ยัง active → rollback และคืน uncommitted ตามประเภทเดิม
- COMMIT failure → ตรวจ `conn.in_transaction`; จากนั้นใช้ fresh read-only connection verify row identity (`attempt_id`, event, body digest)
- exact row มี → treat COMMITTED; ไม่มีและ database healthy → uncommitted; พิสูจน์ไม่ได้ → raise `ProvenanceIndeterminate`
- เพิ่ม tests: commit-before-apply fail, commit-applied/ack-lost, verification connection fail

SQLite ระบุว่าอย่างน้อยกรณี `SQLITE_BUSY` ตอน COMMIT transaction ยัง active และ retry ได้ จึงไม่ควรรวม COMMIT errors ทุกชนิดไว้ใน generic rollback branch: [SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html)

### B3 — `export_jsonl` เป็น destructive in-place rewrite และยังไม่อยู่ใน operational path (blocker)

ตำแหน่ง: `p2_provenance.py:257-278`; actual producer `p2_m4_ops.py:117-193`

`export_jsonl` เปิด final path ด้วย `O_TRUNC`, เขียนตรงลง final และอนุญาต overwrite artifact เดิม ไม่มี temp+atomic publish/no-clobber และไม่มี export receipt ที่ bind row count/max seq/body digest กับ source snapshot

fault-injection ให้ write ครั้งแรกได้ครึ่งหนึ่งแล้วครั้งถัดไปล้ม:

```text
export outcome      = OSError
original preserved  = False
final partial bytes = 48
```

นอกจากนี้ `p2_m4_ops` ไม่เรียก `export_jsonl` เลย ตัวแปร `provenance_log` และ tests ยังคงใช้ชื่อ `prov.jsonl` แต่ไฟล์นั้นกลายเป็น SQLite binary การอ้างว่า “external M4 evidence contract ไม่เปลี่ยน” จึงยังไม่จริงใน call path ปัจจุบัน

แก้ขั้นต่ำ:

- authority path ต้องชื่อ/contract เป็น `.db` ชัดเจน (`provenance_db`)
- export ผ่าน temp file ใน parent เดียวกัน → full write → fsync → immutable no-clobber/atomic publish → parent fsync
- คืน receipt อย่างน้อย source DB identity/schema version, snapshot `max_seq`, row count, JSONL SHA-256 และ final path
- validate rows/digests ก่อน export และเพิ่ม failure/collision/retry tests
- wire export อย่าง explicit ใน operational/adapter flow ก่อนอ้างว่า JSONL contract คงเดิม

### M1 — `body_sha256` และ duplicated columns ไม่ถูกตรวจตอนอ่าน จึงไม่ bind evidence กับ row จริง (major)

ตำแหน่ง: schema `p2_provenance.py:87-96`; `_attempt_records` `p2_provenance.py:102-104`; `read_provenance` `p2_provenance.py:220-237`

code เขียน `attempt_id`, `run_id`, `event`, `body`, `body_sha256` แต่ตอนอ่าน select เฉพาะ `body` แล้ว `json.loads`; ไม่ recompute digest และไม่เทียบค่าภายใน body กับ columns ที่ UNIQUE index/state query ใช้

fault probe แก้ body ผ่าน SQL ให้ `attempt_id="forged"` โดย columns/digest ยังเป็นของ `original` แล้ว `read_provenance` คืน `forged` โดยไม่ error

ผลกระทบ: checksum เป็น dead metadata; export สามารถเผยแพร่ body ที่ไม่ตรง row identity/index และคำว่า “ผูกกับ DB” ยังพิสูจน์ไม่ได้

แก้ขั้นต่ำ: central row decoder ต้อง select columns+body+digest, verify SHA-256, canonical body และ exact equality ของ `attempt_id/run_id/event` ทุกครั้ง ใช้ decoder เดียวกันใน `_attempt_records`, `read_provenance` และ export; mismatch → `ProvenanceError`

### M2 — existing zero-length authority ถูก initialize ใหม่เป็น empty ledger แทน fail-closed (major)

ตำแหน่ง: `_connect` `p2_provenance.py:78-99`; `read_provenance` `p2_provenance.py:220-237`

หาก database file มีอยู่แต่ถูก truncate เป็น 0 bytes `sqlite3.connect` มองเป็น database ใหม่ จากนั้น `CREATE TABLE IF NOT EXISTS` สร้าง schema และ `read_provenance` คืน `[]`

fault-injection:

```text
valid DB with STARTED → truncate to zero → read outcome ACCEPTED, events=[]
```

ผลกระทบ: corrupt/truncated authority สูญหลักฐานทั้งหมดโดยถูกตีความเป็น empty fresh ledger และ test “corrupt db” ปัจจุบันใช้ garbage nonzero จึงไม่ครอบ edge นี้

แก้ขั้นต่ำ: แยก `initialize_new_db()` ออกจาก `open_existing_db()`; existing zero-length/missing schema/wrong `PRAGMA user_version` ต้อง fail-closed ตั้ง `user_version=SCHEMA_VERSION` ตอนสร้างครั้งแรกและ verify schema/index/version ก่อนใช้งาน

## Closures ที่ยืนยันแล้ว

- state machine/reducer และ terminal schema ถูกย้ายมาใช้กับ transaction path โดยไม่ลด validation เดิม
- `BEGIN IMMEDIATE` ทำ read-state → validate → INSERT atomic สำหรับ caller ที่เปิด canonical database path เดียว
- UNIQUE partial indexes กัน duplicate STARTED/terminal เป็น defense-in-depth ใน normal schema
- `synchronous=FULL` และ `journal_mode=DELETE` ถูกตั้งจริงใน targeted test environment
- custom `.lock`/`.intent`, cut recovery และ warnings-after-intent-clear ถูกถอดออกแล้ว
- corrupt non-SQLite database แบบ nonzero ถูก normalize เป็น `ProvenanceError`

## Verification

targeted offline suites:

```text
test_p2_provenance.py  32/32 PASS
test_p2_m4_ops.py      26/26 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                    139/139 PASS
```

Codex fault-injection probes เพิ่มเติมยืนยัน B2/B3/M1/M2 ตาม outputs ใน findings; probe ใช้ temporary source file และถูกลบแล้ว ไม่มีการแก้ production/test code

ไม่ได้ reproduce `793/793` ทั้ง 19 suites เพราะ Python ที่เข้าถึงได้ใน session นี้ไม่มี optional `qdrant_client`; targeted safety suites ไม่พึ่ง dependency ดังกล่าวและผ่านทั้งหมด

## Gate หลัง review

- SQLite provenance direction: **ACCEPT**
- safety implementation: **FIX-THEN-GO**
- Qdrant/Docker adapter coding: **NO-GO** จนปิด B1/B2/B3/M1/M2 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

รอบถัดไปตรวจเฉพาะ canonical single-name DB policy, COMMIT outcome resolution, row decoder/checksum, zero-length/schema-version fail-closed และ atomic immutable export + operational wiring ไม่ต้องทวน reducer/UNIQUE/normal concurrency อีก
