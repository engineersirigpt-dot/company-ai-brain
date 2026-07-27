# What Can We Do Now? — Ideation Brief สำหรับ AI Reviewer

**บทบาทของคุณ:** เสนอไอเดียงานที่ทำได้ตอนนี้ (ideation/advisor) — **ไม่ต้องแก้โค้ด**
**เป้า:** อยากได้มุมมองว่ามีงานอะไรทำเพิ่มได้อีก ณ สถานะปัจจุบัน โดยไม่ชนกับที่กำลังทำและไม่เกินงบ
**อัปเดต:** 2026-07-27
**กติกา:** อ่าน `STATUS.md` ก่อน แล้วเสนอเป็นรายการที่ actionable; อย่าเสนอของที่อยู่ในหัวข้อ "ทำไปแล้ว/กำลังทำ/ถูก block" ด้านล่าง เว้นแต่มีมุมใหม่จริง

---

## 1. Constraints ปัจจุบัน (สำคัญ — ไอเดียต้องผ่านทั้ง 3 ข้อ)

| Constraint | ผลต่อการเสนองาน |
|---|---|
| **OCR job กำลังเขียน Qdrant อยู่** (เหลือ ~5 นาที) | ห้ามเสนองานที่ write/delete Qdrant ตอนนี้ (เช่น ลบ dedup, re-ingest) — จะ race กัน |
| **เครดิต Anthropic เหลือ ~$6** (จาก $9, OCR ใช้ ~$3) | งานที่เรียก LLM เยอะ (faithfulness eval ~$5-8) ยังไม่คุ้มตอนนี้ |
| **7 business decisions block migration** (รอประชุมเฮีย) | งาน RFQ migration เดินต่อไม่ได้จนกว่าได้คำตอบ |
| local Python พัง, งานรันบน server ผ่าน SSH | งานที่รันได้คือบน server/ในคอนเทนเนอร์ |

**เกณฑ์ที่อยากได้:** งานที่ **$0 หรือถูกมาก + ไม่แตะ Qdrant ระหว่าง OCR + ไม่ต้องรอเฮีย + เพิ่มคุณค่าจริง**

---

## 2. สถานะระบบ (ยืนยันแล้ว)

- Deploy บน Ubuntu server (docker): `brain_qdrant` + `brain_api` port 8002
- Qdrant `company_docs`: ~2,404 chunks / 95 เอกสาร (BGE-M3 dense 1024d)
- `POST /search` — retrieval + RBAC, ~0.2s, ไม่ผ่าน LLM (voicebot ใช้อยู่)
- `POST /ask` — retrieval + Claude (opus 4.8) + citation; eval แล้ว hit 92%, hallucination 0/8, leak 0/5
- Auth: `X-API-Key` mode `warn` (ยังไม่ enforce)
- deploy model: bind-mount `./app` → git pull + `docker restart` (ไม่ rebuild)

## 3. ทำไปแล้ว — อย่าเสนอซ้ำ

- `/ask` endpoint (RAG + citation) + baseline eval (100 คำถาม + permission probes)
- Parent-child retrieval (embed child, ตอบด้วย parent_text ใน payload)
- แก้ mojibake ไทย (tone-mark remap 15 ไฟล์ + 266 จุดใน Qdrant)
- RBAC default-deny (`UNCLASSIFIED` = admin only) + แก้ตัวอย่าง filter ที่รั่ว
- Inbound API key + role scope + audit log (mode warn)
- CORS แบบ config ได้ (default off)
- RFQ Schema v0.1 → v0.2 (Packaging-first) + cross-check ผ่าน
- ปิดปริศนา corpus 143→95 (ไฟล์ดิบซ้ำ + ไฟล์ขยะ ไม่มีเอกสารจริงหาย)
- เอกสาร: STATUS.md, INTEGRATION.md, MEETING_BRIEF.md, REVIEW briefs

## 4. กำลังทำอยู่ (อย่าแตะ)

- **OCR 6 ไฟล์ AFII** (text layer พัง) ด้วย Claude vision → re-ingest — เขียน Qdrant อยู่

## 5. Block อยู่ (เสนอได้แต่ทำไม่ได้ตอนนี้)

- RFQ migration — รอ 7 business decisions
- ลบ dedup 4 คู่ — รอ OCR จบ + ยืนยัน canonical
- flip `AUTH_MODE=enforce` — รอ voicebot ใส่ key
- Faithfulness LLM-judge eval — งบตึง + corpus กำลังเปลี่ยน

---

## 6. คำถามถึงคุณ

จาก constraints ข้อ 1 — **มีงานอะไรอีกที่ทำได้ตอนนี้แบบ $0/ถูกมาก, ไม่แตะ Qdrant, ไม่ต้องรอเฮีย?**
เราคิดไว้บ้างแล้ว (reranker เตรียมไว้, unit test, logging/observability, docs) แต่อยากได้มุมที่เรามองข้าม
ช่วยจัดลำดับ effort/impact ให้ด้วย และบอกว่าอันไหน "ทำระหว่างรอ OCR ได้เลย" กับอันไหน "รอ corpus นิ่งก่อน"
