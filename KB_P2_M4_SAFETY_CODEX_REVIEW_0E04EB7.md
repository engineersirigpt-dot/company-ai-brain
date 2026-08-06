# Codex review — P2 M4 safety pieces (`0e04eb7`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: `p2_fs_probe.py`, `p2_m4_ops.py`, `p2_provenance.py` และ typed-ID refactor ตาม `KB_P2_M4_SAFETY_HANDOFF.md`  
ข้อจำกัดที่รักษาไว้: pure/offline เท่านั้น — ไม่เขียน Qdrant/Docker/model adapters, ไม่รัน M4a จริง, ไม่แก้ `STATUS.md`

## Intent

ทำให้ M4a ตรวจ output filesystem ก่อนเสีย model run, ให้ exception เป็น authority ของ operational status, และมี provenance ที่ fail-closed/อยู่รอดข้าม process ก่อนต่อ real adapters

## Verdict

**FIX-THEN-GO Qdrant/Docker adapter wiring**

typed-ID refactor ปิด constraint 4 แล้ว และการแยก probe/wrapper/provenance เป็นสามโมดูลเหมาะสม แต่ constraints 1–3 ยังไม่ครบตาม execution path จริง: probe ไม่ได้ทดสอบ directory durability, probe failure บางชนิดหลุดโดยไม่มี record, provenance ไม่มี STARTED event/ไม่มี fail-closed terminal-write contract และ log ยอม short write รวมถึงเก็บ exception text ดิบ

## Simpler-alternative check

ไม่ต้องเพิ่ม service หรือ database ใหม่ใน PoC นี้ แต่ provenance ควรเป็น **event ledger แบบ STARTED → terminal** ที่มี attempt ID และ writer contract ชัดเจน แทนการ append terminal record เพียงครั้งเดียวหลังงานจบ ส่วน filesystem probe ควร reuse `p2_atomic._fsync_dir()` โดยตรงเพื่อทดสอบ primitive เดียวกับ publisher ไม่ควร infer จาก `os.name` อย่างเดียว

## Findings

### B1 — capability probe ไม่ได้ probe directory durability primitive ที่ publisher ใช้จริง (blocker)

ตำแหน่ง: `p2_fs_probe.py:24-57`, `p2_atomic.py:50-64`, `p2_atomic.py:126-130`

`probe_output_fs()` fsync เฉพาะไฟล์ `a` แล้วคืน `AT.durability_mode()` ซึ่งเป็น label จาก platform แต่ไม่เคยเรียก `AT._fsync_dir(out_dir)`. ดังนั้นบน POSIX filesystem ที่สร้าง hard link ได้แต่เปิด/fsync directory ไม่ได้ probe จะ PASS และความผิดพลาดถูกพบหลัง model run ตอน publisher เท่านั้น

targeted probe ทำให้ publisher directory primitive ล้ม แต่ capability probe ยังคืน success:

```text
AT._fsync_dir -> OSError("dir fsync unavailable")
probe_output_fs -> RETURNED {hardlink_no_clobber: true, cleanup_ok: true, ...}
```

ผลกระทบ: constraint 1 ที่ต้องรู้ durability capability ก่อน provision/model ยังไม่จริง

แก้ขั้นต่ำ:

1. หลัง link/unlink สำเร็จ ให้ probe เรียก primitive เดียวกับ publisher (`AT._fsync_dir(out_dir)`); POSIX error → `CapabilityError`
2. non-POSIX ให้บันทึก explicit `atomic-visibility-only` ตามเดิม
3. test แยก file-fsync fail, directory-open fail และ directory-fsync fail; ทั้งหมดต้องจบก่อน ports/scorer ถูกแตะ

### B2 — probe errors นอก `CapabilityError` หลุดจาก wrapper และไม่มี provenance (blocker)

ตำแหน่ง: `p2_fs_probe.py:29-57`, `p2_m4_ops.py:34-39`

probe แปลงเป็น `CapabilityError` เฉพาะ `os.link`/`os.unlink` บางช่วง แต่ `os.makedirs`, `tempfile.mkdtemp`, file open/write/fsync และ error จาก cleanup สามารถเป็น raw `OSError`. Wrapper จับเฉพาะ `FS.CapabilityError` จึงปล่อย exception เหล่านี้ออกไปก่อน `_rec()`

targeted probe:

```text
probe_output_fs -> PermissionError("out_dir denied")
run_m4a_operational -> PermissionError escaped
provenance log exists -> false
```

นอกจากนี้ `finally: shutil.rmtree(..., ignore_errors=True)` สามารถกลืน probe-directory cleanup failure และคืน capability success ทั้งที่มี artifact probe ค้าง

ผลกระทบ: operational entry point ไม่มี status/record สำหรับ filesystem failure หลายชนิด และคำอ้าง “ทุกผลถูก persist” ไม่จริง

แก้ขั้นต่ำ:

- ให้ `probe_output_fs()` normalize operational filesystem errors ทั้งหมดเป็น `CapabilityError` พร้อม exception chaining
- ห้าม `ignore_errors=True` เป็น authority: cleanup failure ต้องทำให้ probe fail และระบุ path ที่ต้องตรวจ manual
- wrapper ควรมี defensive catch รอบ probe boundary สำหรับ unexpected `Exception` แล้ว map เป็น FAILED/fs_probe โดยใช้ข้อความ sanitized; tests ต้องครอบ makedirs/mkdtemp/open/fsync/rmtree

### B3 — provenance ถูกเขียนหลัง run เท่านั้นและ terminal append failure ไม่มี controlled outcome (blocker)

ตำแหน่ง: `p2_m4_ops.py:25-53`, `p2_provenance.py:12-25`

wrapper ไม่เขียน event ก่อน `RUN.run_m4a()`. หาก process ตาย/ถูก kill กลาง provision/model จะไม่มีหลักฐานว่า attempt เคยเริ่ม และหาก bundle publish สำเร็จแล้ว `append_provenance()` ล้ม (disk full/permission/I/O) exception จะหลุดออกไปโดยไม่มี terminal record หรือ operational status ที่จำแนกได้

targeted probe:

```text
RUN.run_m4a -> completed
append_provenance -> OSError("log disk full")
run_m4a_operational -> OSError escaped after run
```

ผลกระทบ: constraint 3 ยังไม่เป็น durable audit trail; operator แยก “ไม่เคยเริ่ม”, “ตายกลางทาง” และ “artifact มีแต่ provenance หาย” ไม่ได้

แก้ขั้นต่ำ:

1. สร้าง `attempt_id` ใหม่และ append `STARTED` **ก่อน provision/model**; ถ้า STARTED append/fsync ไม่สำเร็จให้ abort ก่อน run
2. append terminal event (`PUBLISHED|DEGRADED|FAILED`) ด้วย attempt ID เดียวกัน
3. terminal append failure หลัง run ต้องยกระดับเป็น `ProvenanceUnconfirmed`/non-zero operational outcome พร้อม artifact path หากมี; ห้ามคืน/พิมพ์ clean PUBLISHED
4. เพิ่ม recovery/read contract ที่ระบุ STARTED ซึ่งไม่มี terminal เป็น `INCOMPLETE/ABORTED-UNKNOWN`

### M1 — JSONL writer สมมติว่า `os.write()` เขียนครบและยังไม่มี crash/concurrency contract (major)

ตำแหน่ง: `p2_provenance.py:12-25`, `p2_provenance.py:28-38`

`os.write(fd, line)` คืนจำนวน byte แต่โค้ดไม่ตรวจ short write. Probe ที่บังคับให้เขียนครึ่งเดียวพบว่า append คืน success, fsync แล้ว `read_provenance()` ล้มด้วย `JSONDecodeError`

```text
append_provenance -> returned normally
log -> partial line
read_provenance -> JSONDecodeError
```

อีกทั้งการสร้าง log ใหม่ fsync เฉพาะ file ไม่ fsync parent directory และยังไม่มี multi-process append test/lock contract

แก้ขั้นต่ำ:

- กำหนด writer model ชัด: ถ้า PoC เป็น single-writer ให้ acquire exclusive process/file lock และ reject writer ที่สอง; ถ้ารองรับ concurrent writers ให้มี lock ครอบ full-record write
- ตรวจจำนวน byte; short write = provenance failure (อย่ารายงาน success) และกำหนด recovery ของ partial tail
- ใช้ canonical JSON `allow_nan=False`, จำกัด record size และ fsync parent directory เมื่อสร้าง log ใหม่บน POSIX
- เพิ่ม subprocess concurrency + injected short-write/truncated-tail tests

### M2 — generic exception ถูก persist ด้วย `repr(e)` ดิบ จึงอาจรั่ว credential/query/payload ลง log ถาวร (major)

ตำแหน่ง: `p2_m4_ops.py:37-49`

real adapters อาจคืน exception ที่มี URL credential, bearer token, query text หรือ payload. Wrapper นำ `repr(e)` ใส่ append-only provenance โดยตรง

targeted probe:

```text
RuntimeError("Authorization: Bearer TOP-SECRET")
provenance file contains "TOP-SECRET" -> true
```

ผลกระทบ: safety ledger กลายเป็นช่องข้อมูลรั่วและเก็บข้อมูลนั้นข้าม process/ระยะยาว

แก้ขั้นต่ำ:

- provenance เก็บเฉพาะ allowlisted `error_code`, `error_type`, phase และข้อความ sanitized ที่ระบบสร้างเอง
- ห้ามเก็บ raw `repr/str` ของ adapter/provider exception; หากต้อง correlate ให้ใช้ event ID และเขียนรายละเอียดเข้า restricted diagnostic channel ที่มี redaction/retention แยก
- เพิ่ม token/URL/query/payload sentinel tests เพื่อยืนยันว่า log ไม่มีค่าดิบ

## Closure ที่ยืนยันแล้ว

### Constraint 4 — CLOSED

ตำแหน่ง: `p2_eval.py:86-99`, `p2_m4_harness.py:21-22`

`typed_id_sha256()` เป็น implementation เดียวสำหรับ int/string typed IDs; evaluator role identity และ harness เรียก helper เดียวกัน จึงไม่มีสูตรซ้ำแล้ว การ reject bool/ชนิดอื่นและ int-vs-string separation ยังอยู่ครบ

### Exception-authority mapping — direction ถูก แต่ต้องปิด provenance failure

`CleanupUnconfirmed`/`DurabilityUnconfirmed` ถูก map เป็น DEGRADED จาก exception ไม่ใช่จากการพบ bundle และ generic runner errors ถูก map FAILED ถูกต้อง เส้นนี้จะปิด constraint 2 ได้เมื่อ B2/B3/M2 ทำให้ทุก boundary error ถูก map+record แบบ sanitized/fail-closed

## Verification

targeted offline suites บนเครื่องนี้:

```text
test_p2_fs_probe.py    7/7 PASS
test_p2_provenance.py  6/6 PASS
test_p2_m4_ops.py     10/10 PASS
test_p2_m4_harness.py 47/47 PASS
รวม                    70/70 PASS
```

tests ปัจจุบันผ่านครบ แต่ไม่ครอบ probes ที่ทำให้ verdict เป็น FIX-THEN-GO ด้านบน ไม่ได้แตะ Docker/Qdrant/model และไม่ได้อ้าง reproduce `746/746` ทั้งชุดใน environment นี้

## Gate หลัง review

- safety pieces: **FIX-THEN-GO**
- Qdrant/Docker adapter wiring: **NO-GO** จนปิด B1/B2/B3/M1/M2 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** module boundaries และ typed-ID refactor ถูกทาง แต่ filesystem/provenance safety ยัง fail-open ใน error/crash paths จึงยังไม่ควรเริ่ม Qdrant/Docker adapter wiring
