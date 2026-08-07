# Codex targeted re-review — P2 M4 safety FIX10 (`74fff70`)

วันที่รีวิว: 2026-08-07

ขอบเขต: `KB_P2_M4_SAFETY_FIX10_HANDOFF.md` และ diff `039e5d0..74fff70` เฉพาะ verify-before-persistent-pragma, exact behavioral schema/application ID, post-COMMIT verification และ attempt-safe diagnostic filename

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้ source/tests/`STATUS.md`, ไม่แตะ Qdrant/Docker/model และไม่รัน M4a จริง

## Intent

ปิดการ mutate foreign SQLite file ก่อน verify, ป้องกัน schema object ที่เปลี่ยนพฤติกรรม INSERT และทำให้ diagnostic export ของแต่ละ attempt มีชื่อที่ไม่ชนกันในทางปฏิบัติ

## Verdict

**FIX-THEN-GO — ยังไม่อนุมัติ Qdrant/Docker adapter slice**

B1.1, schema allowlist/application ID และ M1 filename collision ปิดตาม claim แล้ว แต่ post-COMMIT defense ใหม่มี outcome branch ที่รายงาน ordinary failure ทั้งที่ row commit สำเร็จไปแล้ว จึงเปิด result-alignment blocker ใหม่หนึ่งจุด

M4a real run ยังคง **NO-GO** ตาม gate เดิม

## Simpler-alternative check

fresh-connection read หลัง COMMIT เพิ่ม failure/ambiguity surface โดยไม่จำเป็นต่อ normal SQLite commit contract ทางเล็กกว่าคือให้ INSERT พิสูจน์ผล **ก่อน COMMIT** ด้วย `INSERT ... RETURNING seq` หรือ exact SELECT/rowcount ใน transaction เดิม หลังจาก exact-schema/no-trigger verification ผ่าน แล้วใช้ COMMIT success เป็น durability boundary ตามเดิม

ถ้ายังคง fresh post-COMMIT verification ต้องถือ “verification อ่านไม่ได้” เป็น indeterminate และ map แบบ fail-closed ทั้ง STARTED/terminal ห้ามคืน ordinary retryable failure

## Finding

### B1.3 — post-COMMIT verification failure ถูกปล่อยเป็น ordinary error ทั้งที่ row committed แล้ว (blocker)

ตำแหน่ง: post-check `p2_provenance.py:437-439`; STARTED caller `p2_m4_ops.py:143-147`; terminal mapping `p2_m4_ops.py:159-164`

เส้นทางจริง:

```text
INSERT
→ COMMIT returns success
→ fresh _row_exists() raises sqlite3.OperationalError
→ _locked_write leaks OperationalError
→ STARTED path catches generic Exception and returns FAILED/provenance_started
```

fault probe monkeypatch `_row_exists()` ให้จำลอง fresh-read failure หลัง normal COMMIT:

```text
caller outcome   = OperationalError
committed events = [STARTED]
attempts         = [attempt-0001]
```

ผลกระทบ: caller เห็น ordinary failure และอาจ retry แต่ authority มี STARTED ถูก commit แล้ว การ retry attempt เดิมจะชน duplicate; การใช้ attempt ใหม่ทิ้ง attempt แรกเป็น INCOMPLETE ทั้งที่ run ยังไม่เคยเริ่ม เป็น result/provenance alignment แบบเดียวกับที่ safety layer นี้ตั้งใจปิด

test ปัจจุบันพิสูจน์เพียง “fresh verification คืน False หลัง trigger swallow → `ProvenanceError`” แต่ไม่ครอบ “fresh verification อ่านไม่ได้หลัง COMMIT”

แก้ขั้นต่ำ เลือกหนึ่งทาง:

1. แนะนำ: ตรวจ exact inserted row/`RETURNING` ภายใน transaction ก่อน COMMIT แล้วถอด fresh post-COMMIT read ใน normal-success branch; ack-loss resolver คง fresh verification เดิม
2. หากคง post-check: catch `sqlite3.Error` จาก `_row_exists()` แล้ว raise `ProvenanceIndeterminate` (หรือชนิด verification-indeterminate เฉพาะ) และเพิ่ม STARTED mapping ใน `run_m4a_operational()` ให้คืน `PROVENANCE_INDETERMINATE` ไม่ใช่ `FAILED/provenance_started`

เพิ่ม regression สองระดับ:

- low-level: COMMIT success + post-check read error → row มีจริงและ outcome เป็น indeterminate ไม่ใช่ OperationalError
- wrapper STARTED: outcome indeterminate → abort ก่อน provision/model และห้ามแสดง ordinary retryable FAILED

## Closures ที่ยืนยันแล้ว

- **B1.1 CLOSED:** existing path ตั้งเพียง `busy_timeout` ก่อน `_verify_open`; WAL foreign/tampered DB ถูก reject โดย journal mode ยังคง WAL ไม่ถูก convert เป็น DELETE
- **B1.2 schema boundary CLOSED:** `application_id`, exact table DDL/columns, exact index allowlist และ no-trigger rule ถูกตรวจจริง; trigger `RAISE(IGNORE)` ถูก reject ก่อน append
- **M1 CLOSED ในทางปฏิบัติ:** filename ผูก exact attempt bytes ด้วย SHA-256 prefix 160-bit ทำให้ colon/underscore/case mapping ไม่ชนใน threat model นี้ ควรเรียก “collision-resistant” มากกว่า “injective” เชิงคณิตศาสตร์ แต่ไม่เป็น gate
- B2 same-snapshot receipt และ B3 diagnostic-only decision contract ไม่พบ regression
- main COMMIT failure/ack-loss resolver, row decoder, O_EXCL concurrent init และ atomic publisher ไม่พบ regression ใน targeted suites

## Verification

targeted suites ที่รันจริงใน review นี้:

```text
test_p2_provenance.py  51/51 PASS
test_p2_m4_ops.py      31/31 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                     163/163 PASS
```

เพิ่ม Codex fault probe ชั่วคราวสำหรับ post-COMMIT fresh-read failure; ลบ probe file หลังรันแล้ว ไม่ได้แก้ production/test code

ไม่ได้รันซ้ำ full 19-suite `817/817`; ตัวเลขนั้นคงเป็นหลักฐานจาก FIX10 handoff ไม่ใช่ผลรันใหม่ของ review นี้

## Gate หลัง review

- verify-before-persistent-pragma / exact schema+application ID / attempt filename: **CLOSED ตามขอบเขตด้านบน**
- post-COMMIT verification outcome alignment: **OPEN — FIX-THEN-GO**
- Qdrant/Docker adapter coding: **NO-GO** จนปิด B1.3 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน sign-off + M4b + validated canary

รอบถัดไปตรวจเฉพาะ post-COMMIT verification error semantics + STARTED wrapper mapping ไม่ต้องทวน B1.1, schema allowlist/application ID, snapshot receipt หรือ diagnostic filename อีก
