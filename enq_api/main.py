"""
ENQ API (v1.1 transport) — FastAPI หน้าร้านของ RFQ backend
==========================================================
หุ้ม create_rfq_draft (migration 006) เป็น HTTP — synthetic/manual draft path
โมดูลแยก รันในเครื่อง dev เท่านั้น (ไม่ใช่ app/main.py ที่ deploy) — ยังไม่ deploy

Codex transport review (BFFA702) → ปิดครบ B1/B2/B3 + H1-H5
Codex re-review (8EB6C41) → ปิดเพิ่ม:
  B1  ตัด unauthenticated dev mode ทิ้ง — บังคับ API key แม้ local dev (secrets.compare_digest);
      auth เป็น router-wide dependency (route ใหม่ fail-closed อัตโนมัติ ไม่ต้องใส่ทีละตัว)
  B2  error map แบบ operation-aware ตาม SQLSTATE (ดู ENQ_EXTRACTION_ERROR_TAXONOMY.md): custom RFS01/RFN01/RFR01/RFI01
      + class 22/23502/23503/23514 → 422, RFI01 → 409, real 23505 (dup key) → 422/500 ตาม op, 54000 → 413,
      transient (57014/55P03/40001/40P01/class 08) → 503, อื่น → 500; log เฉพาะ schema identifier (ไม่ log ค่าดิบ)
  M1  middleware: http.disconnect → return ทันที ไม่ส่งต่อ downstream
"""
from __future__ import annotations
import os, json, logging, secrets
from typing import Any, Literal
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field

log = logging.getLogger("enq_api")

# ---- config ----
ENQ_API_KEY    = os.environ.get("ENQ_API_KEY")
MAX_BODY_BYTES = int(os.environ.get("ENQ_MAX_BODY_BYTES", "1000000"))
MAX_JSON_DEPTH = int(os.environ.get("ENQ_MAX_JSON_DEPTH", "12"))
SERVICE        = "enq"                                          # #3 hard-coded server-side
_DEV_DSN_W = "host=localhost port=5433 dbname=rfqtest user=rfq_ingest_login password=ingest connect_timeout=5"
_DEV_DSN_R = "host=localhost port=5433 dbname=rfqtest user=rfq_read_api_login password=readapi connect_timeout=5"
WRITE_DSN = os.environ.get("RFQ_WRITE_DSN", _DEV_DSN_W)         # B3: inbound (create_rfq_draft + begin) — rfq_ingest
READ_DSN  = os.environ.get("RFQ_READ_DSN",  _DEV_DSN_R)         # M1: public read = rfq_read_api (allowlist, ไม่ใช่ SELECT ALL)

# B1: fail-closed — บังคับ auth เสมอ (ไม่มี unauthenticated dev mode อีกต่อไป)
#     local dev ให้ตั้ง ENQ_API_KEY เป็น key ทดสอบคงที่ แล้วส่ง X-API-Key ทุก request
if not ENQ_API_KEY:
    raise RuntimeError("fail-closed: ต้องตั้ง ENQ_API_KEY (บังคับ auth แม้ local dev — ไม่มี unauthenticated dev mode)")
if not ENQ_API_KEY.isascii():                                  # H1: key format = ASCII (compare_digest รับเฉพาะ ASCII)
    raise RuntimeError("fail-closed: ENQ_API_KEY ต้องเป็น ASCII")


# ---- B2/ASGI middleware จำกัด raw body (นับ bytes จริง ก่อน JSON parse; รองรับ chunked) ----
class LimitBodyMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app; self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        body = b""; more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
                if len(body) > self.max_bytes:
                    await send({"type": "http.response.start", "status": 413,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": b'{"detail":"payload_too_large"}'})
                    return
            elif msg["type"] == "http.disconnect":
                return                                          # M1: client ตัดแล้ว — หยุด ไม่เรียก downstream
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}
        await self.app(scope, replay, send)


app = FastAPI(title="ENQ API (RFQ draft)", version="0.3.0")
app.add_middleware(LimitBodyMiddleware, max_bytes=MAX_BODY_BYTES)


# ---- B1: auth dependency — บังคับทุก /enq/* route ผ่าน router-wide dependency ----
def require_actor(x_api_key: str | None = Header(default=None)) -> str:
    # H1: non-ASCII key → 401 (secrets.compare_digest รับเฉพาะ ASCII — กัน 500/traceback)
    if not x_api_key or not x_api_key.isascii() or not secrets.compare_digest(x_api_key, ENQ_API_KEY):
        raise HTTPException(status_code=401, detail="unauthorized")
    return "enq-api"                                           # prod: map API-key/JWT → principal (ยังไม่ทำ)


enq = APIRouter(prefix="/enq", dependencies=[Depends(require_actor)])   # route ใหม่ fail-closed อัตโนมัติ


# ---- B3/H2: DB connection (dedicated login role, ไม่ superuser, มี timeout) ----
@contextmanager
def db(dsn: str):
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET search_path TO rfq")
            cur.execute("SET statement_timeout='10s'")
            cur.execute("SET lock_timeout='5s'")
            cur.execute("SET idle_in_transaction_session_timeout='15s'")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


# ---- H3: strict-ish payload (schema_version required; DB = allowlist ชั้นสุดท้าย) ----
class DraftPayload(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: Literal["draft-v1"]                        # required, no default
    header: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)


def _check_depth(o: Any, d: int = 1):
    if d > MAX_JSON_DEPTH:
        raise HTTPException(status_code=422, detail="payload nesting too deep")
    if isinstance(o, dict):
        for v in o.values(): _check_depth(v, d + 1)
    elif isinstance(o, list):
        for v in o: _check_depth(v, d + 1)


# ---- B2/H4/F1: (operation, SQLSTATE) → (http status, stable public code) ----
# custom RF* codes จาก migration 007 (self-describing) + standard codes; op เลือก label ของ invalid-input
_TRANSIENT = {"57014", "55P03", "40001", "40P01"}              # canceled/timeout, lock, deadlock, serialize
_INVALID_LABEL = {"draft": "invalid_payload", "begin": "invalid_request",
                  "claim": "invalid_request", "fail": "invalid_request",
                  "apply": "invalid_extraction_result"}

def _http_for_pg(op: str, code: str | None) -> tuple[int, str]:
    if not code:
        return 500, "internal_error"
    cls = code[:2]
    if code in _TRANSIENT or cls == "08":                      # transient ก่อน (55P03 = class 55 แต่ retryable)
        return 503, "database_unavailable"
    if code == "RFS01":                                        # extraction: wrong status / lease mismatch|expired
        return 409, "state_conflict"
    if code == "RFN01":                                        # extraction: run_id ไม่พบ
        return 404, "run_not_found"
    if code == "RFR01":                                        # apply: invalid provider result (evidence/derivation/ref)
        return 422, "invalid_extraction_result"
    if code == "RFI01":                                        # explicit ledger conflict (request_id ซ้ำ payload/actor ต่าง)
        return 409, "idempotency_conflict"
    if code == "54000":                                        # program-limit (size/collection cap)
        return 413, "payload_too_large"
    if code == "23505":                                        # real unique violation = duplicate business key (ไม่ใช่ ledger)
        return (422, _INVALID_LABEL.get(op, "invalid_request")) if op in ("draft", "apply") else (500, "internal_error")
    if cls == "22" or code in ("23502", "23503", "23514"):     # data-exception / not-null / FK / check → invalid input
        return 422, _INVALID_LABEL.get(op, "invalid_request")
    return 500, "internal_error"


def _raise_db(e: psycopg2.Error, request_id: str, op: str):
    code = getattr(e, "pgcode", None)
    diag = getattr(e, "diag", None)
    status, detail = _http_for_pg(op, code)
    # log เฉพาะ schema identifier — ไม่ log message_primary ดิบ (อาจมีค่าจาก ENQ จริง)
    log.warning("db error req_id=%s op=%s sqlstate=%s constraint=%s column=%s table=%s → %s",
                request_id, op, code,
                getattr(diag, "constraint_name", None), getattr(diag, "column_name", None),
                getattr(diag, "table_name", None), status)
    raise HTTPException(status_code=status, detail=detail)


@app.get("/health")                                            # /health = public (ไม่ auth) — ไม่แตะข้อมูล RFQ
def health():
    try:
        with db(READ_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
            is_super = cur.fetchone()[0]
        return {"status": "ok", "db": "up", "service": SERVICE, "read_role_superuser": is_super}
    except Exception:
        raise HTTPException(status_code=503, detail="database_unavailable")


@enq.post("/draft")
def create_draft(
    payload: DraftPayload,
    actor: str = Depends(require_actor),                       # cached: router-wide dep รันแล้ว ค่าเดิม
    x_request_id: str | None = Header(default=None),
):
    request_id = (x_request_id or "").strip()
    if not request_id:                                        # H1: idempotency key required
        raise HTTPException(status_code=400, detail="X-Request-Id header required")
    if len(request_id) > 200:
        raise HTTPException(status_code=400, detail="X-Request-Id too long")
    body = payload.model_dump()
    _check_depth(body)
    try:
        with db(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT create_rfq_draft(%s::jsonb, %s, %s, %s)",
                        (json.dumps(body, ensure_ascii=False), actor, SERVICE, request_id))
            rfq_id = cur.fetchone()[0]
    except psycopg2.Error as e:
        _raise_db(e, request_id, "draft")
    return {"rfq_id": str(rfq_id), "request_id": request_id, "actor": actor}


@enq.get("/rfq/{rfq_id}")
def get_rfq(rfq_id: str, actor: str = Depends(require_actor)):   # B1: GET ก็ auth (ผ่าน router dep)
    import uuid as _uuid
    try:
        _uuid.UUID(rfq_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="rfq_id must be a uuid")
    with db(READ_DSN) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, rfq_no, status_code, revision_no, enquiry_ref, source_channel, "
                    "customer_name_raw, priority_code FROM rfq WHERE id=%s", (rfq_id,))
        rfq = cur.fetchone()
        if not rfq:
            raise HTTPException(status_code=404, detail="rfq not found")
        cur.execute("SELECT id, line_no, job_name, is_reprint, previous_job_ref, finished_width_mm, "
                    "finished_length_mm, finished_depth_mm, finishing_state, packing_state, artwork_state "
                    "FROM rfq_item WHERE rfq_id=%s ORDER BY line_no", (rfq_id,))
        items = cur.fetchall()
        for it in items:
            iid = it["id"]
            cur.execute("SELECT option_no, quantity, unit_ref, is_primary FROM rfq_quantity_option "
                        "WHERE rfq_item_id=%s ORDER BY option_no", (iid,))
            it["quantity_options"] = cur.fetchall()
            cur.execute("SELECT component_no, component_name, paper_name_snapshot, paper_gsm_snapshot, "
                        "print_sides_code, color_outside_count, color_inside_count FROM rfq_component "
                        "WHERE rfq_item_id=%s ORDER BY component_no", (iid,))
            it["components"] = cur.fetchall()
            cur.execute("SELECT sequence_no, process_ref, process_name_raw, side_code FROM rfq_process_requirement "
                        "WHERE rfq_item_id=%s ORDER BY sequence_no", (iid,))
            it["processes"] = cur.fetchall()
            cur.execute("SELECT sequence_no, packing_name_raw, quantity_per_pack, unit_ref FROM rfq_packing_requirement "
                        "WHERE rfq_item_id=%s ORDER BY sequence_no", (iid,))
            it["packings"] = cur.fetchall()
            cur.execute("SELECT delivery_no, destination_raw, requested_date FROM rfq_delivery "
                        "WHERE rfq_item_id=%s ORDER BY delivery_no", (iid,))
            it["deliveries"] = cur.fetchall()
        rfq["items"] = items
        return json.loads(json.dumps(rfq, default=str))


# ---- extraction orchestration (Codex-approved slice) — server policy v1 = LOCAL-only ----
# public POST → begin (short txn) → 202 ; durable worker (enq_api/worker.py) claim→provider→apply/fail
# client ส่งได้เฉพาะ source reference + correlation ; target/provider/model/purpose = server policy
POLICY = {"target": "LOCAL", "provider": "typhoon", "model": "v1", "purpose": "enq"}   # cloud routing = increment ถัดไป


class ExtractionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source_ingest_id: str                                      # reference ไป trusted source (client เลือกได้)
    correlation: dict[str, Any] = Field(default_factory=dict)  # begin allowlist: enquiry_ref/source_channel[_other]


@enq.post("/extractions", status_code=202)
def create_extraction(
    req: ExtractionRequest,
    actor: str = Depends(require_actor),
    x_request_id: str | None = Header(default=None),
):
    request_id = (x_request_id or "").strip()
    if not request_id:                                        # idempotency key required (เหมือน draft)
        raise HTTPException(status_code=400, detail="X-Request-Id header required")
    if len(request_id) > 200:
        raise HTTPException(status_code=400, detail="X-Request-Id too long")
    import uuid as _uuid
    try:
        _uuid.UUID(req.source_ingest_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="source_ingest_id must be a uuid")
    _check_depth(req.correlation)
    try:
        with db(WRITE_DSN) as conn, conn.cursor() as cur:
            # server policy: target/provider/model/purpose = constants ฝั่ง server ; attestation/approval = NULL (LOCAL)
            cur.execute(
                "SELECT begin_rfq_extraction(%s, %s, %s, %s, %s, NULL, NULL, %s::jsonb, %s, %s, %s)",
                (req.source_ingest_id, POLICY["target"], POLICY["provider"], POLICY["model"], POLICY["purpose"],
                 json.dumps(req.correlation, ensure_ascii=False), actor, SERVICE, request_id))
            out = cur.fetchone()[0]
    except psycopg2.Error as e:
        _raise_db(e, request_id, "begin")
    # ไม่เผย provider_input_ref / input_sha256 / lease — คืนเฉพาะ handle + status
    return {"run_id": out.get("run_id"), "rfq_id": out.get("rfq_id"),
            "status": out.get("status"), "request_id": request_id, "actor": actor}


@enq.get("/extractions/{run_id}")
def get_extraction(run_id: str, actor: str = Depends(require_actor)):   # B1: auth ผ่าน router
    import uuid as _uuid
    try:
        _uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="run_id must be a uuid")
    with db(READ_DSN) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # get_extraction_status = definer projection ปลอดภัย (ไม่มี lease/ref/hash/provider)
        cur.execute("SELECT run_id, rfq_id, status_code, attempt_no FROM get_extraction_status(%s)", (run_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="extraction run not found")
    return {"run_id": str(row["run_id"]), "rfq_id": str(row["rfq_id"]),
            "status": row["status_code"], "attempt_no": row["attempt_no"]}


app.include_router(enq)
