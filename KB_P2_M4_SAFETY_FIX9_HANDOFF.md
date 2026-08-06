# P2 — SQLite hardening รอบ 9 : exact-schema init/open + same-snapshot receipt + attempt-safe export

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX8_CODEX_REREVIEW_ECB7F7C.md` (SQLite direction **ACCEPT** ; FIX-THEN-GO — 3 blocker)
> รอบ 8 ปิดแล้ว: alias rejection, main COMMIT branches, row decoder, atomic no-clobber primitive
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py`, `p2_m4_ops.py`, `test_p2_provenance.py`, `test_p2_m4_ops.py`

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B1** | blocker | `_connect` รัน `CREATE ... IF NOT EXISTS` + stamp `user_version` ก่อน verify → **adopt/mutate foreign SQLite db** ; `_schema_ok` เช็คแค่ชื่อ object (weak index ผ่าน) | **แยก init/open**: `_try_create` (O_EXCL) → ไฟล์ที่เราสร้างเองเท่านั้น → `_initialize` (create exact schema + stamp ใต้ write lock) ; ไฟล์ที่มีอยู่ → `_open_existing` **verify-only ไม่แก้ schema** ; `_verify_schema` ตรวจ **exact columns (name/type/notnull/pk) + index sql (unique/partial/predicate)** ; foreign/weak/truncated/version ผิด → fail-closed ; concurrent create แก้ด้วย O_EXCL creator + bounded open-wait |
| **B2** | blocker | `export_jsonl` อ่าน rows ผ่าน `read_provenance` (ปิด conn) แล้วเปิด `_db_stats` connection ใหม่ → JSONL body กับ `max_seq`/`row_count` คนละ snapshot | อ่านทุกอย่างจาก **snapshot เดียว** (`_read_snapshot`: rows + `user_version` ใน read transaction เดียว) ; derive `row_count`/`max_seq`/body จาก rows ชุดเดียว ; **ลบ `_db_stats`** ; test seam `_after_snapshot_hook` พิสูจน์ writer commit หลัง freeze ไม่กระทบ receipt/file |
| **B3** | blocker | wrapper export ทุก terminal ไป `<run_id>.provenance.jsonl` เดียว + publisher no-clobber → retry attempt ชน ; export fail เป็น in-process best-effort แต่ terminal ยัง clean | **contract ชัด (Option 2)**: JSONL = **diagnostic snapshot ของ operational ledger ไม่ใช่ clean-publish decision evidence** (decision gate = SQLite ledger authority + runner bundle เท่านั้น) ; export path ผูก **`<run_id>.<attempt_id>`** (retry-safe, ไม่ชน) ; export ล้ม/ชน → แนบ `provenance_export_error` เฉยๆ ไม่เปลี่ยน terminal (authority = db) |

## behavior tests (offline) ที่เพิ่ม/แก้

- **B1**: foreign SQLite db (มี table อื่น) → read/append fail-closed **+ ยืนยันว่าไม่ถูก adopt** (ไม่เพิ่ม events/index, `user_version` ไม่ถูก stamp) ; same-name events แต่ index อ่อน (non-unique/non-partial) → verify reject ; concurrent 4×5 writers **ไม่ pre-seed** (init race handled) → 20 records
- **B2**: `_after_snapshot_hook` commit เพิ่มหลัง freeze → receipt `row_count/max_seq=1` + JSONL 1 บรรทัด + digest ตรงไฟล์ แม้ db มี 2 rows (snapshot-bound)
- **B3**: happy-path export path = `run-1.<attempt_id>.provenance.jsonl` + receipt ผูก db ; **retry same run_id ต่าง attempt** (แรก FAILED, สอง PUBLISHED) → export คนละ path ไม่ชน + terminal ไม่ถูกกลบ
- คงเดิม: M1 tamper (column/body) → ProvenanceError ; B2 COMMIT outcome (retryable/ack-lost/indeterminate) ; M2 zero-length fail-closed ; atomic no-clobber + write-fail cleanup

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 46   test_p2_m4_ops 29   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 810/810** (รอบ 8 = 806 → provenance +3, m4_ops +1)
- provenance suite **เสถียร 6/6 รันซ้ำ** (รวม no-pre-seed concurrent init race + commit-fault-injection)
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## contract ที่ชัดขึ้น (B3 decision)

- **SQLite ledger = authority เดียวสำหรับ operational provenance** (durable, exact-schema, verify ทุก read)
- **M4 decision evidence = runner bundle `<run_id>.bundle.json`** (JSON, ไม่เปลี่ยน) + SQLite terminal
- **`<run_id>.<attempt_id>.provenance.jsonl` = diagnostic snapshot** — downstream **ห้าม** ใช้เป็นหลักฐานตัดสิน ; export ขาด/ล้ม ไม่ทำให้ terminal ไม่ valid

## หมายเหตุ threat model (ยกให้ adapter review)

- exact-schema + canonical path + reject alias ปิด foreign-adopt/alias-journal ; **directory rename→replace (inode ใหม่ที่ path เดิม)** ยังเป็น local-fs-tampering ที่ path string ไม่กัน — ถ้า threat model รวม local fs tampering ต้อง bind directory/file ID ในชั้น adapter
- concurrent **creation** ของ db เดียวกันไม่ใช่ operational scenario (1 run = 1 db) แต่ handle แล้วด้วย O_EXCL + bounded open-wait ; `_OPEN_RETRIES×_OPEN_DELAY` = 2s (fail-closed path)

## ขอ Codex review (safety-pieces slice รอบ 10)

1. exact init/open schema contract (B1) — foreign/weak/truncated/version ผิด fail-closed + ไม่ adopt/mutate ครบไหม ; concurrent create ผ่าน O_EXCL+bounded-wait ปลอดภัยไหม
2. same-snapshot receipt (B2) + attempt-safe diagnostic export contract (B3) ชัด/ปิดครบไหม
3. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 10 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
