# ความเห็นต่อ AI Agent, Estimate Agent V1 และ Company AI Brain

**วันที่ทบทวน:** 2026-07-24  
**เอกสารต้นทาง:** `Ai Agent และ EstimateV1 Update19072026.pdf`  
**ขอบเขต:** อ่าน PDF, Markdown ทั้งหมดในโปรเจกต์ และตรวจเส้นทางโค้ดสำคัญแบบ read-only

---

## สรุปความเห็น

Company AI Brain ไม่ใช่โปรเจกต์เก่าที่บังเอิญเกี่ยวข้องกับแนวคิดใน PDF แต่เป็น **Knowledge Layer ที่แนวคิด AI Board of Advisors และ Estimate Agent จำเป็นต้องมี**

อย่างไรก็ตาม ต้องแยกสองระบบออกจากกันให้ชัดเจน:

```text
Company AI Brain ปัจจุบัน
คำถาม → ค้นเอกสารตามสิทธิ์ → LLM สรุป → Citation

Estimate Agent ใน PDF
Enquiry → RFQ → ตรวจความครบถ้วน → อนุมัติ
→ เลือกวิธีผลิต → คำนวณต้นทุน → เสนอราคา
→ เทียบ Actual Cost → เรียนรู้กลับ
```

Company AI Brain จึงเป็น “ความจำและคลังความรู้” ของ Estimate Agent แต่ยังไม่ใช่ Estimate Agent ทั้งระบบ

---

## สิ่งที่เห็นด้วยมากที่สุดใน PDF

### 1. เริ่มจาก RFQ ก่อน Estimate

RFQ เป็น Data Contract ที่เปลี่ยนข้อมูลจาก Enquiry, LINE, Email หรือเอกสารของลูกค้าให้เป็น Structured Data ที่ตรวจสอบได้

ก่อนส่งเข้า Estimate ควรมีสถานะอย่างน้อย:

```text
Draft
→ Needs Clarification
→ Ready for Review
→ Ready for Estimate
→ Estimating
→ Needs Approval
→ Approved / Rejected
→ Quotation Created
```

### 2. เริ่มจากงานมาตรฐานเพียง 3–5 ประเภท

การเลือกงานที่เกิดบ่อยและมีสูตรชัดเจน พร้อมข้อมูลย้อนหลัง 100–300 งาน จะให้ผลดีกว่าการพยายามรองรับงานทุกประเภทตั้งแต่ V1

### 3. AI เสนอ มนุษย์อนุมัติ

เหมาะสมกับ V1 โดยเฉพาะเรื่อง:

- วิธีผลิต
- เครื่องจักร
- Waste
- ต้นทุน
- Margin
- ราคาต่ำสุด
- กำหนดส่ง

ผู้จัดการยังต้องเป็นผู้รับผิดชอบการตัดสินใจ และระบบต้องบันทึกว่าใครเปลี่ยนตัวเลขอะไร เพราะเหตุใด

### 4. ใช้ Actual Cost ย้อนกลับมาตรวจ Estimate

นี่คือหัวใจของระบบ หากไม่มีวงจรนี้ AI จะทำได้เพียงตอบหรือคำนวณตามสูตร แต่จะไม่สามารถเรียนรู้ว่าค่ามาตรฐานแตกต่างจากผลการผลิตจริงอย่างไร

### 5. สร้าง Agent Blueprint Library

แนวคิดนี้ควรพัฒนาต่อ แต่ควรเป็นแพลตฟอร์มร่วมหนึ่งชุด ไม่ควรสร้าง Agent 15 ตัวเป็นระบบแยกกันจนเกิด AI Silo รอบใหม่

Agent แต่ละตัวควรต่างกันด้วย:

- Mission
- Tools
- Data Scope
- Policy
- Prompt
- Output Schema
- Approval Boundary

โดยใช้ระบบ Auth, Audit, Knowledge และ Model Runtime ร่วมกัน

---

## สถาปัตยกรรมที่แนะนำ

| ส่วน | หน้าที่ |
|---|---|
| Qdrant / Company AI Brain | SOP, WI, Policy, คู่มือ, Technical Knowledge และ Citation |
| PostgreSQL | RFQ, Machine Master, Material Master, Job History, Approval, Formula Version และ Audit |
| Costing Engine | คำนวณต้นทุนแบบ Deterministic และทดสอบย้อนหลังได้ |
| LLM / Agent | อ่าน Enquiry, เติม RFQ, ตรวจข้อมูลขาด, อธิบายทางเลือก และสนทนากับผู้ใช้ |
| Keycloak + Policy Filter | ระบุตัวตนและสร้างสิทธิ์จาก Token |
| MinIO / NAS | RFQ Attachment, Artwork, เอกสารต้นฉบับ และ Parsed Artifact |

ไม่ควรให้ LLM เป็นผู้สร้างสูตรต้นทุนเอง สูตรทุกสูตรต้องมี Owner, Version, Effective Date และผู้อนุมัติ

---

## ประเด็นที่ต้องแก้ก่อนเปิดใช้กับหลายแผนก

### Blocker 1 — RBAC ยังไม่ใช่ Authentication

ปัจจุบันผู้ใช้ส่ง `role` มาเองใน Request Body และระบบตรวจเพียงว่า role นั้นอยู่ในรายการที่รองรับ จากนั้นจึงนำไปสร้าง Qdrant filter

เส้นทางปัจจุบัน:

```text
Caller ส่ง role
→ ตรวจว่าเป็นชื่อ role ที่รู้จัก
→ สร้าง Qdrant payload filter
→ Retrieval
```

ผู้ที่เข้าถึง API ได้จึงสามารถส่ง `"role": "admin"` ได้

**สิ่งที่ต้องเปลี่ยน:** ใช้ Keycloak/OIDC และสร้าง role/group จาก Token ที่ตรวจลายเซ็นแล้วเท่านั้น ห้ามเชื่อ role จาก Request Body

อ้างอิง: [`app/main.py`](app/main.py)

### Blocker 2 — ตัวอย่าง RBAC ในเอกสารอาจทำให้ข้อมูลรั่ว

ตัวอย่างใน `rbac_matrix.md` ใช้:

```python
MatchAny(any=["production", "admin"])
```

เนื่องจากเอกสารทุก collection อนุญาต `admin` อยู่แล้ว filter นี้อาจ match เอกสารทั้งหมด แม้ผู้ใช้เป็น production

โค้ดจริงใน `app/main.py` ใช้ `MatchAny(any=[role])` ซึ่งถูกต้องกว่า แต่ควรแก้ตัวอย่างในเอกสารเพื่อป้องกันการนำไปใช้ผิดในอนาคต

อ้างอิง: [`rbac_matrix.md`](rbac_matrix.md), [`app/main.py`](app/main.py)

### Blocker 3 — เอกสารที่จำแนกไม่ได้ถูกเปิดแบบ Fail-Open

`rbac_config.py` จัดเอกสารที่ไม่ตรงกับ mapping เป็น `PRODUCTION` โดยอัตโนมัติ ซึ่งทำให้เอกสารใหม่หรือชื่อผิดอาจถูกเปิดให้กลุ่ม production อ่าน

**สิ่งที่ต้องเปลี่ยน:** เอกสารที่จำแนกไม่ได้ต้องไป `UNCLASSIFIED` หรือ `QUARANTINE` และห้าม Retrieval จนกว่า Document Owner จะอนุมัติ

อ้างอิง: [`rbac_config.py`](rbac_config.py)

### Major 1 — ยังไม่มี Revision Lifecycle และพบเอกสารซ้ำจากชื่อไฟล์

`document_onboarding_guide.md` ระบุว่าเมื่อส่ง Revision ใหม่ ระบบจะแทนที่เอกสารเดิมอัตโนมัติ แต่ ingestion ปัจจุบันสร้าง Point ID จาก `source + text` แล้วทำ `upsert` โดยไม่มีขั้นตอนลบ chunk ของ Revision เดิม

อย่างไรก็ตาม จากการสแกน Qdrant จริงล่าสุด **ยังไม่พบคู่ Revision เก่า-ใหม่ซ้อนกัน** ปัญหาที่พบและยืนยันแล้วคือ logical document เดิมถูก ingest ซ้ำ 4 ฉบับภายใต้ชื่อไฟล์ที่ต่างกันเล็กน้อย เช่นชื่อที่มีและไม่มีวงเล็บรอบ `(FSC)`

ผลกระทบปัจจุบัน:

- ผลลัพธ์ซ้ำแย่งพื้นที่ `top_k`
- เอกสารเดียวกันอาจถูกนับหลายครั้ง
- เพิ่มความเสี่ยงต่อข้อมูลขัดกันเมื่อมีการอัปเดตในอนาคต

**สิ่งที่ต้องเพิ่ม:** Canonical Document ID, Content Hash, Revision ID, Effective Date และ Superseded Status ก่อนจัดการเอกสารซ้ำหรือ Revision ในระยะยาว

อ้างอิง: [`document_onboarding_guide.md`](document_onboarding_guide.md), [`ingest.py`](ingest.py)

### Major 2 — Evaluation ยังไม่พิสูจน์ `/ask` และ Permission Leakage

ผล Hit@3 90% แสดงว่า Dense Retrieval ใช้งานได้ดีในระดับ PoC แต่ `eval.py` ปัจจุบัน:

- ไม่ใช้ RBAC filter
- ไม่มี User/Role ใน Test Case
- ไม่เรียก `/ask`
- ไม่วัด Answer Faithfulness
- ไม่ตรวจว่า Citation รองรับข้อความที่ตอบจริง
- ไม่วัดการปลอม role

นอกจากนี้ `eval.py` ใช้ `NO_MATCH_THRESHOLD = 0.62` แต่ `/ask` ไม่ได้นำ threshold นี้ไปใช้ หาก Qdrant คืนผลมา ระบบจะส่ง context ให้ LLM เสมอ

ดังนั้นค่า No-match 88% ยังไม่ใช่หลักฐานว่า `/ask` จะปฏิเสธคำถามนอกคลังได้ 88%

อ้างอิง: [`eval.py`](eval.py), [`eval_results.md`](eval_results.md), [`app/main.py`](app/main.py)

### Major 3 — สถานะในเอกสารไม่ตรงกัน

เอกสารแต่ละไฟล์บันทึกสถานะคนละช่วงเวลา:

- `REPORT.md`: 143 เอกสาร / 4,634 chunks / ยังไม่มี RBAC และ LLM
- `README.md`: 143 เอกสาร / 4,634 chunks / มี `/search` และ `/ask`
- `REVIEW_BRIEF.md`: 95 เอกสาร / 2,404 chunks / มี RBAC และ `/ask` บน server จริง

ควรมี `STATUS.md` เป็น Single Source of Truth ระบุ:

- Deployment Version
- Corpus Version
- จำนวนเอกสารและ chunks
- Eval Run ล่าสุด
- Model และ Configuration
- Known Issues
- Production Consumers

---

## สิ่งที่ Company AI Brain ยังขาดสำหรับ Estimate Agent

Company AI Brain รองรับ Knowledge Retrieval แล้ว แต่ Estimate Agent ต้องมีส่วนเพิ่มดังนี้:

1. RFQ Schema และ Workflow
2. Machine Master
3. Material Master และราคาที่มี Effective Date
4. Machine Hourly Rate
5. Waste Standard
6. Production Routing
7. Job History และ Similar Job Search
8. Deterministic Costing Engine
9. Margin และ Approval Rules
10. Actual Cost Feedback
11. Versioning และ Audit Trail
12. Tool Permission สำหรับ Agent

Historical Job ไม่ควรเก็บเฉพาะใน Vector DB ควรเก็บข้อมูลหลักใน PostgreSQL แล้วใช้ Qdrant หรือ Feature Similarity ช่วยค้นงานที่มีลักษณะใกล้เคียง

---

## Evaluation ที่ควรเพิ่มสำหรับ Estimate Agent

### Knowledge/RAG

- Retrieval Hit@K
- Answer Faithfulness
- Citation Accuracy
- No-answer Accuracy
- Permission Leakage Rate

### RFQ Extraction

- Field Accuracy
- Missing-field Detection
- Conflicting-field Detection
- Human Correction Rate

### Costing

- Formula Regression Test
- Estimate เทียบ Actual Cost
- Waste Prediction Error
- Machine Time Error
- Margin Error

### Business Outcome

- Quote Turnaround Time
- จำนวน RFQ ต่อวัน
- อัตราข้อมูลไม่ครบที่ตรวจพบก่อน Estimate
- AI Recommendation Acceptance Rate
- Quote-to-Order Rate
- จำนวนงานที่ขาดทุนจาก Estimate ผิด

---

## ลำดับดำเนินการที่แนะนำ

1. ทำ Authentication, Default-Deny Classification, Revision Lifecycle และ Audit ให้เรียบร้อย
2. สร้าง RFQ Schema และ Workflow ใน PostgreSQL
3. เลือกงานมาตรฐาน 3 ประเภท
4. เตรียม Machine, Material, Waste และ Hourly Rate ที่มีผู้อนุมัติ
5. สร้าง Costing Engine ที่ให้ผลลัพธ์ซ้ำได้และมี Regression Test
6. ต่อ Company AI Brain สำหรับ SOP, ข้อจำกัดเครื่อง และ Citation
7. ใช้ LLM สำหรับอ่าน Enquiry, ตรวจข้อมูลขาด และอธิบายผล
8. ทดลองกับ Estimator 2–3 คน
9. เปรียบเทียบเวลา ความผิดพลาด และ Estimate เทียบ Actual Cost
10. เพิ่ม Reranker, Hybrid Search, Streaming หรือ GPU เมื่อผลทดลองแสดงว่าจำเป็น

---

## Verdict

**เดินหน้าต่อได้ แต่ควรเสริมฐานแล้วค่อยสร้าง Estimate Agent**

สิ่งสำคัญที่สุดในขั้นนี้ไม่ใช่ทำให้ AI ฉลาดขึ้นหรือสร้าง Agent จำนวนมาก แต่คือทำให้ RFQ, สูตร, Revision, Permission และ Feedback จาก Actual Cost เชื่อถือได้

เมื่อฐานเหล่านี้พร้อม Company AI Brain จะกลายเป็น Knowledge Layer กลางของ Agent ทุกตัวได้จริง โดยไม่สร้าง AI Silo รอบใหม่
