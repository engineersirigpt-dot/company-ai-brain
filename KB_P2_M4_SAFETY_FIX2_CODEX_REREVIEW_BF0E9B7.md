# Codex targeted re-review — P2 M4 safety FIX2 (`bf0e9b7`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: `KB_P2_M4_SAFETY_FIX2_HANDOFF.md` — B3.1/B3.2/M1.1/M1.2/M3 เท่านั้น  
ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้โค้ดหรือ `STATUS.md`, ไม่เขียน Qdrant/Docker adapters และไม่รัน M4a จริง

## Intent

ทำให้ operational provenance เป็น fail-closed audit authority ที่ยืนยันได้ว่า attempt เดียวเดินตามลำดับ `STARTED → terminal`, อยู่รอดจาก process crash และ terminal ผูกกับ run/artifact จริงก่อนเริ่ม adapter wiring

## Verdict

**FIX-THEN-GO — Qdrant/Docker adapter wiring ยัง NO-GO**

normal path แข็งขึ้นจริง: attempt ID ถูกสร้าง/ตรวจใน wrapper, tail ที่ไม่มี newline ถูกตัดก่อน append, OS lock คืนหลัง process ตาย และ failure-path cleanup มี note แล้ว แต่ audit authority ยังผิดได้ 2 blocker และ M3 ยังไม่ fail-closed อีก 2 จุด

## Simpler-alternative check

ยังไม่จำเป็นต้องทิ้ง JSONL แต่ควรมี **transition reducer/record validator ตัวเดียว** ที่อ่าน records ตามลำดับ แล้ว reuse ทั้งก่อน append และใน `reconcile()` แทนการมี logic สองชุดที่ไม่สมมูลกัน

หาก post-write/`fsync` recovery เริ่มซับซ้อนเกินไป ทางเลือกที่เล็กกว่าในระยะยาวคือ per-attempt immutable records (`STARTED` และ terminal) ที่ publish แบบ atomic no-clobber ด้วย primitive เดียวกับ `p2_atomic`; อย่าสร้าง state machine และ commit semantics ชุดที่สองโดยไม่มีเหตุจำเป็น

## Findings

### B3.1-R — `reconcile()` ตรวจเพียงจำนวน ไม่ตรวจลำดับหรือ `run_id` binding (blocker)

ตำแหน่ง: `p2_provenance.py:191-215`; tests ปัจจุบัน `test_p2_provenance.py:95-97`

`append_event()` ป้องกัน normal write path บางกรณีแล้ว แต่ `reconcile()` สะสม count ก่อนตรวจท้ายลูป จึงยอม terminal ที่มาก่อน STARTED หากภายหลังมี STARTED หนึ่งแถว และไม่เปรียบเทียบ `run_id` ของ terminal กับ STARTED เลย

targeted probe:

```text
[PUBLISHED(run=r1), STARTED(run=r1)] -> {'a': 'PUBLISHED'}
[STARTED(run=r1), FAILED(run=r2)]    -> {'b': 'FAILED'}
```

ผลกระทบ: ledger ที่มี event ผิดลำดับหรือข้าม run ยังถูกสรุปเป็น terminal สำเร็จได้ จึงยังใช้เป็น audit authority หลัง crash/corruption หรือ raw append ไม่ได้

แก้ขั้นต่ำ:

1. สร้าง reducer ตัวเดียวที่ consume ทีละ record ตามลำดับ: state ว่างรับได้เฉพาะ STARTED; state STARTED รับ terminal ได้ครั้งเดียว; terminal state รับ event เพิ่มไม่ได้
2. terminal ต้องมี `run_id` ตรง STARTED; validate `status == event` และ required terminal schema ด้วย
3. ให้ทั้ง `_validate_transition()` และ `reconcile()` เรียก reducer เดียวกัน
4. เพิ่ม regression tests สำหรับ terminal→STARTED, run mismatch, status/event mismatch และ malformed record

### B3.2-P — append ที่รายงานว่า `fsync` ล้ม ทิ้ง terminal ซึ่ง reader กลับถือว่า committed (blocker)

ตำแหน่ง: `p2_provenance.py:134-168`, `p2_m4_ops.py:67-74`

writer เขียน JSON พร้อม newline ก่อนเรียก `os.fsync(fd)`. หาก fsync โยน exception บรรทัดนั้นยังมองเห็นได้ในไฟล์; `read_provenance()` ใช้ newline เป็น commit marker จึงอ่าน terminal เป็น committed ขณะที่ caller ได้ exception และ wrapper จะรายงาน `PROVENANCE_UNCONFIRMED`

targeted probe:

```text
append PUBLISHED -> OSError (simulated fsync failure)
read events      -> [STARTED, PUBLISHED]
reconcile        -> PUBLISHED
retry FAILED     -> ProvenanceError (duplicate terminal)
```

ผลกระทบ: API result กับ durable ledger ขัดกัน และ retry ไม่สามารถปิด attempt ได้ นี่ชนกับเป้าหมายหลักของ B3 โดยตรง

ปัญหาเดียวกันเกิดได้กับ exception หลัง commit เช่น `_release()` ล้ม: record อาจ durable แล้วแต่ append ถูก report ว่าล้ม

แก้ขั้นต่ำ:

1. กำหนด commit/error boundary ให้ชัด: exception ก่อน commit ต้อง rollback/truncate กลับ `cut` เดิมและ fsync การ rollback ก่อนปล่อย lock
2. หากยืนยัน rollback ไม่ได้ ให้ยกระดับเป็น ledger-unrecoverable/poisoned และห้าม `read/reconcile/append` สรุป clean terminal จน operator recovery; ห้ามตีความเป็น append ธรรมดาที่ retry ได้
3. exception หลัง durable commit เช่น unlock/close cleanup ต้องไม่ถูก map ว่า “record ไม่ได้เขียน”; แยก committed-with-cleanup-warning ออกจาก uncommitted
4. เพิ่ม tests สำหรับ terminal fsync failure, rollback failure และ release-after-commit failure โดย assert ว่า result กับ reconcile ไม่ขัดกัน

### M3.1 — `started_at`/`finished_at` ยังเป็น caller-controlled values ไม่ใช่ trusted clock (major)

ตำแหน่ง: `p2_m4_ops.py:48-69`; tests `test_p2_m4_ops.py:111,123-124`

public wrapper รับ timestamp ทั้งสองค่าจาก caller โดยตรง ไม่มี type/ISO-timezone/order validation และไม่เรียก clock ภายใน ดังนั้น docstring ที่ระบุ “trusted clock” ยังไม่จริง

targeted probe บน invalid-plan path:

```text
started_at  = None
finished_at = {'spoofed': True}
result      = FAILED/plan_invalid
ledger      = บันทึกสองค่าดังกล่าวโดยไม่ reject
```

สำหรับ valid run caller ก็สามารถย้อนหลังเวลา หรือทำ `finished_at < started_at` ได้เช่นเดียวกัน

แก้ขั้นต่ำ: ให้ wrapper รับ trusted clock port แล้วเรียกเองที่ STARTED/terminal boundary; validate ISO-8601+timezone และ monotonic order. บน PUBLISHED ให้ cross-check กับ validated receipt timestamps หรือระบุให้ชัดว่า operational time กับ model-run time เป็นคนละช่วง

### M3.2 — content binding ยัง optional และไม่ fail-closed ที่ terminal boundary (major)

ตำแหน่ง: `p2_m4_ops.py:40-45,90-100`, `p2_provenance.py:114-131`

`_sha256_file()` กลืน `OSError` แล้วคืน `None`; `_terminal()` ยังเขียน `PUBLISHED`/`DEGRADED` ต่อได้โดยไม่ตรวจว่า digest เป็น SHA-256 จริง นอกจากนี้ `append_event()` ยอม terminal schema ขั้นต่ำที่ไม่มี capability/artifact/evidence/receipt binding และ `reconcile()` ก็ยังสรุป clean status

ส่วน evidence/receipt digest ใน success path ถูก copy จาก in-memory result (`ev.get(...)`) ไม่ได้ recompute จาก final bundle ณ operational boundary แม้ `run_m4a()` จะ validate ก่อน publish แล้วก็ตาม จึงยังมี TOCTOU ระหว่าง publish กับ ledger append

ผลกระทบ: artifact ถูกลบ/อ่านไม่ได้/เปลี่ยนหลัง publish สามารถจบด้วย clean `PUBLISHED` ที่ `artifact_sha256=None` หรือ binding ใน terminal ไม่ครบ

แก้ขั้นต่ำ:

1. PUBLISHED ต้อง hash final file ได้และได้ exact 64-hex; hash/read failure → ห้าม PUBLISHED
2. โหลด final bundle แล้ว recompute artifact hash, evidence-body digest และ receipt digest พร้อม re-run public bundle validator ก่อน terminal append
3. terminal record validator ต้อง enforce required fields ตาม event/status; หาก `append_event()` เป็น low-level API ที่ไม่ต้อง enforce M3 ให้ทำเป็น private และห้ามใช้ ledger authority path
4. เพิ่ม tests: artifact หายก่อน terminal, `_sha256_file` failure, missing/null digest, tampered final bundle และ minimal PUBLISHED record

## Closures ที่ยืนยันแล้ว

### B3.1 normal append path — CLOSED เฉพาะ producer path

wrapper สร้าง crypto-random attempt ID เมื่อไม่ส่งค่า, reject unsafe token และ `append_event()` กัน duplicate STARTED/terminal-without-STARTED/duplicate terminal/run mismatch บนประวัติที่ปกติได้จริง ข้อเปิดเหลือ strict reconciliation ตาม B3.1-R

### B3.2 tail repair — CLOSED เฉพาะ pre-append tail

valid JSON ที่ไม่มี newline ถูกมองเป็น uncommitted, partial tail ถูก truncate ใต้ lock ก่อน append และ committed interior corruption ถูก reject จริง ข้อเปิดเหลือ post-write failure semantics ตาม B3.2-P

### M1.1 OS crash-safe lock — CLOSED บน Windows environment นี้

subprocess ถือ lock → parent ถูก block → terminate child → parent acquire ได้โดยไม่ลบ lock file เอง ทดสอบผ่านจริง

### M1.2 probe cleanup observability — CLOSED

failure-path cleanup error ถูกแนบกับ primary `CapabilityError` พร้อม probe path และไม่กลบ primary cause

## Verification

targeted offline suites บนเครื่องนี้:

```text
test_p2_fs_probe.py    12/12 PASS
test_p2_provenance.py  21/21 PASS
test_p2_m4_ops.py      14/14 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
รวม                    116/116 PASS
```

ไม่ได้รัน Docker/Qdrant/model/M4a และไม่ได้อ้างว่า reproduce `770/770` ทั้งชุด การผ่าน 116 checks ไม่หักล้าง targeted probes เพราะ tests ปัจจุบันยังไม่มี order/run-binding, post-write fsync failure และ untrusted-time cases

## Gate หลัง review

- safety pieces: **FIX-THEN-GO**
- Qdrant/Docker adapter wiring: **NO-GO** จนปิด B3.1-R, B3.2-P, M3.1 และ M3.2 แล้ว targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** รอบนี้ปิด normal producer, tail-repair, crash-lock และ cleanup note ได้จริง แต่ ledger ยังสรุป event ผิดลำดับ/คนละ run เป็น clean terminal และ post-write failure ทำให้ result ขัดกับ disk state จึงยังไม่ควรเริ่ม adapter wiring
