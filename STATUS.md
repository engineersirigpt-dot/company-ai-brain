# Company AI Brain — Shared Project Status

> **ไฟล์กลางสำหรับมนุษย์และ AI ทุกตัว**  
> ให้อ่านไฟล์นี้ก่อนเริ่มวิเคราะห์ วางแผน หรือแก้ไขโปรเจกต์ เพื่อใช้ข้อเท็จจริงและการตัดสินใจล่าสุดร่วมกัน  
> เมื่อพบหลักฐานใหม่ ให้แยกให้ชัดระหว่าง **ยืนยันแล้ว**, **ยังต้องตรวจสอบ** และ **ข้อเสนอ**

**อัปเดตล่าสุด:** 2026-07-24

---

## 1. เป้าหมายของระบบ

Company AI Brain คือ Knowledge Layer กลางขององค์กร ให้ AI Project และ Agent ทุกตัวใช้ความรู้ชุดเดียวกัน โดยบังคับ Permission ก่อน Retrieval

โปรเจกต์นี้เป็นฐานให้วิสัยทัศน์ AI Board of Advisors และ Estimate Agent V1 แต่ยังไม่ใช่ Estimate Agent ทั้งระบบ

```text
Company AI Brain
คำถาม → Permission Filter → Retrieval → LLM → Answer + Citation

Estimate Agent V1
Enquiry → RFQ → ตรวจความครบถ้วน → อนุมัติ
→ เลือกวิธีผลิต → Costing → Pricing → Quotation
→ เทียบ Actual Cost → Feedback
```

---

## 2. ข้อเท็จจริงที่ยืนยันแล้ว

### ระบบปัจจุบัน

- Deploy บน Ubuntu server ภายในองค์กร
- Qdrant collection: `company_docs`
- สถานะที่รายงานล่าสุด: **2,404 chunks / 95 เอกสาร**
- `POST /search` ใช้งานโดย Voicebot อีกโปรเจกต์แล้ว
- `POST /ask` ทำ Retrieval → Claude API → คำตอบภาษาไทยพร้อม Citation ได้
- Retrieval บังคับ Qdrant payload filter ก่อนค้นหา
- Parent-Child Retrieval ใช้ child chunk สำหรับค้นและ `parent_text` สำหรับตอบ
- API ยังรับ `role` จาก Request Body และยังไม่มี Authentication ที่พิสูจน์ตัวผู้ใช้

### Corpus และเอกสารซ้ำ

- ผล Evaluation เดิมอ้างอิง corpus **4,634 chunks / 143 เอกสาร**
- สถานะ server ล่าสุดเหลือ **2,404 chunks / 95 เอกสาร**
- **สาเหตุ 143 → 95: ยืนยันแล้ว (2026-07-24, ตรวจโดย Claude — read-only)**
  ห่วงโซ่หลักฐาน: `info/` มีไฟล์ดิบ 435 ไฟล์แต่**ชื่อ unique เพียง 100** (ไฟล์เดิมถูกวางซ้ำ
  ข้ามโฟลเดอร์ Export/Local/Packaging สูงสุด 4 สำเนา) → corpus เก่า 143 ไฟล์เกิดจาก
  ingest สำเนาซ้ำเหล่านั้น (4,634 ≈ 1.9× ของ 2,404 สอดคล้องกัน) → `parsed_output/`
  ปัจจุบันมี 104 ไฟล์ = **เอกสารจริง 95 + ไฟล์ขยะ 9** (default/thumbnail = 0 byte,
  index/mpire/progress_bar = เศษ HTML conversion, CLAUDE/readme/isapi/menu_top_right)
  → server 95 sources ตรงกับเอกสารจริง 95 พอดี
- **ข้อสรุป: ไม่มีเอกสารจริงหายจาก server แม้แต่ไฟล์เดียว — Voicebot ไม่ได้รับผลกระทบ**
- การสแกน Qdrant จริงล่าสุด **ยังไม่พบคู่ Revision เก่า-ใหม่ซ้อนกัน**
- พบ logical document เดิมถูก ingest ซ้ำ **4 ฉบับ** ภายใต้ชื่อไฟล์ที่ต่างกันเล็กน้อย
  (สาเหตุรากเดียวกับเลข 143: สำเนาข้ามโฟลเดอร์ที่ชื่อไฟล์ต่างกันเล็กน้อยรอด dedup มา)
- ตัวอย่างชื่อซ้ำ:
  - `QP-741-03 Rev05 ...(FSC)`
  - `QP-741-03 Rev05 ...FSC`
- เอกสารซ้ำสามารถกินพื้นที่ `top_k`, ทำให้ผลลัพธ์ซ้ำ และเพิ่มความเสี่ยงที่ข้อมูลจะขัดกันในอนาคต

### ข้อจำกัดของ Evaluation ปัจจุบัน

- `eval.py` วัด Dense Retrieval เป็นหลัก
- ผลล่าสุดใน `eval_results.md`: Hit@3 = 90%, MRR = 0.8623
- Evaluation เดิมไม่ได้เรียก `/ask`
- Evaluation เดิมไม่ใส่ RBAC filter หรือ user role ใน test case
- `NO_MATCH_THRESHOLD = 0.62` อยู่ใน `eval.py` แต่ยังไม่ได้ใช้ใน `/ask`
- จึงยังไม่มีหลักฐานเต็มรูปแบบเรื่อง Answer Faithfulness, Citation Accuracy, Hallucination และ Permission Leakage ของ `/ask`

---

## 3. Correction ต่อรายงาน Review

### Revision Lifecycle

ข้อความเดิมที่ว่า “Revision เก่าไม่ถูกแทนที่และอาจซ้อนกันอยู่” ต้องใช้ถ้อยคำที่แม่นยำขึ้น:

> ระบบ ingestion ยังไม่มีกลไก Revision Lifecycle หรือขั้นตอนลบ/ปิด Revision เดิมโดยอัตโนมัติ แต่จากการสแกน Qdrant ปัจจุบันยังไม่พบ Rev เก่า-ใหม่ซ้อนกันจริง ปัญหาที่พบและยืนยันแล้วคือ logical document เดียวถูก ingest ซ้ำภายใต้ชื่อไฟล์ต่างกันเล็กน้อยจำนวน 4 ฉบับ

ดังนั้น:

- **กลไกที่ยังขาด:** Document Identity, Revision ID, Effective Date, Superseded Status
- **ปัญหาที่เกิดขึ้นจริงตอนนี้:** Duplicate logical documents จาก filename variants
- ห้ามกล่าวว่ามี Revision conflict จริงจนกว่าจะพบหลักฐาน

---

## 4. การตัดสินใจเชิงสถาปัตยกรรม

### RFQ ต้องมาก่อน Estimate

RFQ คือ Data Contract ระหว่าง Enquiry กับ Estimate ต้องทำให้ข้อมูลครบ ชัดเจน ตรวจสอบ และอนุมัติก่อนเริ่มคำนวณต้นทุน

### Costing ต้อง Deterministic

- Costing Engine ต้องอยู่บน Structured Data และสูตรที่ควบคุม Version ได้
- ใช้ PostgreSQL สำหรับ RFQ, Master Data, Formula, Approval และ Audit
- ห้ามให้ LLM สร้างหรือคำนวณสูตรต้นทุนหลักเอง
- LLM ใช้สำหรับอ่าน Enquiry, แยกข้อมูล, ตรวจข้อมูลขาด และอธิบายผล
- ทุกสูตรต้องมี Owner และผู้อนุมัติ

### Agent ต้องใช้ Platform ร่วม

Agent ทั้ง 15 ตัวควรแชร์:

- Authentication
- Authorization/Policy
- Audit
- Knowledge Layer
- Model Runtime
- Observability
- Agent Blueprint

Agent แต่ละตัวแตกต่างกันด้วย Mission, Tools, Data Scope, Policy, Prompt และ Output Schema ไม่ควรเป็น 15 ระบบแยก

---

## 5. ลำดับงานที่ตกลงล่าสุด

### ลำดับแรก — ตรวจปริศนา 143 → 95 ✅ เสร็จแล้ว (2026-07-24)

ผล: ไม่มีเอกสารจริงหาย (ดู "Corpus และเอกสารซ้ำ" ข้างบน) — Quick Wins ปลดล็อกแล้ว
ยกเว้นการลบเอกสารซ้ำ 4 ฉบับที่ยังต้อง snapshot + เลือก canonical ก่อนตามกติกา

รายละเอียดที่วางแผนไว้เดิม (เก็บไว้อ้างอิง):

สิ่งที่ต้องหา:

1. สร้าง manifest ของ corpus เดิมและปัจจุบัน
2. เปรียบเทียบ document code, revision, filename, content hash, chunk count, collection และ allowed roles
3. แบ่ง 48 เอกสารออกเป็น:
   - ตั้งใจคัดออก
   - Migration ไม่ครบ
   - Parse/OCR ล้มเหลว
   - ถูกนับซ้ำในตัวเลขเดิม
   - เปลี่ยนชื่อหรือรวมเอกสาร
4. ตรวจว่า Voicebot ใช้หรือเคยอ้างเอกสารที่หายไปหรือไม่
5. ห้ามลบเอกสารซ้ำจนกว่าจะมี snapshot และกำหนด canonical document แล้ว

### Quick Wins — ทำหลังเข้าใจ corpus

ประมาณการเดิม: รวมกันประมาณ 1 วัน

- เปลี่ยน fallback ใน `rbac_config.py` เป็น default-deny / quarantine
- แก้ตัวอย่าง `MatchAny(any=["production", "admin"])` ใน `rbac_matrix.md`
- จัดการ duplicate logical documents 4 ฉบับหลังมี snapshot และ canonical identity
- ใช้ `STATUS.md` เป็นสถานะกลาง
- รัน baseline evaluation กับ `/ask`
- สรุปสาเหตุ 143 vs 95

### งานใหญ่ / Production Readiness

- Keycloak OIDC
- User-level RBAC/ABAC
- Full Audit Log
- Revision Lifecycle
- Rate Limit
- TLS/Reverse Proxy
- Production Monitoring

---

## 6. แนวทาง Authentication ช่วง PoC

### ทางสายกลางที่เลือกพิจารณา

ใช้ API key ต่อ calling service และ map role ฝั่ง server:

```text
API key
→ Service Identity
→ Allowed Roles/Scopes
→ Qdrant Policy Filter
```

กติกา:

- Client ห้ามกำหนด role ที่อยู่นอก scope ของ API key
- ทางที่ปลอดภัยกว่าคือ server ignore หรือ reject role ที่ client ส่งมา
- Key ต้อง revoke และ rotate ได้
- เก็บ key แบบ hash ไม่เก็บ plaintext
- บันทึก service identity, request ID, role และเวลาที่เรียก
- ใช้ผ่าน TLS หรือเครือข่ายภายในที่ควบคุมแล้ว

### ข้อจำกัด

API key พิสูจน์ได้เพียงว่า request มาจาก service ใด เช่น Voicebot แต่ไม่พิสูจน์ตัว end user

ถ้า service เดียวรองรับผู้ใช้หลายสิทธิ์ ต้องใช้ Keycloak/User Token หรือกลไกที่ตรวจสอบ end-user identity ได้ในขั้น Production

---

## 7. ความเสี่ยงที่ต้องติดตาม

| ความเสี่ยง | สถานะ |
|---|---|
| 48 เอกสารอาจหายจาก corpus ปัจจุบัน | ❌ ปิดแล้ว 2026-07-24 — เลข 143 มาจากไฟล์ดิบซ้ำข้ามโฟลเดอร์+ไฟล์ขยะ ไม่มีเอกสารจริงหาย |
| Voicebot อาจค้นความรู้ได้ไม่ครบ | ❌ ปิดแล้ว — corpus ครบ 95/95 |
| ผู้เรียก API สามารถอ้าง `admin` เอง | ยืนยันแล้ว — รอ API key ต่อ service (ดูข้อ 6) |
| Unknown document fallback เป็น `PRODUCTION` | ✅ แก้แล้ว 2026-07-24 — เปลี่ยนเป็น UNCLASSIFIED (admin เท่านั้น) |
| ตัวอย่าง RBAC ใน Markdown ใส่ `admin` ใน MatchAny | ✅ แก้แล้ว 2026-07-24 |
| Duplicate logical documents แย่งพื้นที่ `top_k` | ยืนยันจาก Qdrant scan |
| Revision เก่า-ใหม่ซ้อนกัน | ยังไม่พบ |
| `/ask` ผ่าน full evaluation | ยังไม่ได้ทำ |

---

## 8. เอกสารที่เกี่ยวข้อง

- [`AGENTS.md`](AGENTS.md) — สถาปัตยกรรมและหลักการหลัก
- [`REVIEW_BRIEF.md`](REVIEW_BRIEF.md) — บริบทสำหรับ reviewer
- [`AI_AGENT_ESTIMATE_REVIEW.md`](AI_AGENT_ESTIMATE_REVIEW.md) — Gap analysis และความเห็นฉบับเต็ม
- [`README.md`](README.md) — วิธีใช้ระบบ
- [`REPORT.md`](REPORT.md) — ผล PoC เดิม
- [`eval_results.md`](eval_results.md) — ผล Retrieval Evaluation
- [`rbac_matrix.md`](rbac_matrix.md) — Draft permission matrix

---

## 9. กติกาสำหรับ AI ตัวถัดไป

1. อ่าน `STATUS.md` ก่อนเสนอแผนหรือแก้ไขระบบ
2. แยกคำว่า “ยืนยันแล้ว” ออกจาก “คาดว่า” เสมอ
3. อย่าอ้างว่ามี Revision conflict จริง ปัจจุบันยืนยันเพียง duplicate logical documents
4. อย่าลบหรือ retag เอกสารก่อน snapshot และตรวจผลกระทบ Voicebot
5. อย่าเชื่อจำนวน 143 หรือ 95 โดยไม่มี corpus manifest รองรับ
6. ถ้าพบหลักฐานใหม่ ให้อัปเดตไฟล์นี้พร้อมวันที่และแหล่งที่มา
7. ห้ามอ่านหรือแก้ `.env` และเอกสารภายใน `info/` เว้นแต่ผู้ใช้อนุญาตโดยชัดเจน
