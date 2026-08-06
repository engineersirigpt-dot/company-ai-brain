# Codex targeted re-review — P2 M4 safety FIX5 (`460fe6b`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: `KB_P2_M4_SAFETY_FIX5_HANDOFF.md` และ diff `f602329..460fe6b` เฉพาะ write-ahead intent, terminal anomaly, artifact path/payload binding และ producer surface  
ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้โค้ดหรือ `STATUS.md`, ไม่เขียน Qdrant/Docker adapters และไม่รัน M4a จริง

## Verdict

**FIX-THEN-GO — Qdrant/Docker adapter wiring ยัง NO-GO**

ของเดิมปิดได้จริงหลายส่วน: reader/writer ตรวจ intent ใต้ lock เดียวกัน, clock anomaly ไม่เหลือเป็น clean `PUBLISHED`, path นอก `out_dir` ถูก reject และค่าที่คืนมาหลัง publish มาจาก bundle บนดิสก์จริง แต่ write-ahead protocol ยังไม่ครบตรง recovery/directory durability และมีผลลัพธ์ขัดกันเมื่อ clear-intent fsync ล้ม จึงยังไม่ควรให้ adapter จริงพึ่ง ledger นี้เป็น authority

## Simpler-alternative check

โค้ดนี้กำลังสร้าง transaction log, lock, write-ahead marker, recovery protocol และ state machine เองบน JSONL ซึ่งเป็นจุดที่ทำให้ safety review วนหลายรอบ ทางที่เล็กและเสี่ยงน้อยกว่าสำหรับ local/single-writer PoC คือใช้ SQLite จาก Python stdlib (`synchronous=FULL`, transaction เดียวต่อ event, UNIQUE/state constraints) เป็น authoritative ledger แล้ว export JSONL เป็น evidence artifact ภายหลัง

ถ้าต้องคง JSONL ไว้ ให้ปิด findings ด้านล่างก่อน โดยเฉพาะ recovery ที่ต้องพิสูจน์ record จาก intent ได้ ไม่ใช่ลบ marker ด้วยมืออย่างเดียว

## Findings

### B1 — intent ไม่มีข้อมูลพอให้ repair outcome อย่าง deterministic (blocker)

ตำแหน่ง: `p2_provenance.py:61-77`, `p2_provenance.py:256-302`; test `test_p2_provenance.py:146-153`

intent เก็บเพียง:

```json
{"attempt_id":"...","event":"..."}
```

มันไม่ bind `cut` ก่อน append, serialized-record digest, `run_id`, log identity หรือ protocol version ดังนั้นหลัง process crash operator แยกสองกรณีนี้ไม่ได้อย่างพิสูจน์ซ้ำได้:

1. record ถูกเขียนครบพร้อม newline แต่ยังไม่ผ่าน record `fsync`
2. record ผ่าน `fsync` แล้ว แต่ process ตายก่อน clear intent

test ปัจจุบันจำลอง “repair” ด้วย `os.unlink(<log>.intent)` โดยตรง แล้วถือ ledger ที่เหลือเป็น authority หากกรณีแรกมี full line ที่ยังมองเห็นผ่าน page cache การลบ intent จะยกระดับ record ที่ outcome ยังไม่ยืนยันให้กลายเป็น clean terminal ได้

ผลกระทบ: write-ahead intent fail-closed ตอนตรวจพบจริง แต่ไม่มี safe recovery path; การ repair ตาม test/เอกสารเป็น blind acknowledgement ไม่ใช่ evidence-based resolution

แก้ขั้นต่ำ:

- intent ต้อง bind อย่างน้อย `protocol_version`, canonical log identity, pre-append `cut`, `record_sha256`, `attempt_id`, `run_id`, `event`
- เพิ่ม recovery function ที่ถือ lock เดียวกัน แล้วตรวจ bytes ณ `cut` เทียบ digest: exact committed record → fsync/confirm; missing/partial/mismatch → truncate กลับ `cut` + fsync; จากนั้นจึง clear intent
- ห้ามถือการลบ `.intent` ตรง ๆ เป็น operator repair และต้องมี negative test: full JSON+newline visible แต่ record-fsync ยังไม่ยืนยัน

### B2 — parent-directory durability ยังข้ามได้เมื่อ log path เป็น basename หรือสร้าง directory ใหม่ (blocker)

ตำแหน่ง: `p2_provenance.py:43-52`, `p2_provenance.py:61-87`, `p2_provenance.py:265-293`

`d = os.path.dirname(log_path)` เป็น `""` เมื่อ caller ส่ง `prov.jsonl`; `_fsync_parent("")` return ทันที แม้บน POSIX จึงไม่มี directory-entry durability ทั้ง intent และ ledger ใหม่

อีกกรณีคือ `_locked_append()` ทำ `os.makedirs(d, exist_ok=True)` หาก directory ยังไม่มี แล้ว fsync เฉพาะ directory `d` ภายหลัง การ fsync ข้างใน `d` ไม่ทำให้ directory entry ของ `d` ใน parent/grandparent durable หากเพิ่งสร้างขึ้น

ผลกระทบ: หลัง power loss อาจหายทั้ง intent และ ledger/STARTED แม้ append เคยคืน success ซึ่งขัดกับ durable provenance contract บน Linux/container target

แก้ขั้นต่ำที่ง่ายที่สุด: require canonical absolute `provenance_log` ภายใต้ directory ที่มีอยู่แล้วและผ่าน capability/preflight ก่อนเขียน `STARTED`; reject basename/parent ที่ต้องสร้างเอง หรือ implement durable directory-chain creation พร้อม fsync parent ทุกระดับที่สร้าง ใช้ absolute parent (`dirname(abspath(log_path))`) เสมอ

### M1 — clear-intent parent-fsync failure รายงาน `INDETERMINATE` แต่ marker หายและ reader ถัดไปยอมรับ terminal (major)

ตำแหน่ง: `p2_provenance.py:80-87`, `p2_provenance.py:300-302`, `p2_m4_ops.py:146-151`

`_clear_intent()` ทำ `unlink()` ก่อน แล้วค่อย fsync parent หาก fsync ล้ม code raise `ProvenanceIndeterminate` พร้อมข้อความ/เอกสารว่า intent ค้าง แต่ intent ถูกลบไปแล้วใน namespace ปัจจุบัน

targeted fault-injection หลัง record commit ให้ parent fsync ล้มเฉพาะ clear step ได้ผล:

```text
append_outcome = ProvenanceIndeterminate
intent_exists  = False
events         = [STARTED, FAILED]
reconcile      = FAILED
```

ดังนั้น wrapper ปัจจุบันอาจคืน `PROVENANCE_INDETERMINATE` แต่ process ถัดไปอ่าน terminal ปกติ ความหมายสองฝั่งไม่ตรงกัน และ test suite ไม่มีเคสนี้

แก้ขั้นต่ำ: แยก `unlink` failure ออกจาก fsync-after-unlink failure

- unlink ล้มและ marker ยังอยู่ → `ProvenanceIndeterminate`
- unlink สำเร็จแต่ parent fsync ล้ม → record/rollback outcome ถูกยืนยันไปแล้ว; map เป็น cleanup/durability warning ที่ observable และไม่อ้างว่า intent ยังอยู่ หรือใช้ two-marker protocol ที่ยังเหลือ durable recovery marker จริง
- เพิ่ม test ทั้ง committed และ rollback-confirmed branches พร้อม assert result ของ callerและ state ที่ reader ถัดไปเห็นต้องสอดคล้องกัน

### M2 — exact output path ใช้ `realpath(out_dir)` คนละเวลา จึงยังมี canonical-path TOCTOU (major)

ตำแหน่ง: `p2_m4_ops.py:123-129`, `p2_m4_ops.py:157-178`

STARTED bind `os.path.realpath(out_dir)` ครั้งหนึ่ง แต่หลัง runner จบ code คำนวณ `os.path.realpath(out_dir)` ใหม่เพื่อสร้าง `expected_path` หาก symlink/junction หรือ directory mapping ถูกสลับระหว่าง run terminal สามารถ bind artifact ใต้ target ใหม่ ซึ่งไม่ใช่ `out_dir_realpath` ที่บันทึกใน STARTED

แก้ขั้นต่ำ: canonicalize `out_dir` ครั้งเดียวก่อน STARTED, bind ค่านั้นใน ledger และส่ง canonical path เดียวกันเข้า FS probe, runner และ expected-path verification; ก่อน publish ยืนยัน identity/path ยังตรงค่า frozen เดิม เพิ่ม test retarget/swap ระหว่าง runner กับ verification

## Closures ที่ยืนยันแล้ว

- **B3.2-P.1b:** reader และ writer ตรวจ intent ภายใต้ lock เดียวกัน ปิด check/read race ของรอบก่อน
- **M3.1:** clock invalid/regression ทำให้ `PUBLISHED → DEGRADED/clock_anomaly`; ledger schema reject `PUBLISHED` ที่มี anomaly และ result ไม่แนบ evidence
- **M3.2-B (content):** wrapper ignore payload ใน memory แล้ว validate/คืน evidence+receipt ที่โหลดจาก final bundle บนดิสก์
- **M3.2-B (basic path):** missing/outside path ไม่ผ่าน runner-result guard
- **M3.2-A (call graph ปัจจุบัน):** production call site ที่พบมีเพียง `p2_m4_ops.py → append_event`; `_append_raw` ถูกใช้เฉพาะ tests อย่างไรก็ตาม underscore ไม่ใช่ security boundary จึงควรระบุ scope ว่า ledger กัน accidental misuse ใน codebase ไม่ได้กัน hostile Python caller หรือ disk tampering

## Verification

targeted offline suites ที่รันจริง:

```text
test_p2_provenance.py  33/33 PASS
test_p2_m4_ops.py      24/24 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                    138/138 PASS
```

full offline command ผ่านถึง 17 suites/748 checks ก่อนหยุดที่ `test_p2_provider.py` เพราะ Python interpreter ที่เข้าถึงได้ใน session นี้ไม่มี optional dependency `qdrant_client`; `test_p2_adapter.py` จึงรัน pure section เป็น `21/21` และ skip integration ไม่สามารถยืนยันข้อความ `792/792` ใน handoff บน environment นี้ได้ แต่ไม่มี failure ใน targeted safety suites

fault-injection เพิ่มเติมของ Codex พิสูจน์ M1 ตาม output ที่แสดงข้างต้น; probe เป็น temporary file และถูกลบแล้ว ไม่มีการแก้ source/test files

## Gate หลัง review

- safety pieces: **FIX-THEN-GO**
- Qdrant/Docker adapter coding: **NO-GO** จนปิด B1/B2/M1/M2 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

รอบถัดไปตรวจเฉพาะ: intent body/recovery function, canonical pre-existing provenance directory, clear-intent outcome alignment และ frozen canonical out-dir; ไม่ต้องทวน closures ด้าน lock, anomaly downgrade และ disk-loaded payload อีก
