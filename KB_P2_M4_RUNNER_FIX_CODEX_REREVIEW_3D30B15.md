# Codex targeted re-review — P2 M4 runner FIX (`3d30b15`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: ปิด B1–B4/M1–M2 จาก `KB_P2_M4_RUNNER_CODEX_REVIEW_BFA69A0.md`  
ข้อจำกัดที่รักษาไว้: pure/offline probes เท่านั้น — ไม่เขียน real adapters, ไม่แตะ Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Verdict

**FIX-THEN-GO real adapters**

การแก้เดิมปิดได้จริงดังนี้:

- interlock count/ports/production และ marker proof ถูกบังคับก่อน seed/model แล้ว
- corpus ถูก validate + recompute digest เทียบ RunPlan ก่อน provision
- provider/oracle มี target binding เทียบ collection+endpoint ก่อน seed
- work สำเร็จต้อง teardown ก่อนสร้าง receipt/publish
- direct path traversal ถูกปิด และเปลี่ยนเป็น authoritative single-file bundle แล้ว

แต่ยังเหลือ **2 blocker ที่ tests ปัจจุบันไม่ครอบ**: publisher ไม่ใช่ atomic no-clobber ภายใต้ concurrent writers และ frozen/case specs ยังถูกตรวจหลัง seed/model บางส่วน นอกจากนี้มี 3 major เกี่ยวกับ run-id contract drift, cleanup observability และ crash-durability semantics

## ทางเลือกที่เล็กที่สุด

ไม่ต้องเปลี่ยน architecture อีก: คง single-file bundle และ injectable ports ได้ การแก้ที่เล็กที่สุดคือเพิ่ม pure preflight ของ frozen/cases ก่อน provision และเปลี่ยนขั้น publish จาก `exists → os.replace` เป็น primitive แบบ **create-if-absent/no-replace ที่ atomic จริง**

## Findings

### B1 — Single-file publisher ยัง overwrite/race ได้ เพราะ `exists → os.replace` ไม่ใช่ no-clobber (blocker)

ตำแหน่ง: `p2_atomic.py:73-85`

publisher ตรวจ `os.path.exists(final)` แล้วภายหลังตรวจซ้ำก่อน `os.replace(tmp, final)` แต่ check กับ replace ไม่ใช่ operation เดียวกัน หากสอง process เห็นว่า final ยังไม่มีพร้อมกัน ทั้งคู่สามารถเดินถึง replace ได้ และ `os.replace` มี semantics แทนที่ destination ที่มีอยู่ ไม่ได้เป็น rename-no-replace

targeted race probe บน Windows บังคับให้ writers ทั้งสองอ่าน `exists=False` ที่ check สุดท้ายพร้อมกัน:

```text
writer 1 -> PermissionError
writer 2 -> success
final exists -> True
```

ผลนี้ยืนยันว่า loser ไม่ได้จบด้วย controlled `PublishRefused`; บน POSIX `os.replace` สามารถให้ writer หลัง overwrite writer แรกได้ จึงยังอ้าง immutable/no-clobber ข้าม platform ไม่ได้

แก้ขั้นต่ำ:

1. ใช้ atomic no-replace primitive เช่น hard-link publish (`os.link(temp, final)` บน filesystem เดียวกัน แล้ว unlink temp) ซึ่ง fail ด้วย `FileExistsError` หาก final มีอยู่ หรือใช้ platform primitive `renameat2(RENAME_NOREPLACE)`/atomic claim ที่เทียบเท่า
2. map collision เป็น `PublishRefused` เสมอ
3. เพิ่ม 2-process/thread barrier regression: ต้องสำเร็จ **exactly one**, อีกตัว `PublishRefused`, final content เป็นของ winner และไม่มี overwrite/temp ค้าง
4. ห้ามใช้ check-then-`os.replace` เป็น immutability control

### B2 — Frozen manifest และ case specs ยัง validate หลัง seed; case หลัง ๆ ผิดทำให้ model ถูกเรียกบางส่วน (blocker)

ตำแหน่ง: `p2_m4_runner.py:56-64`, `p2_m4_runner.py:114-128`, public validation ที่ `p2_runplan.py:484-499`

runner ตรวจเพียง `cases` เป็น list ไม่ว่างก่อน provision ส่วน frozen digest/roles/categories/exact coverage ถูก public gate ตรวจตอนท้าย และ case ID/role/query ถูกตรวจทีละรายการหลัง `iso.seed(corpus)`

targeted probe ใช้ case แรกถูกและ case ที่สอง role ผิด:

```text
result: RunnerError
calls: provision, count, ports, prod, write, read, seed, teardown
model_queries: 1
final artifact: none
```

ดังนั้น artifact ยัง fail-closed แต่ runner ไม่ได้ fail-before-mutate/model สำหรับ frozen/case input

แก้โดยเพิ่ม pure preflight ก่อน scorer/provision:

1. `E.validate_m4_frozen_manifest(frozen) == []`
2. recompute frozen digest == `plan.m4_case_manifest_sha256`
3. frozen evaluated roles/required categories == RunPlan
4. case specs ต้องเป็น exact case set ไม่มี duplicate/extra/missing และแต่ละ case ID/role/query text/query vector ต้อง hash ตรง frozen
5. malformed/mismatch ใด ๆ → `RunnerError` ก่อน provision; เพิ่ม tests invalid case แรก/กลาง/ท้ายและ frozen digest/coverage mismatch โดย assert isolation calls/scorer queries ว่าง

### M1 — RunPlan กับ publisher ใช้ run-id contract คนละชุด และ regex `.match()` ยอม final newline (major)

ตำแหน่ง: `p2_runplan.py:28`, `p2_runplan.py:121-124`, `p2_atomic.py:19-34`

RunPlan ตรวจเฉพาะ regex แต่ publisher ตรวจเพิ่ม Windows reserved names ทำให้ `run_id="CON"` ผ่าน RunPlan แล้ว runner provision→seed→เรียก modelครบก่อน publisher ปฏิเสธ

probe:

```text
run_id=CON -> PublishRefused
calls include seed + teardown
model_queries: 2
```

อีกทั้งใช้ `regex.match()` กับ `$`; Python สามารถ match ก่อน final newline ได้ จึงควรใช้ `fullmatch()` หรือ `\Z`

แก้ด้วย validator เดียวที่แชร์ทั้ง RunPlan และ publisher: safe ASCII component + `fullmatch` + reserved names/control chars ความยาวเดียวกันทั้งหมด Invalid run ID ทุกชนิดต้องถูกปฏิเสธก่อน provision เพิ่ม plan-level tests สำหรับ reserved names และ trailing newline

### M2 — `_safe_teardown()` กลืน cleanup failure ทั้งหมดบน failure path (major)

ตำแหน่ง: `p2_m4_runner.py:44-50`, `p2_m4_runner.py:149-151`

original exception ไม่ถูกกลบแล้ว แต่ถ้า teardown ล้มพร้อมกัน cleanup failure จะหายเงียบ ทำให้ operator ไม่รู้ว่า isolated resources อาจค้างอยู่

แก้โดย preserve original exception พร้อม cleanup failure เช่น `add_note`, structured logger/audit callback หรือ `ExceptionGroup` โดยห้ามเปลี่ยน primary cause เพิ่ม regression “work/provision fail + teardown fail” ที่ยืนยันทั้งสองสาเหตุยังสังเกตได้และไม่มี artifact

### M3 — `_fsync_dir()` เป็น best-effort ทุก OS จึงยังรับประกัน crash durability ไม่ได้ตาม docstring (major)

ตำแหน่ง: `p2_atomic.py:37-48`, `p2_atomic.py:92`

function กลืน `OSError` ทั้งตอนเปิด directory และ fsync โดยไม่แยก Windows จาก POSIX แต่ contract ด้านบนระบุ parent fsync = crash durability

บน Windows การระบุ atomic-visibility only/best-effort ยอมรับได้ตาม handoff แต่บน POSIX fsync failure จริงต้องไม่ถูกรายงานเป็น durable success แนะนำคืน durability status ใน receipt/result หรือ raise operational error ที่บอกชัดว่า final อาจปรากฏแล้วแต่ durability ไม่ยืนยัน พร้อม fault-injection test

## สิ่งที่ยืนยันว่าปิดแล้ว

- B1 เดิม: bad count/ports/prod ไม่ write/seed/model; marker mismatch ไม่ seed/model
- B2 เดิม: malformed/mismatched corpus fail ก่อน provision
- B3 เดิม: provider/oracle target mismatch fail ก่อน seed
- B4 เดิม: provision failure เรียก teardown; teardown failure หลัง work ไม่ทิ้ง artifact
- M1 direct traversal: separator/absolute/drive/UNC/Unicode/length ถูก publisher ปฏิเสธ
- single-file bundle ถูก validate ก่อน serialize และ happy-path revalidate ผ่าน public gate

## Test evidence

Codex รันซ้ำ:

- `test_p2_m4_runner.py`: **31/31**
- `test_p2_atomic.py`: **16/16**

targeted probes เพิ่มเติมยืนยัน:

- concurrent final check → one success + one uncontrolled `PermissionError`
- reserved RunPlan ID → seed/model ก่อน publisher refuse
- bad second case → seed + model case แรกก่อน RunnerError

## คำตอบ 4 ข้อจาก handoff

1. **B1/B2/B3/B4 ปิดครบไหม:** findings เดิมส่วนหลักปิดแล้ว แต่ fail-before-mutate ยังไม่ครอบ frozen/cases และ lifecycle failure ยังขาด cleanup observability
2. **Single-file + containment + fsync พอไหม:** single-file/containment ถูกทาง แต่ immutability ยังไม่ atomic ภายใต้ concurrency และ fsync error semantics ยังไม่เท่ากับ durable guarantee
3. **Port contract พร้อมไหม:** target contract พอเป็น first cut; ยังไม่ควร implement real adapters จน B1/B2 และ shared run-id validation ปิด เพราะ adapters จะยึด runner lifecycle นี้
4. **GO real adapters ไหม:** **ยัง NO-GO** — ปิด B1/B2/M1 ก่อน; M2/M3 ควรปิดในรอบเดียวก่อนสร้าง evidence จริง

## Gate หลังรีวิวนี้

- Runner/atomic: **FIX-THEN-GO**
- Real adapters implementation: **NO-GO**
- M4a isolated run: **NO-GO** และยังต้อง adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- Decision benchmark: **NO-GO** จน sign-off + M4b + validated canary

