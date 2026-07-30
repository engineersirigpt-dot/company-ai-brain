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

# start — fail-closed เสมอ: ต้องตั้ง ENQ_API_KEY ไม่งั้น startup error (ไม่มี unauthenticated dev mode)
#         local dev = ใช้ key ทดสอบคงที่ แล้วส่ง X-API-Key ทุก request
ENQ_API_KEY=dev-local-key uvicorn enq_api.main:app --host 127.0.0.1 --port 8090
```

### env (Codex transport fixes)

| var | ค่า | หมายเหตุ |
|---|---|---|
| `ENQ_API_KEY` | **required** — auth key (ต้องส่ง `X-API-Key` ทุก `/enq/*` route) | **fail-closed**: ไม่ตั้ง → startup error; ไม่มี dev bypass อีกต่อไป (compare_digest) |
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
  -H 'X-API-Key: dev-local-key' \
  --data '{"schema_version":"draft-v1","items":[{"line_no":1,"job_name":"box",
           "quantity_options":[{"option_no":1,"quantity":5000,"unit_ref":"PCS","is_primary":true}]}]}'
# → {"rfq_id":"...","request_id":"job-123","actor":"enq-api"}
```

## tests

```bash
# ephemeral PG (loopback + Docker-assigned port ต่อ process) + migrations + login roles → test_api.py (46 checks)
PY=/path/to/python bash enq_api/run_api_tests.sh
```
ครอบ: auth ทุก route + wrong-key + non-ASCII-key + startup fail-closed + no dev-bypass, idempotency required/replay/conflict,
operation-aware SQLSTATE mapper (RFS01/RFN01/RFR01 + 22/23xxx/54000/transient, ไม่รั่ว DB message),
oversize + multi-frame + disconnect (middleware), schema_version required, login least-privilege

## Codex transport fixes ที่ยึด (BFFA702 + 8EB6C41 + confirm 397AB59)
- **B1** auth ทุก `/enq/*` ผ่าน **router-wide dependency** (route ใหม่ fail-closed อัตโนมัติ) + `secrets.compare_digest`;
  **ตัด unauthenticated dev mode ทิ้ง**; **H1** non-ASCII key → `401` (ไม่ใช่ `500`), key format = ASCII ตั้งแต่ startup
- **B2/F1** **operation-aware error mapper** `_http_for_pg(op, sqlstate)` + custom SQLSTATE จาก 007
  (`RFS01`/`RFN01`/`RFR01`) → แยก state-conflict/not-found/invalid-result; log เฉพาะ schema identifier (ไม่ log ค่าดิบ)
- **B2/M1** raw body limit ที่ ASGI middleware (นับ bytes ก่อน parse, กัน chunked) + JSON depth; `http.disconnect` → หยุด ไม่เรียก downstream
- **B3** connect ด้วย LOGIN role non-superuser (`rfq_ingest_login`/`rfq_app_login`) — ไม่ใช่ postgres, ไม่ `SET ROLE`
- **H1(idem)** `X-Request-Id` **required** สำหรับ POST; key เดิม+payload ต่าง → `409`
- **H2** route เป็น sync `def` (threadpool) + statement/lock/idle timeout; **H2(test)** runner bind loopback + Docker-assigned port
- **H3** `schema_version` = required `Literal['draft-v1']` + `extra="forbid"`; DB allowlist = ด่านสุดท้าย

error mapping ครบ → ดู [`ENQ_EXTRACTION_ERROR_TAXONOMY.md`](../ENQ_EXTRACTION_ERROR_TAXONOMY.md):
`RFS01`→`409 state_conflict`, `RFN01`→`404 run_not_found`, `RFR01`→`422 invalid_extraction_result`,
`23505`→`409 idempotency_conflict`, class`22`/`235xx`→`422` (label ตาม op), `54000`→`413`, transient→`503`, อื่น→`500`

## ยังไม่ทำ (increment ถัดไป)
- **begin/claim/apply/fail** (AI extraction path) — ต้อง seed trusted tables + mock provider loop + `provider_input_ref`/`should_execute` handling (guardrails #6-8)
- auth จริง (JWT/Keycloak → map principal → actor), deploy, ข้อมูลจริง/Cloud (รอ Data Owner/DPO)
