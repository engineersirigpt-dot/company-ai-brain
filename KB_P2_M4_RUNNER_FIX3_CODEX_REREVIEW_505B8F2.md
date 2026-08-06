# Codex targeted re-review — P2 M4 runner FIX3 (`505b8f2`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: re-review เฉพาะ B2.1/M3.1/M3.2 จาก `KB_P2_M4_RUNNER_FIX3_HANDOFF.md`  
ข้อจำกัดที่รักษาไว้: pure/offline เท่านั้น — ไม่เขียน real adapters, ไม่แตะ Docker/Qdrant/model, ไม่แก้ `STATUS.md`

## Intent

ปิด frozen fail-before-mutate และทำให้ atomic publisher รายงาน durability/temp-cleanup ตามความจริง เพื่อเปิดงานเขียน real adapters โดยยังไม่อนุญาตให้รัน M4a จริง

## Verdict

**GO — เขียน real adapters ได้**

ไม่พบ blocker/major ใหม่ใน scope รอบนี้ ทั้งสาม findings จาก `83a4ec0` ปิดได้ตาม execution path จริง

## Simpler-alternative check

แนวทางปัจจุบันเล็กพอแล้ว: ใช้ frozen validator เดิมเป็น authority, คง hard-link no-clobber publisher และแยก operational exceptions ตามช่วงที่ final ปรากฏ ไม่จำเป็นต้องเพิ่ม transaction directory, lock file หรือเปลี่ยน artifact format ก่อนเขียน adapters

## Closure verification

### B2.1 — CLOSED: frozen inconsistency ถูกหยุดก่อน provision/seed/model

ตำแหน่ง: `p2_eval.py:86-88`, `p2_eval.py:117`, `p2_eval.py:169-173`, `p2_m4_runner.py:61-112`

- `role_identity_sha256` ไม่ได้ตรวจแค่รูปแบบแล้ว แต่ recompute จาก typed string identity (`sha256("s:" + effective_role)`) และเทียบ exact
- embedded `m4_case_manifest_sha256` ถูกตัดออกจาก frozen schema จึงกลายเป็น unknown field ตั้งแต่ validator แรก ไม่มี digest source ที่สอง
- runner เรียก frozen validator/preflight ก่อนอ่าน scorer metadata และก่อน `isolation.provision()`
- negative tests ยืนยันทั้ง forged role identity และ embedded digest ได้ `RunnerError` พร้อม `isolation.calls == []`; forged case ยังตรวจว่า scorer ไม่ถูกเรียก

ผล: field จาก frozen ที่ public gate ใช้เปรียบกับ case evidence ถูก bind ก่อน mutation แล้ว ส่วน oracle/model outputs เป็น runtime observations โดยธรรมชาติและต้องตรวจหลังรัน จึงไม่ใช่ preflight gap

### M3.1 — CLOSED: platform durability ไม่กลืน POSIX failure

ตำแหน่ง: `p2_atomic.py:45-64`, `p2_atomic.py:126-130`, `p2_m4_runner.py:216-220`

- POSIX `os.open()` และ `os.fsync()` error propagate ออกจาก `_fsync_dir()` แล้วถูกแปลงเป็น `DurabilityUnconfirmed` หลัง final ปรากฏ
- non-POSIX เลือก `unsupported` จาก platform branch โดยตรง ไม่ได้ infer จาก catch-all exception
- runner คืน `durability=durable|atomic-visibility-only` เฉพาะหลัง publisher สำเร็จ
- fault-injection ครอบทั้ง directory-open และ fsync failure

ผล: ไม่มี path ที่ POSIX directory error ถูกคืนเป็น clean `PUBLISHED` แล้ว

### M3.2 — CLOSED: temp cleanup failure ไม่ถูกซ่อนเป็น clean success

ตำแหน่ง: `p2_atomic.py:67-78`, `p2_atomic.py:114-130`

- success path ที่ link final แล้วแต่ unlink temp ไม่ได้ ยกระดับเป็น `CleanupUnconfirmed`; ไม่คืน path แบบ clean success
- collision/error path คง primary exception เดิมและแนบ cleanup note ซึ่งระบุ temp path สำหรับตรวจ manual
- concurrent no-clobber authority ยังอยู่ที่ `os.link()` จุดเดียว จึงไม่ย้อนกลับไปมี check-then-write race
- tests ครอบทั้ง winner cleanup failure และ collision cleanup failure

ผล: final ที่สมบูรณ์อาจปรากฏก่อน exception ได้ตาม contract แต่ caller ไม่สามารถเข้าใจผิดจาก return status ว่าเป็น clean `PUBLISHED`

## Verification

รัน targeted offline suites บนเครื่องนี้:

```text
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_m4.py          59/59 PASS
test_p2_m4_harness.py  46/46 PASS
test_p2_runplan.py     95/95 PASS
รวม                     269/269 PASS
```

ไม่ได้รัน Docker/Qdrant/model และไม่ได้อ้าง reproduce `722/722` ทั้งชุด เพราะ environment ของ Codex ไม่มี optional `qdrant_client`; targeted surface ของ commit นี้ผ่านครบ

## Non-blocking constraints สำหรับ real-adapter slice

1. ทำ startup capability probe บน **output filesystem จริง** ก่อน provision/model: hard-link create-if-absent, temp cleanup และ durability mode ต้องถูกบันทึก
2. adapter/wrapper ต้องถือ exception เป็น authority: หากได้ `CleanupUnconfirmed` หรือ `DurabilityUnconfirmed` ห้ามตีความเพียงเพราะพบ `<run_id>.bundle.json` ว่า run สำเร็จ cleanly
3. บันทึก durability mode และ cleanup/durability exception ลง operational provenance ที่อยู่รอดข้าม process; return dict อย่างเดียวไม่พอสำหรับ run จริง
4. typed-id hash ตอนนี้ตรงกันและถูกทดสอบ แต่ในอนาคตควรย้ายเป็น leaf helper เดียวให้ evaluator กับ harness เรียกร่วมกัน เพื่อลด drift (ไม่ block adapter slice)

## Gate หลัง review

- runner/atomic: **GO / hardened สำหรับ pure-injectable scope ที่ `505b8f2`**
- real adapters: **GO เขียนได้** โดยรวม filesystem capability probe + operational provenance ตาม constraints ด้านบน
- adapter provenance review: **ยังต้องทำก่อน run**
- M4a real run: **NO-GO** จน adapter provenance review ผ่าน + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** ปิด B2.1/M3.1/M3.2 ครบและเปิด gate ให้เขียน real adapters ได้ แต่ยังไม่อนุญาตให้แตะ Qdrant/model หรือรัน M4a จริง
