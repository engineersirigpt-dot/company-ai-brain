# Codex Targeted Re-review — M4 v4 round-2 closure

**Commit reviewed:** `2d491dc`  
**Inputs:** `KB_P2_M4_REAL_RUN_PLAN.md`, `p2_eval.py`, `p2_runplan.py`, `test_p2_m4.py`, `test_p2_runplan.py`  
**Verdict:** **GO M4 harness (pure/injectable)** · **NO-GO M4a real run**  
**Scope:** schema/validator review + pure probes; ไม่รัน Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

เป้าหมายคือให้ harness สร้างหลักฐานว่า permission filter เป็น load-bearing boundary ต่อ case/role โดยไม่ให้ caller เปลี่ยน QueryProbe หรือ runtime pins หลัง freeze

ไม่ควรเพิ่ม validator หลายชั้นที่ caller ต้องประกอบเอง ทางที่เล็กและปลอดภัยที่สุดสำหรับ harness คือมี public entrypoint เดียว เช่น `validate_m4_preflight_bundle(plan, frozen, evidence, receipt)` ซึ่ง:

1. validate RunPlan และ recompute root;
2. compare frozen manifest digest/roles/categories กับ RunPlan;
3. derive `M4RunRequest` จาก RunPlan ภายใน function;
4. validate receipt body แล้ว recompute receipt digest;
5. เรียก `validate_m4_run_evidence(..., require_stage="preflight-n50")`.

ห้าม public runner รับ free-form `expected` หรือ frozen digest จาก CLI แล้วถือว่า authoritative เอง

## Closure ของ findings รอบ `7a975f7`

### B1 QueryProbe frozen binding — CLOSED

**Trace:** frozen case เพิ่ม `query_vector_sha256` ใน `p2_eval.py:100-101,138-153`; `_m4_case_errors()` compare evidence กับ frozen ก่อนตรวจ same-query hashes ที่ `p2_eval.py:573-586`

targeted probe เปลี่ยน `query_vector_sha256`, unfiltered hash และ filtered hash พร้อมกัน แต่ไม่เปลี่ยน frozen manifest:

```text
validate_m4_run_evidence(...) ->
["query_vector_sha256 ไม่ตรง frozen QueryProbe ..."]
```

ดังนั้นช่อง evidence-only post-hoc vector mutation ปิดจริง

### B2 exact M4RunRequest comparison — CLOSED ที่ low-level validator

**Trace:** `expected` เป็น required input (`p2_eval.py:648-663`) และ model/tokenizer/model-file/image/inference/index ถูก compare exact ที่ `p2_eval.py:757-764`; final decision derive expected จาก validated RunPlan ที่ `p2_runplan.py:610-617`

tests ครอบ evidence ไม่ตรง expected, missing inference config และ M4a pin mismatch แล้ว ช่อง format-only แบบเดิมปิดจริง

### M1 receipt reference — CLOSED สำหรับ evidence schema

`run_receipt_sha256` อยู่ใน exact top-level allowlist และต้องเป็น SHA-256 (`p2_eval.py:87-92,755-756`) ทำให้ M4Evidence สามารถ bind receipt แยกโดยไม่ใส่ command/raw logs ลง evidence โดยตรง

การพิสูจน์ receipt body ยังเป็น acceptance ของ harness ตามหัวข้อด้านล่าง ไม่ใช่เหตุให้หยุดเขียน harness

### M2 no-crash + frozen uniqueness — CLOSED

`_extra_keys()` sort ค่า `repr()` จึงไม่ crash เมื่อ unknown keys ต่างชนิด (`p2_eval.py:107-112`) และ frozen authorized/sentinel pairs ถูกบังคับ unique (`p2_eval.py:154-163`) targeted tests ผ่านจริง

## Harness requirements ที่ต้องล็อกก่อนขอ GO run

### M1 — M4a ต้อง anchor trusted inputs เข้ากับ validated RunPlan ภายใน public gate

low-level `validate_m4_run_evidence()` จงใจเชื่อ `frozen` และ `expected` ที่ caller ส่งเข้ามา จึงตรวจได้เพียง evidence เทียบ trusted inputs ไม่ได้พิสูจน์ provenance ของ trusted inputs เอง

targeted probes ยืนยัน seam นี้:

```text
เปลี่ยน evidence.query hashes + frozen.query hash + recompute manifest พร้อมกัน -> []
เปลี่ยน evidence pins/index + expected pins/index พร้อมกัน              -> []
ใช้ model/tokenizer revision แบบ abbreviated 7-hex ในทั้งสองฝั่ง       -> []
```

นี่ไม่ใช่ blocker ต่อการ **เขียน** harness เพราะ public runner ยังไม่มีและเป็นงาน slice นี้ แต่เป็น blocker ต่อ **M4a run/การปลด N-sweep**

**Acceptance สำหรับ harness:**

- รับ frozen RunPlan artifact แล้วเรียก `validate_run_plan()` ก่อนเสมอ;
- recompute root ด้วย `run_manifest_sha256(plan)`;
- compare `m4_case_manifest_sha256`, roles/categories และ eval/corpus/index digests กับ artifacts จริง;
- derive expected request จาก RunPlan ด้วย helper เดียวที่ reuse ทั้ง M4a และ `decide_p2()` ห้าม duplicate mapping;
- full immutable model/tokenizer commit ต้องมาจาก RunPlan validator ไม่ใช้ `_is_hex_commit()` แบบ 7-hex เป็น trust gate;
- freeze `run_id` ใน run request และ compare exact ไม่ควรเป็น optional ใน public M4a gate;
- mutation test ต้องเปลี่ยน evidence+expected/frozen พร้อมกัน แต่คง RunPlan เดิม แล้ว public gate ต้อง fail

### M2 — M4RunReceipt ต้องเป็น body-validated ไม่ใช่ SHA self-stamp

ปัจจุบัน validator ตรวจ `run_receipt_sha256` เฉพาะรูปแบบ ซึ่งถูกต้องสำหรับ summary reference แต่ harness ต้องสร้าง receipt schema exact/hash-only และ recompute digest จาก body ก่อนอ้างใน M4Evidence

ขั้นต่ำควร bind: schema version, run/root/request/frozen/evidence hashes, command or argv hash, started/finished timestamps, exact integer exit code, stdout/stderr hashes, isolation/index/model/image identifiers และ terminal status ห้ามเก็บ secret/raw text

negative controls ต้องครอบ tampered receipt body, digest mismatch, missing/partial write, non-zero exit และ exception ก่อน/หลัง evidence write ทุกกรณีต้องไม่มี PASS artifact ใช้ atomic temp→rename หรือกลไกเทียบเท่า

## Documentation cleanup

`KB_P2_M4_REAL_RUN_PLAN.md:83-84` ยังอ้าง `validate_m4_evidence` และ `p2-m4-v3` ขณะที่ authoritative schema เป็น `validate_m4_run_evidence` / `p2-m4-v4` แก้ก่อนส่ง handoff ให้ผู้เขียน harness เพื่อไม่ให้ implementation อ้าง contract เก่า

## Verification

- `test_p2_m4.py`: **39/39 PASS**
- `test_p2.py`: **166/166 PASS**
- `test_p2_runplan.py`: **95/95 PASS**
- `test_p2_adapter.py`: **21/21 PASS**; Qdrant integration ถูก skip เพราะ optional dependency ไม่มี จึงไม่ถูกนับเป็น real-M4 proof

targeted probes เพิ่มเติม:

- เปลี่ยน QueryProbe เฉพาะ evidence → **reject ถูกต้อง**
- เปลี่ยน evidence pin แต่ expected คงเดิม → tests **reject ถูกต้อง**
- เปลี่ยน evidence+trusted input พร้อมกัน → low-level validator ผ่านตาม trust contract; public harness gate ต้องจับด้วย RunPlan root
- mixed-type unknown keys → คืน error list ไม่ crash

## Gate

| งาน | Verdict |
|---|---|
| เขียน seed manifest + oracle + spy + runner + M4RunReceipt แบบ pure/injectable | **GO** |
| public M4a gate + negative controls ตาม M1/M2 | **ต้องอยู่ใน harness slice นี้** |
| M4a real run บน isolated Qdrant | **NO-GO** จน harness implementation review ผ่าน |
| N-sweep | **NO-GO** จน validated M4a PASS |
| M4b/decision benchmark | **NO-GO** จน Data Owner sign-off + selected-N M4b + validated canary/evidence ครบ |

## Final verdict

**GO M4 harness (pure/injectable).** สอง blocker เดิมปิดแล้วใน low-level schema และ architecture v4 ไม่ต้องรื้อ จุดสำคัญของ slice ถัดไปคือทำ trust anchor ให้จบที่ public M4a entrypoint และทำ receipt digest ให้ derive จาก bodyจริง ก่อนจึงค่อยขอ GO run
