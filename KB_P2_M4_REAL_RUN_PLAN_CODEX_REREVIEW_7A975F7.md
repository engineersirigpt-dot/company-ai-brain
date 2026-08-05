# Codex Targeted Re-review — M4Evidence v4 hardening

**Commit reviewed:** `7a975f7`  
**Inputs:** `KB_P2_M4_REAL_RUN_PLAN.md`, `p2_eval.py`, `p2_runplan.py`, `test_p2_m4.py`  
**Verdict:** **FIX-THEN-GO harness**  
**Scope:** pure/offline schema + validator review only; ไม่รัน Docker/Qdrant/model และไม่แก้ `STATUS.md`

## สรุปตรง ๆ

สิ่งที่แก้จากรอบ `d9e37a8` ปิดจริงเกือบทั้งหมด: rank เท็จถูกจับ, unfiltered pair ซ้ำถูกจับ, evaluated-role coverage ผูกกับ case แล้ว, malformed categories คืน error แทน crash และ exact hash-only allowlist กัน raw field ได้จริง ชุดทดสอบที่อ้างผ่านครบ

แต่ contract ยังไม่ implement-ready สำหรับ harness เพราะ **QueryProbe ยังไม่ถูก freeze ต่อ case** และ **M4a preflight ยังไม่มี exact RunPlan binding** ทั้งสองจุดพิสูจน์ด้วย targeted probe แล้วว่าสามารถเปลี่ยนค่าหลัง freeze โดย validator ยังคืน `[]` ได้ จึงยังไม่ควรให้ harness ผลิตหลักฐานตาม schema นี้แล้วถือว่า M4a ผ่าน

## Findings

### B1 — QueryProbe ผูกกันเอง แต่ยังไม่ผูกกับ frozen case

**ตำแหน่ง:** `p2_eval.py:100,133,513-575`; `KB_P2_M4_REAL_RUN_PLAN.md:38-43,95-114`

`_M4_FCASE_KEYS` ไม่มี `query_vector_sha256` ขณะที่ `_m4_case_errors()` ตรวจเพียงว่า hash ของ unfiltered และ filtered เท่ากับ `per_case.query_vector_sha256` ดังนั้นผู้สร้าง evidence เปลี่ยนทั้งสามค่าไปพร้อมกัน แล้ว recompute `raw_evidence_sha256` ได้โดย frozen manifest ไม่เปลี่ยน

targeted probe:

```text
query_vector_sha256 = 99…99
unfiltered_query_vector_sha256 = 99…99
filtered_query_vector_sha256 = 99…99
raw_evidence_sha256 = recompute(per_case)
validate_m4_run_evidence(...) -> []
```

ผลคือคำสัญญา “deterministic vector / frozen QueryProbe” ยังตรวจย้อนหลังไม่ได้ และอาจเลือก query vector หลังเห็นผลเพื่อทำให้ sentinel ติด top-N

**ต้องแก้ก่อน harness:** เพิ่ม `query_vector_sha256` (หรือ `query_probe_sha256` ที่ derive จาก canonical vector spec) ใน frozen case, รวมเข้า manifest digest และ compare exact ใน `_m4_case_errors()` เพิ่ม regression ที่เปลี่ยน hash ทั้งสามพร้อมกันแล้วต้อง fail การใช้ object `QueryProbe` เดียวกันใน harness ยังต้องทำตามแผน แต่ไม่ทดแทน frozen binding

### B2 — M4a exact pin/image/index binding ยังเป็นคำอ้างในแผน ไม่ใช่ public gate

**ตำแหน่ง:** `p2_eval.py:635-745`; `p2_runplan.py:497-525`; `KB_P2_M4_REAL_RUN_PLAN.md:13,83-89`

`validate_m4_run_evidence()` ตรวจ model/tokenizer/image/model-file/index แค่รูปแบบ ส่วน `_root_binding_errors()` ที่ compare exact กับ RunPlan ถูกเรียกเฉพาะ `decide_p2()` ซึ่งห้าม M4a เข้าอยู่แล้ว การส่ง root hash ที่ถูกต้องไม่ได้ทำให้ metadata ของ M4a ถูก bind กับ root

targeted probe ใช้ M4a ที่มี `run_manifest_sha256` ตรง expected แต่เปลี่ยน model/tokenizer/image/model-file/index เป็นค่า valid คนละชุด และไม่ใส่ `inference_config`:

```text
validate_m4_run_evidence(...,
  run_manifest_sha256=expected_root,
  require_stage="preflight-n50") -> []
```

นี่ขัดกับ negative control “wrong index/run/image/hash → FAIL” และข้อความ “exact expected pin/image/index” ใน plan โดยตรง

**ต้องแก้ก่อน harness:** สร้าง public M4a gate ที่รับ frozen `RunPlan`/`M4RunRequest` เป็น required input แล้ว compare exact อย่างน้อย `run_id`, model/tokenizer revision, image digest, model-file manifest, inference config, retrieval-index manifest, eval/corpus hashes และ root digest เพิ่ม mutation test แยกทุก field รวม missing `inference_config` ห้ามใช้ format-only validator เพื่อปลด N-sweep

### M1 — Durable receipt ที่แผนกำหนดยังไม่มี schema/binding ให้ harness ผลิต

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md:79-81`; `p2_eval.py:87-92,693-704`

แผนต้องการ receipt ที่ครอบ command/timestamps/exit/stdout-stderr hashes และมี digest/path แต่ exact top-level schema ไม่มี receipt reference และ `raw_evidence_sha256` ปัจจุบัน recompute จาก `per_case` เท่านั้น จึงมีสอง contract ที่ชื่อ “raw evidence” แต่ครอบข้อมูลคนละชุด

**ควรปิดในรอบเดียวกับ harness contract:** แยก `M4RunReceipt` exact/hash-only แล้วให้ `M4Evidence` อ้าง `run_receipt_sha256` (path เป็น locator ที่ไม่ใช่ trust signal) หรือประกาศชัดว่า receipt อยู่นอก M4Evidence พร้อม manifest ตัวกลางที่ bind ทั้งคู่ ห้ามเพิ่ม command/raw logs ตรง ๆ เข้า v4 เพราะ exact-key validator จะ reject และเสี่ยง secret

### M2 — malformed unknown keys ยังทำ public validator crash ได้

**ตำแหน่ง:** `p2_eval.py:103-107`

`_extra_keys()` ใช้ `sorted(extra)` ถ้า pure/injectable caller ส่ง dict ที่มี unknown key ต่างชนิด เช่น `None` กับ `int` จะเกิด `TypeError` แทน error list แม้ contract ระบุ fail-closed/no-crash

targeted probe:

```text
frozen[None] = 1
frozen[5] = 2
validate_m4_frozen_manifest(frozen)
-> TypeError: '<' not supported between instances of 'int' and 'NoneType'
```

**แก้:** validate ว่า key ทุกตัวเป็น string ก่อน หรือ format unknown keys ด้วย `repr()` แล้ว sort strings พร้อม regression test ทั้ง frozen/top/per-case/component boundaries

## Non-blocking cleanup

- `KB_P2_M4_REAL_RUN_PLAN.md:83-84` ยังอ้าง `validate_m4_evidence`/`p2-m4-v3` ทั้งที่ authoritative section ด้านล่างเป็น `validate_m4_run_evidence`/`p2-m4-v4`; แก้ก่อนใช้เป็น handoff ให้ผู้เขียน harness
- `validate_m4_frozen_manifest()` ยอม duplicate ใน `authorized_pairs`/`sentinel_pairs`; ควรบังคับ unique เพื่อให้ frozen oracle เป็น set ที่ canonical และไม่เปิด multiset ambiguity

## สิ่งที่ยืนยันว่าปิดแล้ว

- rank claim ต้องตรงตำแหน่งจริงแบบ 1-based และ unfiltered top-N ห้ามซ้ำ/ยาวเกิน N
- cross-role swap ถูกจับต่อ case
- exact case set, required-category coverage และ evaluated-role coverage ทำงานจริง
- malformed role/category ที่ทดสอบไว้คืน error list; safe manifest digest ไม่ crash
- unknown/raw field ใน schema ระดับปกติถูก reject
- same-query/same-N equality ระหว่างสอง call ทำงาน — เหลือเพียง bind QueryProbe นั้นเข้ากับ frozen case ตาม B1

## Verification

- `test_p2_m4.py`: **34/34 PASS**
- `test_p2.py`: **166/166 PASS**
- `test_p2_runplan.py`: **95/95 PASS**
- `test_p2_adapter.py`: **21/21 PASS** (Qdrant integration ถูก skip เพราะ optional dependency ไม่มี; ไม่ได้อ้างเป็น real-M4 proof)

targeted probes เพิ่มเติม:

- เปลี่ยน QueryProbe hash ทั้งสามพร้อมกัน + recompute body digest → **ผ่านผิด (`[]`)**
- M4a ใส่ root hash ถูก แต่เปลี่ยน model/image/index ทั้งชุดและไม่มี inference config → **ผ่านผิด (`[]`)**
- frozen authorized pair ซ้ำ → validator ยังคืน `[]` (non-blocking schema hygiene)
- mixed-type unknown keys → **crash `TypeError`**

## Gate

| งาน | Verdict |
|---|---|
| ปิด B1/B2 + M1/M2 แบบ pure/offline | **GO NOW** |
| เขียน M4 harness (seed/oracle/spy/runner) | **FIX-THEN-GO** หลัง targeted re-review ผ่าน |
| M4a real run บน isolated Qdrant | **NO-GO** จน harness review + negative controls ผ่าน |
| N-sweep | **NO-GO** จน validated M4a PASS |
| M4b/decision benchmark | **NO-GO** จน Data Owner sign-off + selected-N M4b + canary/evidence ครบ |

## Final verdict

**FIX-THEN-GO harness.** โครง v4 per-case authoritative ถูกทางและ findings รอบก่อนปิดจริง แต่ M2 เดิมเรื่อง frozen QueryProbe ปิดเพียงครึ่งเดียว และ M3 exact preflight binding ยังไม่มี enforcement path แก้สอง blocker นี้ พร้อมล็อก receipt shape และ no-crash boundary แล้ว contract จะ implement-ready โดยไม่ต้องรื้อ v4
