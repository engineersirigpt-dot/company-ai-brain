# Codex Targeted Re-review — P2 M4 harness fix round 2

**Commit reviewed:** `3e61c33`
**Input:** `KB_P2_M4_HARNESS_FIX2_HANDOFF.md`
**Verdict:** **FIX-THEN-GO runner**
**Scope:** pure/offline review + targeted probes; ไม่รัน Docker/Qdrant/model, ไม่เขียน runner และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

เป้าหมายคือให้ M4 evidence เป็นผลจาก execution path เดียวจริง: frozen QueryProbe → permission guard → pinned scorer → immutable case record → validated isolation/oracle proofs → receipt

ทางที่เล็กกว่าการเพิ่ม DTO หลายชั้นคือรวม `score_case()` และ `build_case_record()` เป็น boundary เดียว เช่น `run_case()` ซึ่ง validate scorer + input, เรียก model และคืน case record โดยตรง เก็บ trace เป็น implementation detail ไม่เปิด constructor/consumer seam ให้ callerประกอบ recordโดยไม่ score จากนั้นให้ run-level builder รับ proof objects และ case recordsจริง ไม่รับ string/count verdict

ไม่จำเป็นต้องทำ Python object ให้ต้าน malicious process ได้ทั้งหมด—ถ้า process ที่สร้าง evidence ถูกยึด ก็ปลอม dict ได้อยู่แล้ว—but trusted runner path ต้องไม่สามารถออก PASS จาก mock, wrong model, fabricated trace หรือ string `"PASS"` เพราะ wiring ผิดโดยไม่ตั้งใจ

## Findings

### B1 — M4 PASS ยังสร้างได้โดยไม่เรียก scorer และ scorer จริงไม่ถูก bind กับ pin ที่ evidence อ้าง

**ตำแหน่ง:** `p2_m4_harness.py:58-87,104-134,154-166`; `p2_eval.py:681-691`

`CaseTrace` เป็น public `NamedTuple`: caller เรียก constructorหรือ `_replace()` ได้เอง แม้ `setattr()` ไม่ได้ `build_case_record()` ตรวจเพียง `isinstance(trace, CaseTrace)` จึงไม่ได้พิสูจน์ว่า trace มาจาก `score_case()`

พร้อมกันนั้น `assemble_evidence()` hardcode `scorer_kind="pinned-cross-encoder"` และรับ model revision/file manifest/inference config จาก `run_meta`; ไม่มีจุดใดอ่าน `scorer.metadata()` หรือ compare scorer ที่ถูกเรียกกับ M4RunRequest

targeted probes:

```text
CaseTrace(...) สร้างตรง ๆ สอง case โดยไม่สร้าง/เรียก scorer
→ assemble evidence + receipt
→ validate_m4_preflight_bundle(...) = []

RecScorer ไม่มี metadata()
→ happy-path evidence อ้าง scorer_kind=pinned-cross-encoder
→ validate_m4_preflight_bundle(...) = []
```

ดังนั้น run ที่เผลอ wire mock/wrong snapshot ก็สามารถถูกบันทึกเป็น real pinned-model PASS ได้

**ต้องแก้ก่อน runner:**

- รวม score → case record เป็น function เดียว หรือทำ trace เป็น private implementation detail ห้าม public consumerรับ `CaseTrace` ที่ callerสร้างได้;
- validate scorer metadataกับ expected M4RunRequest ก่อน delegate: model name/revision, tokenizer revision, file-manifest, max length, batch size, device และ dtypeต้องตรง exact;
- derive `scorer_kind`/pin fieldsจาก validated scorer proof ห้าม hardcodeหรือรับสำเนาอิสระจาก `run_meta`;
- negative tests: scorerไม่มี metadata, kind=mock, wrong revision/file manifest/inference config และ fabricated/direct `_replace()` trace ต้องไม่สร้าง gate-eligible evidence

### B2 — `build_verdicts()` ยังรับคำกล่าวอ้าง ไม่ได้รับ validated proof objects ตามที่ handoff ระบุ

**ตำแหน่ง:** `p2_m4_harness.py:137-142`

function รับ `isolation="PASS"`, `oracle="PASS"` และ counts จาก caller แล้วกำหนด `sentinel_reached_model=False`/unauthorized count 0 เอง ไม่มี `IsolationProof`, `OracleProof`, proof-body digest หรือ sequence ของ case tracesที่ใช้ derive ค่า

type check ก็ไม่มี: probe `case_count=True, traced_count=True` คืน status `PASS` เพราะ bool เปรียบเทียบแบบ int ได้

receipt ปัจจุบันมีเพียง hash ของ `isolation_marker` ที่ callerส่งเอง ดังนั้น probe ใช้ marker `"fake"` กับ string verdicts แล้ว public gateยังผ่าน

**คำตอบข้อ 3 ของ handoff:** contract นี้ยังไม่พอสำหรับ real runner ควรบังคับ proof objectsตั้งแต่ pure slice นี้ หรืออย่างช้าที่สุดใน commit เดียวกับ runnerโดยต้อง re-review ก่อน M4a

รูปแบบขั้นต่ำ:

- `IsolationProof` มี validated project/network/volume/collection UUID + synthetic marker + canonical proof digest;
- `OracleProof` มี frozen-manifest/index binding + exact case set + canonical proof digest;
- run builderรับ proof objects + actual case records แล้ว derive status/countsเอง ห้ามรับ raw strings/counts;
- evidence/receiptอ้าง proof digests และ public bundle gate recompute/validate proof bodies ไม่ใช่ตรวจเพียงคำว่า PASS;
- exact-int guards (`type(x) is int`) ยังควรมี แม้เปลี่ยนเป็น proof objectsแล้ว

### M1 — การเปลี่ยน exact evidence schema ยังใช้ชื่อ `p2-m4-v4` เดิม

**ตำแหน่ง:** `p2_eval.py:77,93-102`; `KB_P2_M4_REAL_RUN_PLAN.md:83-123`

commit นี้เพิ่ม required `query_text_sha256` ทั้ง frozen case และ per-case evidence ทำให้ artifact v4 เดิมไม่ผ่าน validatorใหม่ แต่ `M4_SCHEMA_VERSION` ยังเป็น `p2-m4-v4` จึงมี schema nameเดียวแต่ shapeสองแบบ

ก่อนมี durable real-run artifactเป็นเวลาที่แก้ง่ายที่สุด: bump schema เป็น v5 (หรือ revision ชื่ออื่นที่ชัด), update fixtures/RunPlan plan docs และเพิ่ม compatibility decision ว่า v4 เก่าถูก reject ไม่ใช่ตีความด้วย validatorใหม่

`KB_P2_M4_REAL_RUN_PLAN.md:102,111,116` ยังไม่ใส่ `query_text_sha256` ใน schema/QueryProbe/frozen manifest แม้ codeบังคับแล้ว ต้อง sync ก่อนใช้เป็น runner handoff

### M2 — raw query validation ยังยอม whitespace/control-only text ถึง scorer

**ตำแหน่ง:** `p2_m4_harness.py:27-30,68-82`

`_text_hash()` ปฏิเสธเฉพาะ `""`; query `"   "` ผ่านและ targeted scorerได้รับจริง แม้ eval-set validatorใช้กติกา non-blank/control-safe ที่เข้มกว่า

ใช้ validation เดียวกับ eval contract: non-blankหลัง `strip()`, reject control/lone-surrogate และอย่า normalize/stripเงียบจน hashต่างจาก stringที่ส่ง model ถ้าจะ normalize ให้สร้าง canonical QueryProbeก่อนทั้ง hashและdelegate

## สิ่งที่ยืนยันว่าปิดแล้ว

- `run_meta` มี exact allowlist และไม่สามารถทับ verdict, `per_case`, schema หรือ raw digestได้แล้ว
- query textจริงถูกส่งเข้า underlying scorer และ `query_text_sha256` ถูก bindกับ frozen manifest
- query text hashที่เปลี่ยนโดยคง vectorเดิมถูก public validator reject
- query vector/candidate authorization ถูก validateก่อน delegate; NaN/sentinelทำให้ underlying call countเป็นศูนย์
- score count mismatch และ non-finite scoreถูก rejectก่อนคืน trace
- run-id/receipt no-crash/timestamp/argv/typed identity fixesเดิมยังทำงาน

## Verification

รันผ่านใน environment นี้:

- `test_p2_m4_harness.py`: **20/20**
- `test_p2_m4.py`: **41/41**
- `test_p2_runplan.py`: **95/95**
- `test_p2.py`: **166/166**
- `test_p2_adapter.py`: **21/21**; Qdrant integrationถูก skip เพราะไม่มี optional `qdrant_client`

targeted probesที่ suiteปัจจุบันยังไม่ครอบ:

- fabricated `CaseTrace` โดยไม่มี scorer call → **public gate ผ่านผิด (`[]`)**
- scorerไม่มี `metadata()` → evidenceยังอ้าง pinned cross-encoder และ **gate ผ่าน (`[]`)**
- `build_verdicts(..., case_count=True, traced_count=True)` → **PASS**
- whitespace-only query → underlying scorerถูกเรียก
- `CaseTrace._replace` มีอยู่ แม้ testตรวจเพียง `setattr()`

## Gate

| งาน | Verdict |
|---|---|
| scorer binding + single score→record boundary + real proof-object contract + schema bump/tests | **GO NOW — pure/offline** |
| เขียน real-path runner | **FIX-THEN-GO** หลัง targeted re-review ผ่าน |
| M4a run บน isolated Qdrant | **NO-GO** จน runner + IsolationProof/OracleProof + atomic-write review ผ่าน |
| N-sweep | **NO-GO** จน validated M4a PASS |
| decision benchmark | **NO-GO** จน Data Owner sign-off + M4b + validated canary/evidenceครบ |

## Final verdict

**FIX-THEN-GO runner.** `run_meta`, real query และ pre-delegate guard ปิดได้แล้ว แต่หลักฐานยังไม่ยืนยันว่า scorerถูกเรียกหรือเป็น pinned modelจริง และ run-level PASS ยังมาจาก strings/counts ที่ callerกรอกเอง จุดใหญ่ที่สุดคือ happy pathที่ใช้ mock/no-metadata แล้วยังได้ evidenceชื่อ `pinned-cross-encoder`; ปิด provenance seam นี้พร้อม proof-object binding ก่อนเริ่ม runner
