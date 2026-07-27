# Company AI Brain — Integration Guide

**สำหรับ:** ทีม/โปรเจกต์อื่นที่จะเชื่อมเข้ามาใช้ Knowledge Layer (voicebot, Estimate Agent, chatbot ฯลฯ)
**อัปเดต:** 2026-07-27 | **API version:** `1.0.0-poc`
**ตรวจโดย:** ยิงทดสอบจริงบน server ทุกข้อ (ไม่ใช่อ่านจากโค้ดอย่างเดียว)

> อ่าน `STATUS.md` ควบคู่เพื่อดูสถานะล่าสุด (เช่น ตอนนี้ `/ask` ใช้ไม่ได้ชั่วคราวเพราะเครดิต Anthropic หมด)

---

## 1. TL;DR สำหรับ integrator

- **Base URL:** `http://192.168.5.32:8002` (HTTP, LAN ภายใน — ยังไม่มี TLS)
- **เลือก endpoint:** มี LLM ของตัวเองแล้ว → ใช้ **`/search`** (เร็ว ~0.2s, ฟรี, ไม่มีวันล่มเพราะ LLM); อยากได้คำตอบสำเร็จรูป → ใช้ **`/ask`** (ช้ากว่า, มีค่าใช้จ่าย, พึ่งเครดิต Claude)
- **Auth:** ใส่ header `X-API-Key: <key>` (ขอจากผู้ดูแล) — ตอนนี้ยังเป็น mode `warn` (ไม่ใส่ก็ผ่าน แต่ถูก log); จะเปลี่ยนเป็น `enforce` เมื่อ consumer ใส่ key ครบ **→ ใส่ key ตั้งแต่วันแรกจะได้ไม่ต้องแก้ทีหลัง**
- **Browser เรียกตรงไม่ได้** (ไม่มี CORS โดย default) — เรียกจาก backend/server-side หรือขอเปิด CORS (ดูข้อ 6)
- **ส่ง `role` ทุก request** — เป็นตัวกำหนดว่าเห็นเอกสารกลุ่มไหน (RBAC)
- Interactive docs: `http://192.168.5.32:8002/docs` | schema: `/openapi.json`

---

## 2. Endpoints

### `GET /health` — เช็คสถานะ (ไม่ต้อง auth)
```json
{"status":"ok","collection":"company_docs","vectors":2404,"model":"BAAI/bge-m3"}
```

### `POST /search` — ค้นเอกสาร (retrieval ล้วน, ไม่ผ่าน LLM)

Request:
```json
{"query": "ขั้นตอนการผสมสี", "role": "production", "top_k": 3}
```
| field | required | หมายเหตุ |
|---|---|---|
| `query` | ✅ | ห้ามว่าง |
| `role` | ✅ | ต้องเป็น role ที่รองรับ (ดูข้อ 3) — กำหนดสิทธิ์เห็นเอกสาร |
| `top_k` | — | default 3, ช่วง **1–10** |

Response:
```json
{
  "query": "...", "role": "production",
  "results": [{
    "rank": 1, "score": 0.71,
    "source": "QP-751-15 Rev.00 การควบคุมกระบวนการผสมสี",
    "collection": "PRODUCTION", "level": 2,
    "heading": "## 5. ขั้นตอนการปฏิบัติงาน",
    "preview": "…ตัดสั้น 400 ตัว ไม่มีขึ้นบรรทัด…",
    "content": "…เนื้อหาเต็ม ≤2000 ตัว คงขึ้นบรรทัด — เอาไปให้ LLM ของคุณเรียบเรียง…"
  }]
}
```
> ใช้ `content` (ไม่ใช่ `preview`) เป็น context ป้อน LLM ฝั่งคุณ — `preview` มีไว้โชว์ UI เท่านั้น

### `POST /ask` — ถาม-ตอบพร้อมอ้างอิง (retrieval + Claude)

Request: `{"question": "...", "role": "production", "top_k": 4}`
Response:
```json
{
  "question": "...", "role": "production",
  "answer": "คำตอบภาษาไทย ... [1][2]",
  "citations": [{"ref": 1, "source": "...", "heading": "...", "score": 0.68}],
  "model": "claude-opus-4-8"
}
```
- คำถามนอกคลัง → `answer` = "ไม่พบข้อมูล..." (ไม่แต่งคำตอบ — วัดแล้ว hallucination 0/8)
- คำถามที่ role ไม่มีสิทธิ์ → ไม่ดึงเอกสารหวงห้ามมาตอบ (permission leak 0/5)

### `GET /collections?role=admin` — สถิติ collection (admin เท่านั้น)

---

## 3. Roles (RBAC)

`admin` `management` `production` `prepress` `qc` `engineering` `sales` `purchasing` `logistics` `hr` `it`

- `role` filter เอกสารก่อน retrieval เสมอ (enforce ที่ Qdrant ไม่ trust ระดับ app)
- เอกสารที่ระบบจำแนกไม่ได้ = `UNCLASSIFIED` เห็นได้เฉพาะ `admin` (default-deny)
- **สำคัญ:** เมื่อเปลี่ยนเป็น `AUTH_MODE=enforce` — API key แต่ละตัวมี scope ว่าใช้ role ไหนได้บ้าง ส่ง role นอก scope → HTTP 403

---

## 4. Error contract (เขียน handler ให้ครบ)

| HTTP | เมื่อไหร่ | body |
|---|---|---|
| `400` | business validation: role ไม่รู้จัก / query ว่าง / top_k นอก 1–10 | `{"detail": "ข้อความอธิบาย"}` |
| `422` | field หาย/ผิดชนิด (FastAPI มาตรฐาน) | `{"detail":[{"type":"missing","loc":["body","role"],...}]}` |
| `401` | (เฉพาะ enforce) ไม่มี/ผิด API key | `{"detail":"Auth failed: missing_or_invalid_key"}` |
| `403` | (เฉพาะ enforce) role นอก scope ของ key | `{"detail":"Auth failed: role_out_of_scope:..."}` |
| `429` | `/ask`: Claude rate limited | `{"detail":"LLM rate limited — retry later"}` |
| `502` | `/ask`: Claude error (เช่น **เครดิตหมด**) | `{"detail":"LLM error: ..."}` |
| `503` | `/ask`: ไม่ได้ตั้ง key / Qdrant ล่ม | `{"detail":"LLM not configured ..."}` |

> ข้อควรระวัง: `400` body `detail` เป็น **string** แต่ `422` เป็น **array** — parse ให้ถูกชนิด

---

## 5. Performance & ความเสถียร (วัดจริง 2026-07-27)

| | `/search` | `/ask` |
|---|---|---|
| latency | ~0.2s (p95 ~0.7s ตอน concurrent) | p50 **7s** / p95 **26s** |
| ขึ้นกับ LLM | ❌ ไม่ | ✅ ต้องมีเครดิต Claude |
| แนะนำ client timeout | 30s | **90–120s** (อย่าตั้งต่ำกว่า 30s) |
| concurrent | ทดสอบ 4 พร้อมกันผ่าน ยังไม่มี load test หนัก | LLM มี rate limit — ทยอยส่ง |

- `/ask` ช้าและอาจล่มตามเครดิต → **ถ้างานคุณ real-time (voicebot) แนะนำ `/search` + LLM ฝั่งคุณ** จะคุม latency/cost ได้เอง
- ยังไม่มี retry/rate-limit ฝั่ง server → client ควรมี exponential backoff เอง (โดยเฉพาะ 429/502/503)

---

## 6. ข้อจำกัดที่ต้องรู้ก่อนเชื่อม (blocker/gotcha)

1. **ไม่มี CORS โดย default** → เรียกจาก JavaScript ใน browser ตรงๆ ไม่ได้ (preflight 405)
   - ทางเลือก A (แนะนำ): เรียกผ่าน backend ของคุณ (server-to-server)
   - ทางเลือก B: ขอผู้ดูแลตั้ง `CORS_ORIGINS="http://your-host:3000"` ใน `.env` แล้ว restart (โค้ดรองรับแล้ว, ระบุ origin ชัดเจน ไม่ใช้ `*`)
2. **HTTP ล้วน ยังไม่มี TLS** — อยู่หลัง LAN; อย่าส่งข้อมูลลับผ่าน public network จนกว่าจะมี reverse proxy + TLS
3. **`role` ยัง trust จาก request body** จนกว่า enforce จะเปิด — อย่าเพิ่งพึ่ง API นี้เป็น security boundary เดียวสำหรับข้อมูลที่ sensitive จริง
4. **`/ask` = ข้อมูลออก Cloud (Claude)** — ปัจจุบัน corpus เป็น SOP/WI ภายใน (ระดับ Internal) โอเค; **ห้ามส่งข้อมูลลูกค้า/ต้นทุน/PII ผ่าน `/ask`** จนกว่าจะมีนโยบายอนุมัติ (ดู tripwire ใน STATUS.md)
5. **PoC** — schema (`1.0.0-poc`) ยังเปลี่ยนได้ แต่จะเป็น **additive** (เพิ่ม field ไม่ลบของเดิม) เหมือนที่เพิ่ม `content` เข้ามา

---

## 7. ตัวอย่างเรียกใช้

```bash
# search (server-to-server, ใส่ key เผื่ออนาคต enforce)
curl -X POST http://192.168.5.32:8002/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your-service-key>" \
  -d '{"query":"ขั้นตอนการผสมสีพิเศษ","role":"production","top_k":3}'
```
```python
import requests
r = requests.post("http://192.168.5.32:8002/search",
    headers={"X-API-Key": KEY},
    json={"query": q, "role": "production", "top_k": 3}, timeout=30)
r.raise_for_status()
for hit in r.json()["results"]:
    context = hit["content"]        # ป้อน LLM ฝั่งคุณ
```

---

## 8. Checklist ก่อน go-live ของ consumer

- [ ] ขอ service API key จากผู้ดูแล + เก็บใน secret store (ไม่ hardcode)
- [ ] ส่ง `X-API-Key` ทุก request ตั้งแต่วันแรก (กัน enforce มาแล้วพัง)
- [ ] map role ของผู้ใช้ฝั่งคุณ → role ในระบบนี้ให้ถูก (อย่า hardcode `admin`)
- [ ] handle 400/422/429/502/503 + backoff
- [ ] ตั้ง timeout: search 30s / ask 120s
- [ ] ถ้าเป็น browser app: เรียกผ่าน backend หรือขอเปิด CORS
- [ ] ไม่ส่ง PII/ข้อมูลลับผ่าน `/ask` จนกว่านโยบายจะอนุมัติ
