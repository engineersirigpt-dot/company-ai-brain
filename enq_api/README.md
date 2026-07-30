# ENQ API (v1.1 transport)

FastAPI "หน้าร้าน" ของ RFQ backend — ทำให้ `create_rfq_draft` (migration 006) เรียกผ่าน HTTP ได้
**โมดูลแยก รันในเครื่อง dev เท่านั้น — ไม่ใช่ `app/main.py` ที่ deploy อยู่ และยังไม่ deploy**

## รัน (local, ต่อ dev DB `rfq_dev` ที่ `localhost:5433`)

```bash
# deps (ครั้งเดียว)
pip install fastapi "uvicorn[standard]" pydantic psycopg2-binary

# dev DB (ถ้ายังไม่รัน)
docker run -d --name rfq_dev -e POSTGRES_PASSWORD=test -e POSTGRES_DB=rfqtest -p 5433:5432 postgres:16
#  แล้วโหลด migrations 001,002,003,005,006,007 (ดู migrations/test/run_all.sh)

# start
uvicorn enq_api.main:app --host 127.0.0.1 --port 8090
```

env (ถ้าไม่ตั้ง = dev mode): `RFQ_DSN`, `ENQ_API_KEY` (ตั้ง = เปิด auth), `MAX_BODY_BYTES`

## endpoints

| method | path | ทำอะไร | DB role |
|---|---|---|---|
| GET | `/health` | เช็ค DB | rfq_app |
| POST | `/enq/draft` | สร้าง RFQ DRAFT จาก payload `draft-v1` | **rfq_ingest** (write) |
| GET | `/enq/rfq/{id}` | อ่าน RFQ tree กลับ | **rfq_app** (read) |

```bash
curl -X POST localhost:8090/enq/draft -H 'Content-Type: application/json' \
  -H 'X-Request-Id: job-123' \
  --data '{"schema_version":"draft-v1","items":[{"line_no":1,"job_name":"box",
           "quantity_options":[{"option_no":1,"quantity":5000,"unit_ref":"PCS","is_primary":true}]}]}'
# → {"rfq_id":"...","request_id":"job-123","actor":"enq-api-dev"}
```

## Codex FastAPI guardrails ที่ยึด
- **#1** write ผ่าน role `rfq_ingest`, read ผ่าน `rfq_app` — บังคับ role boundary แม้ผ่าน API (SET ROLE ต่อ op)
- **#2** `actor` มาจาก server (API key) ไม่ใช่ request body
- **#3** `service='enq'` hard-code ฝั่ง server
- **#5** validate 2 ชั้น: Pydantic (top-level unknown key/size/structure) ที่ API + **DB allowlist strict** ชั้นสุดท้าย
- **#9** `X-Request-Id` → idempotency (POST ซ้ำ = rfq_id เดิม)

error mapping: DB validation (`22023/54000/23503/23505`) → `422`; อื่น ๆ → `500`

## ยังไม่ทำ (increment ถัดไป)
- **begin/claim/apply/fail** (AI extraction path) — ต้อง seed trusted tables + mock provider loop + `provider_input_ref`/`should_execute` handling (guardrails #6-8)
- auth จริง (JWT/Keycloak → map principal → actor), deploy, ข้อมูลจริง/Cloud (รอ Data Owner/DPO)
