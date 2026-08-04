# Codex Review — P2 synthetic eval-set draft

**Commit reviewed:** `241da10`  
**Artifacts:** `p2_build_eval_set.py`, `p2_corpus.json`, `p2_eval_set.json`, `KB_P2_EVALSET_DRAFT.md`  
**Verdict:** **REWORK-BEFORE-HUMAN-REVIEW — ห้ามเลื่อนเป็น `human-reviewed` และห้ามใช้เลือก arm ตอนนี้**

ชุดนี้ใช้เริ่ม plumbing/mechanics smoke ได้ แต่ยังไม่รองรับ N sweep, paired CI หรือ arm-eligibility acceptance ที่ล็อกไว้

## Intent / simpler alternative

เป้าหมายคือสร้างชุด synthetic ที่พิสูจน์ candidate filtering, reranker ordering และ evidence pipeline ก่อนใช้ข้อมูลจริง แนวทาง generator deterministic ถูกต้องและเล็กกว่าการดึง corpus บริษัท แต่ควรแยกสามชั้นให้ชัด:

1. **mechanics corpus** สำหรับ M4/filter/scorer plumbing
2. **ranking decision set** ที่มี candidate depth และ independent intents เพียงพอ
3. **human sign-off record** สำหรับ labels

ไฟล์ปัจจุบันทำชั้น 1 ได้ แต่ยังนำจำนวน 76 cases ไปทำให้ดูเหมือนชั้น 2/3 พร้อมแล้ว

## Blockers

### B1 — candidate pool ต่อ role มีเพียง 2–14 จุด ทำให้ N={10,20,30,50} sweep ไม่มีความหมาย

**ตำแหน่ง:** `p2_build_eval_set.py:42-126`, `KB_P2_PLAN.md` acceptance N sweep

Codex นับผ่าน policy compiler/matcher เดียวกับ retrieval ได้ดังนี้:

| role | authorized points |
|---|---:|
| it | 2 |
| hr | 5 |
| purchasing | 5 |
| logistics | 6 |
| sales | 9 |
| engineering | 11 |
| production | 13 |
| qc | 14 |

ดังนั้น N=20/30/50 คืน corpus authorized ทั้งหมดเหมือนกันเกือบทุก case; CandidateRecall จะสูงโดย construction และ latency ที่ N=30/50 ไม่ได้รันบน batch ขนาดนั้นจริง

**Required change:** ก่อน decision benchmark ต้องมี authorized candidate pool อย่างน้อย **60 points ต่อ evaluated role** พร้อม hard negatives ที่ไม่ใช่คำตอบ อาจใช้ shared synthetic-internal distractor bank ที่ ACL อนุญาตทุก evaluated role บวก role-specific twins แต่ทุก point ต้องมี synthetic marker และผ่าน policy/manifest validation

หากไม่ขยาย corpus ให้ลด N sweep และประกาศ run เป็น mechanics smoke เท่านั้น ห้ามเทียบกับ acceptance ที่ล็อก N={10,20,30,50}

### B2 — dev/test แยกตามลำดับ case ทำให้ paraphrase/intents เดียวกันข้าม split และ bootstrap CI แคบเกินจริง

**ตำแหน่ง:** `p2_build_eval_set.py:194-220`

ทุก intent ถูกสร้างเป็นคู่ Thai/Thai-English แต่ split ใช้ `i % 5` ราย case ผล audit พบ relevant-point groups ข้าม dev/test **15 groups / 36 cases** ตัวอย่างเช่น q-000↔q-001, q-004↔q-005, q-070↔q-071 และ q-074↔q-075

จำนวน 60 test cases จึงไม่ใช่ 60 independent intents; generator ทั้งชุดมีเพียง 38 query pairs/intents และ paired bootstrap ที่ระดับ query จะนับ paraphrase ที่สัมพันธ์กันเป็น independent sample

**Required change:**

- เพิ่ม stable `intent_id`
- Thai/Thai-English/paraphrase ของ intent เดียวกันต้องอยู่ split เดียวกัน
- ทำ group-stratified split ตาม intent + challenge tag + role
- bootstrap ที่ระดับ `intent_id` ไม่ใช่แต่ละ paraphrase
- สำหรับ arm-eligibility ให้ test มีอย่างน้อย **50 independent intent groups**; ถ้ายังไม่ถึงให้รายงานเป็น smoke

### B3 — `category` ผสม language กับ challenge type ทำให้ hard-negative category gate ใช้ไม่ได้

**ตำแหน่ง:** `p2_build_eval_set.py:197-209`

Thai case ใช้ semantic category แต่คู่ Thai-English ถูก override เป็น `thai-eng-mix` เสมอ ทั้งที่มี `lang` แยกอยู่แล้ว เช่น q-004 เป็น `current-superseded` แต่ q-005 ซึ่งเป็น intent เดียวกันกลายเป็น `thai-eng-mix`

ผลคือ test เกือบครึ่งเป็น `thai-eng-mix` 28/60 ขณะที่ `current-superseded` และ `lexical-overlap-wrong-code` มีอย่างละ 1 case จึงใช้กฎ “hard-negative category delta < -0.05” อย่างน่าเชื่อถือไม่ได้

**Required change:**

- เก็บ `lang` เป็นมิติภาษา
- เก็บ semantic challenge แยกเป็น `challenge_tags` หรือคง category เดิมให้ทั้งสองภาษา
- เพิ่ม explicit `hard_negative_ids` ต่อ intent เพื่อพิสูจน์ว่า challenge twin อยู่ใน authorized pool จริง
- แต่ละ category ที่ใช้เป็น acceptance gate ต้องมีอย่างน้อย 5 independent test intents หรือรวมเป็น family ที่ประกาศล่วงหน้า

`table-row` ปัจจุบันเป็น prose numeric fact ไม่ใช่ table-row representation และ `current-superseded` ไม่มีคู่ revision เก่า/ใหม่ใน corpus ให้สร้าง row-like context จริงหรือเปลี่ยนชื่อ category ให้ตรงกับสิ่งที่ทดสอบ

### B4 — มี query ที่ relevant chunk ไม่ได้ตอบสิ่งที่ถามโดยตรง

หลัก “chunk ที่ตอบตรง = grade 3” ใน `p2_build_eval_set.py:8-12` ยังไม่จริงทุก case:

- **q-042/q-043** (`p2_eval_set.json:675`) ถามค่า torque แต่ chunk บอกเพียง “ตามคู่มือผู้ผลิต” ไม่มีตัวเลข
- **q-054/q-055** (`p2_eval_set.json:867`) ถามความแตกต่าง PR กับ PO แต่ chunk เพียงบอกลำดับเปิด PR/ออก PO ไม่ได้นิยามความแตกต่าง
- **q-056/q-057** (`p2_eval_set.json:899`) ถาม ISO และ KPI training แต่ relevant chunk มี ISO/JD ไม่มี KPI
- **q-062–q-067** (`p2_eval_set.json:995-1090`) ใช้การย้อน implication: ไม่มีจุดสกปรกไม่ได้แปลว่าผ่านทุกเกณฑ์, “recall เมื่อส่งถึงลูกค้าแล้ว” ไม่ได้ระบุ workflow เมื่อยังไม่ส่ง และ “ใช้ vendor สำรองได้เมื่อเร่งด่วน” ไม่ได้เขียนชัดว่ากรณีปกติห้ามใช้

**Required change:** ปรับ query ให้ตรงข้อความ หรือเพิ่ม explicit positive/negative rule ใน synthetic chunk อย่าให้ grader ต้องสมมตินโยบายที่เอกสารไม่ได้กล่าว

### B5 — graded relevance 3/2 ถูกกำหนดตามลำดับรายการ ไม่ใช่ rubric ที่ตรวจได้

**ตำแหน่ง:** `p2_build_eval_set.py:177-185`, cases q-070–q-075 (`p2_eval_set.json:1123-1224`)

คำถาม conjunction เช่น “quotation และ approval flow” ถามทั้งสองส่วนเท่ากัน แต่ generator ให้ chunk แรก grade 3 และ chunk ที่สอง grade 2 โดยไม่มีเหตุผลเชิง label ทำให้ primary nDCG@5 ถูก bias ตามลำดับที่ผู้เขียนใส่ tuple

**Required change:** ล็อก rubric เช่น grade 3 = ตอบครบโดยลำพัง, grade 2 = supporting/partial, grade 1 = contextual แล้วให้ reviewer บันทึก rationale ต่อ multi-relevance case ปรับ query ให้มี primary intent ชัด หรือให้ทั้งสอง grade เท่ากัน

### B6 — Codex review ไม่สามารถเปลี่ยนสถานะเป็น `human-reviewed`

`label_status="human-reviewed"` ต้องหมายถึงมีมนุษย์อ่านและลงชื่อรับรองจริง ไม่ควรใช้ Codex/Claude review แทนหลักฐานมนุษย์

**Required change:** หลังแก้ B1–B5 ให้เก็บอย่างน้อย `reviewed_by`, `reviewed_at`, `review_revision` และ decision ต่อ case/corpus; ระหว่าง AI review ใช้ `draft` หรือ `ai-reviewed` เท่านั้น ผู้ใช้/Data Owner ที่ได้รับมอบหมายจึงเปลี่ยนเป็น `human-reviewed`

## Major improvements before freeze

### M1 — query pairs templated และมี exact lexical cues มากเกินไป

Headings/chunks และ queries ถูกเขียนคู่กัน (`p2_build_eval_set.py:43`, `p2_build_eval_set.py:136-191`) ทำให้ dense baseline มีโอกาส ceiling สูง เพิ่ม independent natural-language intents ที่ไม่ copy heading: คำสะกดผิด, คำย่อบริษัท, code หาย, พูดแบบหน้างาน, multi-constraint และ semantic twins ที่แชร์คำหลักแต่ต่างค่าตัวเลข/เงื่อนไข

### M2 — source IDs ดูเหมือนเอกสารบริษัทจริงทั้งที่ข้อมูลเป็น synthetic

ใช้ prefix เช่น `P2-SYNTH-*` และ payload/run marker `synthetic=true` เพื่อไม่ให้ WI/QP ที่แต่งขึ้นถูกนำไปอ้างเป็นนโยบายจริงในภายหลัง โดยยังใช้รหัสคู่คล้ายกันเพื่อทดสอบ lexical confusion ได้

## สิ่งที่ตรวจผ่าน

- `validate_corpus()` = **0 errors**
- `validate_ranking_eval_set()` = 76 expected draft-label errors และ **0 non-label schema/policy errors**
- dev/test count = 16/60, corpus = 29 points, UUID/stored policy shape ถูกต้อง
- relevant point ทุกตัว authorized สำหรับ role ตาม P1 path
- exact relevant-source set ถูกต้อง
- hashes ที่ตรวจซ้ำ:  
  eval `ec452b5f2530e60703497b37e2d5c017ac226bc0bf21f07b9ec07b92a2289630`  
  corpus `acf9b67eddbdaa2d6c16a6383975ca69e0d2ad7e43b42c473835c1da87cd92f7`

## Go / No-Go matrix

| การใช้ | Verdict |
|---|---|
| เริ่ม Slice 2 container/candidate-provider/M4 plumbing | **GO** ตาม `KB_P2_SLICE1_FIX3_CODEX_CONFIRM_45EA2F8.md` |
| ใช้ draft นี้ทำ smoke โดยระบุ mechanics-only | **GO** |
| เปลี่ยน labels เป็น `human-reviewed` | **NO-GO** |
| freeze เป็น decision benchmark / เลือก dense-rerank-fused arm | **NO-GO** |
| production/deploy/cloud/real data | **NO-GO** |

## Final verdict

**REWORK-BEFORE-HUMAN-REVIEW:** จุดใหญ่สุดคือ authorized pool เล็กกว่า N sweep และ dev/test ไม่ independent ชุดนี้พิสูจน์ mechanics ได้ แต่ยังใช้ CI หรือเลือก reranker ไม่ได้
