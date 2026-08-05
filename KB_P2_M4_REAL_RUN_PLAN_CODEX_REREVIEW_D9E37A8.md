# Codex Targeted Re-review — M4Evidence v4 schema

**Commit reviewed:** `d9e37a8`  
**Input:** `KB_P2_M4_REAL_RUN_PLAN.md`, `p2_eval.py`, `p2_runplan.py`, `test_p2_m4.py`  
**Verdict:** **FIX-THEN-GO harness**  
**ขอบเขต:** schema/validator review + pure probes เท่านั้น; ไม่รัน Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

v4 เลือกโครงที่ถูกแล้ว: `per_case[]` เป็นหลักฐาน authoritative และ final decision รับ frozen M4 manifest โดยตรง จึงไม่ต้องออกแบบ v5 ใหม่ งานที่เหลือเป็น targeted hardening ของ rank consistency, frozen-manifest validation/coverage และ exact hash-only shape ก่อนให้ harness ผลิตข้อมูลตาม schema

## Trace ที่ยืนยันแล้ว

- security assertions ถูกย้ายจาก aggregate ไป `_m4_case_errors()` ต่อ case/role (`p2_eval.py:430-528`)
- pair digest ถูก recompute จาก point/text components และ raw digest ถูก recompute จาก canonical `per_case` (`p2_eval.py:436-460,588-599`)
- case set ถูกเทียบ frozen manifest แบบ exact และ cross-role swap test ถูกจับได้ (`p2_eval.py:601-618`, `test_p2_m4.py:84-98`)
- `decide_p2()` require `m4_frozen_manifest`, เทียบ digest กับ RunPlan แล้วส่ง frozen เข้า decision evidence gate (`p2_runplan.py:527-605`)
- M4a/M4b stage binding ยังคง fail-closed

ดังนั้น findings รอบ v3 เรื่อง aggregate cross-role leak, optional expected และ pair self-stamp **ปิดแล้วจริง**

## Findings

### B1 — observed sentinel rank ไม่ถูกเทียบกับตำแหน่งจริงใน unfiltered top-N

**ตำแหน่ง:** `p2_eval.py:491-514`

validator ตรวจเพียงว่า sentinel อยู่ใน `unfiltered_topn_pairs` และค่าที่รายงานใน `observed_sentinel_ranks` อยู่ช่วง `1..N` แต่ไม่ตรวจว่า `unfiltered_topn_pairs[rank-1]` คือ sentinel ตัวนั้นจริง และไม่บังคับความยาว result `<= N`

targeted probe:

```text
unfiltered_topn_pairs = [authorized, sentinel]   # sentinel อยู่ rank 2
observed_sentinel_ranks = [[sentinel, 1]]       # รายงานเท็จว่า rank 1
validate_m4_run_evidence(...) -> []
```

**ต้องแก้:** reject duplicate result pairs, บังคับ `len(unfiltered_topn_pairs) <= selected_n`, สร้าง position map จาก ordered list แบบ 1-based แล้วตรวจ observed rank exact ทุก sentinel; reject duplicate/missing rank records

### B2 — frozen manifest ประกาศ evaluated roles ได้โดยไม่มี case ของ role นั้น

**ตำแหน่ง:** `p2_eval.py:86-91,577-618`, `p2_runplan.py:141-145,597-605`

manifest digest ครอบ `evaluated_roles` แต่ validator ตรวจ coverage เฉพาะ `required_categories`; ไม่มีการเชื่อม evaluated role names กับ `role_identity_sha256` ของ cases และ `decide_p2()` ไม่เทียบ `plan.evaluated_roles/required_categories` กับ frozen fields

targeted probe:

```text
frozen.evaluated_roles = ["qc", "sales"]
frozen.cases = {case_qc เท่านั้น}
validate_m4_run_evidence(...) -> []
```

ผลคือ final benchmark สามารถอ้างว่า M4 ครอบ Sales ทั้งที่ไม่เคยทดสอบ Sales

**ต้องแก้:** frozen case ต้องมี role ที่เชื่อมกับ RunPlan ได้อย่าง authoritative เช่น `effective_role` (ชื่อ role synthetic ไม่ใช่ secret) พร้อม `role_identity_sha256`; validator บังคับ:

- exact set ของ case roles == `frozen.evaluated_roles`;
- `frozen.evaluated_roles` == `plan.evaluated_roles`;
- `frozen.required_categories` == `plan.required_categories`;
- ทุก evaluated role และ required category มีอย่างน้อยหนึ่ง case

### B3 — malformed frozen manifest ทำให้ validator/decision path crash

**ตำแหน่ง:** `p2_eval.py:86-91,531-585`, `p2_runplan.py:597-601`

`m4_case_manifest_sha256()` เรียก `sorted()` และ canonical JSON ก่อน validate schema ตัวอย่าง `required_categories=["negation", None]` ทำให้เกิด:

```text
TypeError: '<' not supported between instances of 'NoneType' and 'str'
```

แทนที่จะคืน error list/`NOT_DECISION_ELIGIBLE` ซึ่งขัด fail-closed contract

**ต้องแก้:** เพิ่ม `validate_m4_frozen_manifest()` ก่อน hash โดยบังคับ exact types, non-blank/unique strings, SHA-256 case keys/pairs, nonempty/disjoint authorized+sentinel lists และ exact allowed keys; ทำ safe digest ที่จับ `TypeError`, `ValueError`, Unicode/NaN canonicalization errors ทั้งใน run validator และ `decide_p2()`

### M1 — hash-only contract ยังยอม unknown/raw fields

**ตำแหน่ง:** `p2_eval.py:430-528,531-631`, `KB_P2_M4_REAL_RUN_PLAN.md` ส่วน Durable evidence/v4 schema

targeted probe เพิ่ม `per_case[0].raw_text="SECRET"`, recompute raw digest แล้ว validator คืน `[]` เพราะไม่มี exact-key allowlist นั่นหมายถึง harness bug สามารถเขียน raw query/text/secret ลง durable evidence แล้วหลักฐานยัง PASS แม้ plan ระบุ hash-only

**ต้องแก้:** reject unknown keys แบบ exact schema ที่ top-level, frozen manifest, frozen case, per-case record, pair component และ rank record ใช้ชื่อ fields hash ชัดเจน; ห้าม raw text/query/vector/credential fields ปรากฏใน evidence

### M2 — same-query/same-N control ควรถูกล็อกใน harness contract

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md` หัวข้อ 3.1 และ v4 per-case schema

schema มี `query_vector_sha256` และ `selected_n` ชุดเดียว แต่ยังไม่บอกวิธีพิสูจน์ว่า raw unfiltered call กับ filtered provider call ใช้ค่าชุดเดียวกัน Harness อาจใช้ vector A ทำให้ sentinel ติด unfiltered แล้วใช้ vector B กับ filtered path

**ต้องแก้ก่อนลงมือ runner:** สร้าง immutable `QueryProbe` ต่อ caseเพียง object เดียวให้ทั้งสอง call ใช้ หรือให้ client spy บันทึก unfiltered/filtered query-vector hash + limit แล้ว validator ตรวจ exact equality กับ frozen case query spec

## สิ่งที่ผ่านแล้ว

- cross-role swap ถูกจับต่อ caseจริง
- frozen manifest เป็น required input; ไม่มี `expected=None` approval path
- raw per-case digest และ pair components ถูก recompute
- exact frozen case set, category coverage, zero-skip, finite scores และ stage binding ทำงานตามที่อ้าง
- stale `:6333` contradiction ถูกแก้แล้ว; internal Docker network designสอดคล้องกัน

## Verification

- `test_p2_m4.py`: **24/24 PASS**
- `test_p2.py`: **166/166 PASS**
- `test_p2_runplan.py`: **95/95 PASS**
- `test_p2_adapter.py`: **21/21 PASS**

targeted probes เพิ่มเติมยืนยัน:

- rank claim ไม่ตรง ordered result → ปัจจุบันผ่านผิด (`[]`)
- evaluated role ไม่มี case → ปัจจุบันผ่านผิด (`[]`)
- malformed frozen categories → ปัจจุบัน crash `TypeError`
- unknown `raw_text` ใน per-case → ปัจจุบันผ่านผิด (`[]`)

## Go / No-Go

| งาน | Verdict |
|---|---|
| ปิด B1–B3/M1 และล็อก M2 ใน schema/harness contract | **GO NOW** — pure/offline targeted fix |
| เขียน M4 harness | **FIX-THEN-GO** หลัง targeted re-review ผ่าน |
| M4a real run | **NO-GO** จน harness review + negative controls ผ่าน |
| N-sweep | **NO-GO** จน M4a PASS |
| M4b/decision benchmark | **NO-GO** จน selected N + sign-off + validated canary/evidence ครบ |

## Final verdict

**FIX-THEN-GO harness.** v4 architecture ใช้ต่อได้และไม่ต้องรื้อ แต่ rank proof กับ evaluated-role coverage ยังสามารถเขียวผิด และ malformed manifest ยัง crash public gate ปิดสามจุดนี้พร้อม exact hash-only schema แล้วจึงเริ่ม harness
