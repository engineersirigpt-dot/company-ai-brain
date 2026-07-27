# /ask Baseline Evaluation — ผลรอบแรก

**วันที่รัน:** 2026-07-27 | **Model:** claude-opus-4-8 | **Corpus:** 2,404 chunks / 95 เอกสาร (หลังแก้ mojibake)
**วิธีรัน:** `python3 ask_eval.py` บน server (เรียก `POST /ask` จริง end-to-end: retrieval + RBAC + LLM + citation)
**ชุดทดสอบ:** `eval_set.json` 100 ข้อ (92 has_answer / 8 no_answer) + permission probes 5 ข้อ
**Raw data:** `ask_eval_raw.json` (บน server)

## สรุปผล

| ตัวชี้วัด | ผล | เกณฑ์อ่านค่า |
|---|---|---|
| Citation hit (has_answer, 92 ข้อ) | **85/92 = 92%** | expected source โผล่ใน citations (top_k=4) — เทียบ retrieval เดิม Hit@3 = 90% |
| No-answer honesty (8 ข้อ) | **8/8 = 100%** | คำถามนอกคลังตอบ "ไม่พบข้อมูล" ทุกข้อ — **hallucination = 0** |
| Permission leakage (5 probes) | **0/5 = ไม่รั่ว** | ถามเรื่อง RECALL/HR/SALES ด้วย role ไม่มีสิทธิ์ → ไม่มีเอกสารหวงห้ามโผล่ใน citation |
| Errors | 0/100 | ไม่มี timeout/crash |
| Latency | p50 **7.3s** / p95 **26.0s** | ต่อคำถาม รวม LLM generate |

## วิเคราะห์ข้อที่พลาด

### Miss 7 ข้อ — ทั้งหมดเป็น "สับสนเอกสารพี่น้อง" (sibling confusion)

ทุกข้อ expected source **มีอยู่ใน corpus จริง** แต่ dense retrieval เลือกเอกสารหัวข้อใกล้กันมากแทน เช่น

| ถามเรื่อง | ควรได้ | ได้ |
|---|---|---|
| ส่งมอบผลิตภัณฑ์ต่างประเทศ | QP-755-05 (ต่างประเทศ) | QP-755-04 (ในประเทศ+นอกประเทศ) |
| เครื่อง Inspection (ปรับตั้ง) | WI-751-12-14 | WI-824-02-01 (Inspection เหมือนกัน) |
| เครื่องไสกาว KM470 | WI-751-09-53 | WI-751-09-54 (เครื่องเย็บกี่ ข้างๆ กัน) |

→ **แนวแก้ที่มีหลักฐานรองรับแล้ว: เพิ่ม reranker (bge-reranker-v2-m3 ตาม stack ที่ lock ไว้)** — sibling confusion คือ failure mode ที่ reranker แก้ตรงจุดที่สุด ควรรัน eval ชุดเดิมซ้ำหลังเพิ่มเพื่อวัด delta

### ตอบ "ไม่พบ" ทั้งที่มีคำตอบ 11 ข้อ (conservative false-negative)

9 ใน 11 ข้อ retrieval **hit เอกสารถูกแล้ว** แต่ LLM ตัดสินว่าเนื้อใน context ไม่พอตอบ (เช่น รายละเอียดอยู่ chunk อื่นของเอกสารเดียวกัน หรือรายละเอียดเฉพาะเจาะจงเกิน เช่น "ใช้กับ Firefox ได้ไหม")
— เป็น failure mode ฝั่งปลอดภัย (ยอมไม่ตอบ ดีกว่าแต่งคำตอบ) สอดคล้อง system prompt ที่สั่งเข้มเรื่องห้ามเดา
— แนวแก้: เพิ่ม `top_k`, ขยาย parent context, หรือผ่อน prompt เล็กน้อย — **ควรแก้หลังใส่ reranker แล้ววัดใหม่** เพราะ reranker จะเปลี่ยน context ที่ LLM เห็นอยู่ดี

## ข้อจำกัดของ baseline รอบนี้

- ยังไม่วัด Answer Faithfulness แบบละเอียด (ต้อง LLM-judge หรือ human review เทียบประโยคต่อประโยค)
- Permission probes 5 ข้อเป็น smoke test — ชุดเต็มควร generate จากทุก collection × ทุก role
- รันด้วย role=admin (เห็นทุกเอกสาร) — ยังไม่ได้วัด hit rate ต่อ role จริง
