# Codex Re-review — P2 Slice 2 infra fixes

**Commit reviewed:** `2364bb1`  
**Input:** `KB_P2_SLICE2_INFRA_FIX_HANDOFF.md`  
**Verdict:** **GO cross-encoder adapter / FIX-THEN-GO benchmark evidence wiring**

เขียน cross-encoder adapter และ pure scoring/metrics scaffolding ต่อได้โดยไม่ต้อง Docker แต่ยังห้ามถือว่า decision entry points ปิดแล้ว และยังไม่ควรเปิด Docker ทำ evidence run จนปิด B3.1, B3.2 และ M1.1 ด้านล่าง

## Intent / ทางเล็กที่สุด

เป้าหมายคือทำให้ mechanics smoke กับ decision benchmark ใช้เส้นทางเดียวกัน แต่ decision result ออกได้เฉพาะเมื่อมีหลักฐาน PASS จริง

ทางเล็กที่สุดไม่ใช่เพิ่ม boolean/string flags อีก ให้สร้าง validator ของ evidence summary สองชนิด (`M4Evidence`, `CanaryEvidence`) แล้วให้ decision manifest รับเฉพาะ summary ที่ validate แล้วหรือรับ immutable hash reference ส่วน smoke manifest ให้ใช้ structural validator ที่ยอม `draft/ai-reviewed` ได้โดยติดป้าย `approved=False` เสมอ

## Findings

### B3.1 — decision manifest ตรวจเพียง truthiness; evidence ที่ระบุ FAIL ยังอนุมัติได้

**ตำแหน่ง:** `p2_eval.py:310-330`, `test_p2.py:257-260`

`decision_benchmark_manifest()` ตรวจเพียง:

```python
if not m4_evidence: ...
if not canary_evidence: ...
```

จึงยอมรับ string ใด ๆ หรือ dict ที่ truthy โดยไม่ตรวจ status, sentinel result, leak count, auth state, arm coverage หรือการผูกกับ run/artifact hashes Test ปัจจุบันใช้ literal `"m4"`/`"canary"` เอง จึงไม่ exercise contract ที่ handoff อ้างว่า “real M4 + P5b canary PASS”

Codex probe บน artifacts ที่ผ่าน structural/sign-off gate ยืนยันว่า input ต่อไปนี้ยังสร้าง manifest `approved=True`:

```text
m4_evidence    = {status: FAIL, sentinel_reached_model: true}
canary_evidence = {status: FAIL, leak: 99, auth: UNVERIFIED}
result.approved = true
```

**ผลกระทบ:** caller สามารถสร้าง decision artifact ที่ดูอนุมัติแล้วโดย real M4/canary ล้มเหลว ตรงข้ามกับ NO-GO ที่กำหนดไว้

**Required change:** เพิ่ม schema/validator ที่บังคับอย่างน้อย:

- M4: exact `status=PASS`, isolated target/interlock PASS, independent-oracle PASS, `sentinel_reached_model=false`, candidate/model-input ID+text hash sets ไม่พบ unauthorized sentinel, model/revision/tokenizer/image/index/run hashesครบ
- Canary: exact `status=PASS`, `leak_count=0`, `auth_status=VERIFIED`, arms exact set `dense/rerank/fused`, ทุก arm PASS และผูก eval/corpus/index/run hashesเดียวกัน
- boolean/int/string ต้องตรวจ exact type ไม่ใช้ truthiness
- decision manifest เก็บ evidence digest/reference ที่ canonical และผูกกับ artifact hashes ไม่รับ arbitrary object/string

เพิ่ม negative tests สำหรับ truthy FAIL dict, wrong run/hash, arm ขาด, leak>0, auth UNVERIFIED และ sentinel ถึง model

### B3.2 — `artifact_manifest_unapproved()` ใช้ไม่ได้กับ eval set ปัจจุบันที่ยัง `ai-reviewed`

**ตำแหน่ง:** `p2_eval.py:356-370`, `p2_eval.py:231-237`, `p2_eval.py:108-109`

smoke manifest เรียก `validate_benchmark()` ซึ่งบังคับ `label_status="human-reviewed"` ทุก case ดังนั้น artifact ที่ออกแบบไว้สำหรับ mechanics smoke ก่อน Data Owner sign-off กลับสร้าง manifest ไม่ได้

Codex rerun กับ `p2_eval_set.json` ปัจจุบัน:

```text
artifact_manifest_unapproved(...) -> ValueError
132 errors: label_status ต้อง human-reviewed
```

**ผลกระทบ:** pure harness ที่กำลังจะเขียนไม่มี manifest entry point ที่ใช้กับสถานะจริง หากแก้ด้วยการเปลี่ยน labels เป็น `human-reviewed` เพื่อให้ smoke รันได้ จะข้าม B6.1

**Required change:** แยก validation policy โดยไม่ duplicate logic เช่นเพิ่ม parameter allowlist ให้ ranking validator:

- mechanics smoke: ยอม `draft/ai-reviewed/human-reviewed`, แต่ output ต้อง `kind=mechanics-smoke-unapproved`, `approved=False`, `decision_eligible=False`
- decision: ยอมเฉพาะ `human-reviewed` และต้องผ่าน combined sign-off/evidence gate

เพิ่ม test ด้วย artifacts `ai-reviewed` จริงว่า smoke manifest สร้างได้แต่ไม่สามารถเปลี่ยนเป็น approved/decision ได้

### M1.1 — sign-off malformed field types ยังผ่าน แม้ handoff ระบุ M1 ปิดแล้ว

**ตำแหน่ง:** `p2_eval.py:272-292`, `test_p2.py:243-250`

การ wrap hashing ปิด crash ของ `None`/NaN แล้ว แต่ fields อื่นตรวจแค่ truthy ผ่าน `signoff.get(f)` ตัวอย่างที่ Codex probe แล้ว `validate_signoff()` คืน `[]`:

```text
git_commit     = true
reviewer       = 123
data_owner_role = ["owner"]
reviewed_at    = {"not": "time"}
decision       = "approved"
hashes         = valid
```

**ผลกระทบ:** durable evidence มี metadata ที่ serialize ได้แต่ไม่มีความหมายและไม่สามารถ audit ผู้อนุมัติ/เวลา/commit ได้อย่างเชื่อถือได้

**Required change:** ตรวจ exact non-blank strings/no control chars, commit เป็น hex commit ID ที่ยอมรับ, `reviewed_at` เป็น ISO-8601 พร้อม timezone, decision exact enum และเพิ่ม negative tests ของ bool/list/dict/control chars คงหลักเดิมว่า code ตรวจ shape/hash ได้ แต่การยืนยันว่าเป็นมนุษย์จริงยังเป็น process/signature gate นอก code

## Findings เดิม

| Finding | Re-review |
|---|---|
| B1 access invariant | **CLOSED สำหรับ pure boundary** — verified enforce + known/in-scope role ถูกบังคับ; registry provenance ยังเป็น composition gate ตอนต่อ auth จริง |
| B2 authorization postcondition | **CLOSED** — returned payload ต้อง policy-v1, stored shape valid และ match compiled access; mismatch fail ทั้ง batch |
| B3 single decision entry | **PARTIAL** — แยกชื่อ entry pointแล้ว แต่ evidence truthiness ทำให้ decision bypass ยังมีอยู่ และ smoke entry ใช้กับ AI labels ไม่ได้ |
| M1 malformed artifact crash | **PARTIAL** — artifacts short-circuit/hash errors controlled; sign-off field schema ยังไม่ปิด |
| M2 payload/parameter edges | **CLOSED สำหรับ scope นี้** — strict strings/source/score, positive limits และ top-N cap ครบตาม finding เดิม |

## Independent verification

- `test_p2.py` — **145/145 PASS**
- `test_p2_provider.py` — **22/22 PASS** ของ pure logic โดย inject stub เฉพาะ Qdrant model classes
- regressions — policy **69/69**, eval contract **64/64**, harness **12/12**, P5b fixtures **11/11**, auth **11/11** (heavy app import skip เดิม 1 จุดเพราะ host ไม่มี `anthropic`)
- targeted provider probes เดิมถูกปิด: unverified/warn/role mismatch และ backend sales-only→qc ถูก reject
- targeted manifest probes ยืนยัน B3.1/B3.2
- targeted sign-off type probe ยืนยัน M1.1

ไม่มี Docker/model/Qdrant จริงถูกเปิดหรือแตะ และไม่ได้แก้ code/`STATUS.md`

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| เขียน pinned cross-encoder adapter แบบ pure/injectable + unit tests | **GO** |
| เขียน scorer/metrics/N-sweep harness scaffolding | **GO** — output ต้องเป็น unapproved เท่านั้นจน B3.2 ปิด |
| wire harness เข้ากับ `decision_benchmark_manifest()` ปัจจุบัน | **NO-GO** — B3.1 ยัง fail-open |
| แก้ B3.1/B3.2/M1.1 | **GO NOW** — เป็น pure change ไม่ต้อง Docker |
| เปิด Docker/build model/ทำ real M4 + N sweep evidence run | **FIX-THEN-GO** หลังสาม finding ปิด |
| เลือก N/freeze/arm verdict/decision benchmark | **NO-GO** จน Data Owner sign-off + validated real M4 PASS + validated canary PASS |
| production/deploy/cloud/ข้อมูลบริษัทจริง | **NO-GO** ตาม gates เดิม |

## Final verdict

**GO adapter code, FIX-THEN-GO evidence harness.** Access และ candidate boundary ปิดจริงแล้ว แต่ decision gate ยังอนุมัติ evidence ที่ล้มเหลวได้ ซึ่งเป็นเหตุหลักที่ยังไม่ควรเปิด Docker สร้างผล benchmark รอบจริง
