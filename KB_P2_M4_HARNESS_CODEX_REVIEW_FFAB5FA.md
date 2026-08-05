# Codex Review — M4 pure/injectable harness

**Commit reviewed:** `ffab5fa`  
**Inputs:** `KB_P2_M4_HARNESS_HANDOFF.md`, `p2_m4_harness.py`, `p2_eval.py`, `p2_runplan.py`, `test_p2_m4_harness.py`  
**Verdict:** **FIX-THEN-GO runner**  
**Scope:** pure/offline code review + targeted probes; ไม่รัน Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

เป้าหมายของ harness คือสร้างหลักฐานจากสิ่งที่ **เข้า real cross-encoder จริง** แล้วผูกหลักฐาน/receipt กับ validated RunPlan ก่อนปลด M4a

ทางที่เล็กที่สุดคือให้ boundary เดียวเป็นเจ้าของ candidate pairs ตั้งแต่ก่อนเรียก scorerจนถึง evidence: `score_candidates(query, [(point_id, text), ...])` ต้องบันทึก pair trace, guard unauthorized ก่อน delegate และคืน trace ที่ `build_case_record()` ใช้โดยตรง ห้ามรับ `model_input` แยกจาก caller เพราะจะเกิดข้อมูลจริงสองชุดที่สลับกันได้

## Findings

### B1 — Spy trace ไม่ได้เป็นแหล่งข้อมูลของ `model_input_pairs`; model เห็น sentinel จริงแต่ evidence ยัง PASS ได้

**ตำแหน่ง:** `p2_m4_harness.py:34-53,73-100`

`SpyScorer.score()` บันทึกเพียงข้อความและคะแนน ไม่มี point ID, pair digest หรือ query identity ขณะที่ `build_case_record()` รับ `model_input` จาก caller อีกชุด แล้วใช้ชุดหลังสร้าง evidence โดยไม่ compare กับ `spy.texts`

targeted probe:

```text
SpyScorer.score("different-model-query", [sentinel_text])   # สิ่งที่ model เห็นจริง
build_case_record(model_input=[authorized_pair], spy=spy)    # สิ่งที่ evidence อ้าง
validate_m4_preflight_bundle(...) -> []
```

ผลคือ invariant หลัก “sentinel ไม่ถึง model” ถูกหลบได้ และ test ปัจจุบันตรวจแค่จำนวน calls/scores ไม่ได้ตรวจ identity ของ input

**ต้องแก้ก่อน runner:**

- เปลี่ยน boundary เป็น `score_candidates(query, candidates)` ที่รับ ordered `(point_id, rerank_text)`/candidate objects;
- derive `model_input_pairs`, components, call/input/score counts และ query hash จาก immutable spy trace เท่านั้น;
- เอา caller-supplied `model_input` ออกจาก `build_case_record()` หรือ compare exact กับ traceแล้ว fail;
- spy ต้องรับ authorized-pair set และตรวจ sentinel/unauthorized **ก่อน**เรียก underlying scorer; negative control ต้องยืนยัน underlying mock call count ยังเป็น 0 เมื่อ sentinel ถูก inject;
- ใช้ spy ใหม่ต่อ caseหรือเก็บ trace แยกด้วย case ID ห้ามใช้ cumulative trace ข้าม case

### B2 — `run_id` binding เป็นวงกลม: expected ถูกสร้างจาก receipt ที่กำลังตรวจ

**ตำแหน่ง:** `p2_runplan.py:471-511`

public gate สร้าง `expected = {...m4_run_request(plan), "run_id": receipt.get("run_id")}` แล้ว receipt validator เพียง compare receipt run ID กับ evidence run ID ดังนั้น receipt+evidence เลือก run ID เดียวกันเองได้โดยไม่ผูกกับ RunPlan

หลักฐานอยู่ใน happy-path fixture เอง: `PLAN.run_id == "run-1"` แต่ evidence/receipt ใช้ `"m4run"` และ gate ผ่าน

**ต้องแก้:** ใช้ `plan["run_id"]` เป็น expected run ID โดยตรง หรือถ้าต้องมี child M4 attempt ID ให้ pre-register `m4_run_id`/attempt ID ใน M4RunRequest ที่ digest ถูกผูกกับ rootก่อนรัน ห้าม derive authoritative ID จาก output receipt

### B3 — malformed receipt ทำ public M4a gate crash แทน fail-closed error list

**ตำแหน่ง:** `p2_eval.py:776-823`; `p2_runplan.py:500-505`

`validate_m4_run_receipt()` อาจคืน errors ได้แล้ว แต่ gate ยังเรียก `m4_run_receipt_sha256(receipt)` แบบไม่ guard ตัวอย่าง `status=float("nan")` ทำ canonical JSON raise `ValueError: Out of range float values...` และหลุดออกจาก public gate

**ต้องแก้:** เพิ่ม safe receipt digest ที่จับ `TypeError`/`ValueError`/Unicode canonicalization errors แล้ว append controlled error; ถ้า receipt validation มี structural errors ให้ short-circuit ก่อน hash ห้าม exception หลุดจาก public gate

### M1 — receipt ยอมเวลาสิ้นสุดก่อนเวลาเริ่ม และ command hash มี ambiguity

**ตำแหน่ง:** `p2_eval.py:798-804`; `p2_m4_harness.py:114-124`

- validator parse timestamps แยกกันแต่ไม่ตรวจ `finished_utc >= started_utc`; targeted probe กลับเวลาแล้ว recompute digest ยังผ่าน `[]`
- command hash ใช้ `" ".join(argv)` ทำให้ `['a b','c']` กับ `['a','b c']` ได้ hash เดียวกัน

**แก้:** parse timestamp แล้ว compare instant จริง; hash canonical JSON ของ argv listที่ validate ว่าเป็น strings แทน joined string และให้ `_bytes_sha256` รับ bytes เท่านั้นเพื่อหลีกเลี่ยง `str(obj)` ambiguity

### M2 — evidence builder hardcode security verdicts ก่อนมี interlock/oracle proof

**ตำแหน่ง:** `p2_m4_harness.py:103-111`

`assemble_evidence()` stamp `isolated_interlock=PASS`, `independent_oracle=PASS`, `sentinel_reached_model=False` และ unauthorized count 0 เอง แม้ pure harness ยังไม่มี isolation interlock และ B1 แสดงแล้วว่า model trace อาจไม่ตรง evidence

**แก้ก่อนเชื่อม runner:** รับ validated interlock/oracle result objects และ derive fieldsจากผลนั้น หรือให้ public gateสร้าง PASS summaryหลัง proof objects ผ่าน ห้าม builderทั่วไปประกาศ security verdictเอง

### M3 — hash helper ใช้ `str()` ทำให้ typed identities ชนกัน

**ตำแหน่ง:** `p2_m4_harness.py:20-31,57-63`

`component(1, "x") == component("1", "x")` เป็นจริง เพราะ `_h()` แปลงทุกอย่างเป็น string ก่อน hash Query vector listก็ใช้ Python `str()` ซึ่งไม่ใช่ canonical cross-runtime representation

**แก้:** normalize Qdrant point ID ตามชนิดที่รองรับพร้อม type tag และ hash canonical JSON ของ validated finite vector; บังคับ `rerank_text`/query text เป็น string ห้าม auto-coerce object

## สิ่งที่ยืนยันว่าปิดแล้ว

- public gate validate RunPlan และ recompute root ก่อนใช้
- frozen manifest digest/roles/categories ถูก compare กับ RunPlan
- M4RunRequest helper ถูก reuse ระหว่าง M4a และ decision path
- receipt bodyผูก root, frozen manifest, raw evidence, model/image/index และ evidence receipt digestถูก recompute
- evidence-only pin mutation, frozen-only QueryProbe mutation, invalid RunPlan, non-zero receipt exit และ ordinary receipt tampering ถูก reject
- stale v3 wording ใน real-run plan ถูกแก้เป็น v4 แล้ว

## Verification

- `test_p2_m4_harness.py`: **12/12 PASS**
- `test_p2_m4.py`: **39/39 PASS**
- `test_p2.py`: **166/166 PASS**
- `test_p2_runplan.py`: **95/95 PASS**
- `test_p2_adapter.py`: **21/21 PASS**; integration ถูก skip เพราะไม่มี optional `qdrant_client`

หมายเหตุ: handoff รายงาน `adapter 22` แต่ suite ปัจจุบันรายงาน **21/21** ควรแก้ตัวเลขเพื่อให้ evidence summary ตรงผลรัน

targeted probes เพิ่มเติม:

- actual scorer เห็น sentinel แต่ evidence อ้าง authorized → **ผ่านผิด (`[]`)**
- Plan/evidence run ID ต่างกัน → **ผ่านผิด**
- malformed receipt มี NaN → **public gate crash `ValueError`**
- finished timestamp ก่อน started → **ผ่านผิด (`[]`)**
- ambiguous argv lists → **command digest ชนกัน**
- point ID `1` กับ `"1"` → **component digest ชนกัน**

## Gate

| งาน | Verdict |
|---|---|
| ปิด B1-B3/M1-M3 + negative tests แบบ pure | **GO NOW** |
| เขียน real-path runner/injectable interlock/atomic writer | **FIX-THEN-GO** หลัง targeted re-review ผ่าน |
| M4a real run บน isolated Qdrant | **NO-GO** จน runner review + atomic failure controls ผ่าน |
| N-sweep | **NO-GO** จน validated M4a PASS |
| M4b/decision benchmark | **NO-GO** จน sign-off + M4b + validated canary/evidence ครบ |

## Final verdict

**FIX-THEN-GO runner.** RunPlan/receipt trust-anchor ส่วนใหญ่ทำงานแล้ว แต่ SpyScorer trace ยังแยกจาก identity ที่ evidence อ้าง จึงยังพิสูจน์ไม่ได้ว่า unauthorized text ไม่เคยถึง real model ปิด B1 ก่อนเป็นอันดับแรก แล้วปิด circular run ID กับ no-crash receipt boundaryก่อนเริ่ม runnerจริง
