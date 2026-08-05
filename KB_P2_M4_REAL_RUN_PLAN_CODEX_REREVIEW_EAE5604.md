# Codex Targeted Re-review — M4Evidence v3 schema

**Commit reviewed:** `eae5604`  
**Input:** `KB_P2_M4_REAL_RUN_PLAN.md` rev2, `p2_eval.py`, `p2_runplan.py` และ regression tests  
**Verdict:** **FIX-THEN-GO harness**  
**ขอบเขต:** schema/validator review + pure tests เท่านั้น; ไม่รัน Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

เป้าหมายคือสร้างหลักฐาน M4 ที่พิสูจน์ permission boundary **ต่อ query/role** ก่อนให้ cross-encoder เห็นข้อความ โดย M4a ใช้ปลด N-sweep และ M4b ผูกกับ final decision

v3 เพิ่ม aggregate fields จำนวนมากแล้ว แต่ทางที่เล็กและปลอดภัยกว่าการเพิ่ม aggregate ต่อคือให้ `per_case[]` เป็นหลักฐาน authoritative เพียงชุดเดียว แล้ว derive counts/pair sets/summary จากมัน หลีกเลี่ยงข้อมูลสองชุดที่ขัดกันเอง:

```text
frozen M4 manifest → per-case oracle/control/provider/spy/model trace
                    → canonical raw digest
                    → derived summary (ห้าม caller กรอกเอง)
```

## Trace ที่ยืนยันแล้ว

เส้นจริงยังถูกทาง: `resolve_effective_access()` → `p2_provider.build_candidates()` ซึ่งใส่ Qdrant filter ก่อน retrieval และ revalidate payload หลัง backend → spy → `PinnedCrossEncoder.score()`

validator v3 ปัจจุบันตรวจ `authorized/provider/model/sentinel/unfiltered` เป็น aggregate lists ด้วย `Counter` (`p2_eval.py:487-513`) และตรวจ frozen request เฉพาะเมื่อ caller ส่ง optional `expected` (`p2_eval.py:527-536`) ส่วน final decision เรียก validator โดยไม่ส่ง `expected`; `_root_binding_errors()` ผูก model/image/index/selection แต่ไม่ผูก M4 case manifest (`p2_runplan.py:492-519`)

## Findings

### B1 — aggregate pair sets ซ่อน permission leak ข้าม case/role ได้

**ตำแหน่ง:** `p2_eval.py:487-513`, `KB_P2_M4_REAL_RUN_PLAN.md` ส่วน Evidence schema v3

ตัวอย่าง:

- case QC: oracle อนุญาต pair `A`
- case SALES: oracle อนุญาต pair `B`
- backend ผิดพลาดสลับผล: QC ได้ `B`, SALES ได้ `A`

เมื่อรวมทั้ง run จะได้ `authorized={A,B}` และ `provider={A,B}` ดังนั้น aggregate subset ผ่าน ทั้งที่รั่วทั้งสอง role ปัญหาเดียวกันเกิดกับ sentinel-in-unfiltered control, model input และ rerank permutation เพราะความสัมพันธ์กับ case/query/role ถูกทิ้งก่อน validate

**ต้องแก้:** schema authoritative ต้องเป็น `per_case[]` โดยแต่ละ record มีอย่างน้อย:

- `case_id_sha256`, role/effective-access identity hash, `selected_n` และ query-vector hash;
- expected authorized/sentinel pair digests จาก frozen manifest;
- ordered unfiltered top-N pairs + observed sentinel ranks;
- ordered provider pairs, model-input pairs, rerank-output pairs;
- model call/input/score counts, finite result และ terminal status ของ case นั้น

ตรวจ subset/disjoint/permutation และ load-bearing control **ภายใน case เดียวกัน** ก่อนค่อย aggregate ห้ามใช้ aggregate เป็น security gate

### B2 — M4b final decision ยังไม่ผูกกับ frozen M4 case/visibility manifest

**ตำแหน่ง:** `p2_eval.py:419,527-539`, `p2_runplan.py:492-519,585-596`, `test_p2.py:306,342-344`

`expected` เป็น optional และ evidence fixture ถูกถือว่า valid เมื่อไม่ส่ง expected Final decision path ก็ไม่ส่ง expected ให้ `validate_m4_evidence()` จึงสามารถใช้ M4b ที่รันกับ case IDs/roles/visibility matrix ชุดอื่นได้ ตราบใดที่จำนวน/hash formats และ eval/corpus/root fields อื่นดูถูกต้อง

**ต้องแก้:** freeze อย่างน้อย `m4_seed_manifest_sha256`/`m4_case_manifest_sha256`, required category set และ evaluated role set ไว้ใน root RunPlan จากนั้น:

- `decide_p2()` ต้องเทียบ digest นี้กับ raw M4 evidence จริง;
- M4b ต้องมี case IDs/roles/categories exact match กับ manifest;
- public approval path ต้องไม่มีโหมด `expected=None` แบบ fail-open

ถ้าต้องการคง validator แบบ format-only ให้แยกชื่อชัด เช่น `validate_m4_summary_shape()`; ส่วน `validate_m4_run_evidence()` ที่ใช้ปลด gate ต้อง require frozen request เสมอ

### B3 — `raw_evidence_sha256` และ pair digests ยังเป็นคำกล่าวอ้าง ไม่ได้ recompute จาก body

**ตำแหน่ง:** `p2_eval.py:414-416,487-525`, `KB_P2_M4_REAL_RUN_PLAN.md:80-118`

validator ตรวจเพียงว่า `raw_evidence_sha256` และ pair digest เป็น 64-hex ไม่ได้รับ raw receipt มา recompute และไม่เห็นองค์ประกอบ `point_id_sha256`/`rerank_text_sha256` จึงยืนยันไม่ได้ว่า pair digest ถูกสร้างตามสูตรหรือ summary มาจาก trace จริง

**ต้องแก้:** ให้ run validator รับ canonical raw evidence body/reference แล้ว:

1. recompute `raw_evidence_sha256` ด้วย canonical JSON (`sort_keys`, `allow_nan=False`);
2. recompute pair digest จาก component hashes ทุก record;
3. derive counts, finite-score verdict, aggregate summary และ case hashesจาก body;
4. reject summary ที่ไม่ตรงค่าที่ derive

แนวนี้ทำให้ `scorer_kind`, counts และ `all_scores_finite` ไม่ใช่ค่าที่ caller self-stamp

### M1 — network contradiction เก่ายังค้างใน Isolation section

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md` หัวข้อ 1 เทียบหัวข้อ 7

หัวข้อ 1 ยังสั่ง runner reject `:6333` แต่หัวข้อ 7 กำหนด endpoint isolated ที่ถูกต้องเป็น `qdrant:6333` และระบุว่า port ไม่ใช่ trust signal

**ต้องแก้:** ลบข้อ reject `:6333` จากหัวข้อ 1 ให้เหลือ exact project/network/volume/collection UUID + synthetic marker interlock ตามหัวข้อ 7

### M2 — plan อ้าง per-case rank/category coverage แต่ schema ยังไม่มี

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md` หัวข้อ 3.1, 4 และ Evidence schema v3

plan ขอ observed sentinel rank ต่อ caseและ required categories ครบ แต่ schema มีเพียง aggregate `unfiltered_topn_pair_digests` กับ `case_id_hashes`; validatorใช้ multiset subsetซึ่งทิ้ง rank/order/category จึงยังพิสูจน์ข้อความใน plan ไม่ได้

**ต้องแก้:** frozen manifest และ per-case evidence ต้องมี `category`, expected sentinel pairs, ordered unfiltered result และ observed rank map; validator บังคับ exact required-category coverage และ zero missing cases

## Findings เดิมที่ปิดแล้ว

- **B1 เดิม (sentinel ต้องติด unfiltered top-N):** logic ระดับ aggregate มีแล้ว; เหลือย้ายให้ตรวจ per case
- **B2 เดิม (ID↔text binding):** pair digest + multiset แก้การแยก ID/text แล้ว
- **B3 เดิม (vacuous/count/finite):** exact counts/types, finite flag, non-mock scorer และ zero-skip aggregate มีแล้ว
- **M1/M3 stage:** preflight บังคับ `decision_eligible=False`, N=50, ไม่มี selection digest; decision path ปฏิเสธ M4a
- **Network architecture:** แนวทาง internal Docker network + no published port ถูกต้อง ยกเว้น stale line ในหัวข้อ 1

## Verification

- `test_p2.py`: **203/203 PASS**
- `test_p2_runplan.py`: **95/95 PASS**

tests ยืนยัน v3 behavior ที่เขียนไว้ แต่ยังไม่มี cross-case/cross-role swap, per-case sentinel rank/category, raw-body digest recompute หรือ final-decision M4-manifest binding จึงไม่หักล้าง findings ข้างต้น

## Go / No-Go

| งาน | Verdict |
|---|---|
| ปรับ schema เป็น authoritative `per_case[]` + raw digest recompute + root manifest binding | **GO NOW** — pure/offline |
| เขียน seed/oracle/spy/runner harness | **FIX-THEN-GO** หลัง schema re-review ผ่าน |
| M4a real run | **NO-GO** จน harness review + negative controls ผ่าน |
| N-sweep | **NO-GO** จน M4a PASS |
| M4b/decision benchmark | **NO-GO** จน selected N + Data Owner sign-off + validated canary/evidence ครบ |

## Final verdict

**FIX-THEN-GO harness.** v3 ปิดช่องเดิมเมื่อมองทั้ง run รวมกัน แต่ permission เป็น invariant ต่อ query/role; aggregate schema ยังซ่อน cross-role leak ได้ ให้ย้าย security assertions ไป `per_case[]` และ bind raw/frozen manifest เข้ากับ final decision ก่อนเริ่ม harness
