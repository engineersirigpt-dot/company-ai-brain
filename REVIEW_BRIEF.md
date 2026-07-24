# Review Brief — สำหรับ AI Reviewer

> **บทบาทของคุณ: Reviewer / Advisor เท่านั้น — ห้ามแก้ไขโค้ด**
> ส่งผลลัพธ์เป็นรายงาน: ข้อสังเกต ความเสี่ยง และข้อเสนอแนะ (อธิบายแนวทาง ไม่ต้องเขียนโค้ดเต็ม)
> ถ้าจะเสนอการเปลี่ยนแปลง ให้บอก "ไฟล์ไหน แก้อะไร เพราะอะไร" พอ — การลงมือแก้มีทีมอื่นรับต่อ
> อ่านโค้ดจริงในโปรเจคนี้ประกอบได้เลย แต่**ห้ามอ่าน/แตะไฟล์ `.env` และไฟล์ใน `info/`** (เอกสารภายในบริษัท)

---

## 1. โปรเจคนี้คืออะไร

**Company AI Brain** — Knowledge Layer กลางขององค์กร (โรงพิมพ์) ให้ AI ทุกตัวในบริษัทดึงความรู้จากฐานเดียวกันแทนที่ต่างคนต่างเก็บ (แก้ AI Silo Problem)

ตำแหน่งในภาพใหญ่ (วิสัยทัศน์ผู้บริหาร = "AI Board of Advisors" 5 ชั้น):

```
ชั้น 5  ผู้บริหาร + ผู้จัดการ (คน)
ชั้น 4  ตัวสังเคราะห์ภาพรวม (Chief AI Advisor)      ← ยังไม่สร้าง
ชั้น 3  Agent เฉพาะแผนก 15 ตัว (เริ่ม Estimate Agent) ← เพิ่งเริ่ม (/ask คือ v1)
ชั้น 2  ฐานความรู้กลาง + RBAC                        ★ โปรเจคนี้ — เสร็จ รันจริงแล้ว
ชั้น 1  เอกสาร (ISO QP/WI, คู่มือ, SOP ภาษาไทย)     ← ingest แล้ว 95 ไฟล์
```

## 2. สถานะจริงที่ verify แล้ว (ข้อมูล ณ 2026-07-24)

สิ่งเหล่านี้**ทดสอบบน server จริงแล้ว** ไม่ใช่แค่ในโค้ด:

- Deploy อยู่บน Ubuntu server ภายในองค์กร (docker compose: `brain_qdrant` + `brain_api` port 8002)
- Qdrant: collection `company_docs` — **2,404 chunks / 95 เอกสาร** (BGE-M3 dense 1024d, cosine)
- `POST /search` — retrieval + RBAC ผ่าน payload filter (`allowed_roles`) ใช้งานโดย voicebot อีกโปรเจคแล้ว
- `POST /ask` — RAG เต็มวง: retrieval → Claude API (`claude-opus-4-8`) เรียบเรียงตอบไทย + citation
  - ทดสอบผ่าน: ตอบเป็นขั้นตอนพร้อม [1][2], คำถามนอกคลังตอบ "ไม่พบข้อมูล" (ไม่ hallucinate), role ปลอมโดน 400
- Parent-Child retrieval: embed child chunk เล็ก แต่ตอบด้วย `parent_text` (~2000 ตัวอักษร) จาก payload
- ภาษาไทยเพี้ยนจาก PDF font (`ขัÊนตอน`→`ขั้นตอน`, `ŚŝŞŜ`→`2564`): แก้แล้ว 15 ไฟล์ + 266 จุดใน Qdrant

## 3. การตัดสินใจที่ล็อกแล้ว (มีเหตุผลรองรับ — อย่าเสนอย้อน เว้นแต่มีเหตุผลใหม่จริงๆ)

| การตัดสินใจ | เหตุผล |
|---|---|
| เนื้อหาสำหรับตอบเก็บใน **Qdrant payload** ไม่อ่านไฟล์ .md จาก disk ตอน runtime | เคยลองแบบอ่าน disk แล้วพังตอน deploy (ไฟล์ไม่อยู่บน server) + มีปัญหา sync/heading ซ้ำ — payload เดินทางไปกับ vector ตอน migrate เสมอ |
| LLM ใช้ **Claude API ก่อน** ยังไม่ซื้อ GPU | PoC ต้องเร็ว, ยังไม่ผ่าน eval, จุดสลับไป vLLM เตรียมไว้แล้วที่ `generate_answer()` ใน `app/main.py` จุดเดียว |
| การแก้ API ต้อง **additive เท่านั้น** (เพิ่ม field ได้ ห้ามแก้/ลบของเดิม) | มีระบบอื่น (voicebot) ใช้ `/search` อยู่ใน production |
| RBAC enforce ที่ Qdrant filter **ก่อน** retrieval เสมอ | หลักการ: "Router ช่วยเลือกทาง แต่ Policy Filter ต้องเป็นคนล็อกประตู" |

## 4. ข้อจำกัดจริงที่มองไม่เห็นจากโค้ด

- Server เป็นเครื่องรวมหลายโปรเจค (มี บอทอื่น + postgres อื่นรันอยู่) — ห้ามเสนออะไรที่กิน resource หนักโดยไม่จำเป็น
- Rebuild docker image ช้ามาก (~20 นาที เพราะ torch ~3GB และ buildkit cache โดน GC บ่อย) — ทางแก้ปัจจุบัน: แยก layer ใน Dockerfile แล้ว แต่ deploy เล็กๆ นิยมใช้ `docker cp` + restart
- ยังไม่มี CI/CD, ยังไม่มี reverse proxy/TLS, auth ระดับ endpoint ยังไม่มี (role ส่งมาใน request body ตรงๆ — Keycloak OIDC อยู่ในแผนแต่ยังไม่ทำ)
- เอกสาร 6 ไฟล์ (กลุ่ม AFII: QP-710-01, QP-721-03, QP-752-02, QP-821-03, WI-423-01-02, WI-755-05-02) text layer พังยับ ต้อง OCR ใหม่ — ยังไม่ได้ทำ
- HNSW index ยังไม่ build (points < indexing_threshold 10k) — ตอนนี้ brute-force ซึ่งยังเร็วพอ

## 5. สิ่งที่อยากให้ review (เรียงตามความสำคัญ)

1. **Gap analysis ก่อนสร้าง Estimate Agent V1** — ชั้น 3 ตัวแรกจะเป็น Agent ประเมินราคางานพิมพ์ (มี requirement doc จากผู้บริหาร: รับ RFQ → ตรวจสเปก → เสนอวิธีผลิต → คำนวณต้นทุน → เสนอราคา) คำถาม: ชั้น 2 ที่มีอยู่ขาดอะไรบ้างที่ Agent แบบนั้นต้องใช้? (เช่น structured data/Machine Master ควรอยู่ตรงไหน — ความเห็นปัจจุบัน: PostgreSQL แยก ไม่ยัดใส่ vector DB)
2. **Evaluation** — มี `eval_set.json` (~100 คำถาม) กับ `eval.py` อยู่แล้วแต่ยังไม่ได้รันกับ `/ask` เลย ช่วยดูว่า eval set ครอบคลุมไหม (hit rate, faithfulness, permission leakage, hallucination) และเสนอวิธีวัดที่ทำได้จริงกับทีมเล็ก
3. **ช่องโหว่ RBAC/security ในดีไซน์ปัจจุบัน** — โดยเฉพาะ: role อยู่ใน request body, ไม่มี auth, ไม่มี rate limit, ไม่มี audit log — อะไรต้องทำก่อนเปิดให้แผนกอื่นใช้จริง เรียงลำดับให้หน่อย
4. **คุณภาพข้อมูล** — กลยุทธ์ OCR ภาษาไทยสำหรับ 6 ไฟล์ AFII (แนวที่คิดไว้: Tesseract tha / typhoon-ocr) + ควรมี data quality gate อะไรใน ingest pipeline กันเอกสารเพี้ยนหลุดเข้าอีก
5. **API design ของ /ask** — จาก perspective ของผู้บริโภค (chatbot, voicebot, Estimate Agent): ขาด field อะไร? ควรมี conversation history ไหม? streaming จำเป็นเมื่อไหร่?

## 6. ไฟล์สำคัญ (เริ่มอ่านตามนี้)

| ไฟล์ | คืออะไร |
|---|---|
| `CLAUDE.md` | สเปกสถาปัตยกรรมเต็ม + stack ที่ lock ไว้ |
| `app/main.py` | FastAPI ทั้งหมด: /health /search /ask /collections + RBAC + LLM |
| `ingest.py` | pipeline: markdown → chunk ตาม heading → embed → Qdrant (+parent_text) |
| `rbac_config.py`, `rbac_matrix.md` | mapping เอกสาร → role |
| `build_parent_payload.py`, `fix_mojibake.py` | เครื่องมือ backfill/ซ่อมข้อมูล (รันแล้ว) |
| `eval_set.json`, `eval.py` | ชุดประเมินที่ยังไม่ได้รันกับ /ask |
| `Dockerfile`, `docker-compose.yml` | deploy (สังเกต layer แยกของ LLM SDK) |

---
*อัปเดตล่าสุด: 2026-07-24 — เขียนโดยทีมพัฒนา (Claude) เพื่อให้ AI reviewer เห็นบริบทที่ไม่อยู่ในโค้ด*
