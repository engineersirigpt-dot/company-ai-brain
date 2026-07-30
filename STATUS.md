# Company AI Brain — Shared Project Status

> **ไฟล์กลางสำหรับมนุษย์และ AI ทุกตัว**  
> ให้อ่านไฟล์นี้ก่อนเริ่มวิเคราะห์ วางแผน หรือแก้ไขโปรเจกต์ เพื่อใช้ข้อเท็จจริงและการตัดสินใจล่าสุดร่วมกัน  
> เมื่อพบหลักฐานใหม่ ให้แยกให้ชัดระหว่าง **ยืนยันแล้ว**, **ยังต้องตรวจสอบ** และ **ข้อเสนอ**

**อัปเดตล่าสุด:** 2026-07-27

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

## 1.5 Module Map — วิสัยทัศน์เฮีย (AI Operating System) ↔ สิ่งที่เรามี

> อ้างอิง: `AI Operating System Update 26072026.pdf` (บทสนทนาเฮีย × พรชัย/ChatGPT)
> เฮียวางแผน **12 โมดูล** + กติกาเดียว: **"ห้ามเขียนโปรแกรมก่อน Workflow ถูกต้อง"** (ตรงกับ schema-first ที่เราทำ)
> **หลักที่ยืนยันร่วมกัน:** AI First Development (ออกแบบ workflow/rule/prompt ก่อน → AI ค่อยเขียน code),
> "AI เสนอ มนุษย์อนุมัติ", "เอางานเก่า 100 งานมาเทสต์", 90 วัน/ททท

| # | Module (คำเฮีย) | สถานะเรา | หมายเหตุ |
|---|---|---|---|
| 12 | **AI Knowledge Brain** ("ความทรงจำองค์กร") | ✅ **PoC รันจริง** (เหลือ hardening) | = company-ai-brain (2,263 chunks, /search มี consumer จริง, /ask ใช้ได้, RBAC ทำงาน) — **ยังไม่ production-ready**: AUTH_MODE=warn, ยังไม่มี user auth, audit/monitoring ใน backlog, /ask ส่ง context ไป Claude |
| 02 | **RFQ** (AI RFQ Agent V1) | 🔨 **กำลังทำ** | schema v0.2 + migration + test 17/17 ผ่าน (prototype) — เหลือ service layer + ENQ→RFQ AI extraction |
| 01 | ENQ | ⬜ ถัดจาก RFQ | AE รับ ENQ → AI กรอก RFQ (ต่อกับ Module 02) |
| 03 | AI Estimate | ⬜ ออกแบบไว้ | ต่อ RFQ → `/search`/`/ask` (SOP) + Costing deterministic (PostgreSQL, tb_master_* จริง) |
| 11 | **Printing Doctor** (TCE + PAR) | 🟡 **ไพ่สำรอง** | **PAR-lite** (retrieval/root-cause/preventive checklist) ทำได้ด้วย Brain — แต่ต้อง **CAR สังเคราะห์/redact ก่อน** ไม่ ingest ของจริงที่มี PII/trade secret; **TCE** ต้องรอ Cost Data Contract + deterministic costing — ตัวเลขใน demo ต้องระบุชัดว่า simulated/illustrative |
| 04–10 | Planning/Purchasing/Production/QC/Delivery/Finance/CEO Dashboard | ⬜ ยังไม่เริ่ม | roadmap เฮีย — อย่าเปิดพร้อมกัน (เฮียเองเตือน) |

**Insight เชิงกลยุทธ์จากเอกสาร:**
1. เฮีย emotionally สนใจ **Printing Doctor/True Cost of Error มากสุด** (วิสัยทัศน์ 20-30 ปี) + ย้ำอยากได้ **quick win เร็ว**
   → **PAR** (retrieval over CAR เก่า) = Brain ทำได้ทันที; **TCE** (คำนวณค่าเสียหาย) = ต้องมี costing engine ก่อน
2. **Division of responsibility (operating constraint ของทีมนี้ ไม่ใช่ข้อจำกัดถาวรของโมเดล):**
   ในช่องทางสนทนา ChatGPT เข้า server บริษัทเองไม่ได้ (หน้า 17) — แต่ agent ที่มี tools/credentials/authorization อาจ deploy ได้
   ปัจจุบันทีมใช้: **GPT/Codex = ออกแบบ/review, Claude = build/test/deploy บน server จริง, `STATUS.md` = source of truth ร่วม**
3. วินัย: **ลึก 1 โมดูล (RFQ) + เดโมถูกๆ 1 (PAR-lite) พอ** — ห้าม 12 โมดูลพร้อมกัน
   PAR-lite guardrail: เริ่มด้วย synthetic/redacted CAR 20–50 เคส, `/search` เป็น default, ใช้ `/ask` เมื่อ auth/egress review ผ่าน, ห้ามคำนวณ TCE จริงจนกว่า costing contract พร้อม

**แผนไม่ pivot:** เฮียยืนยันเอง (หน้า 16-18) ว่าก้าวแรก = RFQ Agent V1 → ทำ RFQ ให้จบก่อน, PAR-lite เก็บเป็นไพ่ถ้าเฮียกดดันอยากเห็นของเร็ว

---

## 2. ข้อเท็จจริงที่ยืนยันแล้ว

### ระบบปัจจุบัน

- Deploy บน Ubuntu server ภายในองค์กร
- Quick Wins ชุด RBAC ล่าสุดอยู่ที่ commit `8e6ea48` — push ขึ้น GitHub และ server pull แล้วเมื่อ 2026-07-24
- Qdrant collection: `company_docs`
- สถานะที่รายงานล่าสุด: **2,263 chunks / 95 เอกสาร** (2,404 → 2,263 หลัง re-ingest 6 ไฟล์ AFII ด้วย OCR เมื่อ 2026-07-27; จำนวนเอกสารเท่าเดิม)
- `POST /search` ใช้งานโดย Voicebot อีกโปรเจกต์แล้ว
- `POST /ask` ทำ Retrieval → Claude API → คำตอบภาษาไทยพร้อม Citation ได้
- Retrieval บังคับ Qdrant payload filter ก่อนค้นหา
- Parent-Child Retrieval ใช้ child chunk สำหรับค้นและ `parent_text` สำหรับตอบ
- API ยังรับ `role` จาก Request Body และยังไม่มี Authentication ที่พิสูจน์ตัวผู้ใช้
- Default-deny ทดสอบบน server แล้ว: source ชื่อที่จำแนกไม่ได้ได้ `allowed_roles=["admin"]`, source เดิมยังได้ role ตามปกติ และ service ไม่ได้รับผลกระทบ

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
- **อัปเดต 2026-07-27: `/ask` ผ่าน baseline eval แล้ว** (`ask_eval.py` รันบน server, 100 ข้อ + 5 permission probes):
  citation-hit 85/92 = 92% | no-answer honest 8/8 (hallucination 0) | permission leak 0/5 | latency p50 7.3s / p95 26s
  ข้อพลาดหลักคือ sibling-document confusion 7 ข้อ → หลักฐานสนับสนุนการเพิ่ม reranker (ดู `ask_eval_results.md`)
  ที่ยังไม่ครอบคลุม: faithfulness แบบ LLM-judge, permission probes ชุดเต็ม, hit rate ต่อ role

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

- [x] เปลี่ยน fallback ใน `rbac_config.py` เป็น default-deny / `UNCLASSIFIED` — commit `8e6ea48`, verify บน server แล้ว
- [x] แก้ตัวอย่าง `MatchAny(any=["production", "admin"])` ใน `rbac_matrix.md` พร้อมคำเตือน — commit `8e6ea48`
- [~] จัดการ duplicate logical documents 4 ฉบับ — **วิเคราะห์เสร็จ 2026-07-27 รอยืนยัน canonical ก่อนลบ**
      ผลตรวจ: ทั้ง 4 คู่**เนื้อหาต่างกันจริง** (คนละ parse/ต้นฉบับ ไม่ใช่สำเนาแท้) ห้ามลบมั่ว
      Snapshot ถ่ายแล้ว: `company_docs-1219394608722843-2026-07-27-10-31-42.snapshot` (56MB ใน qdrant volume)
      ข้อเสนอ canonical (เก็บตัวเนื้อหาสมบูรณ์กว่า — รอ human ยืนยัน):
      | คู่ | เก็บ (แนะนำ) | เหตุผล |
      |---|---|---|
      | QP-741-03 | ฉบับ `...FSC` (ไม่มีวงเล็บ) | 6.7KB/4 headings vs 3.2KB/1 |
      | WI-751-01-01 | ฉบับ `Flow_...` | 15.3KB vs 10.9KB ⚠️ ชื่อต่างกันมาก อาจเป็นคนละเอกสาร ให้คนเปิดดู |
      | WI-760-01-02 | ฉบับชื่อเต็ม `...ที่ใช้ในการสอบเทียบ` | 34.3KB vs 29.5KB |
      | WI-823-01-04 | ฉบับไม่มีช่องว่างหลัง Rev.00 | 9.5KB vs 8.3KB |
- [x] ใช้ `STATUS.md` เป็นสถานะกลาง และให้ `AGENTS.md`/`CLAUDE.md` ชี้มาอ่านก่อนทำงาน
- [x] รัน baseline evaluation กับ `/ask` — 2026-07-27 ผล: citation-hit 92%, no-answer honest 8/8,
      permission leak 0/5, latency p50 7.3s (รายละเอียด+วิเคราะห์ใน `ask_eval_results.md`)
- [x] สรุปสาเหตุ 143 vs 95 — ไม่มีเอกสารจริงหายและ Voicebot ไม่ได้รับผลกระทบ

### งานถัดไปที่เสนอ — ยังไม่ได้เริ่ม

### คำตอบ Business รอบแรก (2026-07-27 — จากผู้พัฒนา ยังไม่ใช่มติทางการจากผู้บริหาร)

| คำถาม | คำตอบ | สถานะ |
|---|---|---|
| งาน 3 ประเภทแรก | **เริ่มจาก Packaging (กล่อง)** — สอดคล้องระบบ Estimate Packaging ที่มีอยู่ + corpus มี QP/WI Packaging ครบ | ✅ ใช้ได้เลย |
| Master Data owner/รหัส | **[แก้ไข 2026-07-27 โดยผู้พัฒนา]** ตระกูล `tb_master_*` (paper, corrugated_board, coating, waste, price_rate, machine ฯลฯ) **ดึงจาก database จริงของบริษัทผ่าน API** — เว็บ RFQ_Estimate ไม่ได้เป็นเจ้าของ; **PostgreSQL local ของเว็บเก็บเฉพาะ RFQ list/เอกสาร RFQ** → v0.2 ให้ `*_ref` อ้างรหัสจาก company DB (ผ่าน API เดียวกับที่เว็บใช้) ไม่ copy master มาเก็บเอง | ✅ ยืนยันโดยผู้พัฒนา — เหลือหาว่า API/DB บริษัทใครดูแล |
| เลข RFQ | ระบบเว็บ RFQ Estimate เดิมเป็นคนออกเลข (job_id) — format ที่แน่นอนต้องดูจาก DB จริง | 🔶 รอยืนยัน format |
| ผู้ยืนยัน Ready for Estimate | ยังไม่รู้ — แต่ระบบเดิมมี status flow (Draft/Pending/Reject/Approved) + JWT roles อยู่แล้ว → ข้อเสนอ: reuse กลไก approve เดิม | 🔶 เปิดอยู่ — ถามเฮีย |
| นโยบายข้อมูลเข้า Claude | **ผู้พัฒนาปรับ scope: ใช้ Claude เต็มที่กับข้อมูลปัจจุบันไปก่อน** (SOP/Internal — ไม่ severe) แล้วค่อยเพิ่มคัดกรองทีหลัง | ⚠️ มี tripwire ด้านล่าง |

**Tripwire ข้อ 5 (สำคัญ):** นโยบาย "เต็มที่ไปก่อน" ใช้ได้กับ corpus ปัจจุบันซึ่งเป็น SOP/WI ล้วน
แต่**ทันทีที่ RFQ จริงเริ่มไหลเข้าระบบ** (มีชื่อ/เบอร์/อีเมลลูกค้า = PDPA, ต้นทุน = ความลับการค้า)
ต้องกลับมาเปิด redaction/local-LLM path หรือได้อนุมัติจากผู้บริหารก่อน — ห้ามปล่อยผ่านเงียบๆ

**อัปเดต 2026-07-27 (รอบ 2):** **RFQ Schema v0.2 (Packaging-first) ผ่าน cross-check แล้ว — ✅ อนุมัติเริ่มเตรียม migration**
ปิดครบ 3 blocker (Data Egress schema-enforced, stable UUID subject + DB guard, revision chain trigger)
เหลือ 3 จุดเก็บตอน migration (trigger ข้าม status_history, CHECK subject_type ใน field_policy,
audit child tables) + open decisions 8 ข้อใน `RFQ_SCHEMA_V0_2.md` ข้อ 11 = blocker list ของ migration
ดูรายละเอียดใน `RFQ_SCHEMA_V0_2.md` ข้อ 13

#### RFQ v0.2 — Migration TODO และ Decision Gate

**Accepted findings จาก cross-check ข้อ 13:**

- [ ] **Supersede audit ต้อง atomic:** ตอน clone revision ต้องบันทึกทั้ง
      `READY_FOR_ESTIMATE → SUPERSEDED` ของ revision เดิมและการสร้าง `DRAFT`
      revision ใหม่ใน transaction เดียวกัน ห้ามให้ trigger เปลี่ยนสถานะโดยไม่มี
      `rfq_status_history`; migration ต้องจัดลำดับการสร้าง table/function/trigger
      ให้รองรับ และมี concurrency/rollback test
- [ ] **Field policy ต้อง validate ชื่อ:** เพิ่ม `CHECK`/reference validation สำหรับ
      `rfq_field_policy.subject_type` ให้รับเฉพาะรายการที่รองรับกับ `ANY`;
      `field_name` ต้องตรวจผ่าน versioned field registry ตอน seed/startup เพราะ
      free text ที่สะกดผิดจะตก default `BLOCK` แบบปลอดภัยแต่ debug ยาก
- [ ] **Child-table audit ต้องพิสูจน์ได้:** migration acceptance test ต้องยืนยันว่า
      shared audit บันทึก field-level change ของ child tables ครบอย่างน้อย actor,
      service, request_id, rfq_id, revision_no, stable subject ID, before/after,
      reason และ changed_at; ถ้ายังไม่มี shared audit implementation ต้องสร้าง
      durable audit/outbox ก่อนถือว่า migration ผ่าน

**Open decisions ที่ block migration (จาก `RFQ_SCHEMA_V0_2.md` ข้อ 11):**

- [ ] ระบุทีม/บุคคลที่เป็น Company API/Data Owner
- [x] ยืนยันว่า API คืน stable `ref` — **มีหลักฐานแล้ว (2026-07-27, Claude probe แบบ read-only):**
      `GET /estimate/master_data` (port 3099/4010 บน server) คืน numeric `id` ครบทั้ง 5 type ที่ตรวจ
      (paper 340 แถว, corrugated 1,522, coating 48, boxtemplate 12, process_type 18) —
      corrugated มี `id` จริง ข้อกังวลใน v0.2 ข้อ 6.6 ตกไป; paper มี `is_enabled` ใช้เช็ค active ได้
      **เหลือคำถามเดียวให้ API owner:** `id` คงที่ข้ามการ reload/re-import master หรือไม่
      (ถ้า reload แล้ว serial เปลี่ยน ref จะพัง — ต้องยืนยันก่อน migration)
- [ ] ถ้า `id` ไม่ stable ข้าม reload ให้ระบุผู้อนุมัติ canonical key contract
- [ ] ตัดสินใจว่า `job_id` reserve ตอนสร้าง Draft หรือออกตอน save/handoff
- [ ] ระบุ Ready approver role และตัดสินใจว่าต้องแยกจากผู้จัดทำหรือไม่
- [ ] กำหนด retention ของ Enquiry, Artwork, Dieline และข้อมูลผู้ติดต่อ
- [ ] กำหนดกรณีที่อนุญาต Cloud extraction ของ RFQ จริงและผู้มีอำนาจอนุมัติ
- [ ] เลือก physical PostgreSQL placement และ owner ของ backup/restore

**อัปเดต 2026-07-27:** RFQ Schema draft v0.1 (Codex) ผ่าน cross-check review (Claude) แล้ว —
verdict: อนุมัติทิศทาง, มี 3 จุดต้องแก้ก่อน migration (PDPA field classification,
field_path stability, revision chain integrity) ดูรายละเอียดใน `RFQ_SCHEMA_DRAFT.md` ข้อ 13
คำถาม business 12 ข้อในไฟล์เดียวกัน ข้อ 11 = วาระประชุมผู้บริหาร

ลำดับที่ Codex แนะนำ ณ 2026-07-24:

1. **API key ต่อ service** — ปิดช่องที่ caller อ้าง `admin` เอง เนื่องจาก API มี consumer จริงแล้ว
2. **จัดการ duplicate logical documents** — snapshot ก่อน, เลือก canonical จาก `parsed_output`, ลบหรือปิดอีกฉบับ แล้วทำ retrieval smoke test
3. **รัน `/ask` baseline evaluation** — รันหลัง corpus สะอาด เพื่อให้ baseline ไม่ถูก duplicate รบกวน

เหตุผลที่เลือก API key ก่อน: duplicate กระทบคุณภาพ `top_k` แต่ role spoofing กระทบขอบเขตข้อมูลที่ caller เข้าถึงได้ จึงมี severity สูงกว่า

### Hardening backlog (จาก GPT trace 2026-07-27 — accepted)

**ทำแล้ว:**
- [x] **Auth fail-closed** (commit ด้านล่าง) — GPT เจอบั๊ก: `enforce` + registry ว่าง เคย fail-open,
      AUTH_MODE สะกดผิดตกไป warn เงียบๆ → แก้: validate AUTH_MODE + key registry ตอน startup,
      enforce+ไม่มี key = refuse to start, `check_service_auth` ไม่พึ่ง `and keys` แล้ว
      + `test_auth.py` 11 tests (มี regression ของบั๊กนี้) รันผ่านโดยไม่ต้อง model/Qdrant
- [x] Input length cap: `query`/`question` min 1 / max 2000 (Pydantic Field)

**ทำต่อได้เลย (mock/offline, $0, ไม่แตะ Qdrant):**
- [ ] **Per-request budget cap /ask** — validate `LLM_MAX_TOKENS` ตอน startup, ตั้งเพดาน PoC ต่ำลง,
      log token usage (ห้าม log question/context), unit test ด้วย fake LLM
- [ ] **Citation integrity guard** — ปัจจุบัน `/ask` คืน retrieved docs ทุกฉบับใน `citations`
      ไม่ว่าคำตอบใช้ `[n]` จริงหรือไม่ → parse `[n]` จาก answer, reject เลขนอกช่วง, คืนเฉพาะที่อ้างจริง
      **⚠️ นัยต่อ eval: ค่า citation-hit 92% = ยืนยัน retrieval hit เท่านั้น ยังไม่ยืนยันว่า [n] ในข้อความถูก**
- [ ] **Auth cutover telemetry** — structured log สรุป readiness ต่อ consumer ก่อน flip enforce
      (นับ missing/invalid/out-of-scope แยก service; ไม่ log key/question/content)
- [ ] **`/live` endpoint** — process-only (ไม่แตะ Qdrant) + docker healthcheck; `/health` เดิมคง readiness

**รอ corpus นิ่ง (ตอนนี้นิ่งแล้วหลัง OCR):**
- [ ] **Replacement protocol แบบ generation/staging** — ป้องกันช่วงข้อมูลหายถ้า upsert ล้มหลัง delete
      (ทั้ง ingest.py upsert-only เหลือ chunk เก่า และ ocr_reingest delete-before-upsert)
- [ ] Retrieval regression (eval เดิม + canary 6 ไฟล์ AFII) — $0 ไม่ผ่าน LLM
- [ ] Corpus release manifest (source, hash, parser/OCR version, chunk count, RBAC, timestamp)
- [ ] Snapshot restore drill ใน collection แยก (ไม่แตะ live)
- [ ] Reranker benchmark (ด้วย corpus release เดียวกัน)

### RFQ Migration — Codex review commit 8149e64 (9 findings) → Claude แก้ 2026-07-27

**แก้แล้วระดับ DDL + verify ด้วย test 17/17 (ephemeral postgres:16):**
- [x] B2 — `rfq_field_evidence.extraction_run_id` + composite FK + CHECK (AI_INFERENCE ต้องมี run) → SEC-004 พิสูจน์ได้แล้ว
- [x] M6 — `rfq_status_history.readiness_run_id` composite FK (กันอ้าง run ข้าม RFQ)
- [x] Egress edge — CLOUD+BLOCKED บันทึกได้ (audit attempt ที่ถูกห้าม), REDACTED_ALLOW บังคับ manifest ไม่ว่าง
- [x] Blocker 1 (DB-level) — trigger กัน `rfq_estimate_link` ให้สร้างได้เฉพาะ RFQ ที่ READY+current
- [x] M5 — schema isolation (`CREATE SCHEMA rfq` + search_path) + ห่อ BEGIN/COMMIT ทุกไฟล์
- [x] M4 — แยก prod (`003_field_policy.sql`) ออกจาก test fixtures (`migrations/test/`), ตั้ง `source_system='SYNTHETIC_TEST'`
- [x] test gap — neg6 tightened (assert error = enquiry guard) + เพิ่ม neg8/9/10/11 + pos4/5

**RFQ service layer v1 (`004`) — Codex review commit 489c5f0 พบ 4 BLOCKER → rework เป็น v2 (`005`)**

> บทเรียน (Codex F1 ถูก): guard แบบ session flag `rfq.privileged` ใน 004 **ไม่ใช่ security boundary** —
> ใคร ๆ ก็ `SET rfq.privileged='on'` เองแล้ว raw UPDATE ผ่านได้; และ REVOKE รายคอลัมน์หลัง table-level
> GRANT **ไม่มีผล** ใน PostgreSQL → 004 ให้ความมั่นใจผิด ๆ

**RFQ service layer v2 — ✅ `005_service_layer_v2.sql` (2026-07-27), verify 41 checks green (ephemeral pg16):**
- security boundary = **ROLE + REVOKE DML + SECURITY DEFINER** (ไม่ใช่ flag):
  - `rfq_app` = read-only (SELECT) + EXECUTE service function เท่านั้น → **เขียนตารางตรงไม่ได้เลย** (ปิด F1/F2)
  - ownership โอนให้ `rfq_owner`; function ทั้งหมด SECURITY DEFINER + pinned `search_path` + REVOKE จาก PUBLIC
- [x] **F1** raw UPDATE / `SET flag` bypass → app โดน 42501 แม้ตั้ง flag เอง (พิสูจน์ใน T04)
- [x] **F2** raw INSERT ready / child mutation / reparent → app INSERT/UPDATE/DELETE ทุกตาราง denied (T04)
- [x] **F3** readiness TOCTOU → **parent-lock protocol**: `mark_ready` + readiness-input mutator
      (`add/resolve_clarification`, `add/revoke_signoff`) LOCK parent RFQ (FOR UPDATE) ก่อน + freeze เมื่อ locked
      → serialize จริง (T03a/b: B ถูก block แล้ว reject, state ไม่เพี้ยน)
- [x] **F4** `mark_ready(NULL)` bypass + `is_current` → NULL/stale = 40001, require current (T05, st3b/st4b)
- [x] **F6** `create_rfq_revision` clone spec tree ครบ (item→qty/variant/component→corrugated/process/packing/
      delivery, remap FK ด้วย natural key) + atomic (T08: fail กลาง clone → rollback ครบ)
- [x] **Blocker 1/3 + M1/M2** (รอบก่อน) คงอยู่: readiness rules, both-side history atomic, revision trigger validation-only

**Codex verification commit 3516564 → Verdict: boundary ผ่าน, พบ V1 BLOCKER → Claude แก้ (commit ด้านล่าง)**
- [x] **V1 (BLOCKER)** authorization/separation-of-duties — `rfq_app` ปลอม REVIEWER/CONFIRMED sign-off + spoof
      policy version + สั่ง Ready เองได้ → แก้:
      - แยก role **`rfq_ingest`** (ENQ worker) — เรียก `mark_ready/add_signoff/revoke_signoff/create_rfq_revision`
        **ไม่ได้** (T09 พิสูจน์: 42501); reviewer/Ready capability = `rfq_app` เท่านั้น (ผ่าน FastAPI authz)
      - `mark_ready` **ตัด policy-version param** → ใช้ trusted constant ในตัว (T10: spoof เชิงโครงสร้างไม่ได้,
        readiness_run บันทึก version ที่เชื่อถือได้เสมอ, ไม่มี 6-arg overload)
      - **ค้าง (app-layer):** `p_actor` ต้องมาจาก authenticated server context (FastAPI) — DB บังคับไม่ได้ → เป็น contract ของ FastAPI
- [x] **V5 (partial)** normalize role attributes (ALTER ROLE NOLOGIN/NOSUPERUSER/…) + explicit `REVOKE CREATE` จาก app/ingest

**Codex go/no-go commit 612eb2f → Verdict: CONDITIONAL GO → Claude ทำ ENQ→initial DRAFT (`006`)**
- [x] **create_rfq_draft(jsonb, actor, service, request_id)** — ENQ write path v1 (`006_enq_ingest.sql`):
      header + spec tree atomic ผ่าน single SECURITY DEFINER function; grant EXECUTE **เฉพาะ `rfq_ingest`**
      - **#1** schema_version=`draft-v1` + reject unknown key ทุกชั้น (top/header/item/child) — allowlist strict, ไม่มี generic mapper
      - **#2/#3** lifecycle server-controlled (revision_no=1/is_current/status=DRAFT/row_version=1, created_by=trusted actor);
        payload ตั้ง status/row_version/actor/identity = โดน reject (unknown key)
      - **#4/#10** atomic — fail กลาง insert → rollback ครบ ไม่มี partial draft (T12)
      - **#7** limit: payload ≤1MB, items ≤100, child/array ≤200 (reject ก่อน expand)
      - **#9** idempotency: `rfq_ingest_request` PK(service,request_id)+sha256 — replay เดิม→id เดิม, payload/actor ต่าง→conflict
      - FK resolve ด้วย natural key (process.component_no→component id, delivery.option_no→quantity id)
- [x] **Codex go/no-go F1** เอา broad SELECT ของ `rfq_ingest` ออก → **create-only จริง** (T11: ไม่มี table SELECT/DML, effective EXECUTE = [create_rfq_draft] เท่านั้น)
- [x] **fail-closed:** v1 ปฏิเสธ `extraction_runs`/`field_evidence` ใน payload → สร้าง AI evidence โดยไม่มี provenance ไม่ได้;
      **#6** (validate run SUCCEEDED/ไม่ BLOCKED/policy allow) → v1.1 พร้อม extraction pipeline จริง

**Codex 006 review commit 28cfd79 → FIX-THEN-PROCEED, พบ F1-F9 → Claude harden (commit ด้านล่าง)**
- [x] **F2** internal ref ที่ระบุแล้ว resolve ไม่ได้ (process.component_no/delivery.option_no) → **reject 23503** (เดิมเงียบเป็น NULL) — is10a/b
- [x] **F3** child-array limit ≤200 ครอบ **ทุก 6 arrays** (เดิมแค่ 2) ผ่าน `_child_array` helper (type+count) — is11a/b
- [x] **F4A** idempotency concurrency: **advisory-xact-lock claim** ก่อนสร้าง tree → concurrent same key ได้ id เดียวกัน (ไม่ใช่ loser 23505) — T14
- [x] **F4B** idempotency bind actor: replay ที่ actor ต่าง = conflict (เดิมเทียบแค่ payload) — is5c
- [x] **F5A** corrugated ผิดชนิด = reject (เดิมข้ามเงียบ) — is12; **F5B/F7** ตัด opaque JSON (`grade_spec_snapshot`/`specification_extra`) ออกจาก draft-v1 — is13a/b
- [x] **F8** arg length/charset limit + normalize (tab/control char/ยาวเกิน = reject) — is14a/b/c; effective allowlist: REVOKE PUBLIC จาก legacy trigger fn (owner→rfq_owner กัน trigger chain พัง) — T11 enumerate
- [x] **F8.1** `_reject_unknown_keys`/`_child_array` เป็น SECURITY INVOKER (ลด surface); **F9** doc: "006 ไม่มี dependency ต่อ pgcrypto" (ไม่ใช่ 'เลี่ยงทั้ง repo')
- [ ] **F1/F6 (gated)** AI-derived values ยังไม่มี provenance ใน v1 → v1 = **manual/synthetic DRAFT เท่านั้น**; ก่อนต่อ AI extractor จริงต้องทำ **ENQ v1.1** (extraction run + evidence atomic)
- [x] payload contract → [`RFQ_DRAFT_PAYLOAD_V1.md`](RFQ_DRAFT_PAYLOAD_V1.md)
- [x] **test**: `020` 18 + `030` 15 + `040` **27** (is15 = รัน payload ตัวอย่างในเอกสารจริง กัน doc drift) + `rfq_concurrency_tests.py` **T01-T14** =
      **74 implemented checks + 1 gated skip (T07/F7)**; รันซ้ำได้ด้วย `migrations/test/run_all.sh` — ALL SUITES PASSED

**ยังเหลือ (documented, gated ตาม review — ไม่ block ENQ→initial DRAFT):**
- [ ] **V2 (HIGH, ก่อน Ready จริง)** sign-off latest/active-decision rule (ตอนนี้ `EXISTS CONFIRMED` → CONFIRMED แล้ว REJECTED
      ยังผ่าน) + `revoke_signoff` เป็น append-only/มี revoked_by/at/reason (ตอนนี้ DELETE ทิ้ง audit) → รวมใน F5 gate
- [ ] **V3 (HIGH, ก่อน draft-edit)** เมื่อมี draft edit/upsert: ทุก readiness mutation ต้อง lock parent + reject terminal
      + **bump parent `row_version`** (ตอนนี้ clarification/signoff lock parent แล้วแต่ยังไม่ bump version)
- [ ] **V4 (MED, ก่อน revision endpoint)** attachment/field-evidence carry-forward policy — attachment ใช้ link/lineage ไม่ duplicate binary;
      evidence สร้าง derivation provenance (map old subject→new) ไม่ copy UUID เก่า
- [ ] **F5** validator ยัง minimal (`pkg-minimal-v1`) — เติม master-gateway revalidate / egress gate / rules ครบก่อน Ready จริง
- [x] **F7 idempotency (ปิดแล้ว)** — sequential replay (PK+hash+actor) + **concurrent same-key** ผ่าน advisory-xact-lock
      → ทั้งคู่ได้ id เดียว (T14); เหลือ **operational**: FastAPI ต้อง commit/rollback ทันที + ตั้ง statement/txn timeout
      กัน connection ค้างถือ advisory lock (documented ใน payload contract)
- [ ] **F8** durable audit ของ readiness attempt ที่ fail (RAISE=rollback ทำหาย — v1 ยอมรับ)
- [ ] **ENQ v1.1** — extraction_runs + field_evidence ใน payload (พร้อม #6: validate run SUCCEEDED/ไม่ BLOCKED/policy allow
      ก่อน insert AI evidence) + resolve subject-ref เป็น UUID; ตอนนี้ v1 fail-closed (reject keys เหล่านี้)
- [ ] **V5 (เหลือ)** production migration: fixed migration owner + `ALTER DEFAULT PRIVILEGES FOR ROLE <owner>`,
      inspect effective privilege ของ login role จริง (ไม่ใช่แค่ SET ROLE), migration runner + version tracking
- [ ] M3 orphan protection, external_ref append-only audit

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

### สถานะ Credential ปัจจุบัน

- ปัจจุบันมีเพียง `ANTHROPIC_API_KEY` สำหรับให้ Company AI Brain เรียก Claude API
- Key นี้เป็น **outbound provider credential** ไม่ได้ใช้ยืนยันตัว Voicebot หรือ service ที่เรียก Company AI Brain
- ห้ามส่ง `ANTHROPIC_API_KEY` ให้ client และห้ามนำมาใช้ซ้ำเป็น service API key
- ระบบยังไม่มี **inbound service API key** ต้องสร้าง secret แยกต่างหากเมื่อเริ่มงานส่วนนี้

### Data Egress — ต้องแยกจาก Authentication

Service API key แก้เฉพาะ **Inbound Access** ว่า service ใดเรียก Company AI Brain ได้ แต่ไม่ป้องกันข้อมูลออกนอกองค์กร

เส้นทางของ `/ask` ปัจจุบัน:

```text
User/Voicebot
→ Company AI Brain
→ Retrieval จาก Qdrant ภายใน
→ ส่ง Question + Retrieved Context ไป Claude API
→ รับคำตอบกลับ
```

ดังนั้นเมื่อใช้ `/ask` เนื้อหาที่ดึงจากเอกสารบริษัทจะถูกส่งไปยังผู้ให้บริการ Claude ตาม context ที่ประกอบใน `app/main.py` ส่วน `/search` ไม่เรียก Claudeเอง แต่ต้องตรวจ consumer เช่น Voicebot ต่อด้วย เพราะ consumer อาจนำผลจาก `/search` ไปส่ง Cloud LLM อีกทอดหนึ่ง

Authentication และ Data Egress เป็นคนละปัญหา:

| ปัญหา | วิธีควบคุม |
|---|---|
| ใครเรียก Brain ได้ | Service API key ช่วง PoC / Keycloak ใน Production |
| เอกสารใดส่งออกไป Cloud LLM ได้ | Outbound policy ตาม classification |
| ห้ามข้อมูลออกนอกองค์กรทั้งหมด | ปิด Cloud `/ask` และใช้ Local LLM/vLLM |

ก่อนทำ Service API key ต้องตัดสินใจ Data Egress Policy ด้วย ทางเลือกที่เสนอ:

1. **Local-only:** `/ask` ใช้ vLLM ภายในทั้งหมด
2. **Split policy:** เอกสารที่อนุมัติเท่านั้นส่ง Cloud; L3/L4 บังคับ Local-only
3. **PoC Cloud แบบจำกัด:** ใช้เฉพาะชุดข้อมูลทดสอบที่ไม่มีข้อมูลลับ พร้อมลดและ redact context

Service API key ไม่ควรถูกนำเสนอว่าแก้ปัญหาความลับของข้อมูลที่ส่งไป Claude

### ทางสายกลางที่เลือกพิจารณา

ใช้ API key ต่อ calling service และ map role ฝั่ง server:

```text
API key
→ Service Identity
→ Allowed Roles/Scopes
→ Qdrant Policy Filter
```

กติกา:

- สร้าง key ใหม่สำหรับแต่ละ calling service เช่น Voicebot โดยไม่เกี่ยวกับ Claude API key
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
| ผู้เรียก API สามารถอ้าง `admin` เอง | 🟡 กลไกปิดพร้อมแล้ว 2026-07-27: X-API-Key + role scope + audit log **รันอยู่ใน mode `warn`** (commit `0b16bd5`) — ทดสอบ enforce branch ผ่าน (401/403) ขั้นสุดท้าย: แจก key ให้ voicebot (ค่า key อยู่ `~/company-ai-brain/api_keys_ISSUED.txt` บน server, chmod 600) แล้วเปลี่ยน `.env` เป็น `AUTH_MODE=enforce` + `docker restart brain_api` |
| `/ask` ส่ง Question + Retrieved Context ไป Claude API | ยืนยันจากเส้นทางโค้ด — ต้องตัดสินใจ Data Egress Policy |
| Unknown document fallback เป็น `PRODUCTION` | ✅ แก้แล้ว 2026-07-24 — เปลี่ยนเป็น UNCLASSIFIED (admin เท่านั้น) |
| ตัวอย่าง RBAC ใน Markdown ใส่ `admin` ใน MatchAny | ✅ แก้แล้ว 2026-07-24 |
| Duplicate logical documents แย่งพื้นที่ `top_k` | ยืนยันจาก Qdrant scan |
| Revision เก่า-ใหม่ซ้อนกัน | ยังไม่พบ |
| `/ask` ผ่าน full evaluation | ✅ baseline ผ่านแล้ว 2026-07-27 (hit 92%, hallucination 0, leak 0) — เหลือ faithfulness judge + probes ชุดเต็ม |
| **[ใหม่ 2026-07-27]** Estimate master_data API ตอบโดย**ไม่มี auth** บน port ภายใน (3099/4010) และเปิดเผยราคา (`price`, `price_import`, `rate`, `min_cost`) ให้ทุกเครื่องใน LAN | ยืนยันจาก probe — แจ้งทีม Estimate/API owner พิจารณา (เกี่ยวพันงาน inbound API key) |
| เครดิต Anthropic API หมด → `/ask` 502 (เคยเกิด 2026-07-27) | ✅ แก้แล้ว — เติมเครดิต, `/ask` กลับมา 200; **บทเรียน: ตั้ง billing alert ใน Console** |
| เอกสารไทยเพี้ยนกลุ่ม AFII 6 ไฟล์ (text layer พัง) | ✅ **แก้แล้ว 2026-07-27** — OCR ด้วย Claude vision → re-ingest 114 chunks; scan corpus แล้ว afii=0, mojibake=0; `/ask` ตอบเนื้อหา FSC ได้; markdown สะอาด sync กลับ `parsed_output/` local แล้ว |

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

---

## 10. ข้อเสนอ Local LLM Hardware — ยังไม่ Lock

**สถานะ:** ข้อเสนอสำหรับวางแผน ต้อง benchmark ด้วย eval set และ workload จริงก่อนซื้อ

### ความสัมพันธ์กับ PDF

PDF เป็น Business/Agent Blueprint และไม่ได้กำหนด Local LLM, Qwen, vLLM หรือสเปก GPU โดยตรง

| ส่วนใน PDF | สิ่งที่ต้องสร้าง | เทคโนโลยีในโปรเจกต์ |
|---|---|---|
| AI Board / Agent แต่ละแผนก | Application และ Agent Workflow | Open-WebUI/หน้าจอ Agent + FastAPI |
| Pornchai Knowledge Base / สมองกลาง | Knowledge Layer ร่วม | Company AI Brain + Qdrant + RBAC |
| Agent ตอบและอธิบาย | Generator/Reasoning Engine | ปัจจุบัน Claude API; เป้าหมาย Local คือ Qwen3-32B ผ่าน vLLM |
| RFQ → ตรวจสอบ → Ready for Estimate | Structured Workflow | PostgreSQL + RFQ service |
| คำนวณต้นทุนและราคา | Deterministic Business Engine | Costing Engine + Master Data ใน PostgreSQL |
| ผู้จัดการตรวจและอนุมัติ | Human Approval | Workflow, Audit และ Authority Rules |

Local LLM จึงเป็น “เครื่องยนต์ภาษา” ใต้ Agent และ Company AI Brain ไม่ใช่ Agent, Knowledge Base หรือ Costing Engine ทั้งระบบ

คำสั่ง `vllm serve Qwen/Qwen3-32B` ทำเพียงเปิด model server หลังจากมี GPU/driver/model weights แล้ว งานเชื่อมระบบยังต้อง:

1. เปิด vLLM endpoint ภายใน เช่น port 8001
2. เปลี่ยน `generate_answer()` ใน `app/main.py` จาก Anthropic client ไปเรียก vLLM OpenAI-compatible API
3. ส่ง system prompt + retrieved context ไป Qwen ภายใน
4. ปิด Claude fallback/outbound network สำหรับ Local-only policy
5. รัน `/ask` evaluation ใหม่ทั้งคุณภาพ, latency, citation และ permission

### Capability Ladder — LLM ไม่เท่ากับ Agent

| สิ่งที่มี | สิ่งที่ทำได้ | ตรงกับ PDF แค่ไหน |
|---|---|---|
| LLM อย่างเดียว | สนทนา, คิด, ร่างเอกสารจาก prompt/ไฟล์ที่ป้อนครั้งนั้น | เป็นเพียงเครื่องยนต์ภาษา/ที่ปรึกษาทั่วไป |
| LLM + Company AI Brain | ตอบจากเอกสารบริษัทพร้อม permission และ citation | เป็น Knowledge Assistant และฐานของ Agent |
| เพิ่ม RFQ + Master Data + Costing Engine | อ่าน Enquiry, ตรวจข้อมูลขาด, เสนอวิธีผลิตและคำนวณต้นทุนที่ตรวจสอบได้ | เริ่มเป็น Estimate Agent V1 |
| เพิ่ม Workflow + Approval + Audit + Integration | ส่งอนุมัติ, สร้าง Quotation, ติดตาม KPI และส่งข้อมูลต่อ OTB/ERP | ใกล้ความต้องการฉบับเต็มใน PDF |
| เพิ่ม Agent Blueprint/Tools รายแผนก | Agent หลายแผนกใช้ Brain/Auth/Audit ร่วมกัน | ไปสู่ AI Board of Advisors |

มี LLM อย่างเดียวจึงยังไม่ครบความต้องการใน PDF เพราะไม่มี company memory ที่ควบคุมได้, live master data, deterministic costing, workflow, approval, audit และการเชื่อมต่อระบบจริง

### Assumption สำหรับออกแบบ

- Model หลัก: Qwen3-32B
- Runtime: vLLM
- RAG context ใช้งานจริงตั้งต้น: 8K tokens ไม่เปิด 32K/131K โดยไม่จำเป็น
- ผู้ใช้ระดับ Pilot: 10–20 คน, concurrent generation โดยทั่วไป 2–5 requests
- Voicebot ใช้ non-thinking/low-latency path
- Estimate Agent ใช้ 32B สำหรับ reasoning/explanation แต่ Costing ยังเป็น deterministic engine

### Memory Planning

- Qwen3-32B มี 32.8B parameters
- BF16 weights ใช้หน่วยความจำเชิงทฤษฎีประมาณ 65.6GB ก่อน runtime overhead และ KV cache
- INT4/AWQ weights ใช้โดยประมาณ 18–22GB หลังรวม metadata แต่ต้อง benchmark คุณภาพและ throughput
- การที่ weights ใส่ GPU ได้ไม่ได้แปลว่าจะรองรับ concurrency ได้ เพราะ KV cache ใช้ VRAM เพิ่มตามจำนวน token และ request

### ระดับเครื่องที่เสนอ

| ระดับ | GPU | การใช้งาน |
|---|---|---|
| PoC ประหยัด | 1× RTX 5090 32GB | Qwen3-32B INT4/AWQ, context 8K, concurrency ต่ำ; ไม่ใช่ production HA |
| แนะนำสำหรับบริษัท | 1× RTX PRO 6000 Blackwell Server 96GB + GPU 16–24GB สำหรับ embedding/reranker | Qwen3-32B BF16/FP8 บนการ์ดเดียว จัดการง่าย มี ECC และเหลือ VRAM สำหรับ KV cache |
| ทางเลือก Data Center | 2× L40S 48GB + GPU 16–24GB สำหรับ embedding/reranker | Qwen3-32B BF16 แบบ tensor parallel, VRAM รวม 96GB, การ์ด server 350W/ใบและ ECC |
| ถ้ามี 4090 อยู่แล้ว | 4× RTX 4090 24GB | ใช้ได้สำหรับ PoC/benchmark แต่ไม่มี ECC/NVLink, GPU power รวมสูงสุด 1.8kW และต้องออกแบบ PCIe/ไฟ/ความร้อนจริงจัง |
| Production HA | 2 nodes ของ configuration ที่เลือก | แยก replica หลัง load balancer เพื่อ maintenance/failure โดยไม่หยุดบริการ |

### Server รอบ GPU

- CPU: Server CPU 24–32 cores ขึ้นไป พร้อม PCIe lanes พอสำหรับจำนวน GPU
- RAM: 256GB ECC; เลือก 512GB ถ้าต้องรองรับ model ใหญ่ขึ้นหรือหลาย service
- Storage: Enterprise NVMe อย่างน้อย 2×3.84TB แบบ mirror สำหรับ model cache, artifacts และข้อมูลระบบ
- Network: 10GbE ขั้นต่ำ; 25GbE ถ้าแยก LLM, Vector DB และ storage คนละเครื่อง
- Power/UPS: คำนวณจาก GPU TDP + CPU + headroom; consumer multi-GPU ห้ามใช้ PSU/ปลั๊กบ้านแบบเดา
- Cooling: ต้องคำนวณ heat load และ airflow; L40S/Server Edition แบบ passive ต้องอยู่ใน chassis ที่ออกแบบรองรับ

### Software/Network

- Ubuntu LTS + Docker + NVIDIA Container Toolkit
- vLLM OpenAI-compatible endpoint ภายในเท่านั้น
- Qwen3-32B เริ่มที่ `max_model_len=8192`
- BGE-M3 และ reranker แยก process/GPU จาก generation เมื่อขึ้น production
- Block outbound internet จาก inference network; ดาวน์โหลด model ผ่าน maintenance workflow และเก็บใน internal cache/registry
- ห้ามมี fallback ไป Claude สำหรับ collection ที่กำหนด Local-only

### Recommendation ปัจจุบัน

ถ้าซื้อใหม่และเน้นความเรียบง่าย/เสถียร ให้ขอราคา **1× RTX PRO 6000 Blackwell Server 96GB** เทียบกับ **2× L40S 48GB** ก่อน ส่วน **1× RTX 5090 32GB** เหมาะสำหรับพิสูจน์ Qwen3-32B INT4 และเก็บ benchmark เท่านั้น

อย่าซื้อ 4× RTX 4090 ใหม่โดยอัตโนมัติเพียงเพราะเป็น target เดิม ให้เทียบราคาทั้งเครื่อง, PCIe layout, PSU, UPS, แอร์, ECC, warranty และ throughput ต่อบาทกับตัวเลือก 96GB server GPU ก่อน

---

## 11. Privacy Assessment ของ PDF

**ข้อสรุป:** PDF ให้ความสำคัญกับ Business Workflow, Human Approval และ Accountability มากกว่า Privacy/Security โดยตรง จึงห้ามตีความว่าเอกสารอนุมัติให้ส่งข้อมูลบริษัทไป Cloud LLM

### สิ่งที่ PDF กล่าวถึง

- หน้า 12: ผู้ใช้แต่ละกลุ่มเห็นข้อมูลไม่เท่ากันตามสิทธิ์ เช่น Sales อาจเห็นราคาขายแต่ไม่เห็นรายละเอียดต้นทุน
- หน้า 17: เมื่อมนุษย์แก้เครื่องจักร, Waste, เวลา หรือ Margin ต้องบันทึกว่าใครแก้อะไรและเพราะเหตุใด
- หน้า 18: มี Approval Rules ตามระดับ Margin และอำนาจอนุมัติ
- หน้า 25: ทุกสูตรต้องมี Owner/ผู้อนุมัติ, ทุกการแก้ข้อมูลต้องมี Version/ประวัติ, AI ต้องบอกแหล่งข้อมูลและสมมติฐาน และผู้จัดการยังรับผิดชอบการตัดสินใจ

สิ่งเหล่านี้คือ **Governance, Authorization และ Auditability** ไม่ใช่ Privacy/Data Residency โดยตรง

### สิ่งที่ PDF ไม่ได้กำหนด

- ข้อมูลประเภทใดส่งออกนอกบริษัทได้
- Cloud LLM ใช้ได้หรือไม่
- Data Classification เช่น L1–L4
- PII, Customer Data และ Commercial Confidentiality
- Encryption in transit/at rest
- Data Retention และการลบข้อมูล
- Authentication เช่น Keycloak/API key
- Vendor risk, DPA หรือการใช้ข้อมูลโดยผู้ให้บริการ
- Network egress และ Local-only requirement
- Prompt injection, log redaction และ incident response

### ระดับที่ประเมิน

| มิติ | ระดับใน PDF |
|---|---|
| Human approval / accountability | สูง |
| Role visibility / authority | มีแนวคิด แต่ยังไม่เป็น security specification |
| Audit/versioning | ค่อนข้างสูงในเชิง requirement |
| Privacy / PII | แทบไม่กล่าวถึง |
| Data residency / Cloud egress | ไม่กล่าวถึง |
| Technical security controls | ไม่กล่าวถึง |

PDF จึงควรใช้เป็น Business Blueprint ส่วน Privacy, Security และ Data Egress ต้องมีเอกสารนโยบาย/requirements แยกก่อนนำข้อมูลจริงเข้า Agent

---

## 12. Model Strategy — Claude + Local LLM (ยังไม่ Lock)

**คำศัพท์:** Claude เป็น LLM แบบ Cloud อยู่แล้ว ส่วน “Local LLM” ในข้อเสนอนี้หมายถึง Qwen หรือโมเดลอื่นที่รันบน server บริษัท

### ข้อเสนอ

ไม่ควรส่งทุกคำถามให้ Claude และ Local LLM พร้อมกันโดยอัตโนมัติ ให้ใช้ **Policy-based Routing** เลือก generator ตาม classification, workload และ data-egress policy:

```text
Authentication
→ RBAC/ABAC Filter
→ Retrieval
→ Data Egress Policy
   ├─ Search-only / deterministic tool
   ├─ Local LLM
   └─ Claude เฉพาะข้อมูลที่อนุมัติให้ออก Cloud
```

### Routing เริ่มต้นที่เสนอ

| งาน/ข้อมูล | Backend |
|---|---|
| Calculation, Costing, Margin Rules | Deterministic Engine ไม่ใช้ LLM คิดสูตร |
| L3/L4, Customer Cost, HR, Finance, Actual Cost | Local LLM เท่านั้น |
| L1/L2 ที่ผ่านการอนุมัติ Cloud | Claude หรือ Local ตาม quality/latency |
| Voicebot คำถามสั้น | Local model ขนาดเล็ก/non-thinking path |
| Estimate reasoning/explanation | Local Qwen3-32B + structured tools |
| Benchmark/Evaluation ด้วยข้อมูลสังเคราะห์หรือข้อมูลที่อนุมัติ | Claude ใช้เป็น reference ได้ |

### สิ่งที่ไม่ควรทำ

- ส่ง context เดียวกันให้สองโมเดลทุกครั้ง
- ส่งคำตอบ Local พร้อมเอกสารลับไปให้ Claude “ตรวจคำตอบ”
- ให้ Router/LLM ตัดสิน classification เองโดยไม่มี policy ที่บังคับ
- ใช้ Claude เป็น fallback อัตโนมัติสำหรับ L3/L4 เมื่อ Local ล่ม
- ให้ LLM ตัวใดตัวหนึ่งคำนวณต้นทุนแทน Costing Engine

### ลำดับที่เหมาะกับทีมเล็ก

1. ระยะ PoC: Company AI Brain + Claude เฉพาะ corpus ที่อนุมัติ
2. วัด quality, latency, API cost และ data sensitivity
3. เพิ่ม Local LLM เป็น backend ที่สลับได้ผ่าน `generate_answer()`
4. บังคับ L3/L4 เป็น Local-only
5. ใช้ Claude เป็น optional backend/reference ไม่ใช่ dependency ของทุก request

Hybrid มีประโยชน์เมื่อมี policy และเหตุผลชัดเจน แต่การมีสองโมเดลไม่ได้ทำให้ระบบดีขึ้นเอง ส่วนที่เพิ่มคุณค่ามากกว่าคือ Company AI Brain, structured data, deterministic tools, evaluation และ workflow

---

## 13. UI Scope — Company AI Brain กับ Agent Frontend

### ตำแหน่งของโปรเจกต์ปัจจุบันใน PDF

Company AI Brain ตรงกับส่วน **Pornchai Knowledge Base / สมองกลาง** ใน PDF โดยเป็น Knowledge Layer ที่ Agent ทุกตัวอ่านข้อมูลชุดเดียวกัน ไม่ใช่ Estimate Agent หรือหน้า RFQ ทั้งระบบ

สถานะปัจจุบัน:

- เป็น backend service ผ่าน FastAPI
- มี `/search`, `/ask`, `/health`, `/collections`
- ไม่มี frontend implementation ใน repository นี้
- Voicebot สามารถใช้ `/search` ได้โดยไม่ต้องมีหน้าเว็บของ Brain
- `/ask` เป็น Knowledge Assistant เบื้องต้น แต่ยังไม่มี RFQ/Costing/Approval workflow

### ต้องมีหน้าเว็บหรือไม่

**Company AI Brain Core:** ไม่จำเป็นต้องมีหน้าเว็บสำหรับผู้ใช้ทั่วไป สามารถเป็น backend-only และให้ Open-WebUI, Voicebot, Claude/GPT tool หรือ Agent อื่นเรียก API ได้

**Production Operations:** ควรมี Admin Console ในภายหลังสำหรับ:

- Upload/ingest document
- Preview parsed content/OCR
- เลือก collection, classification และ allowed groups
- อนุมัติ/ปฏิเสธก่อน publish
- จัดการ canonical document, duplicate และ revision
- ดู ingest status, quality gate, audit และ evaluation

**Estimate Agent ตาม PDF:** ต้องมีหน้าเว็บหรือ application UI เฉพาะ เพราะหน้า 28–29 ของ PDF กำหนด RFQ form และ workflow ที่ chat อย่างเดียวไม่พอ

หน้าขั้นต่ำ:

1. RFQ Inbox/List
2. Create/Edit RFQ Form
3. Missing Data และ Ready-for-Estimate Checklist
4. Production Alternatives
5. Cost Breakdown, Pricing และ Similar Jobs
6. Human Review/Approval
7. Quotation และ Audit History
8. Dashboard/KPI ในระยะถัดไป

### การแบ่งระบบที่แนะนำ

```text
Open-WebUI / Voicebot
        ↓
Company AI Brain API
        ↓
Qdrant Knowledge Layer

Estimate Web App
        ↓
Estimate API / Workflow
        ├─ PostgreSQL RFQ + Master Data
        ├─ Deterministic Costing Engine
        └─ Company AI Brain API
```

Frontend และ Agent ห้ามเรียก Qdrant โดยตรง ต้องผ่าน API/Policy Layer เสมอ

---

## 14. RFQ_Estimate Inspection — จุดเชื่อม RFQ → Estimate

**ตรวจจาก source จริงวันที่:** 2026-07-27
**โปรเจกต์:** `../RFQ_Estimate` (ในเอกสารภายในเรียก Estimate-Packaging)

### ข้อสรุป

`RFQ_Estimate` เป็น Web Application และ Costing Engine เดิมที่ควรนำกลับมาใช้เป็น **ปลายทางของ RFQ** ไม่ใช่ Company AI Brain และยังไม่ใช่ RFQ Intake/Workflow ตาม PDF ครบทั้งกระบวนการ

สิ่งแรกที่ควรทำจริงคือ:

> กำหนด Data Contract และ Readiness Gate ระหว่าง `Enquiry/RFQ` กับ `Estimate-Packaging` ก่อนต่อ AI, Brain หรือเปลี่ยน LLM

### หลักฐานจาก Code Path ปัจจุบัน

1. หน้า `/` เป็น RFQ List แต่ปุ่มสร้าง RFQ ใหม่เปิด `/estimate` โดยตรง (`public/js/function_index.js:42-44`)
2. AI RFQ ส่งข้อความ/ไฟล์ไป Claude ที่ `/ai/parse-spec`, เก็บ JSON ชั่วคราวใน `sessionStorage` แล้วเปิด `/estimate?ai=1` (`public/js/function_ai_rfq.js:97-125`)
3. Claude ทำหน้าที่สกัดข้อมูลและเติมฟอร์มเท่านั้น ไม่ได้คำนวณต้นทุน (`router/ai.js:901-985`)
4. สูตรคำนวณ 16 cost items ทำงานใน JavaScript ฝั่ง browser แล้วแปลง `est.mainData` เป็น payload หลายตาราง (`public/js/prepare_data.js`)
5. การบันทึกยิง `POST /estimate/save_rfq` ไป backend แยก (`public/js/function_estimate.js:2353-2384`)
6. Payload ระบุ `doc_type: 'est'` แสดงว่า record นี้คือ Estimate แม้ UI จะเรียกว่า RFQ (`public/js/prepare_data.js:107-148`)
7. Workflow ที่มีใน source คือ `Draft → Pending → Reject/Approved` สำหรับการอนุมัติ Estimate (`public/js/documentStatusManagerClass.js:351-358`)
8. ไม่พบ state `Enquiry` หรือ `Ready for Estimate` ใน application source
9. Backend หลักจริงอยู่นอก repository; `test-backend` เป็นเพียง mock ที่ endpoint บางส่วนยังเป็น stub และใช้ `job_data` JSONB เป็น source of truth

### ความหมายเมื่อเทียบกับ PDF

PDF หน้า 28–29 แยกขั้นตอนประมาณ:

```text
Enquiry
→ Create RFQ
→ Check Completeness
→ Approve RFQ
→ Ready for Estimate
→ Estimate / Costing / Approval
```

แต่ระบบปัจจุบันทำประมาณ:

```text
Create "RFQ"
→ เปิดหน้า Estimate ทันที
→ คำนวณราคา
→ Draft / Pending / Reject / Approved
→ Quotation
```

ช่องว่างหลักจึงไม่ใช่ “ยังไม่มี LLM” แต่คือยังไม่มี boundary ที่ชัดระหว่างข้อมูลที่ Sales/AE รับจากลูกค้า กับข้อมูลที่ผ่านการตรวจจนพร้อมให้ Estimator คำนวณ

### งานแรก — Sprint 0: RFQ Contract + Ready Gate

**เป้าหมาย:** ทำให้ RFQ ที่ผ่าน `Ready for Estimate` สามารถเติมข้อมูลเข้า Estimate-Packaging เดิมได้โดยไม่ต้องกรอกซ้ำ และข้อมูลไม่ครบต้องผ่านไม่ได้

Deliverables:

1. สร้าง `RFQ Contract v1` จาก field ใน PDF เทียบกับ `est.mainData`
2. แบ่ง field เป็น:
   - Required ก่อน Ready for Estimate
   - Optional
   - Estimator-only
   - Derived/Calculated
3. ใส่ metadata ขั้นต่ำ:
   - `rfq_id`
   - `schema_version`
   - `revision`
   - `status`
   - `source_document`
   - `field_evidence`
   - `created_by`, `checked_by`, `approved_by`
   - timestamps และ audit reason
4. แยก RFQ workflow ออกจาก Estimate approval:
   - `ENQUIRY_DRAFT`
   - `RFQ_DRAFT`
   - `RFQ_CHECKED`
   - `RFQ_APPROVED`
   - `READY_FOR_ESTIMATE`
5. ทำ adapter ที่ map `RFQ Contract v1 → est.mainData`; ห้ามให้ Claude เขียนฐานข้อมูลหรือส่ง Ready เอง
6. ทดลองกับ RFQ จริง 10 ใบก่อนสร้าง UI ใหญ่หรือย้ายสูตรคำนวณ

Acceptance criteria:

- RFQ ข้อมูลไม่ครบถูก block พร้อมรายการ field ที่ขาด
- ผู้มีอำนาจเท่านั้นที่เปลี่ยนเป็น `READY_FOR_ESTIMATE`
- RFQ ที่ Ready แล้วเปิดหน้า Estimate เดิมและ prefill ได้โดยไม่กรอกซ้ำ
- ทุกค่าที่ AI อ่านได้มี source/evidence และมนุษย์แก้ไขได้
- ผลคำนวณจาก Estimate เดิมยังตรง baseline เดิมใน test cases

### ลำดับงานหลัง Sprint 0

1. **RFQ Contract + Ready Gate** — ทำก่อน
2. **ทดสอบ AI extraction** กับ RFQ จริง โดยวัด field accuracy และ missing-field recall
3. **แยก Costing Engine ไป server/PostgreSQL** แบบ deterministic พร้อม regression tests; ห้าม rewrite สูตรพร้อมกับออกแบบ RFQ
4. **เชื่อม Company AI Brain** เพื่อค้น SOP, policy, similar jobs และ citation หลัง data boundary ชัด
5. **เลือก Claude/Local LLM** ตาม data-egress policy และ benchmark ไม่ใช่เลือกจากความรู้สึก

### Safety Gate ก่อนแก้หรือรัน RFQ_Estimate

Branch ที่ผู้พัฒนาใช้สำหรับงาน AI/RFQ คือ `ai-fixes-jun15` แต่ current checkout ของ `RFQ_Estimate` ณ วันที่ตรวจอยู่คนละ branch:

- Intended working branch: `ai-fixes-jun15` ที่ commit `c6e1f35`
- Local branch นี้ ahead `origin/ai-fixes-jun15` และ `sirisol/ai-fixes-jun15` อยู่ 1 commit
- Current worktree checkout: `Ai_3Dand2D-dieline` ที่ commit `83089f4`
- Reflog ยืนยันว่ามีการ checkout จาก `ai-fixes-jun15` ไป `Ai_3Dand2D-dieline` เมื่อ 2026-07-23 14:15:38 +0700
- ตรวจ code path ซ้ำจาก Git object ของ `ai-fixes-jun15` โดยไม่ checkout แล้ว: ข้อสรุปยังเหมือนเดิม คือสร้าง RFQ แล้วเข้า `/estimate` โดยตรง, AI ส่งข้อมูลผ่าน `sessionStorage`, บันทึกเป็น `doc_type: 'est'`, มีสถานะ Draft/Pending/Reject/Approved และไม่พบ Enquiry/Ready-for-Estimate workflow
- มี modified/untracked files จำนวนมาก จึงต้อง snapshot/แยก branch ก่อนเปลี่ยนสถาปัตยกรรม
- `index.js` มี local mock login/check-auth และ hardcoded proxy ไป production API
- proxy จับ error แล้วคืน `{ success: true, data: [] }` ซึ่งซ่อน failure จริง

ดังนั้นห้ามใช้ checkout นี้เป็น Pilot environment จนกว่าจะ:

1. แยก Dev/Test/Prod config ชัดเจน
2. ชี้ไป test backend/DB ที่แยกจาก production
3. ปิด mock auth และ production proxy ใน environment ที่ผู้ใช้งานเข้าถึง
4. รักษา worktree ปัจจุบันก่อนแก้ด้วย branch/commit ที่ทีมตกลงร่วมกัน
5. ห้าม checkout ไป `ai-fixes-jun15` ทับ worktree ปัจจุบันทันที เพราะมีไฟล์ค้างจำนวนมากและ branch histories ต่างกันมาก; ให้สร้าง backup/worktree แยกก่อน

### Decision ที่ Lock สำหรับการคุยรอบถัดไป

- Company AI Brain = Knowledge Layer กลาง
- RFQ_Estimate = Existing Estimate Web App + legacy costing ที่ควร reuse
- Claude/Local LLM = ตัวช่วย extract/reason/explain ไม่ใช่ owner ของสูตรหรือ workflow
- งานแรก = RFQ Data Contract + Ready-for-Estimate Gate
- ยังไม่ซื้อ GPU และยังไม่ย้ายสูตรจนกว่าจะผ่าน pilot contract กับ RFQ จริง

---

## 15. ข้อเสนอ Local AI — Legal, Privacy และ Trade Secret Gap

**ตรวจข้อมูลล่าสุด:** 2026-07-27

### ข้อสรุปที่ต้องสื่อให้ถูกต้อง

ห้ามเสนอว่า “กฎหมายบังคับให้ใช้ Local AI” หรือ “ใช้ Cloud AI แล้วผิดกฎหมาย” เพราะไม่ถูกต้องแบบเหมารวม

ข้อเสนอที่ป้องกันความเสี่ยงและน่าเชื่อถือกว่าคือ:

> PDF กำหนด Business Workflow, Approval และ Accountability แต่ยังไม่มี Data Classification, Data Egress Policy, PDPA/Trade Secret Assessment หรือ Vendor Governance ขณะที่ระบบ Estimate จะประมวลผลข้อมูลลูกค้า ผู้ติดต่อ สเปกงาน ราคาต้นทุน Margin เรท Supplier และวิธีการผลิต จึงควรกำหนดให้ข้อมูล Confidential/Restricted ใช้ Local AI เป็นค่าเริ่มต้น และอนุญาต Cloud AI เฉพาะข้อมูลที่ผ่านการจัดชั้น ลดทอน และอนุมัติแล้ว

Local AI เป็น **risk-control architecture** ไม่ใช่ใบรับรองว่าระบบถูกกฎหมายโดยอัตโนมัติ

### เหตุผลทางกฎหมายและ Governance

1. RFQ อาจมีชื่อ เบอร์โทร อีเมล ที่อยู่ และข้อมูลผู้ติดต่อ จึงอาจเป็นข้อมูลส่วนบุคคลภายใต้ PDPA
2. การส่งข้อมูลดังกล่าวให้ Cloud AI เป็นกิจกรรมประมวลผลโดยบุคคลภายนอก และอาจเป็นการส่งหรือโอนข้อมูลไปต่างประเทศ จึงต้องพิจารณาฐานกฎหมาย วัตถุประสงค์ ผู้ประมวลผล สัญญา มาตรการรักษาความปลอดภัย ระยะเวลาเก็บ และหลักเกณฑ์ตามมาตรา 28/29
3. ราคาต้นทุน Margin เรท Supplier สูตรการผลิต Machine Configuration และ Customer Specification อาจไม่ใช่ข้อมูลส่วนบุคคล แต่มีความเสี่ยงด้านความลับทางการค้าและข้อผูกพัน NDA/สัญญาลูกค้า
4. ETDA แนะนำให้องค์กรกำหนด Data Governance, ควบคุมการเข้าถึง ประเมินบริการจากบุคคลภายนอก และไม่ให้นำข้อมูลภายใน/ข้อมูลชั้นความลับ/ข้อมูลส่วนบุคคลไปใช้กับ Generative AI ที่ไม่ได้รับอนุมัติ
5. API key เป็นเพียงการยืนยันตัวตนเพื่อใช้บริการ ไม่ได้ป้องกัน data egress หรือทำให้ PDPA/สัญญาครบถ้วน

### ข้อเท็จจริงของ Claude Commercial/API ที่ต้องใช้ประกอบการตัดสินใจ

- Anthropic ระบุว่า Commercial/API data ไม่ถูกใช้ฝึกโมเดลโดยค่าเริ่มต้น
- Standard API retention ปัจจุบันลบ input/output ภายใน 30 วัน โดยมีข้อยกเว้นเรื่อง Usage Policy/กฎหมาย
- Zero Data Retention ต้องเป็นข้อตกลงที่ Anthropic อนุมัติและไม่ได้ครอบคลุมทุก feature โดยอัตโนมัติ
- Data processing อาจเกิดหลายภูมิภาค และ data storage โดยค่าเริ่มต้นอยู่สหรัฐฯ หากไม่มีข้อตกลงอื่น
- DPA/SCC มีใน Commercial Terms แต่บริษัทไทยยังต้องตรวจว่าตรงกับ PDPA, สัญญาลูกค้า และนโยบายบริษัทหรือไม่

ดังนั้น “Anthropic ไม่เอาข้อมูลไป train” ช่วยลดความเสี่ยงบางส่วน แต่ไม่เท่ากับ “ข้อมูลไม่ออกจากบริษัท” และไม่แทนที่ Data Transfer/Vendor Assessment

### Policy-based Routing ที่เสนอ

| ชั้นข้อมูล | ตัวอย่างใน Estimate/RFQ | Model Policy |
|---|---|---|
| L1 Public | เอกสารเผยแพร่ เว็บไซต์ ข้อมูลผลิตภัณฑ์สาธารณะ | Cloud หรือ Local |
| L2 Internal | คู่มือทั่วไปที่ไม่มีข้อมูลลูกค้า/ต้นทุน | Cloud ที่องค์กรอนุมัติ หรือ Local |
| L3 Confidential | RFQ ลูกค้า ราคาเสนอซื้อ/ขาย ต้นทุน Margin Supplier rate Similar jobs | Local-only เป็นค่าเริ่มต้น |
| L4 Restricted | ข้อมูลส่วนบุคคลอ่อนไหว ความลับตามสัญญา Key/Password ข้อมูล HR/Finance สำคัญ | Local-only หรือ block; ต้องมี Owner/DPO/Legal อนุมัติข้อยกเว้น |

Cloud exception ต้องมีอย่างน้อย:

1. Data owner อนุมัติ
2. Redaction/Minimization ก่อนส่ง
3. ใช้ Commercial API ที่บริษัททำสัญญา ไม่ใช้บัญชี Consumer
4. ตรวจ DPA, subprocessor, processing location และ cross-border safeguards
5. กำหนด retention/deletion และพิจารณา Zero Data Retention
6. Encryption, RBAC, audit log และ incident procedure
7. ห้าม feedback/upload ซ้ำไปช่องทางที่ทำให้ retention policy เปลี่ยนโดยไม่ตั้งใจ

### ถ้อยคำเสนอผู้บริหาร

> “ในเอกสารปัจจุบันมีเรื่องสิทธิ์อนุมัติและ Audit แล้ว แต่ยังไม่มีข้อกำหนดว่าข้อมูลประเภทใดส่งออกนอกบริษัทได้ ระบบ Estimate จะอ่าน RFQ ลูกค้า ต้นทุน Margin เรท Supplier และวิธีการผลิต ซึ่งบางส่วนเข้าข่ายข้อมูลส่วนบุคคล และอีกส่วนเป็นความลับทางการค้า ผมจึงไม่เสนอว่า Cloud AI ผิดกฎหมายทั้งหมด แต่เสนอให้เพิ่ม Data Classification และ Data Egress Policy เป็น requirement ก่อนเริ่มใช้งานจริง โดยข้อมูล Confidential/Restricted ให้ประมวลผลด้วย Local AI ภายในบริษัทเป็นค่าเริ่มต้น ส่วน Claude ใช้กับข้อมูล Public/Internal ที่ผ่านการอนุมัติหรือลดทอนแล้ว วิธีนี้ลดความเสี่ยง PDPA การโอนข้อมูลข้ามประเทศ และการสูญเสียความลับ โดยยังรักษาคุณภาพของ Cloud AI ในงานที่เหมาะสม”

### Decision ที่ขอจากผู้บริหาร

1. อนุมัติให้ Data Classification + Data Egress Policy เป็น Architecture Gate
2. แต่งตั้ง Business Data Owner และให้ DPO/Legal ตรวจ use case RFQ/Estimate
3. อนุมัติ Hybrid PoC:
   - Local AI กับข้อมูล RFQ/ต้นทุนจริง
   - Claude กับข้อมูลสังเคราะห์, redacted หรือข้อมูลที่อนุมัติ Cloud
4. Benchmark คุณภาพ/latency/cost ด้วย eval set เดียวกันก่อนซื้อ GPU
5. ห้ามส่ง Production RFQ จริงไป Cloud จนกว่า DPA, cross-border, retention และสัญญาลูกค้าจะผ่านการตรวจ

### คำแนะนำเลือกโมเดล ณ สถานะปัจจุบัน (2026-07-27)

**Working decision จากผู้พัฒนา (2026-07-27): ใช้ Claude-first สำหรับ PoC**

ใช้ Hybrid แบบแบ่งตามชั้นข้อมูล แต่เริ่ม Cloud เพียงเจ้าเดียว:

1. ระหว่างพัฒนา PoC ให้ใช้ **Claude Commercial/API** ซึ่งมี key อยู่แล้ว กับข้อมูลสังเคราะห์ ข้อมูลที่ redact แล้ว หรือข้อมูล Public/Internal ที่บริษัทอนุมัติ เพื่อทำ RFQ extraction, mapping และช่วยสร้างคำอธิบาย
2. ข้อมูล RFQ จริง ต้นทุน Margin ราคา Supplier สูตรหรือวิธีการผลิต ให้ใช้ **Local LLM** เป็น production-default; ระหว่างที่ Local ยังไม่พร้อม ห้ามแก้ปัญหาด้วยการส่งข้อมูลจริงเข้า Cloud โดยไม่มีการอนุมัติ
3. Local baseline ตาม architecture ปัจจุบันคือ **Qwen3-32B ผ่าน vLLM**; Ollama เหมาะกับการทดลองหรือเครื่องนักพัฒนา แต่ไม่ใช่ตัวตัดสิน business logic และไม่จำเป็นต้องเป็น production runtime
4. **ยังไม่เพิ่ม GPT เป็น Cloud provider ตัวที่สอง** เพราะทำให้ต้องประเมินสัญญา retention, data flow, access key, audit และค่าใช้จ่ายเพิ่มอีกชุด โดยยังไม่มีหลักฐานว่าให้ผลลัพธ์ดีกว่า Claude สำหรับ use case นี้
5. ทำ interface ของ Brain ให้เปลี่ยน model/provider ได้ แล้วใช้ eval set เดียวกันเปรียบเทียบ Claude กับ Local ก่อนซื้อ GPU หรือ lock provider
6. สูตร Estimate, margin, approval และ Ready-for-Estimate gate ต้อง deterministic ใน PostgreSQL/service layer ไม่ให้ Claude, GPT หรือ Local LLM เป็นผู้ตัดสินสูตร

หากถูกบังคับให้เลือกเพียงตัวเดียว:

- **PoC ที่ใช้ข้อมูลสังเคราะห์/redacted:** เลือก Claude API เพื่อเดินงานเร็วและยังไม่ซื้อ GPU
- **ระบบจริงที่ต้องอ่าน RFQ/ต้นทุนลับโดยยังไม่มี cloud approval:** เลือก Local LLM

Decision นี้ lock เฉพาะแนวทางพัฒนา PoC และการใช้ข้อมูลสังเคราะห์/redacted เท่านั้น ยังไม่ใช่การอนุมัติให้ส่ง production RFQ หรือต้นทุนจริงเข้า Claude; ข้อมูลจริงยังต้องรอ Data Owner/DPO/Legal และผล benchmark

### สิ่งที่ Local AI ยังต้องมี

แม้รันในบริษัทก็ยังต้องมี:

- Authentication + RBAC/ABAC
- Encryption in transit/at rest
- Audit log และ retention/deletion
- Data minimization และ purpose limitation
- Permission filter ก่อน retrieval
- Backup/incident response
- Human approval สำหรับราคาและการส่ง Quotation

### แหล่งอ้างอิงหลัก

- [ETDA — Generative AI Governance Guideline for Organizations](https://www.etda.or.th/getattachment/6050a4b7-defd-4dba-8cbc-ff6a444a3d08/20241125-Generative-AI-Guideline_V2-0.pdf.aspx)
- [ETDA — Data Governance for AI](https://www.etda.or.th/th/Useful-Resource/Knowledge-Sharing/Articles/aigc/Data_Governance.aspx)
- [PDPC/GPPC — Privacy Policy และตัวอย่างมาตรการส่งข้อมูลต่างประเทศ](https://gppc.pdpc.or.th/privacy-policy/)
- [Government Data Catalog — ประกาศการส่งหรือโอนข้อมูลต่างประเทศตามมาตรา 28](https://gdcatalog.go.th/en/dataset/gdpublish-dataset-11-056)
- [กรมทรัพย์สินทางปัญญา — สรุปพระราชบัญญัติความลับทางการค้า](https://www.ipthailand.go.th/th/dip-law-2/item/description_secret.html)
- [Anthropic — Commercial/API Data Retention](https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)
- [Anthropic — Zero Data Retention Scope](https://privacy.anthropic.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to)
- [Anthropic — Processing and Storage Locations](https://privacy.anthropic.com/en/articles/7996890-where-are-your-servers-located-do-you-host-your-models-on-eu-servers)
- [Anthropic — Data Processing Addendum](https://privacy.anthropic.com/en/articles/7996862-how-do-i-view-and-sign-your-data-processing-addendum-dpa)

**หมายเหตุ:** ส่วนนี้เป็น Architecture/Risk Recommendation ไม่ใช่คำวินิจฉัยทางกฎหมาย ต้องให้ DPO/Legal ตรวจข้อเท็จจริง สัญญาลูกค้า และการไหลของข้อมูลจริงก่อน Production
