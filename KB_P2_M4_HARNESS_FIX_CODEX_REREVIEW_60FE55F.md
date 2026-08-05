# Codex Re-review — P2 M4 harness fixes

**Commit reviewed:** `60fe55f`  
**Input:** `KB_P2_M4_HARNESS_FIX_HANDOFF.md`  
**Verdict:** **FIX-THEN-GO runner**  
**Scope:** pure/offline review + targeted probes; ไม่รัน Docker/Qdrant/model, ไม่แก้โค้ดหรือ `STATUS.md`

## สรุปก่อน

B1 เดิมฝั่ง candidate guard, B2, B3, M1 และ M3 ปิดได้ตามที่รายงาน: candidate ที่ไม่ authorized ถูกหยุดก่อน underlying scorer, `model_input_pairs` derive จาก scorer trace, run ID ผูก RunPlan, malformed receipt ไม่ทำ public gate crash, เวลา/argv/bytes ถูกตรวจเข้มขึ้น และ typed ID/vector hash ไม่ชนแบบเดิม

อย่างไรก็ตาม harness ยังสร้าง M4 PASS ที่ไม่ตรง execution จริงได้ 2 ทาง และ actual query ที่เข้า cross-encoder ยังไม่ถูกผูกกับ evidence จึงยังไม่ควรเริ่ม real-path runner บน contract ชุดนี้

## Findings

### B1 — `run_meta` ทับ security verdict ที่ validated แล้วให้กลับเป็น PASS ได้

**ตำแหน่ง:** `p2_m4_harness.py:140-153`

`assemble_evidence()` สร้าง security fields จาก `verdicts` ก่อน แต่ปิดท้ายด้วย `ev.update(run_meta)` โดยไม่มี allowlist หรือ collision guard ดังนั้น caller สามารถส่ง proof ที่ FAIL แล้วใส่ค่า PASS ซ้ำใน `run_meta` ได้ รวมถึงทับ `per_case`, `raw_evidence_sha256`, `schema_version`, `scorer_kind` และ `evidence_stage`

targeted probe ใช้ `verdicts` ดังนี้:

```text
status=FAIL
isolated_interlock=FAIL
independent_oracle=FAIL
sentinel_reached_model=True
unauthorized_in_model_inputs=1
```

แล้วใส่ค่าตรงข้ามผ่าน `run_meta`; ผลสุดท้ายคือ:

```text
VERDICT_AFTER_RUN_META = PASS/PASS/PASS/False/0
validate_m4_preflight_bundle(...) = []
```

จึงยังถือว่า M2 เดิม **ไม่ปิด** และเป็น false-PASS บน public approval surface

**ต้องแก้:**

- กำหนด exact allowlist ของ run metadata และ reject key ที่ชน protected/evidence fields;
- สร้าง protected fieldsหลัง metadata หรือประกอบ output แบบ explicit ห้ามใช้ unrestricted `dict.update()`;
- อย่ารับ `verdicts: dict` ลอย ๆ ใน real runner ให้รับ proof bundle ที่สร้างจาก validated `IsolationProof`, `OracleProof` และ finalized case traces เท่านั้น;
- เพิ่ม negative test: proof เป็น FAIL แต่ metadata พยายามทับเป็น PASS ต้อง raise หรือ gate fail

### B2 — actual query ที่เข้า cross-encoder เป็นค่าคงที่ `"m4"` และไม่อยู่ใน trace/evidence

**ตำแหน่ง:** `p2_m4_harness.py:75-87`; `p2_eval.py:93-101`

`M4Scorer.score_candidates()` รับเพียง `query_vector` แต่เรียก underlying ด้วย:

```python
self._s.score("m4", [txt for _, txt in candidates])
```

targeted scorer ยืนยันว่า `PinnedCrossEncoder` boundary จะเห็น query เป็น `'m4'` ไม่ใช่ query ของ case ขณะที่ evidence บันทึกเพียง hash ของ retrieval vector เท่านั้น

ดังนั้นข้อความว่า “model_input พิสูจน์ได้ว่าเป็นสิ่งที่เข้า cross-encoder จริง” ยังจริงเฉพาะ candidate text ไม่ครอบ input pair `(query text, candidate text)` ของ cross-encoder และ real runner จะ rerank ทุก case ด้วย query คงที่

**ต้องแก้:**

- ให้ QueryProbe มีทั้ง query text และ vector โดยเก็บ raw query เฉพาะใน memory ส่วน evidence เก็บ `query_text_sha256`;
- bind `query_text_sha256` กับ frozen case/RunPlan artifacts และ compare exact ใน validator;
- เปลี่ยน boundary เป็น `score_candidates(query_text, query_vector, candidates)` แล้วส่ง query text จริงเข้า underlying scorer;
- negative test ต้องยืนยัน fake scorer ได้ query ของ caseจริง และเปลี่ยน query textโดยคง vectorเดิมแล้ว gate fail

ถ้า M4a ตั้งใจทดสอบ permission mechanics ด้วย query คงที่จริง ต้องประกาศให้ชัดว่าเป็น synthetic probe ไม่ใช่ real case rerank และห้ามนำ score/ranking จาก run นี้ไปใช้เป็น quality evidence แต่ทางที่ตรงกับ runner/N-sweep มากกว่าคือผูก query text ให้ครบตั้งแต่ตอนนี้

### M1 — scorer trace ที่เรียกว่า immutable ยังแก้ย้อนหลังได้และทำหลักฐานต่างจาก model call จริง

**ตำแหน่ง:** `p2_m4_harness.py:65-87,104-137`

`components`, `pairs`, `scores`, `calls`, `query_vec_sha` และ `sentinel_reached` เป็น public mutable state ทั้งหมด `build_case_record()` อ่าน state เหล่านี้ภายหลังโดยไม่มี seal/consume boundary

targeted probe:

1. ให้ authorized set ที่ scorer รับมี sentinel แล้วเรียก modelด้วย sentinel textจริง;
2. หลัง call แก้ public trace ให้เป็น authorized pair;
3. สร้าง evidence/receipt ใหม่

ผลคือ:

```text
MODEL_SAW = ['ts']
validate_m4_preflight_bundle(...) = []
```

นี่ไม่ใช่การขอให้ Python ป้องกัน malicious process ทุกแบบ แต่ contract ปัจจุบันเปิดให้ runner reuse/mutate trace โดยไม่ตั้งใจ และ docstring ที่บอกว่า immutable ไม่ตรง implementation

**ต้องแก้:** ใช้ one-shot scorer ต่อ caseและคืน frozen `CaseTrace`/tuple หลัง score สำเร็จ; state ภายในเป็น private, ห้าม call ซ้ำ, validate output count + finite ก่อน seal และให้ `build_case_record()` consume finalized trace ก้อนเดียว การเรียกครั้งสองหรือแก้ trace หลัง finalize ต้อง fail test

### M2 — input validation เกิดหลัง underlying model callบางส่วน

**ตำแหน่ง:** `p2_m4_harness.py:75-86`

candidate components ถูก validateก่อน delegate แต่ query vector ถูก `_vec_hash()` หลัง `_s.score()` สำเร็จแล้ว ทำให้ malformed/non-finite QueryProbe ยังแตะ underlying modelก่อน harness fail ข้อนี้ควรปิดพร้อม B2: validate query text, vector, candidates, authorized set และ one-shot stateทั้งหมดก่อน delegate

## สิ่งที่ยืนยันว่าปิดแล้ว

- unauthorized/sentinel candidate ถูก guard ก่อน underlying scorer เมื่อ authorized set ถูกต้อง; negative testยืนยัน underlying call count = 0
- caller ไม่มี `model_input` parameter แยกใน `build_case_record()` แล้ว
- evidence/receipt run ID ที่ตรงกันเองแต่ไม่ตรง `plan.run_id` ถูก reject
- receipt ที่มี NaN คืน error listแทน exception
- `finished_utc < started_utc` ถูก reject
- argv hash ใช้ canonical list; stdout/stderr บังคับ bytes
- point ID `1` กับ `"1"` และ malformed/non-finite vectorไม่ collapse เป็น digest เดียว

## Verification

รันผ่านใน environment นี้:

- `test_p2_m4_harness.py`: **18/18**
- `test_p2_m4.py`: **39/39**
- `test_p2_runplan.py`: **95/95**
- `test_p2.py`: **166/166**
- `test_p2_adapter.py`: **21/21**; Qdrant integration ถูก skip เพราะไม่มี optional `qdrant_client`
- `test_p2_pin.py`: **14/14**
- `test_p2_dockerbuild.py`: **41/41**
- `test_policy.py`: **69/69**
- `test_eval_contract.py`: **64/64**
- `test_auth.py`: **11/11**; heavy app import ถูก skip เพราะไม่มี `anthropic`

`test_p2_provider.py` และ `test_p2_harness.py` รันไม่ได้ใน environment นี้เพราะไม่มี `qdrant_client`; จึงไม่อ้างว่า 2 suite นี้ผ่านจากการรันของ Codex รอบนี้

targeted probes เพิ่ม:

- bad proof ถูก `run_meta` ทับเป็น PASS → **public gate ผ่านผิด (`[]`)**
- underlying scorer ได้ query `'m4'` ทุก case → **ยืนยัน actual query ไม่ถูก wire**
- model เห็น sentinel แล้ว public trace ถูกแก้เป็น authorized → **public gate ผ่านผิด (`[]`)**

## Gate

| งาน | Verdict |
|---|---|
| ปิด B1/B2/M1/M2 + negative tests แบบ pure | **GO NOW** |
| เขียน real-path runner | **FIX-THEN-GO** หลัง targeted re-review ของ harness ผ่าน |
| M4a run บน isolated Qdrant | **NO-GO** จน runner/interlock/oracle/atomic-write review ผ่าน |
| N-sweep | **NO-GO** จน validated M4a PASS |
| decision benchmark | **NO-GO** จน Data Owner sign-off + M4b + validated canary/evidence ครบ |

## Final verdict

**FIX-THEN-GO runner.** การแก้รอบนี้ปิด findings เดิมได้เกือบทั้งหมด แต่ M2 ยัง bypass ได้ผ่าน `run_meta.update`, trace ยังแก้ย้อนหลังให้ต่างจากสิ่งที่ model เห็นจริงได้ และ cross-encoder ยังไม่ได้รับ query ของ case ปิดสามจุดนี้ก่อน แล้วค่อยเริ่ม runnerจริง จะรักษาหลัก “หลักฐานต้องมาจาก execution source เดียว” ได้ครบโดยไม่ต้องย้อน schemaอีกตอน M4a run
