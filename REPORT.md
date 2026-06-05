# PoC Report — Company AI Brain
**วันที่:** 2026-06-02  
**สถานะ:** Phase 1 PoC สำเร็จ — พร้อม Go/No-Go Phase 2

---

## เป้าหมาย

สร้าง AI ตัวกลางที่รวมข้อมูลทั้งหมดขององค์กร ให้ AI Project ต่างๆ (Chatbot, HR AI, Meeting AI) เชื่อมต่อมาใช้ข้อมูลร่วมกันแทนที่จะมีข้อมูลแยกกัน

---

## สิ่งที่ทำสำเร็จใน Phase 1

### Pipeline ที่ทำงานได้จริง
```
เอกสาร (PDF/XLSX/DOCX) → แปลงเป็น Markdown → แบ่ง Chunk → Embed ด้วย BGE-M3 → เก็บใน Qdrant → ค้นหาด้วย Semantic Search
```

### หลักฐาน
| รายการ | ผล |
|--------|-----|
| เอกสารที่ ingest | **143 ไฟล์** (PDF, DOCX, XLSX จากโรงพิมพ์จริง) |
| Vectors ใน Qdrant | **4,634 chunks** |
| Embedding model | BGE-M3 (Thai+EN, 1024 dimensions) |
| รองรับ PDF scanned | ✅ ใช้ EasyOCR Thai+English |
| รองรับ subfolder | ✅ recursive ทุก folder |

### ผล Evaluation (รันจริง 2026-06-02 — ดูรายละเอียดใน `eval_results.md`)

Eval set: **45 คำถาม** ครอบคลุม Thai / English / QP / WI / has_answer / no_answer

| Metric | ผล | เกณฑ์ |
|--------|-----|-------|
| Hit@3 | 36/37 = **97%** | > 80% = ดี |
| MRR | **0.9459** | > 0.7 = ดี |
| Avg Score@1 | **0.7044** | > 0.65 = ดี |
| No-match detection | 7/8 = **88%** | ระบบส่วนใหญ่ไม่ตอบเมื่อไม่มีข้อมูล |

ข้อสังเกต: พบ 1 false positive เรื่อง permission leakage — ต้องแก้ด้วย RBAC ใน Phase 2 (ดูรายละเอียดใน eval_results.md)

---

## ข้อจำกัดที่พบ

1. **ยังไม่มี RBAC** — ใครถามก็เห็นทุกเอกสาร ต้องแบ่ง permission ตาม department ก่อน production
2. **ยังไม่มี LLM** — ทดสอบแค่ retrieval ยังไม่ถึงขั้นตอบคำถามเป็นภาษาคน
3. **ยังไม่มี GPU** — รันบน CPU ใช้เวลา ingest ~5 ชั่วโมงต่อ 143 ไฟล์ (production ต้องการ GPU)
4. **Eval set ยังไม่ครบ** — 45 คำถาม จากเป้า 100-200 คำถาม

---

## Decision ที่ต้องการจากหัวหน้า

1. **Data Classification** — เอกสารไหนเป็น Public / Internal / Confidential เพื่อออกแบบ RBAC
2. **Go/No-Go Phase 2** — ผล retrieval ผ่านเกณฑ์ทุก metric พร้อมเริ่ม build FastAPI + RBAC + vLLM

---

## Phase 2 ที่แนะนำ (ถ้าอนุมัติ)

1. กำหนด Data Classification + ออกแบบ RBAC
2. เพิ่ม eval set ให้ถึง 100-200 คำถามจากคำถามจริงของพนักงาน
3. ซื้อ GPU + build FastAPI + RBAC + vLLM
4. Benchmark Typhoon2-70B vs Qwen3-32B บน eval set จริง

> **หลักการ:** retrieval quality พิสูจน์แล้วว่าใช้งานได้กับเอกสารโรงพิมพ์จริง
> ขั้นตอนถัดไปคือ lock permission และ build API layer
