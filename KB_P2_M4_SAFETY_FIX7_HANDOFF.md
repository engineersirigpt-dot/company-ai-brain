# P2 — REWORK รอบ 7 : ย้าย provenance ledger ไป **SQLite authority** (ปิด B1/B2/M1 เชิงโครงสร้าง)

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX6_CODEX_REREVIEW_6721721.md` (REWORK — แนะนำ SQLite ; 2 blocker + 1 major)
> **เจ้าของงานเลือก:** ย้ายไป SQLite (ตาม fallback ที่ handoff รอบ 6 กำหนดไว้)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py` (rewrite), `test_p2_provenance.py` (rewrite) · `test_p2_m4_ops.py` (M2 secret test → binary-safe)
> **ไม่แตะ** `p2_m4_ops.py` — public surface เดิม (`append_event`/`read_provenance`/`reconcile`/`ProvenanceIndeterminate`) คงครบ

## ทำไม SQLite ปิด findings รอบ 6 เชิงโครงสร้าง (ไม่ใช่ patch)

| Finding รอบ 6 | เดิม (JSONL WAL) | หายไปเพราะ SQLite |
|---|---|---|
| **B1** short-ledger `cut` clamp → committed prefix หายเงียบ | recovery hand-roll เทียบ `cut`/digest แล้ว truncate/clear เอง | **ไม่มี `cut`/manual recovery แล้ว** — SQLite rollback journal ทำ crash recovery แบบ atomic ต่อ transaction ; committed row อยู่, in-flight rollback อัตโนมัติ |
| **B2** `log_id=abspath` ไม่ใช่ file identity → alias ล็อกคนละ sidecar | `.lock`/`.intent` keyed on path → hard-link/symlink alias bypass | **ไม่มี sidecar แล้ว** — SQLite ล็อกที่ **inode ของ db** ; hard-link/symlink alias → ล็อกตัวเดียวกัน (พิสูจน์ด้วย hard-link concurrency test) |
| **M1** `warnings.warn` raise ได้ใต้ `-W error` หลัง commit | post-commit cleanup ใช้ warning ใน correctness path | **ไม่มี intent lifecycle / post-commit warning แล้ว** — commit คือ `COMMIT` ของ SQLite (durable, ไม่มี cleanup step ที่ raise) |

## Design ใหม่ (`p2_provenance.py`)

- `sqlite3` stdlib เป็น authority ; `PRAGMA synchronous=FULL` + rollback journal (`journal_mode=DELETE`) → **durable ต่อ event**
- schema: `events(seq PK AUTOINC, attempt_id, run_id, event, body, body_sha256)` + **UNIQUE partial index** `ux_started`/`ux_terminal` (defense-in-depth เหนือ state machine)
- `append_event` / `_append_raw` → `_locked_write`: `BEGIN IMMEDIATE` (write lock ทันที) → **read attempt state + validate transition (reducer เดียวกับ `reconcile`) + INSERT** ใน transaction เดียว → `COMMIT` ; bad transition → ProvenanceError + `ROLLBACK` (ไม่มี row ตกค้าง)
- SQLITE_BUSY (writer อื่นถือ lock) → **`ProvenanceLocked`** ; busy_timeout ทำให้ writers serialize
- **state machine + terminal schema (`_reduce`/`_validate_terminal_schema`) คงเดิมทุกบรรทัด** — B3.1/B3.1-R/M3.2-A/M3.1 (clock_anomaly reject บน PUBLISHED) ไม่เปลี่ยน
- `read_provenance` → `SELECT body ORDER BY seq` ; db ไม่มี = `[]` ; db/body corrupt = ProvenanceError
- `export_jsonl(log, out)` → dump committed events เป็น **JSONL bundle (evidence artifact)** + fsync file/parent → external M4 evidence contract ไม่เปลี่ยน
- `ProvenanceIndeterminate` คงไว้เพื่อ API compat (SQLite transaction atomic → ไม่มี indeterminate window ในทางปฏิบัติ ; wrapper ยัง handle ได้)

## behavior tests (offline) — `test_p2_provenance.py` (rewrite, 32 checks)

- **state machine**: dup STARTED / terminal-ก่อน-STARTED / dup terminal / run_id mismatch / reuse ข้าม run → ProvenanceError + bad-transition rollback ไม่ทิ้ง row
- **UNIQUE index**: raw bypass reducer → duplicate STARTED/terminal โดน integrity error
- **reconcile** order-sensitive (INCOMPLETE / terminal-only / dup / ก่อน-STARTED / run mismatch / status!=event) + **M3.2-A** schema binding
- **serialize guard**: not-dict/NaN/oversize
- **durability**: `_connect` → `synchronous=FULL(2)` + `journal_mode=delete`
- **corrupt db** → ProvenanceError (ไม่ crash)
- **B2 file identity**: hard-link alias เขียนพร้อมกัน 2 thread → serialize ที่ inode เดียว, records ครบ 11 ไม่ corrupt (skip graceful ถ้า fs ไม่รองรับ hard link)
- **concurrency**: 4×5 writers → 20 records ครบ ไม่ corrupt
- **lock contention**: อีก connection ถือ `BEGIN IMMEDIATE` → `ProvenanceLocked` ; ปล่อยแล้ว append ได้
- **crash-safe**: subprocess ถือ write tx (uncommitted) → parent `ProvenanceLocked` → kill → OS ปล่อย lock + SQLite rollback tx ที่ยังไม่ commit → parent เขียนได้ (ไม่มี row 'h')
- **evidence**: `export_jsonl` → JSONL ตามลำดับ + reconcile ตรงกับ db

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 32   test_p2_m4_ops 26   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 793/793** (รอบ 6 = 799 ; provenance 38→32 เพราะตัด WAI-mechanic tests ที่ไม่มีแล้ว, m4_ops คงที่ 26)
- provenance suite **เสถียร 5/5 รันซ้ำ** (concurrency/alias/crash-safe timing-sensitive แต่ผ่านทุกครั้ง)
- targeted safety suites (provenance/m4_ops/runner/atomic/fs_probe) ไม่พึ่ง qdrant_client
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## หมายเหตุ threat model (ยกให้ adapter review)

- SQLite ล็อก inode → alias-bypass ปิด ; แต่ **directory rename→replace ด้วย inode ใหม่ที่ path เดิม** ยังเป็น local-fs-tampering ที่ string path ไม่กัน (เหมือน `out_dir` canonical รอบ 6) — ถ้า threat model รวม local fs tampering ต้อง bind directory file ID เพิ่มในชั้น adapter
- durability เป็น behavioral guarantee (synchronous=FULL) — test ยืนยันได้แค่ pragma + rollback recovery ; power-loss durability พิสูจน์เต็มต้องมี fault-injection ระดับ fs (นอก scope offline)

## ขอ Codex review (safety-pieces slice รอบ 8)

1. SQLite ledger — state machine/schema/reconcile ที่ ledger boundary ครบเท่าเดิมไหม ; `BEGIN IMMEDIATE` + UNIQUE index ปิด concurrent double-STARTED/terminal จริงไหม ; alias/lock/crash-recovery ปิด B1/B2/M1 เชิงโครงสร้างจริงไหม
2. `export_jsonl` เป็น evidence artifact ที่ผูกกับ db ได้ถูกต้องไหม ; external M4 evidence contract ไม่เปลี่ยน
3. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 8 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
