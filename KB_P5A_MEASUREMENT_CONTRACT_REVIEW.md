# Codex Review — P5a Measurement Contract (`5ba9242`)

**วันที่:** 2026-08-04  
**Review target:** `KB_P5A_MEASUREMENT_CONTRACT_HANDOFF.md` และ implementation ใน commit `5ba9242`  
**ขอบเขต:** review เท่านั้น — ไม่แก้ implementation, `STATUS.md` หรือ Qdrant

## Verdict

**FIX-THEN-GO** — pure helpers แก้การ collapse 401/403 เป็น citations ว่างได้ระดับหนึ่ง แต่ปลายทาง suite ยัง exit 0 เมื่อทุก request ถูก deny/no-result และ outcome model ปัจจุบันรวม transport กับ retrieval จน no-answer metric วัดผิดแกน จึงยังใช้เป็น gate ของ P1 ไม่ได้

ทางที่เล็กและชัดกว่าคือใช้ **`/search` + synthetic canary manifest** เป็น permission-leakage harness หลัก ($0, deterministic, ไม่ผ่าน LLM) แล้วใช้ `/ask` แยกวัด citation integrity/no-answer/egress ไม่ต้องบังคับสองโจทย์ให้อยู่ใน verdict เดียว

## Findings

### B1 — `DENIED/NO_RESULT` ยังทำให้ suite เขียวได้ จึงยังไม่ปิดบั๊กเดิม

**Finding:** `ask_eval.py:152-156` ตั้ง `broken` จาก `LEAK`, probe `ERROR` และ main `ERROR` เท่านั้น แต่ไม่รวม probe `DENIED/NO_RESULT` หรือ main `DENIED/NO_RESULT`

**Why it matters:** ใน `AUTH_MODE=enforce` ถ้าไม่ใส่ key ทุก request ได้ 401, `classify_outcome()` คืน `DENIED`, แต่ `broken == 0` และ process exit 0 เช่นเดิม CI จึงยังสามารถรายงานเขียวเพราะ auth ปฏิเสธ harness ทั้งชุด

กรณี has-answer ทุกข้อได้ 200 แต่ retrieval ว่างก็ exit 0 เช่นกัน เพราะ cases เหล่านั้นไม่เข้า `has`/`noa` และไม่เข้า `r_errs`

**Evidence:**

- `eval_contract.py:30-34` จำแนก 401/403 เป็น `DENIED` และ 200+0 citations เป็น `NO_RESULT`
- `ask_eval.py:130-134` นับ `r_denied` แต่ไม่เคยนับ main `NO_RESULT`
- `ask_eval.py:153` ไม่ใช้ `r_denied`, `pv[DENIED]` หรือ `pv[NO_RESULT]` ตัด exit code
- unit tests `test_eval_contract.py:33-47` ทดสอบเฉพาะ helper verdict ไม่ได้เรียก `main()` หรือ assert process exit

**Suggested change:** เพิ่ม end-to-end harness tests ที่ monkeypatch `call()` แล้ว assert `SystemExit.code` อย่างน้อย:

- ทุก main/probe เป็น 401 → non-zero
- ทุก has-answer เป็น 200+empty → non-zero/quality failure
- probe transport error/429/500 → non-zero
- suite ที่มี positive/negative controls ครบและไม่มี leak เท่านั้นจึง exit 0

อย่าแก้แค่เติม `DENIED/NO_RESULT` ลง `broken` โดยไม่ทำ B2 เพราะ `NO_RESULT` ไม่ใช่ failure เสมอไป

### B2 — 4-way outcome รวมคนละแกน ทำให้ no-answer cases ถูกตัดออกจาก metric

**Finding:** `classify_outcome()` ใช้จำนวน citation เป็นตัวตัด `OK` กับ `NO_RESULT` (`eval_contract.py:21-34`) ทั้งที่ HTTP 200 และ retrieval ว่างยังเป็น transport success และเป็น expected behavior ที่ถูกต้องได้สำหรับ no-answer case

**Why it matters:** `/ask` เส้นทาง no points ตอบ HTTP 200 พร้อมข้อความ “ไม่พบข้อมูล...” และ citations ว่าง (`app/main.py:345-350`) แต่ harness จัดเป็น `NO_RESULT`; จากนั้น `ask_eval.py:130-146` ประเมิน no-answer honesty เฉพาะ outcome `OK` เท่านั้น ผล no-answer ที่ถูกต้องจึงหายจาก denominator

**Suggested change:** แยกอย่างน้อยสองแกน:

- `transport_outcome = SUCCESS | DENIED | ERROR | MALFORMED_RESPONSE`
- `retrieval_outcome = HAS_RESULTS | NO_RESULTS`

แล้วใช้ expected case ตัดสิน:

- `has_answer`: ต้อง transport success และมี retrieval hit ตาม expected identity
- `no_answer`: transport success; no-results หรือคำตอบปฏิเสธอย่างซื่อสัตย์อาจเป็น pass ตาม contract
- permission negative probe: transport ต้อง success; ผล filtered/no forbidden point จะ pass ได้ต่อเมื่อ positive control ของ canary เดียวกันพิสูจน์ว่าระบบค้น canary เจอจริง

### B3 — Expected allow-set มาจาก source เดียวกับ policy ที่ทดสอบ จึงจับ policy misconfiguration ไม่ได้

**Finding:** `ROLE_COLLECTIONS` ถูกสร้างโดย invert `rbac_config.COLLECTIONS` (`ask_eval.py:28-32`) ซึ่งเป็น configuration เดียวกับที่ ingestion ใช้สร้าง `allowed_roles` (`ingest.py:204-214`)

**Why it matters:** ถ้ามีคนแก้ config ผิดให้ `qc` อ่าน `SALES` ได้ ทั้งระบบและ expected allow-set จะเปลี่ยนพร้อมกัน แล้ว test ยัง CLEAN นี่พิสูจน์เพียง “implementation สอดคล้องกับ config” ไม่ได้พิสูจน์ว่า config สอดคล้องกับสิทธิ์ที่ธุรกิจอนุมัติ

**Suggested change:** ใช้ independent, reviewed fixture เช่น `permission_eval_cases.json` ซึ่งระบุ:

- synthetic `point_id`/document ID
- authorized roles/groups/clearance ที่คาดหวัง
- positive role และ forbidden roles
- unique canary token

fixture ต้องไม่ generate จาก `rbac_config.py` ตอนรัน test ส่วนการ invert config เก็บไว้เป็น configuration-conformance test แยกอีกชุดได้

### B4 — Claim ว่าเช็ค point identity ยังไม่ตรงกับ implementation

**Finding:** `eval_contract.py:10` และ comment ใน response schema อธิบายว่า assert point ID subset แต่ `ask_eval.py:111-113` ส่ง `collection` เข้า `leak_verdict()` จริง; `point_id` ที่เพิ่มใน `app/main.py:219,243` ถูกเก็บใน raw reportเท่านั้น (`ask_eval.py:117-118`)

**Why it matters:** collection-level check จับ point/document exception ไม่ได้ และจะใช้ไม่ได้ทันทีเมื่อ P1 มี ACL ละเอียดกว่า collection เช่น group, document override หรือ quarantine status

**Suggested change:** permission oracle ต้อง map `point_id → expected principals` จาก synthetic manifest และ assert IDs โดยตรง ใช้ collection เป็น diagnostic field ไม่ใช่ security identity

ไม่ควรผูก `confidentiality_level` เพิ่มตอนนี้ เพราะยังไม่มี trusted caller clearance และ semantics ตาม review ก่อนหน้า

### M1 — Positive control เป็น requirement ไม่ใช่ optional improvement

**Finding:** ทั้งห้า probe เป็น negative query อย่างเดียว (`ask_eval.py:34-41`) ไม่มีการพิสูจน์ว่า query/canary เดียวกันถูก role ที่มีสิทธิ์ค้นเจอ

**Why it matters:** ถ้า corpus ไม่มีเอกสาร, embedding/query หาไม่เจอ, payload เสีย หรือระบบ deny ทุกคน negative probes จะไม่เห็น secret เหมือนกัน จึงแยก policy filter ที่ถูกจากระบบพังไม่ได้

**Suggested change:** synthetic canary แต่ละตัวต้องมีคู่:

1. authorized role + valid scoped key → ต้องพบ exact point ID/canary
2. unauthorized role + valid scoped key → ต้องไม่พบ exact point ID/canary

แนะนำให้ทำคู่นี้ผ่าน `/search` ก่อน เพราะไม่เสียค่า LLM และตรวจ `content`/point ID ได้ตรง จากนั้นมี `/ask` probe เพิ่มเฉพาะ egress/answer canary

### M2 — API key ตัวเดียวไม่พิสูจน์ role-scope end-to-end

**Finding:** `ask_eval.py:67` รับ `KB_EVAL_API_KEY` เพียงค่าเดียว แต่ permission probes ใช้หลาย role (`ask_eval.py:107-108`)

**Why it matters:** ถ้า key ไม่ scope ครบ ทุก role อื่นจะเป็น `DENIED`; ถ้าใช้ key กว้างครบทุก role จะพิสูจน์ retrieval filter ได้ แต่ไม่พิสูจน์ว่า service role-scope ป้องกัน role spoofing

**Suggested change:** แยก test สองชั้นอย่างชัดเจน:

- auth contract: key A ขอ role นอก scope → ต้อง 403
- retrieval contract: role แต่ละตัวใช้ valid key ที่ scope role นั้น หรือ role→key mapping สำหรับ eval

ควรมี preflight ว่า key/case matrix พร้อมก่อนเริ่ม expensive `/ask` loop เพื่อไม่เสียเวลาแล้วได้ผล inconclusive ทั้งชุด

### M3 — Response/JSON failure บางชนิดไม่ผ่าน outcome classifier

**Finding:** `call()` จับเฉพาะ `HTTPError`, `URLError`, `TimeoutError`, `OSError` (`ask_eval.py:44-56`) แต่ `json.load()` อาจโยน `JSONDecodeError`/`UnicodeDecodeError`; response ที่เป็น JSON แต่ไม่ใช่ object จะพังเมื่อเรียก `resp.get()`

**Why it matters:** process จะ non-zeroจริง แต่ไม่มี structured `ERROR`, ไม่มี raw report และขัด claim ว่า “ทุกการเรียก” ถูกจำแนกเป็น contract เดียว ทำให้วิเคราะห์ partial body/proxy errorยาก

**Suggested change:** validate HTTP 200 response shape ก่อนใช้; invalid JSON, wrong top-level type, missing/non-list citations/results หรือ malformed citation ให้ `MALFORMED_RESPONSE`/`ERROR` โดยไม่เก็บ raw body ที่อาจมีข้อมูลลับ

### M4 — Retrieval-hit สามารถเป็น true เมื่อ citation source ว่าง

**Finding:** `ask_eval.py:91-94` ใช้ `any(exp in s or s in exp ...)`; ถ้า `s == ""` และ `exp` ไม่ว่าง เงื่อนไข `s in exp` เป็น true

**Why it matters:** payload/citation ที่ source หายสามารถถูกนับเป็น expected-source hit ได้ ทำให้ retrieval metricสูงเกินจริง

**Suggested change:** ใช้ canonical document ID/exact expected source เป็นหลัก อย่างน้อยต้อง reject blank source ก่อน substring comparison และเพิ่ม regression test `source="" → miss/error`

### N1 — Test command ไม่ portable บน Windows cp874

**Finding:** `test_eval_contract.py` print อักขระ `→`; เมื่อรัน `py -3 test_eval_contract.py` ใน workspace Windows ปัจจุบันเกิด `UnicodeEncodeError` ก่อนจบ test

**Evidence:** rerun รอบ review นี้ล้มด้วย cp874; เมื่อตั้ง `PYTHONUTF8=1` แล้วจึงได้ **18/18 passed**

**Suggested change:** ตั้ง stdout UTF-8 ใน test เหมือน `eval.py`, ใช้ test runner ที่ capture Unicode หรือเปลี่ยน output เป็น ASCII เพื่อให้คำสั่งใน doc รันได้ตรงทุกเครื่อง

## คำตอบ 4 ข้อใน handoff

1. **Contract ครบไหม?** — ยังไม่ครบ ต้องปิด B1-B4; 429/5xx เป็น ERROR ถูกทิศ แต่ malformed/partial JSON ยังไม่ถูก normalize และ transport/retrieval ต้องแยกแกน
2. **Collection allow-set ถูกไหม?** — ใช้เป็น diagnostic/config-conformance ได้สำหรับ model ปัจจุบัน แต่ยังไม่ใช่ independent security oracle และไม่ควรเพิ่ม confidentiality จนมี trusted clearance; ใช้ synthetic point manifest เป็นหลัก
3. **เพิ่ม negative/positive control ไหม?** — ต้องเพิ่ม **positive control คู่ทุก negative probe** ถือเป็น gate ไม่ใช่ optional
4. **GO P1 ไหม?** — **NO-GO ต่อ implementation P1 ตอนนี้**; ร่าง policy contract ได้ แต่ต้องปิด B1-B4/M1 ก่อน และ P1 ต้องเป็น auth + policy compiler + effective ACL fail-closed ตาม review เดิม ไม่ใช่เพิ่ม `role AND confidentiality AND group` ตรง ๆ

## Minimum acceptance ก่อน GO P1

- [ ] all-DENIED/all-NO_RESULT ไม่สามารถ exit 0 แบบไร้ positive control
- [ ] transport outcome แยกจาก retrieval outcome
- [ ] independent synthetic point manifest ไม่ derive expected policy จาก `rbac_config.py`
- [ ] `/search` positive/negative pair ต่อ canary ด้วย valid role-scoped keys
- [ ] point ID เป็นตัวตัด leak จริง; missing policy fields fail/inconclusive
- [ ] malformed JSON/response shape → structured ERROR และ non-zero
- [ ] blank source ไม่เป็น retrieval hit
- [ ] test `main()`/exit behavior เพิ่มจาก pure helper tests

**Final verdict:** **FIX-THEN-GO** — จุดใหญ่สุดคือ CI ยัง exit 0 ได้เมื่อ auth deny ทั้งชุด จึงยังไม่ควรใช้ P5a วัดหรือรับรอง P1

## Verification

- Static trace: request → `call()` → `classify_outcome()` → case filtering → `leak_verdict()` → `broken` → `sys.exit()`
- `PYTHONUTF8=1; py -3 test_eval_contract.py` → **18/18 passed**
- ไม่ได้เรียก API/Qdrant และไม่ได้แก้ implementation หรือ `STATUS.md`
