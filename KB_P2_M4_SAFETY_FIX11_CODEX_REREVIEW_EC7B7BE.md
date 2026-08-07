# Codex targeted re-review — P2 M4 safety FIX11 (`ec7b7be`)

วันที่รีวิว: 2026-08-07

ขอบเขต: `KB_P2_M4_SAFETY_FIX11_HANDOFF.md` และ diff `74fff70..ec7b7be` เฉพาะ B1.3 — in-transaction inserted-row verification, normal COMMIT boundary และ STARTED `ProvenanceIndeterminate` mapping

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้ source/tests/`STATUS.md`, ไม่แตะ Qdrant/Docker/model และไม่รัน M4a จริง

## Verdict

**GO/SHIP — safety-pieces slice ผ่าน targeted review; อนุมัติให้เริ่มเขียน Qdrant/Docker adapter slice**

ไม่พบ blocker/major/minor ใหม่ในขอบเขต B1.3 และไม่พบ regression ใน targeted suites

นี่เป็นไฟเขียวให้ **เขียน adapter แบบยังไม่รันจริง** เท่านั้น การรัน M4a จริงยังคง **NO-GO** จน adapter provenance review ผ่านและมี Data Owner sign-off แบบ hash-bound

## Simpler-alternative check

การถอด fresh post-COMMIT read ออกจาก normal-success path คือทางเล็กสุดที่ปิดปัญหาเดิมตรงจุดอยู่แล้ว `INSERT ... RETURNING seq` จะรวม INSERT/lookup เป็น statement เดียวได้ แต่ไม่ได้เพิ่ม safety ที่จำเป็นภายใต้ exact-schema/no-trigger boundary และอาจเพิ่ม SQLite-version constraint; `last_insert_rowid()` + exact row lookup ใน connection/transaction เดียวกันจึงรับได้ใน slice นี้

## Trace ที่ยืนยัน

### 1. Normal append ไม่มี failure surface หลัง COMMIT แบบเดิมแล้ว

เส้นทางจริงใน `p2_provenance.py:403-444`:

```text
BEGIN IMMEDIATE
→ validate transition
→ INSERT
→ last_insert_rowid() + SELECT body_sha256 ใน transaction เดิม
→ verification fail: ROLLBACK + ProvenanceError
→ verification pass: COMMIT
→ return โดยไม่มี fresh _row_exists()
```

ดังนั้น trigger `RAISE(IGNORE)` ที่จำลองให้หลุด schema verification ถูกจับก่อน COMMIT และ rollback สะอาด ขณะที่ normal COMMIT success ไม่ขึ้นกับ availability ของ fresh reader อีกต่อไป

`last_insert_rowid()` ไม่ปะปนข้าม writer เพราะอ่านบน connection เดิมภายใต้ `BEGIN IMMEDIATE`; exact schema/no-trigger verification ที่ปิดใน B1.2 ยังเป็น boundary หลัก ส่วน lookup ก่อน COMMIT เป็น defense-in-depth สำหรับ silent insert loss

### 2. Ack-loss ยังคงแยก committed/uncommitted/indeterminate ถูกต้อง

เฉพาะกรณี `_do_commit()` โยน exception เท่านั้นที่ `p2_provenance.py:384-400` เปิด fresh `_row_exists()`:

- transaction ยัง active → rollback → `uncommitted`
- transaction ปิดและพบ row → `committed`
- transaction ปิดแต่ fresh verification อ่านไม่ได้ → `ProvenanceIndeterminate`

จึงไม่ทำให้ B2 outcome resolver ถอยหลังจาก contract เดิม

### 3. STARTED wrapper mapping ปิด result-alignment แล้ว

`p2_m4_ops.py:143-150` จับ `ProvenanceIndeterminate` ก่อน generic exception และคืน:

```text
status = PROVENANCE_INDETERMINATE
phase  = provenance_started
```

พร้อมหยุดก่อน filesystem probe/provision/provider/model ส่วน terminal path ที่ `p2_m4_ops.py:162-167` ใช้ semantics เดียวกันอยู่แล้ว

Codex fault probe แบบ cross-layer จำลอง `COMMIT` ลงจริงแล้ว ack หาย และบังคับให้ fresh verification โยน `sqlite3.OperationalError`; ผลจริง:

```text
wrapper_status = PROVENANCE_INDETERMINATE
wrapper_phase  = provenance_started
ledger_events  = [STARTED]
isolation_calls = []
```

จุดสำคัญคือ caller ไม่เห็น ordinary `FAILED` ทั้งที่ authority มี row commit แล้ว และไม่มีงาน downstream เริ่มทำ

## Closure

- **B1.3 CLOSED:** in-transaction verification จับ silent insert loss ก่อน COMMIT; normal-success path ไม่มี fresh post-COMMIT read
- **STARTED alignment CLOSED:** ambiguous commit ถูกเปิดเผยเป็น `PROVENANCE_INDETERMINATE` และไม่ provision
- **B1.1/B1.2/M1:** คง CLOSED ตามรอบก่อน ไม่ได้ทวนทั้งชุด และไม่พบ regression จาก diff นี้
- **B2 same-snapshot / B3 diagnostic contract:** ไม่พบ regression ใน targeted path

## Verification

targeted suites ที่ Codex รันจริง:

```text
test_p2_provenance.py  52/52 PASS
test_p2_m4_ops.py      32/32 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_atomic.py      25/25 PASS
test_p2_fs_probe.py    12/12 PASS
รวม                    165/165 PASS
```

เพิ่ม fault-injection probe ชั่วคราวสำหรับ actual low-level ack-loss → STARTED wrapper mapping; probe ผ่านและถูกลบแล้ว ไม่ได้แก้ production/test code

ไม่ได้รัน full 19-suite `819/819`; ตัวเลขนั้นคงเป็นหลักฐานจาก FIX11 handoff ไม่ใช่ผลรันใหม่ของ review นี้

## Gate หลัง review

- safety-pieces B1.3: **CLOSED / GO**
- Qdrant/Docker adapter coding: **GO** — เขียนแบบ injectable/offline และนำกลับมา review provenance/isolation ก่อนรัน
- M4a real run: **NO-GO** จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน sign-off + M4b + validated canary

