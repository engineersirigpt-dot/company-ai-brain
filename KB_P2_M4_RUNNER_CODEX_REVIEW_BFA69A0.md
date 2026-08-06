# Codex review — P2 M4 runner + atomic writer (`bfa69a0`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: `p2_m4_runner.py`, `p2_atomic.py`, tests และ public gates ที่ runner เรียกใช้  
ข้อจำกัดที่รักษาไว้: review/pure probes เท่านั้น — ไม่เขียน real adapters, ไม่แตะ Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Verdict

**FIX-THEN-GO real adapters**

โครง injectable runner ควรมีอยู่และแยก infra ports ถูกทิศ แต่ยังมี 4 load-bearing gaps ใน orchestration/provenance และ 1 gap ใน publisher path control ดังนั้นยังไม่ควรใช้ interface นี้เป็นฐานเขียน real adapters

`test_p2_m4_runner.py 22/22` และ `test_p2_atomic.py 14/14` ผ่านจริง แต่ negative tests ปัจจุบันวัดเพียง “ไม่มี final artifact” หลายกรณี จึงไม่เห็น side effect ที่เกิดก่อน public gate ปฏิเสธ

## ทางเลือกที่เล็กกว่า/ตรงกว่า

สำหรับ artifact authority ใช้ **ไฟล์ final เดียว** เช่น `<safe-run-id>.bundle.json` ซึ่งบรรจุ `{evidence, receipt}` จะง่ายและเสี่ยงน้อยกว่า directory ที่มีสองไฟล์: serialize snapshot เดียว → validate snapshot เดียว → fsync temp file → atomic no-clobber publish → fsync parent directory ส่วนไฟล์ evidence/receipt แยกสามารถสร้างเป็น derived convenience copies ภายหลังได้

ถ้าต้องคง two-file directory bundle ให้แก้ findings ด้านล่างและระบุว่าเป็น atomic visibility + crash durability ระดับใดอย่างชัดเจน

## Findings

### B1 — Interlock ผิดยัง mutate target และเรียก model ก่อนถูกปฏิเสธ (blocker)

ตำแหน่ง: `p2_m4_runner.py:68-80`, `p2_m4_runner.py:83-101`, `p2_m4_runner.py:122-123`

เส้นทางปัจจุบันคือ observe count/ports/production → **write marker** → build proof → **seed corpus** → provider/oracle/model ทุก case → ค่อย validate public bundle ตอน publish

ดังนั้น `initial_point_count != 0`, published port, production endpoint หรือ marker mismatch ทำให้ไม่มี PASS artifact จริง แต่ไม่ได้ fail ก่อน side effect

probe ที่รันกับ `endpoint_is_production=True`:

```text
result: PublishRefused
calls: provision, count, ports, prod, write, read, seed, teardown
seeded: True
model_queries: 2
final artifact: False
```

ผลกระทบ: ถ้า real adapter classify endpoint ว่า production ถูกต้อง runner ยังเขียน marker/seed synthetic corpus และส่ง candidate เข้า model ก่อนมารู้ตัวที่ปลายทาง

แก้ขั้นต่ำ:

1. ตรวจ exact count/ports/production **ทันทีหลัง observe และก่อน write_marker**; ค่าใดผิดต้อง abort
2. หลัง write/read ให้สร้างและ validate `IsolationProof` ทันที **ก่อน seed**; marker mismatch/shape ผิดต้อง abort
3. เพิ่ม tests ว่า count/ports/prod ผิด → `write=False`, `seeded=False`, provider/oracle/model ไม่ถูกเรียก; marker mismatch → อนุญาตเฉพาะ write/read แต่ `seeded=False` และ model ไม่ถูกเรียก

### B2 — Corpus ที่ seed ไม่ถูก bind กับ `RunPlan.corpus_manifest_sha256` (blocker)

ตำแหน่ง: `p2_m4_runner.py:48`, `p2_m4_runner.py:80`, `p2_m4_runner.py:107-112`

`corpus` มาจาก caller และถูกส่งเข้า `iso.seed(corpus)` โดยตรง แต่ evidence stamp `plan["artifact_digests"]["corpus_manifest_sha256"]`; ไม่มีจุดใดเรียก `E.validate_corpus(corpus)` หรือ recompute `E.corpus_manifest_sha256(corpus)` เทียบ plan

หลักฐานจาก happy-path test: runner seed `corpus=["doc-a", "doc-b"]` ซึ่งไม่ใช่ frozen corpus dict ตาม contract ขณะที่ plan ใช้ digest `"a" * 64` และยัง publish PASS ได้

ผลกระทบ: evidence สามารถอ้าง corpus A ทั้งที่ Qdrant ถูก seed ด้วย corpus B ทำให้ M4 provenance และผล benchmark ใช้ตัดสินไม่ได้

แก้ขั้นต่ำก่อน provision:

1. บังคับ corpus เป็น canonical frozen corpus dict
2. `E.validate_corpus(corpus) == []`
3. recompute `E.corpus_manifest_sha256(corpus)` และต้องตรง RunPlan exact
4. mismatch/malformed ต้อง `RunnerError` ก่อน provision/seed/model และไม่มี artifact

### B3 — Provider/Oracle target ไม่ได้ bind กับ isolation handle (blocker)

ตำแหน่ง: `p2_m4_runner.py:65`, `p2_m4_runner.py:89-90`, `p2_m4_runner.py:100-104`

`iso.provision()` คืน `handle` ที่มี collection/endpoint แต่ `provider.filtered_candidates(...)`, `oracle.unfiltered_topn(...)` และ `oracle.observe_visibility(...)` ไม่รับ handle/target identity และไม่คืน observed target identity

จากนั้น OracleProof ถูก stamp ด้วย `handle["collection_id"]` แม้ runner ไม่สามารถพิสูจน์ว่า oracle/provider query collection นั้นจริง

ผลกระทบ: adapter ที่ pre-bind ผิด collection หรือชี้ production โดยพลาดสามารถ query target อื่น แต่ evidence ยังอ้าง isolated collection ID และ public gate ผ่านได้หากผล pairs ตรง frozen

แก้ contract อย่างใดอย่างหนึ่ง:

- ให้ `provision()` คืน opaque `target` capability แล้วส่ง target เข้า provider/oracle ทุก call; หรือ
- ให้ provider/oracle bind target หลัง provision และ expose `observed_target_identity()` ซึ่ง runner เทียบ exact กับ isolation handle ก่อน seed/query

ต้องคง client provider/oracle แยกกัน แต่ทั้งสองต้องพิสูจน์ว่าใช้ collection/endpoint เดียวกับ isolated target เพิ่ม negative test target ID/endpoint mismatch → abort ก่อน model/publish

### B4 — Lifecycle boundary ไม่ครอบ provision failure และ publish เกิดก่อน teardown (blocker)

ตำแหน่ง: `p2_m4_runner.py:65-66`, `p2_m4_runner.py:122-126`

สองเส้นที่ probe ยืนยัน:

1. `iso.provision()` อยู่นอก `try`; ถ้ามันสร้าง resource บางส่วนแล้ว raise, `teardown()` ไม่ถูกเรียก
2. bundle ถูก publish ก่อนเข้า `finally`; ถ้า `teardown()` raise, caller ได้ failure แต่ final PASS artifact ถูกทิ้งไว้แล้ว

```text
provision raises -> teardown=False
teardown raises after happy run -> RuntimeError + final artifact=True
```

แก้ขั้นต่ำ:

- ให้ cleanup scope ครอบ provision และกำหนด `teardown()` ให้ partial-provision-safe/idempotent
- ทำ work + assemble evidence ก่อน, teardown ให้สำเร็จ, แล้วค่อยสร้าง finished receipt/validate/publish
- ถ้า teardown fail ต้องไม่มี PASS artifact; เพิ่ม tests provision-partial-fail และ teardown-fail
- อย่าให้ teardown exception กลบ original exception โดยไม่มีการ preserve context

### M1 — `run_id` เป็น filesystem path injection และเขียนออกนอก `out_dir` ได้ (major)

ตำแหน่ง: `p2_runplan.py:120-121`, `p2_atomic.py:51-69`

ทั้ง RunPlan และ publisher ตรวจเพียง non-blank จากนั้นใช้ `os.path.join(out_dir, run_id)` และใช้ run_id เป็น temp prefix

safe probe ใน temporary directory:

```text
run_id = "x/../../escape"
resolved final = <temp-root>/escape
common path != out_dir  # escaped=True
```

แก้ defense-in-depth ทั้งสองชั้น:

1. RunPlan จำกัด run_id เป็น safe basename เช่น `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`
2. publisher reject absolute path, separators, `.`/`..`, drive/UNC/reserved components
3. resolve final แล้ว assert parent exact เท่ากับ resolved `out_dir`
4. เพิ่ม traversal tests ทั้ง `/`, `\\`, `..`, absolute, drive/UNC และ Unicode separator edge

### M2 — Directory rename ยังไม่ crash-durable ตาม claim (major)

ตำแหน่ง: `p2_atomic.py:71-80`

ไฟล์และ temp directory ถูก fsync ก่อน `os.replace` แต่ไม่มี fsync ของ parent `out_dir` หลัง rename จึงรับประกัน atomic visibility ได้ แต่บน POSIX ยังไม่รับประกันว่า directory entry ใหม่จะอยู่หลัง power loss/crash

แก้โดย fsync parent หลัง rename (และกำหนด policy ชัดว่าถ้า fsync ไม่รองรับ/ล้ม จะ fail หรือรายงาน durability level อย่างไร) พร้อม fault-injection test เท่าที่ platform รองรับ

## Test gaps ที่ทำให้ 36/36 ยังเขียว

- invalid interlock tests assert `no artifact` แต่ไม่ assert `seeded=False`/model not called
- happy path ใช้ corpus list + digest ลอย จึงผ่านโดยไม่ได้ทดสอบ corpus binding
- ไม่มี provider/oracle target-mismatch test
- ไม่มี provision failure หรือ teardown failure test
- immutable test เป็น sequential เท่านั้น และไม่มี run_id containment tests

Codex รันยืนยัน:

- `test_p2_m4_runner.py`: **22/22**
- `test_p2_atomic.py`: **14/14**
- targeted probes ด้าน B1/B4/M1 ให้ผลตาม findings ข้างบน

## คำตอบ 4 ข้อจาก handoff

1. **Orchestration fail-closed ครบไหม:** ยังไม่ครบ — artifact fail-closed แต่ infrastructure/model side effects ยังไม่ fail-before-mutate ตาม B1 และ corpus/target provenance ยังไม่ bind ตาม B2/B3
2. **Atomic control พอไหม:** two-file directory rename atomic ใน happy path แต่ path containment และ crash durability ยังไม่ครบ; authoritative single bundle file ง่ายกว่า
3. **Port contract ครบก่อน real adapter ไหม:** ยังไม่ครบ — ต้องเพิ่ม isolated target binding ให้ provider/oracle และ lifecycle semantics สำหรับ partial provision/teardown
4. **GO real adapters ไหม:** **NO-GO จน B1-B4 และ M1 ปิด**; M2 ควรปิดในรอบเดียวก่อนสร้าง evidence จริง

## Gate หลังรีวิวนี้

- Runner/atomic implementation: **FIX-THEN-GO**
- Real adapters implementation: **NO-GO**
- M4a isolated run: **NO-GO** และยังต้อง Data Owner sign-off แบบ hash-bound ตามเดิม
- N-sweep: รอ validated M4a PASS
- Decision benchmark: **NO-GO** จน sign-off + M4b + validated canary
