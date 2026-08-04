# Codex Review — KB Next Steps (Knowledge Brain / Module 12)

**วันที่:** 2026-08-04  
**Review target:** `KB_NEXT_STEPS.md` เทียบกับ `STATUS.md` และเส้นทางจริงใน `app/main.py`, `rbac_config.py`, `ingest.py`, `ask_eval.py`  
**ขอบเขต:** review/design เท่านั้น — ไม่แก้โค้ด, ไม่แตะ Qdrant, ไม่แก้ `STATUS.md`, ไม่ deploy

## Verdict

**REORDER-THEN-GO** — ทิศทาง P1 → P2 ถูก แต่ต้องแก้ premise ของ P1 และทำ evaluation harness ชุดเล็กก่อน ไม่ควรเริ่มจากการเอา `role AND confidentiality AND group` ใส่ Qdrant filter ตรง ๆ

ประเด็นหลักคือ ปัจจุบัน `allowed_roles` ไม่ใช่เพียง metadata มิติหนึ่ง แต่มันคือ **effective ACL ที่คำนวณจาก collection ตอน ingest** (`rbac_config.py:9-60,123-129`, `ingest.py:204-214`) ส่วน `confidentiality_level` เป็น classification ของเอกสาร ยังไม่มี `clearance` ของ caller ให้เทียบ และ payload จริงยังไม่มี `department`/`allowed_groups` เลย ดังนั้นเพิ่ม AND หลายมิติตอนนี้อาจล็อกข้อมูลผิดหรือสร้างความปลอดภัยลวง ๆ มากกว่าจะปิดช่องรั่ว

## Findings ที่ต้องแก้ในแผน

### B1 — Security claim ยังใช้ไม่ได้ขณะ `AUTH_MODE=warn`

`check_service_auth()` พบ key หาย/ผิด/role เกิน scope แล้วเพียง log แต่ยังปล่อยผ่านใน warn mode (`app/main.py:131-162`) ขณะเดียวกัน role `admin` ทำให้ `make_rbac_filter()` คืน `None` และค้นแบบไม่มี payload filter (`app/main.py:165-170`)

ผลคือ caller ที่ไม่มี key ยังส่ง `role=admin` ได้ในระบบที่รันอยู่ รวมถึงเรียก `/collections?role=admin` ได้ด้วย การเพิ่ม confidentiality/group filter ให้ non-admin ไม่ปิดช่องนี้

**ข้อเสนอ:** ขยาย P1 ให้รวม authorization cutover readiness ด้วย:

- synthetic tests ต้องพิสูจน์ missing/invalid/out-of-scope key → 401/403 ใน enforce mode
- เก็บ auth telemetry แบบไม่เก็บ key/query/content เพื่อรู้ว่า Voicebot พร้อม flip หรือยัง
- ห้ามประกาศ permission leakage = 0 สำหรับระบบจริงจน server เปลี่ยนเป็น `AUTH_MODE=enforce`
- หลังเพิ่ม ACL schema แล้ว แม้ admin ก็ควรใช้ explicit filter (`allowed_roles` มี admin + schema/policy status ถูกต้อง) แทน `None` เพื่อไม่ให้เอกสาร tag หายหลุดผ่าน bypass

การ flip server จริงยังเป็น deploy action และอยู่นอก scope รอบนี้ แต่ contract/test/telemetry ทำ local ได้

### B2 — P1 ต้องแยก “policy decision” ออกจาก “enforcement point”

Qdrant filter เหมาะเป็น enforcement point ก่อน retrieval แต่ไม่ควรเป็นที่นิยาม policy จาก request body โดยตรง ลำดับที่ถูกคือ:

1. authenticated service/user claims จากฝั่ง server
2. policy layer คำนวณ effective access เช่น roles/groups/clearance
3. compile เป็น Qdrant payload filter
4. Qdrant ค้นเฉพาะ candidate ที่ได้รับอนุญาต
5. reranker เห็นเฉพาะ candidate ที่ผ่านข้อ 4 แล้ว

สำหรับ PoC ปัจจุบัน ให้ `allowed_roles` เป็น canonical effective ACL ต่อไปก่อนก็เพียงพอ เพราะ `confidentiality_level` ถูกใช้สร้าง role list อยู่แล้ว ส่วน classification ควรนำไปใช้กับ **egress policy** มากกว่าบังคับ reader ACL ซ้ำอีกรอบ

ห้ามเพิ่ม `allowed_groups`/`department` จนกว่าจะตอบ semantics ให้ชัดว่าเป็น AND หรือ OR, ใครเป็น owner ของ mapping และ caller ได้ claim เหล่านั้นจากแหล่งที่เชื่อถือได้อย่างไร

### B3 — Permission eval ปัจจุบันสามารถ “เขียวผิดเหตุผล”

`ask_eval.py:31-38` ไม่ส่ง `X-API-Key`; เมื่อ API flip เป็น enforce ทุก probe จะได้ 401 แต่ `ask_eval.py:87-100` จับ error แล้วแทน response ด้วย citations ว่าง จากนั้นนับว่าไม่รั่วได้ นอกจากนี้ยังตรวจเพียง keyword ในชื่อ source ไม่ได้ตรวจ point/document identity, `content` หรือข้อความ answer

ดังนั้น baseline `0/5` เป็น smoke test ที่มีประโยชน์ แต่ยังไม่ใช่หลักฐาน permission leakage = 0

**Acceptance criteria ของ P5a/P1 test:**

- HTTP/auth error ต้องทำให้ suite fail ไม่ใช่นับเป็น no-leak
- ใช้ valid key ที่ scope ตาม role ของแต่ละ case
- synthetic corpus มี canary ลับเฉพาะ role/group/classification
- assert ว่า IDs ของทุก retrieved point เป็น subset ของ allow-set
- ตรวจทั้ง `/search.results[].content`, `/ask.answer` และ citations
- ครอบทุก role × policy class รวม missing/malformed ACL, `UNCLASSIFIED`, admin และ role spoofing
- แยกผล `DENIED`, `NO_RESULT`, `ERROR` ออกจากกัน ห้าม collapse เป็น citations ว่างเหมือนกันหมด

### B4 — P3 ห้าม persist raw query/context เป็นค่า default

ข้อเสนอเดิมให้เก็บ `query` และ “สิ่งที่ส่งไป Claude” จะสร้างคลังข้อมูลลับสำเนาที่สอง และเพิ่ม blast radius/retention burden โดยไม่จำเป็นสำหรับ audit ทั่วไป

ค่า default ควรเก็บเฉพาะ:

- request/trace ID, timestamp, authenticated service/user reference
- requested/effective role และ policy version
- endpoint, provider/model, decision (`ALLOW`, `DENY`, `REDACT`, `LOCAL_ONLY`)
- retrieved point/document IDs, classification สูงสุด, context byte/token count
- hash ของ query/context และ outcome/latency/token usage
- error code ที่ sanitize แล้ว ห้าม raw provider error/content

raw query/context ให้เก็บได้เฉพาะ debug flow ที่อนุมัติแล้ว มี redaction, access control, retention/expiry และ sampling ชัดเจน

### M1 — Data egress ไม่ได้มีเฉพาะ `/ask → Claude`

`/search` คืน `content=build_content(payload)` ให้ caller โดยตรง (`app/main.py:271-310`) ดังนั้นมันเป็น outbound data boundary เช่นกัน และ Brain ไม่รู้ว่า Voicebot จะส่ง content ต่อไป provider ใด

P4 จึงควรครอบสองเส้นทาง:

- **Provider egress:** `/ask` ส่ง question + parent context ไป Claude (`app/main.py:347-357`)
- **Consumer egress:** `/search` ส่ง raw parent content ให้ service caller

Authorization ว่า “อ่านได้” ไม่เท่ากับอนุญาต “ส่งออก Cloud ได้” ต้องมี egress decision แยกจาก reader ACL และใช้กับ admin ด้วย หาก service จะรับ raw content ควรมี capability/egress profile ที่กำหนดจาก server registry ไม่ใช่ request body

### M2 — ค่า citation 92% ยังเป็น retrieval hit ไม่ใช่ citation accuracy

`/ask` คืน citation ของ retrieved point ทุกตัว ไม่ว่า answer จะใช้ `[n]` จริงหรือไม่ (`app/main.py:370-382`) ตรงกับ backlog ใน `STATUS.md:215-217` ดังนั้นอย่าใช้ metric นี้ตัดสินผล rerankerในชื่อ “citation accuracy” จนกว่าจะมี citation integrity guard

ก่อน benchmark P2 ต้องอย่างน้อย:

- parse `[n]` ที่ปรากฏใน answer
- reject/flag reference นอกช่วง
- คืนหรือประเมินเฉพาะ citation ที่ answer อ้างจริง
- แยก `retrieval source hit`, `citation-reference validity` และ `answer faithfulness` เป็นคนละ metric

### M3 — Reranker ทำได้ใน PoC แต่ต้องไม่เปลี่ยน live contract เงียบ ๆ

วาง rerankerใน shared retrieval pipeline ไม่ใช่ใน `generate_answer()` เพราะ `/search` และ `/ask` ต้องได้ ranking logic เดียวกัน:

`authenticate → compile policy → Qdrant retrieve candidate_k → rerank authorized candidates → top_n → /search หรือ /ask`

เงื่อนไขสำหรับ P2:

- Qdrant ACL filter ต้องทำก่อน rerank เสมอ
- rerank child text/heading ที่ใช้ค้น แล้วค่อยใช้ `parent_text` สำหรับตอบ
- เริ่ม offline/shadow หรือ feature flag เพราะ `/search` มี Voicebot ใช้อยู่แล้ว
- คง `score` เดิมเป็น vector score หรือเพิ่ม `rerank_score` แบบ additive; ห้ามเปลี่ยนความหมาย field เดิมเงียบ ๆ
- วัด latency p50/p95, memory และ concurrency บนเครื่องที่จะรันจริง; inline CPU รับได้สำหรับ PoC ถ้าผ่าน budget แต่ production ค่อยแยก process/GPU
- ใช้ candidate pool มากกว่า output เช่น retrieve 20 แล้วคืน 3-4; ค่าจริงเลือกจาก eval ไม่ hard-code จากความรู้สึก

## ลำดับงานที่แนะนำ

### 0. P5a — Repair measurement contract ก่อน (เล็ก, $0)

ไม่ต้องทำ P5 เต็มก้อนก่อน แต่ให้ปิด test ที่เขียวผิดเหตุผล, เพิ่ม valid service key, แยก error/no-result/deny และทำ citation integrity guard จาก backlogเดิม จากนั้น snapshot dense baseline ปัจจุบัน

### 1. P1 revised — Auth + ACL schema/filter hardening

- นิยาม `acl_schema_version`/policy status และ valid payload contract
- non-empty, known `allowed_roles`; missing/malformed tag → quarantine/default-deny
- admin ใช้ explicit ACL filter ไม่ bypass ทุก payload
- policy compiler รับ trusted principal แล้วค่อยสร้าง Qdrant filter
- synthetic matrix test ตาม B3

ยังไม่ต้องเพิ่ม group/department จน business semantics และ trusted claims พร้อม

### 2. P2 — Reranker แบบ offline/shadow

วัด dense baseline เทียบ dense→rerank ด้วย eval ชุดเดียวกันก่อนเปิดให้ Voicebot จากหลักฐานเดิมที่ miss 7 ข้อเป็น sibling-document confusion งานนี้มีเหตุผลรองรับและควรเป็น quality slice ถัดจาก security contract

ควรเพิ่ม experiment อีกหนึ่งแขนคือ **dense+sparse hybrid** เพราะ architecture ระบุ BGE-M3 dense+sparse/Qdrant hybrid แต่โค้ดจริงใช้ dense vector เดียว (`ingest.py:163-183`, `app/main.py:284-292`) โดยเฉพาะรหัส QP/WI และตัวเลขอาจได้ประโยชน์จาก exact-token retrieval อย่าล็อกว่ารerankerต้องชนะก่อนวัด

### 3. P5b — Full evaluation และ Go/No-Go report

รัน dense vs hybrid vs rerank ด้วย corpus/query/policy เดียวกัน วัด retrieval, citation integrity, no-answer, permission matrix, faithfulness, latency และ cost แล้วใช้ผลตัดสิน hardware/วิธี deploy

### 4. P4 + P3 — Release gates ก่อนข้อมูลจริงหรือ deploy

ออกแบบ egress decision ให้ครอบ `/ask` และ `/search` แล้ว persist **metadata-only audit** ของ decision หากจะรับ PII/trade secret ต้องปิดสองข้อนี้พร้อม auth จริง/approval ก่อน ไม่ควรใช้ “flag+log แต่ยังส่ง” เป็น fail-safe สำหรับระดับ local-only

## คำตอบตรง 4 คำถาม

1. **P1 ก่อน P2 ถูกไหม?** — ถูกหลังทำ P5a ขนาดเล็กเพื่อให้วัดผลไม่เขียวผิดเหตุผล; ไม่ต้องรอ P5 เต็มก้อน
2. **Qdrant filter หลายมิติพอไหม?** — Qdrant เป็น enforcement point ที่ถูก แต่ต้องมี server-side policy compiler ก่อน ปัจจุบัน effective `allowed_roles` พอสำหรับ reader ACL; classification ใช้ egress gate และยังไม่ควรสร้าง group/clearance ที่ไม่มี trusted claims
3. **Reranker ต่อ request รับได้ไหม?** — รับได้สำหรับ PoC แบบ feature-flag/shadow เมื่อ ACL filter มาก่อน และวัด latency/memory จริง ห้ามใส่ใน `generate_answer()` หรือเปลี่ยน `/search.score` เงียบ ๆ
4. **Gap อะไรควรมาก่อน?** — auth warn/admin bypass, leakage test false-pass, citation integrity, `/search` egress และ no-match threshold ที่มีใน `eval.py` แต่ยังไม่ใช้ใน `/ask` สำคัญกว่าปรับ chunk/Thai normalization ตอนนี้ Parent-child มีแล้ว; Thai normalization ยังขาดจาก ingestion แต่หลักฐาน failure ล่าสุดชี้ sibling confusion/OCR/table มากกว่า จึงเก็บเป็น experiment หลัง error analysis

## Go/No-Go สำหรับ Claude

**GO งานถัดไป:** ทำ P5a + P1 revised แบบ local/synthetic โดยเริ่มจาก test contract และไม่แตะ Qdrant จริง  
**NO-GO:** เพิ่ม `role AND confidentiality AND group` โดยยังไม่มี trusted caller claims/semantics, persist raw query/context, หรือเปิด rerankerกับ `/search` live ทันที  
**Deploy/real data:** ยัง NO-GO ตาม gate เดิม

## Verification note

Review นี้ trace จาก source ปัจจุบันและเอกสารผล eval; ไม่มีการแก้/รันระบบจริง การลองเรียก `test_auth.py` ใน workspace นี้ไม่สำเร็จเพราะ Windows Python launcher ถูก sandbox ปฏิเสธ จึงไม่ได้อ้างว่ารัน tests ใหม่ในรอบนี้
