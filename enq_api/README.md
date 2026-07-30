# ENQ API (v1.1 transport)

FastAPI "หน้าร้าน" ของ RFQ backend — ทำให้ `create_rfq_draft` (migration 006) เรียกผ่าน HTTP ได้
**โมดูลแยก รันในเครื่อง dev เท่านั้น — ไม่ใช่ `app/main.py` ที่ deploy อยู่ และยังไม่ deploy**

## รัน (local, ต่อ dev DB `rfq_dev` ที่ `localhost:5433`)

```bash
# deps (ครั้งเดียว)
pip install fastapi "uvicorn[standard]" pydantic psycopg2-binary

# dev DB (ถ้ายังไม่รัน)
docker run -d --name rfq_dev -e POSTGRES_PASSWORD=test -e POSTGRES_DB=rfqtest -p 5433:5432 postgres:16
#  โหลด migrations 001,002,003,005,006,007 (ดู migrations/test/run_all.sh)
#  แล้วสร้าง login roles non-superuser (ครั้งเดียว):  psql ... < enq_api/dev_roles.sql

# start — fail-closed: ต้องตั้ง ENQ_API_KEY หรือ ENQ_DEV_MODE=1 ไม่งั้น startup จะ error
ENQ_DEV_MODE=1 uvicorn enq_api.main:app --host 127.0.0.1 --port 8090
```

### env (Codex transport fixes)

| var | ค่า | หมายเหตุ |
|---|---|---|
| `ENQ_API_KEY` | เปิด auth (ต้องส่ง `X-API-Key` ทุก route) | **fail-closed**: ถ้าไม่ตั้ง และไม่มี `ENQ_DEV_MODE=1` → startup error |
| `ENQ_DEV_MODE` | `1` = loopback dev (actor=`enq-api-dev`, ไม่ตรวจ key) | ใช้เฉพาะ local synthetic เท่านั้น |
| `RFQ_WRITE_DSN` | DSN ของ **rfq_ingest_login** (non-superuser) | default = `rfq_ingest_login@localhost:5433` |
| `RFQ_READ_DSN` | DSN ของ **rfq_app_login** (non-superuser) | default = `rfq_app_login@localhost:5433` |
| `ENQ_MAX_BODY_BYTES` | จำกัด raw body (default `1000000`) | นับ bytes จริงที่ ASGI ก่อน parse (กัน chunked bypass) |
| `ENQ_MAX_JSON_DEPTH` | จำกัด nesting (default `12`) | |

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

## tests (Codex H5)

```bash
# ephemeral PG + migrations + login roles + set env → รัน test_api.py (14 checks)
PY=/path/to/python bash enq_api/run_api_tests.sh
```
ครอบ: auth ทุก route + startup fail-closed, idempotency required/replay/conflict, oversize→413,
schema_version required, unknown-key + error no-leak, read role ไม่ใช่ superuser

## Codex transport fixes ที่ยึด (review BFFA702)
- **B1** auth ทุก `/enq/*` route (รวม GET) + **fail-closed** default; dev เปิดได้เฉพาะ `ENQ_DEV_MODE=1`
- **B2** จำกัด raw body ที่ ASGI middleware (นับ bytes ก่อน parse, กัน chunked) + JSON depth limit
- **B3** connect ด้วย dedicated LOGIN role non-superuser (`rfq_ingest_login`/`rfq_app_login`) — ไม่ใช่ postgres
- **H1** `X-Request-Id` **required** สำหรับ POST (ไม่ generate เอง); key เดิม+payload ต่าง → `409`
- **H2** route เป็น sync `def` (FastAPI → threadpool) + statement/lock/idle timeout
- **H3** `schema_version` = required `Literal['draft-v1']` + `extra="forbid"`; DB allowlist = ด่านสุดท้าย
- **H4** ไม่ส่ง raw DB message กลับ client — map pgcode → stable public code + log ฝั่ง server

error mapping: `22023/23503`→`422`, `23505`→`409`, `54000`→`413`, อื่น ๆ →`500` (ไม่มี DB message)

## ยังไม่ทำ (increment ถัดไป)
- **begin/claim/apply/fail** (AI extraction path) — ต้อง seed trusted tables + mock provider loop + `provider_input_ref`/`should_execute` handling (guardrails #6-8)
- auth จริง (JWT/Keycloak → map principal → actor), deploy, ข้อมูลจริง/Cloud (รอ Data Owner/DPO)
