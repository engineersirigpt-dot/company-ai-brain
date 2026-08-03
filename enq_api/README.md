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

### DB role model (M1/M2 role split — migration 009)

4 non-superuser role แยกตาม surface (credential เดียวถูกเจาะ = ทำได้เฉพาะ surface ตัวเอง):

| role (login) | ใช้โดย | ทำได้ |
|---|---|---|
| **rfq_ingest** (`rfq_ingest_login`) | FastAPI inbound (`RFQ_WRITE_DSN`) | `create_rfq_draft`, `begin_rfq_extraction` เท่านั้น |
| **rfq_worker** (`rfq_worker_login`) | worker (`RFQ_WORKER_DSN`) | `list_claimable`, `claim`, `apply`, `fail` เท่านั้น |
| **rfq_read_api** (`rfq_read_api_login`) | FastAPI read (`RFQ_READ_DSN`) | SELECT business tree tables + `get_extraction_status` (M1: ไม่มี SELECT ALL — อ่าน run/attachment/trusted ตรง ๆ ไม่ได้) |
| **rfq_app** (`rfq_app_login`) | reviewer (internal, ไม่ใช่ public API) | SELECT ALL + reviewer capability |

### env

| var | ค่า | หมายเหตุ |
|---|---|---|
| `ENQ_API_KEY` | **required** — auth key (`X-API-Key` ทุก `/enq/*`) | **fail-closed**: ไม่ตั้ง → startup error (compare_digest, ASCII) |
| `RFQ_WRITE_DSN` | inbound = **rfq_ingest_login** | default `@localhost:5433` |
| `RFQ_READ_DSN` | public read = **rfq_read_api_login** (M1 allowlist) | default `@localhost:5433` |
| `RFQ_WORKER_DSN` | worker = **rfq_worker_login** (M2) | ใช้โดย `python -m enq_api.worker` |
| `ENQ_MAX_BODY_BYTES` / `ENQ_MAX_JSON_DEPTH` | body/depth limit (default `1000000`/`12`) | |

## endpoints

| method | path | ทำอะไร | DB role |
|---|---|---|---|
| GET | `/health` | เช็ค DB | rfq_read_api |
| POST | `/enq/draft` | สร้าง RFQ DRAFT จาก payload `draft-v1` (manual) | **rfq_ingest** (write) |
| GET | `/enq/rfq/{id}` | อ่าน RFQ tree กลับ | **rfq_read_api** (read) |
| POST | `/enq/extractions` | เริ่ม AI extraction (begin) → `202` durable run | **rfq_ingest** (write) |
| GET | `/enq/extractions/{run_id}` | สถานะ run (projection ปลอดภัย) | **rfq_read_api** (read) |

```bash
# manual draft
curl -X POST localhost:8090/enq/draft -H 'X-API-Key: dev-local-key' -H 'X-Request-Id: job-123' \
  -H 'Content-Type: application/json' \
  --data '{"schema_version":"draft-v1","items":[{"line_no":1,"job_name":"box",
           "quantity_options":[{"option_no":1,"quantity":5000,"unit_ref":"PCS","is_primary":true}]}]}'
# → {"rfq_id":"...","request_id":"job-123","actor":"enq-api"}

# AI extraction (server policy = LOCAL/typhoon/v1/enq ; client ส่งแค่ source ref + correlation)
curl -X POST localhost:8090/enq/extractions -H 'X-API-Key: dev-local-key' -H 'X-Request-Id: enq-1' \
  -H 'Content-Type: application/json' --data '{"source_ingest_id":"<uuid>","correlation":{"enquiry_ref":"E1"}}'
# → 202 {"run_id":"...","rfq_id":"...","status":"PENDING",...}   (worker จะ claim→provider→apply)
```

## extraction orchestration (Codex-approved flow)

```
POST /enq/extractions → begin (short txn) → 202 (run = durable work record)
  ↓
enq_api/worker.py (durable poller, แยก process — ไม่ใช่ BackgroundTasks):
  list_claimable_extractions → claim (short txn, commit) → provider **นอก DB txn** (เฉพาะ should_execute=true)
  → apply / fail (txn ใหม่) ; lease หมด → reclaim + old-lease ถูก fence (RFS01)
```
- **server-controlled ทั้งหมด:** actor/service/target/provider/model/purpose/`provider_input_ref`/`input_sha256`/lease/egress — client ส่งได้แค่ source ref + correlation
- **provider สังเคราะห์** ([`provider.py`](provider.py)) — LOCAL mock, deterministic, **ไม่เรียก cloud/ข้อมูลจริง**
- รัน worker: `RFQ_WORKER_DSN=... python -m enq_api.worker`  (worker ใช้ `RFQ_WORKER_DSN` = rfq_worker_login เท่านั้น)

## tests

```bash
# transport/mapper/auth (50 checks)
PY=/path/to/python bash enq_api/run_api_tests.sh
# extraction orchestration end-to-end (21 checks — Codex acceptance checklist)
PY=/path/to/python bash enq_api/run_orchestration_tests.sh
```
transport ครอบ: auth ทุก route + wrong-key + non-ASCII-key + startup fail-closed + no dev-bypass, idempotency,
operation-aware SQLSTATE mapper (RFS01/RFN01/RFR01/RFI01 + 22/23xxx/54000/transient, ไม่รั่ว DB message),
oversize + multi-frame + disconnect, schema strict, login least-privilege
orchestration ครอบ: POST→202 durable, replay no-dup, forge trusted field→422, worker poll→claim→provider→apply→SUCCEEDED,
GET safe projection (no leak), should_execute=false→no provider, reclaim + old-lease fencing (RFS01)

## Codex transport fixes ที่ยึด (BFFA702 + 8EB6C41 + confirm 397AB59)
- **B1** auth ทุก `/enq/*` ผ่าน **router-wide dependency** (route ใหม่ fail-closed อัตโนมัติ) + `secrets.compare_digest`;
  **ตัด unauthenticated dev mode ทิ้ง**; **H1** non-ASCII key → `401` (ไม่ใช่ `500`), key format = ASCII ตั้งแต่ startup
- **B2/F1** **operation-aware error mapper** `_http_for_pg(op, sqlstate)` + custom SQLSTATE จาก 007
  (`RFS01`/`RFN01`/`RFR01`) → แยก state-conflict/not-found/invalid-result; log เฉพาะ schema identifier (ไม่ log ค่าดิบ)
- **B2/M1** raw body limit ที่ ASGI middleware (นับ bytes ก่อน parse, กัน chunked) + JSON depth; `http.disconnect` → หยุด ไม่เรียก downstream
- **B3** connect ด้วย LOGIN role non-superuser แยกตาม surface (inbound `rfq_ingest_login` / worker `rfq_worker_login` / read `rfq_read_api_login`) — ไม่ใช่ postgres, ไม่ `SET ROLE` ; **startup fail-closed** ถ้า DSN ชี้ role ผิด surface
- **H1(idem)** `X-Request-Id` **required** สำหรับ POST; key เดิม+payload ต่าง → `409`
- **H2** route เป็น sync `def` (threadpool) + statement/lock/idle timeout; **H2(test)** runner bind loopback + Docker-assigned port
- **H3** `schema_version` = required `Literal['draft-v1']` + `extra="forbid"`; DB allowlist = ด่านสุดท้าย

error mapping ครบ → ดู [`ENQ_EXTRACTION_ERROR_TAXONOMY.md`](../ENQ_EXTRACTION_ERROR_TAXONOMY.md):
`RFS01`→`409 state_conflict`, `RFN01`→`404 run_not_found`, `RFR01`→`422 invalid_extraction_result`,
`RFI01`→`409 idempotency_conflict` (ledger), `23505` dup-business-key→`422` (draft/apply) / `500` (begin·claim·fail),
class`22`/`235xx`→`422` (label ตาม op), `54000`→`413`, transient→`503`, อื่น→`500`

## ยังไม่ทำ (increment ถัดไป)
- **cloud routing** — server policy v1 = LOCAL-only; CLOUD (REDACT/ALLOW → attestation/approval lookup) ยังไม่เปิด
- **provider จริง** (vLLM/Typhoon local, Cloud) แทน synthetic mock — gated ตาม Data Owner/DPO/Legal
- auth จริง (JWT/Keycloak → map principal → actor + RFQ authz), deploy, ข้อมูลจริง/Cloud
