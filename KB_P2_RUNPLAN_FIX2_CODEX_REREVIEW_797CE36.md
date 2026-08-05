# Codex Targeted Re-review — P2 RunPlan Fix2

**Commit reviewed:** `797ce36`  
**Input:** `KB_P2_RUNPLAN_FIX2_HANDOFF.md`  
**Verdict:** **GO สำหรับ pinned build prep เท่านั้น · FIX-BEFORE-RUN**

## Intent และทางที่เล็กที่สุด

เป้าหมายของรอบนี้คือปิด decision bypass ให้หมดก่อนเริ่มเตรียม container ของ reranker จริง

ไม่ต้องแก้โครง root/selection manifest อีกรอบ โครงนั้นทำงานแล้ว จุดที่เหลือคือให้ quality rows ถูก join กับ frozen eval cases ภายใน decision boundary และจำกัด threshold domain ให้มีความหมาย จากนั้น runner ค่อยแปลง output ของ `p2_harness` เป็น evidence schemaเดียว ไม่ควรให้ callerเติม identity/tag เอง

## Findings

### M1 — Quality evidence ยังสวมผลจาก query/intent คนละชุดกับ frozen eval set ได้

**ตำแหน่ง:** `p2_runplan.py:232-285`, `p2_runplan.py:498-548`; producer ปัจจุบัน `p2_harness.py:52-57`

root ผูก hash ของ `cases` ถูกแล้ว และ quality evidence ผูก root/SelectionManifest ถูกแล้ว แต่ `validate_quality_evidence()` ตรวจเพียงจำนวน, uniqueness และ shape ของ `query_id`/`intent_id`/`challenge_tags` โดยไม่เทียบค่ากับ test cases ที่ถูก hash ไว้

Codex probe เปลี่ยน query/intent IDs ทั้ง 50 รายการเป็นค่าที่ไม่มีใน eval set, recompute raw digest ให้ self-consistent แล้วได้:

```text
fabricated_query_ids -> DECISION, approved=True
```

`derive_hardneg_deltas()` เชื่อ `challenge_tags` จาก quality rows ด้วย จึงสามารถย้าย tag ไปผูกกับ query ที่คะแนนดีกว่าได้ ขณะเดียวกัน `_eval_one()` ของ harness ยังไม่ส่ง `challenge_tags` ออกมา แปลว่า real runner ต้องเติม field นี้ภายหลัง และหากเติมจาก caller จะเปิดช่องเดียวกัน

**ต้องปิดก่อน model run/N-sweep:** ใน `decide_p2()` สร้าง map จาก frozen test cases แล้วบังคับ exact query-id set; สำหรับแต่ละ row ให้ `intent_id`, `role`, `challenge_tags` ตรง case เดิม หรือทางที่ปลอดภัยกว่าคือไม่รับ identity/tag จาก evidenceเลยและ join/derive จาก cases ด้วย `query_id` เพิ่ม negative tests สำหรับ fake/missing/extra query ID, changed intent และ changed tags

### M2 — Threshold schema ยังยอมค่าที่อยู่นอก domain ของ metric

**ตำแหน่ง:** `p2_runplan.py:80-103`

threshold ถูกอ่านจาก plan จริงและ override หลัง hash ไม่ได้แล้ว แต่ validator ยังรับค่าที่ไม่มีความหมายกับ nDCG delta เช่น:

```text
candidate_recall=0.0001
ci_lower_min=-999
noninferior_floor=-999
hardneg_floor=-999
validate_run_plan(...) -> []
```

นี่ไม่ใช่ post-hoc bypass แต่เปิดให้ preregister policy ที่อ่อนกว่ากติกา P2 เดิมมากโดยไม่มี validation warning

**แก้ก่อน benchmark จริง:** จำกัด delta/CI/floor ทุกค่าใน `[-1,1]` พร้อม relationship ที่ต้องการ และตัดสินให้ชัดว่า `candidate_hit` ต้อง exact `1.0` กับ CandidateRecall target `0.95` ตาม acceptance เดิม หรืออนุญาต configurable ผ่าน `threshold_policy_version` ที่ review แยกแล้ว ห้ามใช้เพียง “เป็นค่าบวก/ลบ”

### N1 — Harness comment ยังอ้าง approval function ที่ถูกลบแล้ว

**ตำแหน่ง:** `p2_harness.py:4`

comment ระบุว่ายังไม่ wire เข้า `decision_benchmark_manifest` ทั้งที่ function ถูกลบแล้ว ไม่กระทบ runtime แต่เสี่ยงให้คนเขียน Slice 2 runner ต่อผิด entry point

**แก้เมื่อสร้าง runner:** เปลี่ยนเป็น `decide_p2()` และระบุชัดว่า harness output เป็น unapproved raw mechanics จนถูก join กับ frozen cases + สร้าง bound evidence

## สิ่งที่ยืนยันว่าปิดแล้ว

| Finding | Re-review |
|---|---|
| B1 plan threshold/gate authoritative | **CLOSED** — decision ใช้ค่าจาก plan ไม่มี override argument |
| B2 root artifact/model binding | **CLOSED สำหรับ fields ใน pure schema** — eval/corpus/index/model/tokenizer/file-manifest/image/config ถูกเทียบ |
| B3 single approval surface | **CLOSED** — `decision_benchmark_manifest` ถูกลบ; `decision_evidence_errors` ไม่ stamp approval |
| B4 SelectionManifest + digest recompute | **CLOSED สำหรับ N/body consistency** — selection mismatch และ digest mismatch ถูก reject; เหลือ M1 identity join |
| M1 exact N keys | **CLOSED** |
| Model full commit/resolved snapshot | **CLOSED ใน pure boundary** — runtime assertion รอ container ตามแผน |

## Independent verification

- `test_p2_runplan.py` — **80/80 PASS**
- `test_p2.py` — **179/179 PASS**
- targeted probes ยืนยัน M1 และ M2 ตามด้านบน
- ไม่ได้เปิด Docker, Qdrant หรือโหลด model
- ไม่ได้แก้ code/`STATUS.md`; `tmp/` เดิมไม่ได้แตะ

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| เลือก immutable model commit แบบ full 40-hex | **GO** |
| สร้าง `Dockerfile.p2`/compose แบบ pinned โดยยังไม่ build/run benchmark | **GO** |
| เพิ่ม runner adapter ที่ join harness rows กับ frozen cases + bound evidence | **GO NOW — ต้องทำก่อน run** |
| model-load smoke / real M4 / N sweep | **FIX-BEFORE-RUN จน M1 ปิดและ threshold policy lock** |
| decision benchmark จริง | **NO-GO ตามเดิมจน Data Owner sign-off + validated real M4/canary** |

## Acceptance ก่อนเปิด container run

1. quality query-id set ต้องตรง frozen test cases exact และ intent/role/tags เปลี่ยนไม่ได้
2. hard-negative categories ต้อง derive จาก tags ของ frozen cases ไม่ใช่ tags ที่ runnerส่งมาเอง
3. threshold ทุกตัวมี domain/relationship หรือ policy version ที่ lock ชัด
4. harness/runner output ระบุ unapproved จนผ่าน `decide_p2`

**Final verdict:** **GO สำหรับเลือก commit และเขียน pinned Docker/compose เท่านั้น; FIX-BEFORE-RUN** — bypass เดิมปิดแล้ว แต่ quality rows ยังไม่พิสูจน์ว่าเป็นผลของ frozen eval queries จริง
