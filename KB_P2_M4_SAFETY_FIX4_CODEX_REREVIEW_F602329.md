# Codex targeted re-review — P2 M4 safety FIX4 (`f602329`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: `KB_P2_M4_SAFETY_FIX4_HANDOFF.md` — B3.2-P.1/P.2 และ M3.1/M3.2-A/B เท่านั้น  
ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้โค้ดหรือ `STATUS.md`, ไม่เขียน Qdrant/Docker adapters และไม่รัน M4a จริง

## Intent

ทำให้ commit outcome, poison/health state, terminal schema, clock และ final artifact binding เป็น authority ที่ process ถัดไปตรวจซ้ำได้แบบ fail-closed ก่อนเริ่ม adapter wiring

## Verdict

**FIX-THEN-GO — Qdrant/Docker adapter wiring ยัง NO-GO**

confirmed rollback, reducer schema, unified clock source และ missing-path normalization ปิดจริง แต่ poison marker ยังไม่ durable/atomic และมี TOCTOU race; POSIX parent durability ถูกลดเป็น warning; clock anomaly ยังคืน clean `PUBLISHED`; runner-result shape ยังยอมใช้ bundle นอก `out_dir` พร้อมคืน evidence/receipt เป็น `None`

## Simpler-alternative check

การพยายามสร้าง poison **หลัง** storage ล้มไม่สามารถรับประกัน fail-closed ได้ เพราะ failure เดิมอาจทำให้ poison เขียนไม่ได้ ทางเล็กกว่าคือ write-ahead intent:

1. สร้าง durable `IN_PROGRESS`/intent marker แบบ no-clobber และ fsync directory **ก่อนแตะ ledger**
2. append + fsync หรือ rollback + fsync
3. ลบ intent และ fsync directory เมื่อ outcome ยืนยันแล้วเท่านั้น
4. intent ค้าง/อ่านไม่ได้ = indeterminate โดย default; operator repair

ทุก poison/intent check ต้องเกิดใต้ lock และ reader ต้องตรวจ health หลัง snapshot อีกครั้ง หรืออ่านภายใต้ lock เดียวกัน

## Findings

### B3.2-P.1a — poison เป็น best-effort; ถ้า marker เขียนไม่ได้ future process ยอม clean terminal (blocker)

ตำแหน่ง: `p2_provenance.py:39-53,253-260,295-304`

`_poison()` กลืน `OSError`, ไม่เขียน body, ไม่ fsync marker และไม่ fsync parent directory จากนั้น `_locked_append()` โยน `ProvenanceIndeterminate` โดยสมมติว่า process ถัดไปจะเห็น marker

targeted probe บังคับ commit fsync + rollback truncate ล้ม และจำลอง poison creation ไม่สำเร็จ:

```text
append result        -> ProvenanceIndeterminate
poison marker        -> ไม่มี
future read events   -> [STARTED, PUBLISHED]
future reconcile     -> PUBLISHED
```

ผลกระทบ: failure mode ที่ poison ถูกสร้างมาแก้ยังย้อนกลับไปเป็น clean success ได้หลัง restart

แก้ขั้นต่ำ: อย่าสร้าง health marker หลัง failure เป็นกลไกหลัก ใช้ durable write-ahead intent ก่อน append; หากยังใช้ poison ต้อง atomic-create + write/fsync + parent-fsync และ poison creation failure ต้องทำให้ระบบหยุดถาวรผ่าน external durable authority ไม่ใช่ปล่อย future process อ่าน ledger ตามปกติ

### B3.2-P.1b — poison check อยู่นอก lock ทำให้ writer ผ่าน check แล้วเขียนต่อหลัง ledger ถูก poison (blocker)

ตำแหน่ง: `p2_provenance.py:226-231`; reader `p2_provenance.py:295-304`

writer เรียก `_check_poison()` ก่อน acquire lock หาก writer B ผ่าน check แล้วรอ lock ขณะที่ writer A poison ledger, B จะ acquire lock ภายหลังและเขียนต่อโดยไม่ re-check

targeted race probe:

```text
writer B ผ่าน poison check แล้วถูกพักก่อน lock
สร้าง .poison
ปล่อย writer B
writer error                  -> ไม่มี
record ของ B ถูก append หลัง poison -> True
```

reader ก็ check marker ก่อนอ่านเพียงครั้งเดียว Poison สามารถเกิดหลัง check แต่ก่อน snapshot เสร็จได้

แก้ขั้นต่ำ: re-check poison/intent หลัง acquire writer lock; reader อ่านภายใต้ shared/exclusive lock เดียวกัน หรือ double-check durable marker หลังอ่านและทิ้ง snapshot หาก state เปลี่ยน

### B3.2-P.2 — POSIX parent-directory fsync failure ถูกกลืนทั้งที่เป็น durability boundary (blocker)

ตำแหน่ง: `p2_provenance.py:269-277`

หลังสร้าง log ใหม่ code fsync directory เพื่อให้ directory entry durable แต่ตอนนี้ `OSError` ถูก `pass` เป็น “cleanup warning” การ fsync file สำเร็จไม่ได้รับประกันว่าไฟล์ใหม่จะยังมีอยู่หลัง power loss หาก parent directory ยังไม่ durable

นี่สำคัญกับ adapter/container track เพราะ runtime เป้าหมายเป็น Linux แม้ targeted tests รอบนี้รันบน Windows

แก้ขั้นต่ำ: parent-directory fsync ต้องอยู่ใน COMMITTED boundary เช่นเดียวกับ `p2_atomic`; failure → INDETERMINATE และ write-ahead intent ต้องค้าง ห้าม report STARTED/PUBLISHED durable การกลืน post-commit close/unlock error ทำได้เฉพาะเมื่อเป็น cleanup จริงและควรมี observable warning ไม่ใช่ `pass` เงียบ

### M3.1 — `clock_anomaly=True` ไม่ load-bearing; status และ reconcile ยังเป็น clean `PUBLISHED` (major)

ตำแหน่ง: `p2_m4_ops.py:134-148`, schema/reducer `p2_provenance.py:152-213`; test `test_p2_m4_ops.py:147-150`

wrapper ใช้ `ports.clock` ตัวเดียวกับ runner แล้ว แต่ terminal clock invalid/regression เพียงเพิ่ม field โดยไม่เปลี่ยน event/status Test ใหม่ยืนยันเองว่า:

```text
terminal clock invalid -> result.status = PUBLISHED
ledger event/status     -> PUBLISHED
clock_anomaly           -> True
reconcile               -> PUBLISHED
```

ผู้ใช้หรือ adapter ที่ gate ด้วย `status`/`reconcile()` จึงยังถือเป็น clean success โดยไม่จำเป็นต้องอ่าน flag

แก้ขั้นต่ำ: PUBLISHED ต้อง reject `clock_anomaly=True` ที่ ledger schema หรือ map outcome เป็น DEGRADED/PROVENANCE_UNCONFIRMED; decision gate ต้อง consume anomaly แบบบังคับ ไม่ใช่ optional metadata สำหรับ receipt interval ต้องใช้ receipt ที่โหลดและ validate จาก disk

### M3.2-B — result-shape guard ตรวจเฉพาะ path; bundle นอก `out_dir` และ evidence/receipt หายยัง PUBLISHED (major)

ตำแหน่ง: `p2_m4_ops.py:170-186`

guard ตรวจเพียง `result["path"]` เป็น non-empty string `_verify_published()` โหลด valid bundle ตาม path นั้น แต่ไม่ยืนยันว่า path เท่ากับ `<real out_dir>/<run_id>.bundle.json`; จากนั้น wrapper คืน `result.get("evidence")`/`result.get("receipt")` จาก memory แทน content ที่เพิ่ง validate จาก disk

targeted probe ให้ runner result ชี้ valid bundle ของ run เดียวกันซึ่งอยู่นอก requested `out_dir` และตัด evidence/receipt ออก:

```text
status                    -> PUBLISHED
path outside out_dir      -> True
returned evidence/receipt -> None, None
```

ผลกระทบ: operational ledger bind artifact หนึ่ง แต่ caller ได้ payload คนละชุดหรือไม่มี payload และ isolation output directory contract ถูกข้าม

แก้ขั้นต่ำ:

1. canonicalize และ require exact expected final pathใต้ `out_dir`
2. ให้ `_verify_published()` คืน disk-loaded evidence+receipt พร้อม bindings
3. ใช้ disk receipt สำหรับ interval cross-check และคืน disk-loaded values เท่านั้น
4. validate exact runner-result schema/status/durability; เพิ่ม missing evidence, missing receipt, mismatched memory-vs-disk และ outside-path tests

### M3.2-A follow-up — terminal schema ตรวจรูปแบบ แต่ raw writer ยังปลอม binding ได้ (major)

ตำแหน่ง: `p2_provenance.py:152-177,285-292`

การ reject missing binding ปิด finding เดิมได้ระดับ shape แต่ `append_event()` ยังเป็น public surface ที่รับ hash 64 ตัว/path/capability ปลอมโดยไม่ recompute และ `append_provenance()` สามารถเขียน raw record เข้า log เดียวกันได้

ใน repo ปัจจุบัน producer จริงมีเพียง `p2_m4_ops.py` จึงแก้เล็กได้: ทำ raw append/event functions เป็น private หรือแยก raw test log; expose typed API ที่รับ verified terminal จาก `_verify_published()` เท่านั้น เพื่อให้คำว่า “ledger boundary” เป็น authority จริง

## Closures ที่ยืนยันแล้ว

### B3.2 confirmed rollback — CLOSED

commit fsync ล้มแต่ truncate+rollback fsync สำเร็จ → reader เห็นเพียง STARTED และ retry terminal ปิด attempt ได้จริง

### B3.2 post-commit result alignment — CLOSED เฉพาะ file-fsync แล้ว

release exception หลัง durable file commit ไม่ทำให้ append report uncommitted อีก ข้อเปิดคือ warning ไม่ observable และ parent-directory durability

### M3.2-A missing-field schema — CLOSED

minimal PUBLISHED ที่ขาด artifact/evidence/receipt hashes ถูก append และ reconcile ปฏิเสธจริง ข้อเปิดคือ authenticity/private producer surface

### M3.1 unified clock source — CLOSED

wrapper กับ runner ใช้ `ports.clock` authority เดียวกัน และ initial invalid clock fail ก่อน STARTED/provision จริง ข้อเปิดคือ anomaly outcome

### M3.2-B missing-path normalization — CLOSED

runner result ที่ไม่มี path ถูก normalize เป็น FAILED/run_result_malformed และปิด attempt ได้ ข้อเปิดคือ shape/path/disk-return ที่ไม่ครบ

## Verification

targeted offline suites บนเครื่องนี้:

```text
test_p2_fs_probe.py    12/12 PASS
test_p2_provenance.py  32/32 PASS
test_p2_m4_ops.py      22/22 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
รวม                    135/135 PASS
```

ไม่ได้รัน Docker/Qdrant/model/M4a และไม่ได้อ้างว่า reproduce `789/789` ทั้งชุด Tests ปัจจุบันยังไม่มี poison-creation failure, poison-check race, POSIX parent-fsync failure, anomaly gate และ outside-path/missing-payload cases

## Gate หลัง review

- safety pieces: **FIX-THEN-GO**
- Qdrant/Docker adapter wiring: **NO-GO** จนปิด B3.2-P.1a/P.1b/P.2, M3.1, M3.2-B และจำกัด M3.2-A producer surface แล้ว targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** normal recovery และ schema shape แข็งขึ้นจริง แต่ poison/parent durability ยังไม่ survive process crash อย่าง fail-closed และ PUBLISHED ยังไม่ bind anomaly/path/payload แบบ load-bearing จึงยังไม่ควรเริ่ม adapter wiring
