# Codex targeted re-review — P2 M4 safety FIX6 (`6721721`)

วันที่รีวิว: 2026-08-06

ขอบเขต: `KB_P2_M4_SAFETY_FIX6_HANDOFF.md` และ diff `460fe6b..6721721` เฉพาะ intent v2/recovery, provenance directory, clear-intent outcome และ canonical `out_dir`

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้โค้ดหรือ `STATUS.md`, ไม่เขียน Qdrant/Docker adapters และไม่รัน M4a จริง

## Intent

ทำให้ local provenance ledger ตัดสินผล append หลัง crash ได้จากหลักฐานบนดิสก์อย่าง deterministic และทำให้ path/durability/result ของ caller สอดคล้องกันก่อนใช้กับ real adapters

## Verdict

**REWORK — แนะนำใช้ SQLite เป็น authoritative ledger ตาม fallback ที่ handoff กำหนดไว้; Qdrant/Docker adapter wiring ยัง NO-GO**

intent v2 เป็นทิศทางที่ถูกและปิด blind-unlink ได้ แต่ fault-injection รอบนี้ยังพบ crash-safety hole 2 จุดและ result-alignment hole 1 จุด เนื่องจาก handoff ระบุเองว่า “ถ้ารอบ 7 ยังเจอ crash-safety hole อีกให้พิจารณา SQLite” เงื่อนไขนั้นเกิดขึ้นแล้ว การแก้ custom JSONL ต่อจะยังต้องสร้าง file-identity, locking และ recovery semantics ที่ SQLite จัดการให้แล้ว

หากเจ้าของงานตัดสินใจคง JSONL ต่อ ให้ถือ verdict เป็น **FIX-THEN-GO** และปิด B1/B2/M1 ด้านล่างก่อน

## Simpler alternative ที่แนะนำ

ใช้ `sqlite3` จาก Python stdlib เป็น authority:

- database อยู่ใน pre-existing/probed directory
- `PRAGMA synchronous=FULL`; transaction เดียวสำหรับ STARTED/terminal
- schema บังคับ `attempt_id`, run binding, event ordering และ terminal uniqueness
- เก็บ canonical JSON body + digest ใน row; export JSONL/bundle หลัง transaction สำเร็จเพื่อใช้เป็น evidence artifact
- ใช้ SQLite locking/rollback journal แทน sidecar `.lock`/`.intent` และ custom crash recovery

ทางนี้ลด surface ที่กำลัง review ได้แก่ short-file recovery, hard-link/symlink alias locks, tail truncation, intent lifecycle และ warning-after-commit โดยไม่เปลี่ยน M4 evidence contract ภายนอก

## Findings

### B1 — recovery ลด `cut` ลงตาม ledger ที่สั้นผิดปกติ แล้วล้าง intent ทำให้ committed prefix หายเงียบ (blocker)

ตำแหน่ง: `p2_provenance.py:139-170` โดยเฉพาะ `p2_provenance.py:153-155`

intent bind `cut` ซึ่งหมายถึงความยาวของ committed prefix ก่อน append แต่เมื่อ ledger ปัจจุบันสั้นกว่า `cut` code ทำ:

```python
if cut > len(data):
    cut = len(data)
```

จากนั้น classify เป็น `UNCOMMITTED`, truncate/fsync และ clear intent ทั้งที่การที่ prior committed prefix สั้นลงคือ corruption/lost durable state ไม่ใช่ append ที่ rollback ได้

fault-injection:

```text
intent.cut      = ขนาดหลัง STARTED
truncate ledger = cut - 1
recover outcome = UNCOMMITTED
intent exists   = False
reader events   = []
```

ผลกระทบ: STARTED ที่เคย fsync แล้วหาย แต่ recovery ยกระดับเหตุการณ์นี้เป็น clean rollback และทำลายหลักฐานเดียวที่บอกว่ามีความเสียหาย

แก้ขั้นต่ำถ้ายังคง JSONL:

- `len(data) < meta["cut"]` ต้อง raise `ProvenanceIndeterminate` และคง intent ไว้ ห้าม clamp/truncate/clear
- validate `type(cut) is int` ไม่รับ `bool`; จำกัด cut ให้สมเหตุสมผล
- เพิ่ม test short-by-1, empty/missing log with nonzero cut และ oversized cut ทุกเคสต้อง fail-closed

### B2 — `log_id = abspath` ไม่ใช่ file identity; log inode เดียวกันผ่าน alias ถือ lock ได้พร้อมกัน (blocker)

ตำแหน่ง: `p2_provenance.py:44-55`, `p2_provenance.py:79-96`, `p2_provenance.py:236-247`

ledger ใช้ `<path>.lock` และ `<path>.intent` เป็น sidecar ขณะที่ `_log_id()` คืนเพียง `abspath` ไม่ resolve symlink และไม่ bind inode/file ID หาก log มี hard link หรือ file-symlink สองชื่อ ทั้งสอง caller เขียนไฟล์ข้อมูลเดียวกันแต่ใช้ lock/intent คนละไฟล์

fault probe บน hard link:

```text
os.path.samefile(real, alias) = True
_log_id(real) == _log_id(alias) = False
ถือ _lock(real) แล้ว acquire _lock(alias) = สำเร็จ
```

ผลกระทบ: mutual exclusion และ one-intent-at-a-time invariant พัง; concurrent writers สามารถคำนวณ `cut` เดียวกันและเขียนทับ/ต่อกันโดยไม่เห็น intent ของอีก path

แก้ขั้นต่ำถ้ายังคง JSONL:

- canonicalize `log_path` ครั้งเดียวที่ public entry และใช้ค่าเดียวกับ log/lock/intent ทุก operation
- reject file symlink และ existing log ที่ `st_nlink != 1` หรือเปลี่ยนไป lock actual ledger handle/file identity แทน path sidecar
- bind stable file identity (`st_dev`/`st_ino` หรือ Windows file ID) ใน intent สำหรับ existing log และ verify ตอน recovery
- เพิ่ม two-alias concurrency test; alias ที่ชี้ inode เดียวกันต้องถูก reject หรือ block ด้วย lock เดียวกัน

### M1 — `warnings.warn` ทำให้ known-committed append กลับมา raise ได้เมื่อ environment ใช้ warnings-as-errors (major)

ตำแหน่ง: `p2_provenance.py:99-117`, call sites `p2_provenance.py:392-394`

การแยก unlink failure ออกจาก parent-fsync-after-unlink failure ถูกต้อง แต่ `warnings.warn(..., RuntimeWarning)` ไม่ใช่ non-throwing notification เมื่อ `PYTHONWARNINGS=error`, `-W error` หรือ test/application ตั้ง filter เป็น error มัน raise หลัง record commit และ intent unlink สำเร็จ

fault-injection:

```text
append outcome = RuntimeWarning exception
intent exists  = False
next reconcile = FAILED
```

ผลกระทบ: caller เห็น operation fail แต่ process ถัดไปเห็น terminal committed ซึ่งย้อนกลับไปเป็น result-alignment bug แบบเดียวกับรอบก่อน

แก้ขั้นต่ำ: ห้ามใช้ exception-capable warning ใน correctness path ให้ `_clear_intent` คืน structured cleanup status แล้ว `_locked_append`/caller preserve known commit outcome; observability ให้ส่งผ่าน sink/logger ที่ถูก guard ไม่ให้ exception เปลี่ยนผลธุรกรรม หรือจับ warning exception ภายในโดย explicit พร้อม test ภายใต้ `warnings.simplefilter("error")`

## Closures ที่ยืนยันแล้ว

- **B1 เดิม:** intent v2 bind `cut` + serialized-line digest และ recover exact full line ด้วย re-fsync; partial/mismatch rollback กลับ cut โดยไม่ blind unlink
- **B2 เดิม:** basename ใช้ absolute parent แล้ว และ writer reject parent ที่ยังไม่มีแทน auto-create
- **M1 เดิม:** unlink failure กับ fsync-after-unlink failure ถูกแยกคนละ branch; จุดค้างคือ notification สามารถ raise
- **M2:** `out_dir` ถูก canonicalize ก่อน STARTED, ค่าเดียวกันถูกส่งเข้า FS probe/runner/expected path และ symlink/junction retarget ที่เปลี่ยน `realpath` ถูก reject เป็น `FAILED/out_dir_retargeted`
- reader/writer/recover ยังทำภายใต้ OS lock เดียวกันเมื่อใช้ path เดียวกัน

ข้อจำกัดที่ควรบันทึกสำหรับ adapter review: string `realpath` ปิด symlink retarget แต่ไม่พิสูจน์ directory identity หาก directory ถูก rename แล้วแทนที่ด้วย inode ใหม่ที่ path เดิม; หาก threat model รวม local filesystem tampering ต้อง bind directory file ID เพิ่ม

## Verification

targeted offline suites:

```text
test_p2_provenance.py  38/38 PASS
test_p2_m4_ops.py      26/26 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                    145/145 PASS
```

Codex fault-injection probes เพิ่มเติมยืนยัน B1/B2/M1 ตาม output ใน findings ทั้งสาม; probe ใช้ temporary source file และถูกลบแล้ว ไม่มีการแก้ production/test code

ไม่ได้ reproduce `799/799` ทั้ง 19 suites เพราะ Python ที่เข้าถึงได้ใน session นี้ไม่มี optional `qdrant_client`; targeted safety suites ไม่พึ่ง dependency ดังกล่าวและผ่านทั้งหมด

## Gate หลัง review

- authoritative provenance ledger: **REWORK เป็น SQLite (แนะนำ)** หรือ **FIX-THEN-GO** หากเจ้าของงานยืนยัน JSONL
- Qdrant/Docker adapter coding: **NO-GO** จนเลือก ledger direction และ targeted review ของ B1/B2/M1 ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

ถ้ายังคง JSONL รอบถัดไปตรวจเฉพาะ short-ledger fail-closed, same-file alias locking/file identity และ warnings-as-errors; ไม่ต้องทวน intent digest happy path, pre-existing parent หรือ canonical `out_dir` อีก
