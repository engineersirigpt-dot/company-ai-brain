# Codex targeted re-review — P2 M4 safety FIX (`8066f0e`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: re-review B1–B3/M1–M2 จาก `KB_P2_M4_SAFETY_FIX_HANDOFF.md`  
ข้อจำกัดที่รักษาไว้: pure/offline เท่านั้น — ไม่เขียน Qdrant/Docker/model adapters, ไม่รัน M4a จริง, ไม่แก้ `STATUS.md`

## Intent

ทำให้ filesystem preflight และ operational provenance เป็น fail-closed authority ที่ทนต่อ bad input, concurrent process และ process crash ก่อนเปิดงาน Qdrant/Docker adapter wiring

## Verdict

**FIX-THEN-GO Qdrant/Docker adapter wiring**

B1/B2 เดิมเรื่อง directory primitive/error normalization ปิดแล้ว และ raw exception redaction ปิดแล้ว แต่ event ledger ยังมี 3 blocker: attempt identity/state transition ไม่บังคับ, truncated-tail recovery ไม่ซ่อม log ก่อน append และ O_EXCL sentinel lock ค้างถาวรเมื่อ process ตาย นอกจากนี้ failure-path probe cleanup ยังไม่ observable และ ledger ยังไม่ bind immutable run/capability metadata

## Simpler-alternative check

JSONL ใช้ต่อได้ แต่ควรใช้ **OS-backed file lock ที่ถูกปล่อยอัตโนมัติเมื่อ process ตาย** และให้ writer ตรวจ/ตัด uncommitted tail ภายใต้ lock ก่อน append ทุกครั้ง หากต้องเขียน stale-lock recovery/lease/state machine เองมากกว่านี้ ทางที่เล็กและตรวจง่ายกว่าคือ per-attempt immutable `STARTED`/`terminal` records ที่ publish แบบ atomic no-clobber

## Findings

### B3.1 — caller ควบคุม `attempt_id` และ `reconcile()` ยอม transition ที่เป็นไปไม่ได้ (blocker)

ตำแหน่ง: `p2_m4_ops.py:26-35`, `p2_provenance.py:102-114`

`run_m4a_operational()` รับ `attempt_id` จาก caller โดยไม่ validate/สร้างใหม่. ค่า `None` ถูกเขียนลง STARTED และ PUBLISHED ได้, runner ทำงานสำเร็จ แต่ `reconcile()` ข้าม record ทั้งคู่เพราะ attempt ID ไม่ใช่ string

targeted probe:

```text
attempt_id=None
run result.status = PUBLISHED
ledger contains STARTED + PUBLISHED
reconcile(...) = {}
```

state machine เองก็ยอม terminal ที่ไม่มี STARTED และยอมใช้ attempt ID ซ้ำ/หลาย terminal โดยให้ค่าหลังสุดทับค่าก่อน:

```text
[PUBLISHED] without STARTED                  -> {a: PUBLISHED}
STARTED(r1), PUBLISHED(r1), STARTED(r2), FAILED(r2) -> {a: FAILED}
```

ผลกระทบ: ledger สามารถสร้าง success ที่ไม่มี run attempt จริง หรือทำให้ผลสำเร็จเดิมหายเพราะ reuse ID จึงยังเป็น audit authority ไม่ได้

แก้ขั้นต่ำ:

1. ให้ operational wrapper สร้าง cryptographically random attempt ID เอง (หรือ validate UUID/token ที่ server สร้าง) ห้ามรับ `None`/blank/control/oversize
2. bind `attempt_id` แบบ create-once: STARTED ต้องเป็น event แรกและมีครั้งเดียว; terminal ต้องมีครั้งเดียวและตาม STARTED เท่านั้น
3. terminal ต้องมี `run_id` เดียวกับ STARTED; duplicate/conflicting/out-of-order event → `ProvenanceError`, ห้าม last-write-wins
4. เพิ่ม tests: None/blank, terminal-only, duplicate STARTED, duplicate/conflicting terminal และ attempt ID reuse ข้าม run

### B3.2 — “truncated-tail recovery” แค่มองข้าม tail แต่ไม่ repair ก่อน append จึงทำ terminal ถัดไปหาย (blocker)

ตำแหน่ง: `p2_provenance.py:48-82`, `p2_provenance.py:85-99`

reader split/filter บรรทัดแล้ว drop JSON ที่พังเฉพาะบรรทัดสุดท้าย แต่ writer ไม่ตรวจหรือตัด tail ที่พังก่อน append. record ใหม่จึงต่อท้ายเศษ JSON เดิมในบรรทัดเดียวและถูก drop ไปด้วย

targeted probe:

```text
STARTED\n + partial '{"..."'       -> read = [STARTED]
append FAILED\n                    -> read = [STARTED]
reconcile                         -> INCOMPLETE   # terminal ที่เพิ่ง fsync หายจากมุม reader
```

อีก edge หนึ่ง: final JSON ที่ valid แต่ไม่มี newline ถูกยอมเป็น committed terminal ทั้งที่ newline เป็น byte สุดท้ายของ append protocol และ writer อาจตายก่อน fsync/return:

```text
'{"attempt_id":"ghost","event":"PUBLISHED"}' (ไม่มี \n)
read/reconcile -> PUBLISHED
```

ผลกระทบ: recovery สามารถทิ้ง terminal ที่เขียนใหม่ หรือยืนยัน terminal ที่ writer ไม่เคย commit สำเร็จ

แก้ขั้นต่ำ:

- ถือ newline เป็น commit marker: tail ที่ไม่มี `\n` ต้อง uncommitted เสมอ แม้ JSON parse ผ่าน
- ภายใต้ writer lock ให้ inspect log และ `ftruncate()` ถึง byte หลัง newline สุดท้ายก่อน append; fsync หลัง truncate ตาม contract
- หาก corruption อยู่ก่อน committed tail ให้ fail `ProvenanceError` ห้ามซ่อมเดา
- เพิ่ม tests “partial tail → append terminal → terminal อ่าน/reconcile ได้” และ “valid JSON without newline → drop”

### M1.1 — O_EXCL sentinel lock ไม่ crash-safe และ release failure ถูกกลืน (blocker)

ตำแหน่ง: `p2_provenance.py:30-45`, `p2_provenance.py:61-82`

lock เป็นไฟล์ว่าง `<log>.lock`. หาก process ตายหลังสร้างไฟล์แต่ก่อน `_release_lock()`, OS ไม่ลบไฟล์ให้ writer ถัดไป; ทุก attempt จะจบ `ProvenanceLocked` ตลอดไปจนคนลบเอง. `_release_lock()` ยังกลืน unlink failure และคืน append success ทั้งที่ lock ค้าง

targeted probe:

```text
สร้าง stale .lock (จำลอง process ตาย)
_acquire_lock(retries=1) -> ProvenanceLocked
lock ยังอยู่และไม่มี owner/lease สำหรับ safe recovery
```

นี่ชนกับเป้าหมายหลักของ ledger ที่ต้องบอกสถานะหลัง process crash โดยตรง

แก้ขั้นต่ำ:

- ใช้ OS file locking (`fcntl` บน POSIX / `msvcrt` หรือ library ที่รองรับ Windows) ซึ่งปล่อย lock เมื่อ fd/process ตาย
- หากคง sentinel file ต้องมี owner PID + process-start identity/lease และ stale takeover แบบ atomic ที่พิสูจน์ว่า owner ตายจริง; PID อย่างเดียวไม่พอเพราะ reuse
- release failure ต้อง surface เป็น `ProvenanceError/UnlockUnconfirmed`, ไม่กลืน
- เพิ่ม **subprocess crash test**: child acquire แล้วถูก terminate; parent ต้อง acquire ต่อได้โดยไม่ลบ lock manual

### M1.2 — probe cleanup failure บน failure path ยังถูกซ่อนและทิ้ง directory โดยไม่มีพิกัด (major)

ตำแหน่ง: `p2_fs_probe.py:27-66`

เมื่อ primary probe operation ล้ม `ok=False`; หาก `shutil.rmtree()` ล้มใน `finally`, code ไม่ raise และไม่แนบ note เพื่อไม่กลบ primary error. ผลคือ caller เห็นแค่ link/fsync error และไม่รู้ว่า `.fsprobe.*` ค้าง

targeted probe:

```text
os.link -> OSError("link fail")
shutil.rmtree -> OSError("cleanup fail")
CapabilityError notes -> None
out_dir contains .fsprobe.<id>
```

แก้ขั้นต่ำ: คง primary `CapabilityError` แต่แนบ cleanup exception + exact probe path ด้วย `add_note()`/structured field; success-path cleanup failure ยัง raise ตามเดิม

### M3 — terminal provenance ยังไม่ bind immutable run request/capability proof (major)

ตำแหน่ง: `p2_m4_ops.py:28-38`, `p2_m4_ops.py:47-66`

STARTED/terminal records มีเพียง attempt ID, run ID, timestamp, phase/status/error type และบางกรณี path/durability. ผล `probe_output_fs()` (`hardlink_no_clobber`, `cleanup_ok`, real out_dir) ไม่ถูก persist และ ledger ไม่ bind `run_manifest_sha256`/M4 frozen digest/model/image หรือ artifact/evidence digest

ผลกระทบ: หลังข้าม process operator รู้ว่า “run-1 PUBLISHED” แต่พิสูจน์ไม่ได้ว่า attempt นั้นใช้ immutable RunPlan/corpus/model/output filesystem ชุดใด; path อย่างเดียวไม่ใช่ content binding

แก้ขั้นต่ำก่อน adapter provenance review:

- STARTED bind `run_manifest_sha256`, `m4_case_manifest_sha256`, model/image identifiers และ configured output realpath หลัง pure plan validation
- terminal bind capability summary และสำหรับ PUBLISHED/DEGRADED bind artifact/evidence/receipt digest ที่ recompute ได้
- ใช้ timestamp จาก trusted clock แยก `started_at`/`finished_at`; ปัจจุบัน `recorded_at=now` ค่าเดียวถูกใช้ทั้งสอง event

## Closure ที่ยืนยันแล้ว

### B1/B2 เดิม — CLOSED

`probe_output_fs()` เรียก `AT._fsync_dir(out_dir)` จริง, POSIX directory failure ถูก normalize เป็น `CapabilityError`, wrapper มี defensive boundary และ success-path rmtree failure ไม่ถูกคืนเป็น PASS

### M2 เดิม — CLOSED

operational ledger เก็บเฉพาะ error type/phase/status/path ที่สร้างโดยระบบ ไม่ persist `repr/str` ดิบ; secret sentinel ใน adapter exception ไม่ปรากฏใน log

### Full-write bounds — CLOSED เฉพาะ healthy lock/log

writer loop จน byte ครบ, reject zero-progress/NaN/oversize และ concurrent thread test ผ่าน แต่ crash recovery/lock semantics ยังเปิดตาม B3.2/M1.1

## Verification

targeted offline suites บนเครื่องนี้:

```text
test_p2_fs_probe.py    11/11 PASS
test_p2_provenance.py  15/15 PASS
test_p2_m4_ops.py      11/11 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
รวม                    106/106 PASS
```

tests ปัจจุบันผ่านครบ แต่ไม่ครอบ state/crash probes ด้านบน ไม่ได้แตะ Docker/Qdrant/model และไม่ได้อ้าง reproduce `760/760` ทั้งชุดใน environment นี้

## Gate หลัง review

- safety pieces: **FIX-THEN-GO**
- Qdrant/Docker adapter wiring: **NO-GO** จนปิด B3.1/B3.2/M1.1/M1.2/M3 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** filesystem probe และ redaction ปิดจริง แต่ ledger ยังยืนยันลำดับเหตุการณ์/commit/crash recovery ไม่ได้ จึงยังไม่ควรเริ่ม Qdrant/Docker adapter wiring
