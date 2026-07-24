# company-ai-brain

Enterprise Knowledge System — ระบบ AI กลางสำหรับองค์กร ให้ทุก AI project เชื่อมต่อและใช้ข้อมูลบริษัทร่วมกัน

## สถานะ: PoC Phase 1 — Ingestion Pipeline

---

## Shared Status สำหรับ AI ทุกตัว

ก่อนวิเคราะห์ วางแผน หรือแก้ไขโปรเจกต์ ให้อ่าน `STATUS.md` เพื่อใช้ข้อเท็จจริง การตัดสินใจ และลำดับงานล่าสุดร่วมกัน โดยเฉพาะสถานะ corpus 143 → 95, duplicate documents, Voicebot impact และแนวทาง Authentication ช่วง PoC

---

## แนวคิดหลัก

Company AI Brain คือ Knowledge Layer กลางที่รวบรวมข้อมูลทั้งหมดขององค์กร แล้วให้ AI project ต่างๆ เชื่อมต่อมาใช้ร่วมกัน แทนที่แต่ละ AI จะมีข้อมูลแยกกัน (AI Silo Problem)

**Mental model สำคัญ:**
> Router ช่วยเลือกทาง แต่ Policy Filter ต้องเป็นคนล็อกประตู

---

## Stack ที่ Lock แล้ว

```
Frontend    : Open-WebUI
Proxy       : Nginx
Auth        : Keycloak OIDC
API Brain   : FastAPI  (logic หลัก — auth, retrieval, audit)
LLM Runtime : vLLM
Router      : Typhoon2-8B   (Thai query rewrite + routing)
Generator   : Qwen3-32B     (baseline)
Fallback    : Qwen2.5-32B   (ถ้า Qwen3 latency ไม่นิ่ง)
Embedding   : BGE-M3        (Thai+EN, dense+sparse)
Reranker    : bge-reranker-v2-m3
Vector DB   : Qdrant        (hybrid search, self-hosted)
Cache       : Redis          (permission-aware key)
Doc Store   : MinIO / NAS   (raw files + parsed artifacts)
Metadata DB : PostgreSQL
Ingestion   : Docling / Unstructured + Parent-Child + table row JSON
Monitor     : Prometheus + Grafana + audit log
GPU Target  : 4x RTX 4090   (benchmark ก่อนซื้อจริง)
```

---

## Architecture 3 ชั้น

```
ชั้น 1 — Knowledge Sources
  เอกสาร HR, SOP, IT Manual, Excel, PDF, เสียงประชุม

ชั้น 2 — Company AI Brain (ตัวกลาง)
  AI Router → Qdrant payload filter → Vector search → Rerank → Answer

ชั้น 3 — AI Applications
  Chatbot, HR AI, Meeting Summarizer, ฯลฯ
```

---

## RBAC — หลักการสำคัญ

Permission ต้อง enforce ก่อน retrieval เสมอ ห้าม trust Router คนเดียว

```
Keycloak → token + role/group
    ↓
FastAPI แปลงเป็น Qdrant payload filter
    ↓
ทุก query มี filter: department, confidentiality_level, allowed_groups
```

Cache key ต้องรวม: user_scope + role + collection + ACL version

---

## Folder-based Routing (Collections)

```
HR      → นโยบาย, สวัสดิการ, สัญญา
SALES   → ลูกค้า, ยอดขาย, pipeline
IT      → คู่มือ, incident, server
OPS     → SOP, คู่มือเครื่องจักร, safety
FINANCE → รายงาน, งบประมาณ (permission สูงสุด)
```

---

## PoC Phase 1 — สิ่งที่ต้องทำก่อน

### เป้าหมาย: Ingestion + Evaluation ก่อนซื้อ Hardware

1. เลือกเอกสารจริง 30-50 ไฟล์
   - SOP ภาษาไทย, HR policy, IT manual, Excel ตาราง, PDF ซับซ้อน

2. Build ingestion pipeline
   - raw file → parsed markdown/json → chunk → embed → Qdrant → rerank → answer with citation

3. สร้าง evaluation set 100-200 คำถาม
   - Thai only, Thai+English, คำถามจากตาราง, ข้ามเอกสาร
   - คำถามที่ไม่มีคำตอบ (วัด hallucination)
   - คำถามที่ user ไม่มีสิทธิ์ (วัด permission leakage)

4. วัดผลก่อนตัดสินใจ hardware
   - retrieval hit rate, citation accuracy
   - permission leakage rate, hallucination rate
   - answer faithfulness, latency

5. Benchmark model
   - Typhoon2-8B + Qwen3-32B vs Typhoon2-8B + Qwen2.5-32B
   - ใช้ eval set ชุดเดียวกัน

---

## Thai Chunking Strategy

```
1. Parse → structured markdown/json (ใช้ Docling/Unstructured)
2. Chunk ตามโครงสร้าง: document > heading > section > paragraph
   ไม่ตัดแบบ 500 tokens ทื่อๆ
3. Normalize ภาษาไทยก่อน embed:
   - Unicode normalization
   - ลบ zero-width chars
   - custom dictionary สำหรับชื่อเครื่องจักร/คำย่อบริษัท
4. Parent-Child retrieval:
   - embed child chunk เล็ก (300-700 tokens)
   - ดึง parent section มาตอบ
5. ตาราง: เก็บทั้ง table markdown + row-level JSON
   แต่ละ row repeat header/context
```

---

## Port Map

```
Qdrant      :6333
vLLM        :8001
FastAPI     :8000
PostgreSQL  :5432
Redis       :6379
MinIO       :9000
Keycloak    :8080
Open-WebUI  :3000
Grafana     :3001
Prometheus  :9090
Nginx       :80 / :443
```

---

## งานที่ต้องทำ

- [ ] คุยหัวหน้าเรื่อง Data Classification (Public/Internal/Confidential)
- [ ] เลือกเอกสาร 30-50 ไฟล์สำหรับ PoC
- [ ] ติดตั้ง Docker + Qdrant + MinIO
- [ ] Build ingestion pipeline (Docling + BGE-M3)
- [ ] สร้าง evaluation set 100-200 คำถาม
- [ ] Benchmark Qwen3-32B vs Qwen2.5-32B
- [ ] ตัดสินใจ GPU หลัง PoC ผ่าน eval
