# Codex targeted re-review — P2 M4 safety FIX3 (`56ad337`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: `KB_P2_M4_SAFETY_FIX3_HANDOFF.md` — B3.1-R/B3.2-P/M3.1/M3.2 เท่านั้น  
ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้โค้ดหรือ `STATUS.md`, ไม่เขียน Qdrant/Docker adapters และไม่รัน M4a จริง

## Intent

ทำให้ ledger เป็น fail-closed audit authority ที่ผลจาก append, state บนดิสก์, trusted time และ content binding ให้คำตอบเดียวกัน แม้ write/fsync/rollback/cleanup บางขั้นล้ม

## Verdict

**FIX-THEN-GO — Qdrant/Docker adapter wiring ยัง NO-GO**

B3.1-R ปิดจริง และ success-path disk re-verification ถูกทาง แต่ B3.2-P ยังมี blocker เดิมใน failure-of-recovery กับ post-commit exception; M3.1/M3.2 ยังเป็น convention ของ wrapper บางส่วน ไม่ใช่ ledger contract ที่ fail-closed ครบ

## Simpler-alternative check

อย่าเพิ่ม exception mapping แยกกระจัดกระจายอีก ให้ `_locked_append()` คืน/โยน **outcome ที่แบ่งชัดสามสถานะ**:

1. `UNCOMMITTED` — rollback ยืนยันแล้ว; retry ได้
2. `COMMITTED` — record durable แล้ว แม้ unlock/close cleanup มี warning
3. `INDETERMINATE` — commit หรือ rollback ยืนยันไม่ได้; poison ledger และห้าม reconcile เป็น clean terminal

ถ้าพิสูจน์สามสถานะนี้บน shared JSONL ยากเกินไป per-attempt immutable event files ผ่าน atomic publisher จะเล็กและตรวจง่ายกว่า

## Findings

### B3.2-P.1 — rollback failure ถูกกลืน ทำให้ append รายงาน error แต่ ledger เป็น `PUBLISHED` (blocker)

ตำแหน่ง: `p2_provenance.py:175-194`

เมื่อ record fsync ล้ม code พยายาม `ftruncate(fd, cut)` และ fsync rollback แต่ exception ของทั้งสองขั้นถูก `pass` ทิ้ง จากนั้นโยนเฉพาะ original error กลับ caller

targeted probe บังคับให้ terminal fsync และ rollback truncate ล้มพร้อมกัน:

```text
append result -> OSError
disk events   -> [STARTED, PUBLISHED]
reconcile     -> PUBLISHED
```

test ใหม่ผ่านเพราะจำลองเพียง fsync ล้มแต่ `ftruncate` สำเร็จ; rollback fsync ยังล้มและถูกกลืน ทว่า in-process reader เห็นผล truncate จึงเขียวโดยไม่ได้พิสูจน์ durability หลัง crash

ผลกระทบ: ปัญหา result-vs-disk เดิมยังเกิดได้ และระบบอาจปล่อย adapter ต่อจาก terminal ที่ caller เชื่อว่าเขียนไม่สำเร็จ

แก้ขั้นต่ำ:

1. ห้ามกลืน rollback truncate/fsync error
2. หาก rollback + rollback fsync สำเร็จเท่านั้นจึงคืน `UNCOMMITTED` และอนุญาต retry
3. หากยืนยัน rollback ไม่ได้ ให้สร้าง `ProvenanceIndeterminate`/poison state ซึ่ง `read/reconcile/append` ต้อง fail closed จน operator repair; ห้ามสรุปจาก newline ว่า clean terminal
4. เพิ่ม test แยก: append fsync fail + truncate fail, append fsync fail + rollback fsync fail และ process crash หลังแต่ละ boundary

### B3.2-P.2 — exception หลัง durable commit ยังถูก report เป็น append failure (blocker)

ตำแหน่ง: `p2_provenance.py:195-205`, caller mapping `p2_m4_ops.py:120-124`

docstring บอกว่า close/release หลัง commit เป็น cleanup warning แต่ implementation ปล่อย exception จาก `os.close()`/`_release()` ออกไปตามปกติ Wrapper จึง map เป็น `PROVENANCE_UNCONFIRMED` ทั้งที่ terminal durable และ `reconcile()` เห็น clean terminal

targeted probe ให้ `_release()` ปลด lock สำเร็จแล้วโยน error:

```text
append result -> OSError
disk events   -> [STARTED, PUBLISHED]
reconcile     -> PUBLISHED
```

ผลกระทบ: caller อาจ retry terminal แล้วโดน duplicate หรือยืนยันกับผู้ใช้ว่า provenance ไม่สำเร็จ ทั้งที่ ledger บันทึก PUBLISHED แล้ว

แก้ขั้นต่ำ: หลัง `committed=True` ต้องรักษา committed outcome; cleanup failure ให้แนบ warning/health signal แยกโดยไม่เปลี่ยน append เป็น uncommitted exception หรือคืน structured `COMMITTED_WITH_WARNING`. เพิ่ม close/release-after-commit tests ที่ assert caller outcome ตรงกับ reconcile

### M3.1 — terminal clock failure และ clock regression ถูกซ่อน; wrapper/runner ใช้คนละ clock (major)

ตำแหน่ง: `p2_m4_ops.py:40-53,91-117`, `p2_m4_runner.py:125-127,211-215`

initial clock invalid ถูก reject ก่อน STARTED แล้ว แต่ `_terminal()` จับ clock error ทุกชนิดแล้วใช้ `finished_at=started_at` เงียบ ๆ; clock ถอยหลังก็ clamp โดยไม่บันทึก anomaly นอกจากนี้ wrapper รับ `clock` แยกจาก `ports.clock` ที่ runner ใช้สร้าง receipt จึงไม่มีหลักประกันว่า operational ledger กับ receipt มาจาก trusted source เดียวกัน

targeted probe (valid STARTED clock, invalid terminal clock):

```text
result       -> FAILED/plan_invalid
started_at   -> 2026-08-06T09:00:00+07:00
finished_at  -> 2026-08-06T09:00:00+07:00
clock error  -> ไม่มี field ใดบอกว่า terminal clock ล้ม
```

branch เดียวกันใช้กับ clean PUBLISHED จึงสามารถซ่อน terminal clock failure ได้เช่นกัน

แก้ขั้นต่ำ: ใช้ clock port ตัวเดียวกับ runner หรือ inject authority เดียวแล้วส่งต่อ; terminal clock invalid/regression ต้องบันทึก explicit clock anomaly และห้าม clean PUBLISHED โดยเงียบ สำหรับ PUBLISHED ให้ cross-check operational interval กับ validated receipt timestamps

### M3.2-A — ledger ยอม clean `PUBLISHED` ที่ไม่มี content binding (major)

ตำแหน่ง: `p2_provenance.py:114-149,213-231`

shared reducer ตรวจเพียง order/run/status แต่ไม่ enforce terminal schema ตาม event จึงยังสามารถเรียก `append_event()` โดยตรงด้วย terminal ขั้นต่ำและได้ clean result:

```text
terminal keys -> [attempt_id, event, run_id, status]
reconcile     -> PUBLISHED
```

ไม่มี `artifact_sha256`, `evidence_body_sha256`, `run_receipt_sha256`, capability, path หรือ finished timestamp แต่ audit authority ยอมรับ

นี่คือส่วนของ M3.2 จาก review ก่อนที่ยังไม่ได้ปิด: disk re-verify ใน wrapper ดีแล้ว แต่ binding ยังไม่ load-bearing ที่ ledger boundary

แก้ขั้นต่ำ: reducer/record validator ต้อง enforce event-specific schema โดย PUBLISHED ต้องมี valid 64-hex bindings และ trusted terminal fields; DEGRADED/FAILED ใช้ schema แยก หาก `append_event()` ตั้งใจเป็น raw API ให้เปลี่ยนเป็น private และห้าม raw append เข้าสู่ authority log

### M3.2-B — malformed runner result สามารถหลุดจาก verify-failure handler และทิ้ง STARTED ค้าง (major)

ตำแหน่ง: `p2_m4_ops.py:147-151`

หาก `result` ไม่มี `path`, expression ใน `try` โยน `KeyError` แต่ `except` กลับอ่าน `result["path"]` ซ้ำเพื่อสร้าง FAILED terminal จึงโยน `KeyError` ออกจาก wrapper ก่อน `_terminal()` ทำงาน

ผลกระทบ: contract regression/adapter bug ที่ควรถูก normalize เป็น FAILED/verify_publish กลับ crash และทิ้ง attempt เป็น INCOMPLETE

แก้ขั้นต่ำ: validate exact runner-result shape ก่อนใช้, เก็บ sanitized validated path ตัวเดียว และอย่า dereference untrusted result ซ้ำใน error handler; เพิ่ม missing-path/non-dict/bad-evidence result tests

## Closures ที่ยืนยันแล้ว

### B3.1-R — CLOSED

`_reduce()` ตัวเดียวถูก reuse ทั้ง append validation และ reconcile; terminal-before-STARTED, run mismatch, status/event mismatch และ event หลัง terminal ถูก reject ตามลำดับจริง

### B3.2 tail repair + normal rollback — CLOSED เฉพาะเมื่อ rollback ยืนยันได้

partial tail ถูก truncate ก่อน append และ terminal fsync failure ที่ rollback truncate สำเร็จทำให้ reader เห็นเพียง STARTED พร้อม retry terminal ได้จริง ข้อเปิดคือ rollback failure และ post-commit outcome ด้านบน

### M3.1 initial trusted-clock gate — CLOSED

clock ค่าแรกที่ malformed ถูก reject ก่อน STARTED/provision จริง ข้อเปิดคือ terminal clock และ clock authority สองตัว

### M3.2 PUBLISHED disk re-verification — CLOSED เฉพาะ wrapper success path

wrapper reload final bundle, recompute artifact hash และ re-run public bundle validator; tampered/missing bundle ไม่ได้ clean PUBLISHED จริง ข้อเปิดคือ ledger schema enforcement และ malformed-result error path

## Verification

targeted offline suites บนเครื่องนี้:

```text
test_p2_fs_probe.py    12/12 PASS
test_p2_provenance.py  28/28 PASS
test_p2_m4_ops.py      20/20 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
รวม                    129/129 PASS
```

ไม่ได้รัน Docker/Qdrant/model/M4a และไม่ได้อ้างว่า reproduce `783/783` ทั้งชุด การผ่าน 129 checks ไม่หักล้าง probes เพราะ tests ปัจจุบันไม่มี rollback-failure, post-commit cleanup, terminal-clock failure, minimal-PUBLISHED schema และ malformed-result cases

## Gate หลัง review

- safety pieces: **FIX-THEN-GO**
- Qdrant/Docker adapter wiring: **NO-GO** จนปิด B3.2-P.1, B3.2-P.2, M3.1, M3.2-A และ M3.2-B แล้ว targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** reducer และ disk verify ผ่านจริง แต่ commit/rollback outcome ยังขัดกับ disk state ได้ และ terminal authority ยังยอมเวลาหรือ binding ที่ไม่ครบ จึงยังไม่ควรเริ่ม adapter wiring
