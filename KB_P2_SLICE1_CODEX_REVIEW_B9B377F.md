# Codex Review — P2 Slice 1 (`b9b377f`)

**วันที่:** 2026-08-04  
**Target:** `KB_P2_SLICE1_HANDOFF.md`, commit `b9b377f`  
**Scope:** Slice 1 pure ordering/metrics/eval contract และ go/no-go สำหรับ Slice 2 isolated model run  
**ไม่ได้แก้:** implementation, `STATUS.md`, container หรือ Qdrant

## Verdict: **FIX-THEN-GO SLICE 2**

`dense_order`, `rerank_order`, `fused_rrf` และ metric formulas หลักเดินถูกทิศ แต่ security/evidence boundary ใน `p2_eval.py` ยังมี fail-open หนึ่งจุดและ label-freeze contract ยังไม่ตรงคำว่า “authorized/frozen” จริง จึงยังไม่ควรเริ่ม model/container run จนกว่าจะปิด B1/B2/M1/M2 ด้านล่าง

การแก้ทั้งหมดเป็น pure/local ขนาดเล็ก ไม่ต้องแตะ infra หลังผ่าน targeted re-review ให้ **GO Slice 2** ตาม scope ที่เสนอ

## Intent / simpler path

Slice 1 ควรทำเพียงสองอย่างให้เชื่อถือได้:

1. รับ candidate universe ที่ผ่าน policy มาแล้ว แล้วคืน permutation แบบ deterministic สำหรับ dense/rerank/RRF
2. วัดผลกับ relevance/corpus snapshot ที่ validate และ freeze จริง โดย quality report เกิดได้เฉพาะเมื่อ permission gate ผ่าน

ไม่จำเป็นต้องเพิ่ม weighted fusion, API endpoint หรือ sparse hybrid ในรอบนี้

## Findings

### B1 — `permission_gate_ok(False)` fail-open เป็น `True`

**Finding:** `p2_eval.py:52-57` ใช้ `permission_exit_code == 0`; ใน Python `False == 0` และ `0.0 == 0` เป็นจริง

**Why it matters:** orchestration ที่ส่ง boolean `False` เพื่อแทน permission failure อาจได้รับอนุญาตให้สร้าง quality report ต่อ ทั้งที่ contract ระบุ fail/ERROR/INCONCLUSIVE ต้อง block

**Suggested change:** รับเฉพาะ exact integer exit code หรือ structured result:

```python
def permission_gate_ok(exit_code):
    if type(exit_code) is not int:
        raise ValueError("permission exit code must be exact int")
    return exit_code == 0
```

เพิ่ม test: `0→True`; `1/-1→False`; `False/True/0.0/None/"0"→ValueError` และ Slice 2 runner ต้องหยุด/exit non-zero ก่อนคำนวณหรือ publish quality summary เมื่อ gate ไม่ผ่าน

### B2 — label authorization เช็กเพียง membership ไม่ใช่ P1 effective policy

**Finding:** `p2_eval.py:40-43` ถือ point authorized เมื่อ `role in allowed_roles` แต่ P1 retrieval ต้อง AND `acl_schema_version`, `policy_version`, `policy_status=ACTIVE` และ role ผ่าน compiled filter

**Why it matters:** stale-schema, QUARANTINED หรือ malformed point ที่ยังมี role ใน payload สามารถผ่าน label validation ทั้งที่ query จริงห้าม retrieve ทำให้ `CandidateRecall@N` ดูต่ำปลอมและหลักฐาน “ACL coverage 100%” ไม่จริง

**Suggested change:** frozen corpus entry ต้องมี full policy payload และ validator ต้องใช้ `compile_retrieval_filter()` + `matches_policy()`/shared policy evaluator สำหรับ role เดียวกับ candidate provider ห้าม reimplement membership check

เพิ่ม tests อย่างน้อย:

- ACTIVE + version/schema ถูก + role ตรง → pass
- missing ACL, stale schema, wrong policy version, QUARANTINED → reject แม้ role อยู่ใน `allowed_roles`
- unknown role และ malformed/scalar roles → reject
- admin ไม่มี bypass

### M1 — `frozen_hash()` freeze เฉพาะ cases ไม่ได้ freeze corpus ที่ใช้ benchmark

**Finding:** `p2_eval.py:47-49` hash เฉพาะ eval cases ขณะที่ labels อ้าง point IDs/text/policy ใน corpus ที่เปลี่ยนได้

**Why it matters:** corpus text, source mapping หรือ ACL เปลี่ยนแต่ dataset hash เดิม ผล benchmark สองรอบจะดูเหมือนเทียบของเดียวกันทั้งที่ candidate universe/ข้อความให้ reranker ไม่เหมือนเดิม

**Suggested change:** แยกและบันทึกอย่างน้อย:

- `eval_set_sha256`
- `corpus_manifest_sha256` จาก canonical rows ที่มี `point_id`, stable document/source id, `sha256(rerank_text)`, full policy fields และ `rerank_text_version`
- `benchmark_contract_version`

run metadata ต้องผูกสอง hash นี้กับ git commit, model revision, tokenizer revision และ container image ก่อน benchmark ห้ามใช้ข้อความ “frozen corpus” หากมีเพียง cases hash

### M2 — plan บอกมี no-answer แต่ validator บังคับ relevance ไม่ว่าง

**Finding:** `KB_P2_PLAN.md:42` รวม no-answer ใน hard negatives แต่ `p2_eval.py:33-36` reject relevance ว่างทุกกรณี

**Why it matters:** no-answer ไม่ใช่ hard negative ของ ranking; เป็น abstention/threshold problem หากยัด relevant point ปลอมเพื่อให้ validator ผ่านจะทำ metric ผิดความหมาย

**Suggested change:** เลือกอย่างใดอย่างหนึ่งให้ชัด:

1. Slice 2 ranking dataset มีเฉพาะ `case_type="ranking"` และ relevance ไม่ว่าง; ย้าย no-answer ไปไฟล์/suite แยกจนมี threshold contract — **แนะนำทางนี้**
2. หรือรองรับ `case_type="no_answer"`, relevance ต้องว่าง และ exclude จาก Hit/MRR/Recall/nDCG aggregation โดยอัตโนมัติ

ห้ามนับ no-answer เป็นศูนย์ใน ranking aggregate

### M3 — metric/fusion helpers ยังยอม config/input ที่ทำคะแนนผิดความหมาย

**Finding:** `retrieval_metrics.py:18-58` ไม่ validate `n/k`, duplicate ranked IDs หรือ relevance grades; `fused_rrf()` (`rerank.py:81-103`) ไม่ validate `rrf_k`

**Why it matters:** `k<0` ใช้ Python negative slicing, duplicate relevant ID ทำ nDCG เกิน 1 ได้ และ `rrf_k=-rank` ทำ division-by-zero/คะแนนผิด การพิมพ์ config ผิดจึงอาจสร้าง evidence ที่ดู valid

**Suggested change:**

- `n/k` ต้อง exact positive int
- ranked/candidate IDs ต้อง unique non-empty strings
- grades จำกัด exact int ในชุดที่ contract ล็อก เช่น `{1,2,3}`
- metric result ต้อง finite และ nDCG/recall อยู่ `[0,1]`
- `rrf_k` ต้อง exact positive int; `dense_rank_map` ต้องครบ exact universe เมื่อส่งเข้ามา
- latency values ต้อง finite และไม่ติดลบ

เพิ่ม edge tests ก่อน Slice 2

### M4 — unauthorized-sentinel test ปัจจุบันยังเป็น tautology ไม่ใช่ filter integration

**Finding:** `test_p2.py:73-81` สร้าง list ที่ไม่มี sentinel ตั้งแต่แรก แล้วตรวจว่า scorer ไม่เห็น sentinel ส่วน `authorized_ids` เป็น set ที่ caller ส่งเอง

**Why it matters:** test นี้พิสูจน์เพียง `score_fn` เห็นสิ่งที่ caller ส่งให้ แต่ยังไม่พิสูจน์ internal candidate provider ใส่ compiled filter ก่อนอ่านข้อความจาก Qdrant

**Disposition:** ไม่ block การยอมรับ ordering core ของ Slice 1 แต่เป็น mandatory Slice 2 integration acceptance:

- seed semantically-perfect unauthorized sentinel ใน isolated Qdrant
- candidate provider รับ trusted `EffectiveAccess` ไม่รับ raw role ที่ไม่ resolve
- query ด้วย compiled filter เดียวกับ API
- independent oracle/direct scroll รู้ว่า ID ใด authorized; ห้ามสร้าง oracle จาก provider output
- spy adapter ยืนยัน unauthorized point ID/text ไม่เคยถึง cross-encoder
- dense/rerank/fused outputs ต้องมี exact ID set เท่ากับ authorized candidate pool

### M5 — eval schema validation ยังไม่ครอบ fields ที่ผล document-level ใช้

`validate_eval_set()` ยังไม่ตรวจชนิด/รูปของ `cases`, `query_id`, trimmed query, `lang/category/split/label_status`, `relevant_sources` หรือ consistency ระหว่าง relevant point กับ source labels

ก่อน Slice 2 ให้กำหนด schema version และ fail เมื่อ:

- field บังคับหาย/ผิดชนิด/มี control character
- grade นอก allowlist
- `relevant_sources` ไม่ตรง source ของ relevant point ตาม frozen manifest
- test label ไม่ใช่ `human-reviewed`
- query/source/point id ซ้ำหรือว่าง

## สิ่งที่ยืนยันผ่านใน Slice 1

- candidate shape, unique IDs/ranks และ finite dense/rerank scores ถูกตรวจ
- dense/rerank ordering deterministic และไม่ mutate input
- score count/NaN/Inf ถูก reject
- RRF ใช้ 1-based rank, exact universe set-equality และ deterministic tie-break
- point metrics, graded nDCG, document collapse และ percentile formulas หลักถูกทดสอบกับตัวอย่างที่เหมาะสม
- weighted-score และ dense+sparse hybrid ถูก defer ถูกต้อง

ดังนั้นไม่ต้องรื้อ `rerank.py` architecture; แก้ boundary validation/gate แล้วใช้ interface เดิมต่อได้

## คำตอบคำถามใน handoff

### 1. Slice 1 ครบหรือยัง

ordering/fusion core: **รับได้**  
security/eval contract: **ยังไม่ครบ — ปิด B1/B2/M1/M2/M3 ก่อน GO Slice 2**

### 2. Scope Slice 2

**รับ scope แบบ conditional GO** หลัง pure fixes ผ่าน:

- isolated container/Qdrant network+volume+ports และ unique run marker
- local `bge-reranker-v2-m3` โดย pin model/tokenizer revision และบันทึก checksum/image/hardware/batch/max-length/truncation
- internal candidate provider ใช้ trusted `EffectiveAccess` + shared compiled filter/Qdrant adapter; ไม่แก้ public API cap และไม่เรียก production endpoint/collection
- synthetic frozen corpus + human-reviewed ranking labels + hard negatives
- sweep `N={10,20,30,50}`; accuracy สามารถ retrieve top-50 หนึ่งครั้งแล้วใช้ prefixes แต่ latency ต้องวัด batch N จริงแยกกัน
- p5b/P2 permission canaries ต้องไหลผ่าน candidate provider และทุก ordering arm ไม่ใช่เพียง rerun `/search` dense เดิม
- durable evidence ผูก clean commit + eval/corpus hashes + pinned model/container
- no company data/cloud egress และ teardown เฉพาะ isolated stack

### 3. Hard-negative categories

ชุดปัจจุบันใช้ได้สำหรับ mechanics แต่ให้เพิ่ม:

- lexical-overlap สูงแต่ผิดเอกสาร/ผิด procedure code
- ตัวเลข หน่วย และ table cell ที่ใกล้กัน
- acronym/คำทับศัพท์ Thai↔English
- negation หรือสถานะ current/superseded ในข้อมูลสังเคราะห์
- semantically-perfect unauthorized twin **เฉพาะ permission suite**

ย้าย no-answer ไป abstention suite แยกตาม M2

### 4. Primary metric / acceptance

ให้ล็อก **`nDCG@5` เป็น primary metric เดียว**; `MRR@5` เป็น key secondary เพื่อไม่เปิดทางเลือกผู้ชนะจากสอง primary metrics

acceptance 5 ข้อใช้ได้ แต่ก่อน model run ต้องใส่ตัวเลขจริงสำหรับ:

- CandidateRecall@N target/วิธีเลือก N
- allowed degradation หรือ minimum improvement ของ nDCG@5
- CI rule เช่น lower bound ของ paired delta ต้องไม่ต่ำกว่า tolerance
- rerank/fusion p95 latency budget บน hardware ที่ระบุ

หาก `fused_rrf` ไม่ชนะ rerank-only ตาม primary criterion ให้เลือก rerank-only ซึ่งง่ายกว่า

## Handoff สั้นให้ Claude

**FIX-THEN-GO:** ปิด B1 type-strict permission gate, B2 full-policy label authorization, M1 corpus+eval freeze hashes, M2 แยก no-answer, M3 metric/RRF input guards และ M5 schema checks ใน pure code/tests ก่อน จากนั้นส่ง targeted re-review; เมื่อผ่านให้ GO Slice 2 isolated container/model ตาม scope ที่ยืนยัน โดย M4 unauthorized-sentinel ต้องพิสูจน์ที่ candidate-provider integration จริง

