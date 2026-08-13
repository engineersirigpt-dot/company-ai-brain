# Company AI Brain — PoC Phase 1

Enterprise Knowledge System — ระบบ AI **ตัวกลาง** สำหรับองค์กร  
รวบรวมความรู้ทั้งหมดของบริษัทไว้ที่เดียว ให้ AI Project อื่นๆ เชื่อมต่อมาใช้ร่วมกัน

> **สถานะความปลอดภัย:** M4 permission-leak proof = **CLOSED + FROZEN** (ผ่าน independent review) — พิสูจน์ว่าเอกสารข้ามสิทธิ์ **ไม่ถึงโมเดล**. ยังเป็น PoC (ยังไม่ปิด gate ด้าน user-auth/egress — ดู `STATUS.md`)

---

## แนวคิดหลัก — ทำไมต้องมี AI Brain?

```text
ปัญหาเดิม (AI Silo):
  Chatbot HR    → มีข้อมูล HR ของตัวเอง
  Chatbot IT    → มีข้อมูล IT ของตัวเอง
  Meeting AI    → มีข้อมูลแยกต่างหาก
  → ข้อมูลซ้ำ, อัปเดตยาก, ไม่รู้ว่าใครถูก

ทางแก้ (AI Brain):
  AI Brain ← เอกสารทั้งหมดของบริษัท
      ↑
  Chatbot HR  |  Chatbot IT  |  Meeting AI  |  AI อื่นๆ
  → ข้อมูลชุดเดียว อัปเดตที่เดียว ทุกตัวได้ของใหม่พร้อมกัน
```

---

## สำหรับ AI Project ที่จะมาเชื่อมต่อ

AI Brain เป็น **knowledge service** — AI ฝั่งคุณไม่ต้องเก็บเอกสารเอง เลือกใช้ได้ 2 แบบ:

- `POST /search` — retrieval อย่างเดียว (ได้ chunk + `content` เต็ม เอาไปให้ LLM ฝั่งคุณเรียบเรียงเอง)
- `POST /ask` — ถาม-ตอบจบในตัว (retrieval + LLM เรียบเรียง + citation) เหมาะกับ chatbot/voicebot ที่ไม่อยากต่อ LLM เอง

### API Endpoint — /search (retrieval)

```http
POST http://192.168.5.32:8002/search
Content-Type: application/json

{
  "query": "คำถามที่ user ถาม",
  "role": "production",
  "top_k": 3
}
```

### API Endpoint — /ask (ถาม-ตอบพร้อมอ้างอิง)

```http
POST http://192.168.5.32:8002/ask
Content-Type: application/json

{
  "question": "ขั้นตอนการผสมสีพิเศษทำยังไง",
  "role": "production",
  "top_k": 4
}
```

ตอบกลับ: `{"answer": "...คำตอบภาษาไทยพร้อม [1] [2]...", "citations": [{"ref": 1, "source": "...", "heading": "...", "score": 0.84}], "model": "claude-opus-4-8"}`

> ฝั่ง server ต้องตั้ง `ANTHROPIC_API_KEY` ใน `.env` ข้าง docker-compose.yml (ดู `.env.example`)
> ถ้าไม่ตั้ง `/ask` จะตอบ 503 แต่ `/search` ใช้ได้ปกติ — จุดสลับไป LLM local (vLLM) อยู่ที่ `generate_answer()` ใน app/main.py

### Response

```json
{
  "query": "วิธีผสมสีพิมพ์",
  "role": "production",
  "results": [
    {
      "rank": 1,
      "score": 0.8457,
      "source": "QP-751-15 Rev.00 การควบคุมกระบวนการผสมสี",
      "collection": "PRODUCTION",
      "level": 2,
      "heading": "## ขั้นตอนการผสมสี",
      "preview": "1. ตรวจสอบใบสั่งงาน..."
    }
  ]
}
```

### Roles ที่รองรับ

| Role | แผนก |
| --- | --- |
| `production` | ฝ่ายผลิต |
| `prepress` | ฝ่ายเตรียมพิมพ์ |
| `qc` | ฝ่ายคุณภาพ |
| `engineering` | ฝ่ายวิศวกรรม |
| `sales` | ฝ่ายขาย |
| `purchasing` | ฝ่ายจัดซื้อ |
| `logistics` | ฝ่ายคลัง/จัดส่ง |
| `hr` | ฝ่ายบุคคล |
| `it` | ฝ่าย IT |
| `management` | ผู้จัดการ |
| `admin` | เห็นทุก document |

> ### ⚠️ สำคัญมากสำหรับ integration
> **ต้องส่ง `role` ของ user จริง (ตามแผนก) ทุก request** — ถ้า caller ส่ง `admin` ตายตัว หรือไม่ส่ง role → **RBAC ถูก bypass (ทุกคนเห็นทุก document)**. Brain กัน leak ได้ก็ต่อเมื่อ caller map `user → role แผนก` ให้ถูก (ดู field `level`/`collection` ใน response ไว้ audit ได้ด้วย)

### ตัวอย่าง Integration (Python)

```python
import requests

def ask_brain(question: str, user_role: str) -> list[dict]:
    resp = requests.post(
        "http://192.168.5.32:8002/search",
        json={"query": question, "role": user_role, "top_k": 3},
    )
    return resp.json()["results"]

# ใช้ใน AI ของคุณ
contexts = ask_brain("วิธีเสนอราคาขาย", user_role="sales")
# → เอา contexts ไปใส่ใน prompt แล้วให้ LLM ตอบ
```

### Health Check

```bash
curl http://192.168.5.32:8002/health
# {"status":"ok","vectors":4634,"model":"BAAI/bge-m3"}
```

---

## สำหรับ Developer — ติดตั้งและพัฒนา

### Environment

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### สร้าง .env

```bash
cp .env.example .env
# แก้ค่าใน .env
```

### Pipeline (local)

```bash
# 1. แปลงเอกสาร → Markdown
python parse_doc.py <file.pdf>

# 2. Ingest ทั้ง folder
python batch_ingest.py info/

# 3. ค้นหาแบบมี RBAC
python search_rbac.py "คำถาม" --role production

# 4. วัดคุณภาพ
python eval.py eval_set.json
```

### Deploy บน Ubuntu Server

```bash
# 1. รัน Qdrant + API
docker compose up -d

# 2. ย้ายข้อมูลจาก local → server (ครั้งแรก)
python migrate_to_server.py --server http://192.168.5.32:6333

# 3. ทดสอบ
curl http://192.168.5.32:8002/health
```

---

## Architecture

```text
เอกสารบริษัท (PDF/DOCX/XLSX)
    ↓ parse_doc.py
Markdown
    ↓ batch_ingest.py + BGE-M3
Qdrant Vector DB (4,634 chunks, 143 ไฟล์)
    ↓ FastAPI + RBAC filter
POST /search  ←  AI Project ใดก็ได้
```

## ไฟล์สำคัญ

| ไฟล์ | หน้าที่ |
| --- | --- |
| `app/main.py` | FastAPI service |
| `ingest.py` | chunk + embed + store |
| `batch_ingest.py` | ingest ทั้ง folder |
| `parse_doc.py` | PDF/DOCX/XLSX → Markdown |
| `rbac_config.py` | กฎ RBAC ทั้งหมด |
| `eval.py` | วัดคุณภาพ retrieval |
| `migrate_to_server.py` | ย้าย Qdrant local → server |
| `docker-compose.yml` | Qdrant + API สำหรับ Ubuntu |

## ไฟล์รองรับ

PDF (text-based, scanned), XLSX, DOCX, PPTX, HTML, Images

> **หมายเหตุ:** PDF สแกนใช้ EasyOCR Thai+EN — ingest ช้ากว่า text-based ~10x
