# P2 — ปิด safety รอบ 6 : intent v2 (evidence-based recovery) + parent durability + clear-intent alignment + canonical out_dir

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX5_CODEX_REREVIEW_460FE6B.md` (FIX-THEN-GO — 2 blocker + 2 major)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py`, `p2_m4_ops.py` (code) · `test_p2_provenance.py`, `test_p2_m4_ops.py` (tests)

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B1** | blocker | intent เก็บแค่ `{attempt_id, event}` — ไม่ bind `cut`/digest/identity → หลัง crash แยก "record durable แต่ยังไม่ clear" กับ "record ยังไม่ยืนยัน" ไม่ได้ ; test เดิม repair ด้วย `os.unlink` ตรงๆ (blind) | **write-ahead intent v2**: `_write_intent` bind `protocol_version`/`log_id`/`cut`/`record_sha256`/`attempt_id`/`run_id`/`event` ; เพิ่ม `_recover_locked` (+ public `recover()`) ทำ **evidence-based recovery ใต้ lock** — เทียบ bytes ณ `cut` กับ digest: ตรง+ปิด `\n` → **COMMITTED (re-fsync ยืนยัน)** ; partial/absent/mismatch → **UNCOMMITTED (truncate กลับ `cut`)** ; corrupt intent → `ProvenanceIndeterminate` (ต้อง operator, ไม่ auto-heal, **ไม่มี blind unlink**) ; `append_event`/`read_provenance` เรียก recover เองใต้ lock |
| **B2** | blocker | `os.path.dirname(log_path)` เป็น `""` เมื่อ path เป็น basename → `_fsync_parent("")` return เงียบ (ไม่มี directory durability) ; `makedirs` แล้ว fsync ลูกไม่ทำให้ dir entry ที่เพิ่งสร้าง durable | `_parent_dir(log_path) = dirname(abspath(...))` (absolute เสมอ, ไม่มีวันเป็น `""`) ; **parent ต้อง pre-exist** (ผ่าน capability/preflight) มิฉะนั้น `ProvenanceError` — **ยกเลิก `makedirs` auto-create** ; ทุก `_fsync_parent` ใช้ absolute parent ใน commit boundary |
| **M1** | major | `_clear_intent` `unlink` ก่อนแล้ว fsync ; fsync ล้ม → raise `ProvenanceIndeterminate` อ้างว่า intent ค้าง ทั้งที่ marker ถูกลบไปแล้ว → wrapper บอก INDETERMINATE แต่ reader ถัดไปเห็น terminal ปกติ (ขัดกัน) | แยกสองความล้ม: **unlink ล้ม + marker ยังอยู่ → `ProvenanceIndeterminate`** (จริง) ; **unlink สำเร็จแต่ parent fsync ล้ม → `RuntimeWarning`** (outcome ยืนยัน durable แล้ว, การลบ intent ยังไม่ durable — ถ้า marker กลับมา `_recover_locked` re-confirm จาก digest ได้) ไม่ใช่ INDETERMINATE |
| **M2** | major | STARTED bind `realpath(out_dir)` ครั้งหนึ่ง แต่ terminal คำนวณ `realpath(out_dir)` **ใหม่** → symlink/junction swap ระหว่าง run ทำให้ bind artifact ใต้ target ใหม่ (TOCTOU) | `_canon(out_dir)` **ครั้งเดียวก่อน STARTED** → ใช้ค่าเดียวกันทั้ง STARTED bind / FS probe / runner / expected-path ; ก่อน publish **re-verify `_canon(out_dir) == out_dir_real`** มิฉะนั้น `FAILED/out_dir_retargeted` |

## behavior tests (offline) ที่เพิ่ม/แก้

- **B1** (4 เคส): committed replay (full line+digest ตรง → COMMITTED + **re-fsync spy ≥1** พิสูจน์ยืนยัน durable ไม่ใช่ blind unlink) ; partial (digest ไม่ตรง → UNCOMMITTED + truncate กลับ `cut` → reader เห็นแค่ STARTED) ; corrupt intent → `ProvenanceIndeterminate` ที่ recover/read/append ; `read_provenance` **auto-recover** committed ใต้ lock
- **B2**: append เข้าไปใน parent ที่ยังไม่มี → `ProvenanceError` + ledger ไม่ถูกสร้าง
- **M1**: clear-intent parent-fsync ล้ม (call 2) → append **committed** (record durable) + `RuntimeWarning` + marker หายจริง (ไม่ indeterminate)
- **M2**: `_canon` คืนค่าต่างกันตอน re-verify (จำลอง swap) → `FAILED/out_dir_retargeted` + ไม่แนบ evidence ; STARTED bind `out_dir_realpath` = canonical ตอนเริ่ม
- **B3.2-P** (ปรับ): record-commit fsync ล้ม (intent fsync = call 1 ผ่าน, record fsync = call 2 ล้ม) → UNCOMMITTED + intent เคลียร์

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 38   test_p2_m4_ops 26   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 799/799** (เดิม 792 → provenance +5, m4_ops +2)
- clean env (ไม่มี qdrant_client): test_p2_adapter รัน pure section + skip integration ; targeted safety suites (provenance/m4_ops/runner/atomic/fs_probe) ไม่พึ่ง qdrant_client
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## recovery invariant สรุป (ให้ Codex ตรวจ)

1. ทุก mutation ของ ledger นำหน้าด้วย `_write_intent` (bind `cut`+digest) durable ก่อนเสมอ → ไม่มี mutation ที่ไม่มี intent คุ้ม
2. `recover`/`read`/`append` resolve intent **ใต้ lock เดียวกัน** — เทียบ digest ณ `cut`: COMMITTED (re-fsync) หรือ UNCOMMITTED (truncate) ; ไม่ตัดสินจากการมี/ไม่มี marker เฉยๆ
3. corrupt/identity-mismatch intent = `ProvenanceIndeterminate` (operator เท่านั้น) — ไม่ auto-heal, ไม่ blind-accept
4. parent directory ต้อง pre-exist + fsync ใน commit boundary (POSIX) → dir entry durable ; Windows = atomic-visibility only (no-op)
5. clear-intent: unlink-fail = INDETERMINATE ; unlink-ok + fsync-fail = warning (recovery re-confirm ได้)
6. `out_dir` canonicalize ครั้งเดียว bind ทุก consumer + re-verify ก่อน publish

## หมายเหตุ: ทางเลือก SQLite (Codex simpler-alternative)

Codex เสนอย้าย authoritative ledger ไป **SQLite stdlib** (`synchronous=FULL`, 1 transaction/event, UNIQUE/state constraints) แล้ว export JSONL เป็น evidence ทีหลัง — เพื่อเลิก hand-roll WAL/lock/recovery
- **ตัดสินใจรอบนี้:** คง JSONL ต่อ เพราะ (1) recovery เป็น deterministic/evidence-based แล้ว (2) evidence artifact ที่ผูกไว้ทั้ง contract เป็น JSONL bundle (3) การย้าย engine = รื้อ + reset review clock
- **fallback:** ถ้ารอบ 7 ยังเจอ crash-safety hole ใน JSONL อีก → ค่อยพิจารณา SQLite (ยกเป็น decision ให้เจ้าของงาน)

## ขอ Codex review (safety-pieces slice รอบ 7)

1. intent v2 body/recovery — survive crash ทุกจุด (ก่อน/หลัง intent, ก่อน/ระหว่าง/หลัง record fsync, ก่อน/หลัง clear) แบบ evidence-based จริงไหม ; มี window ไหนที่ recover ตัดสินผิดจาก digest ได้
2. canonical pre-existing provenance directory (B2) + clear-intent alignment (M1) + frozen canonical out_dir (M2) ปิดครบไหม
3. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 7 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
