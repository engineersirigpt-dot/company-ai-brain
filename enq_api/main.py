"""
ENQ API (v1.1 transport) — FastAPI หน้าร้านของ RFQ backend
==========================================================
หุ้ม create_rfq_draft (migration 006) เป็น HTTP — synthetic/manual draft path
โมดูลแยก รันในเครื่อง dev เท่านั้น (ไม่ใช่ app/main.py ที่ deploy) — ยังไม่ deploy

แก้ตาม Codex transport review (B1/B2/B3 + H1-H4):
  B1  auth ทุก /enq/* route (รวม GET) + fail-closed default (ต้องมี ENQ_API_KEY เว้นแต่ ENQ_DEV_MODE=1)
  B2  จำกัด raw body ที่ ASGI middleware (นับ bytes จริงก่อน parse — กัน chunked bypass) + nesting-depth limit
  B3  connect ด้วย dedicated LOGIN role (rfq_ingest_login write / rfq_app_login read) — ไม่ใช่ superuser, ไม่ SET ROLE
  H1  idempotency key (X-Request-Id) required สำหรับ mutating; ไม่ generate เอง
  H2  route เป็น sync def (FastAPI ส่งเข้า threadpool) + connect/statement/lock/idle timeout
  H3  schema_version = required Literal['draft-v1'] (ไม่ default)
  H4  ไม่ส่ง raw DB message กลับ client — map pgcode → stable public code + log ฝั่ง server
"""
from __future__ import annotations
import os, json, logging
from typing import Any, Literal
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field

log = logging.getLogger("enq_api")

# ---- config ----
DEV_MODE       = os.environ.get("ENQ_DEV_MODE") == "1"
ENQ_API_KEY    = os.environ.get("ENQ_API_KEY")
MAX_BODY_BYTES = int(os.environ.get("ENQ_MAX_BODY_BYTES", "1000000"))
MAX_JSON_DEPTH = int(os.environ.get("ENQ_MAX_JSON_DEPTH", "12"))
SERVICE        = "enq"                                          # #3 hard-coded server-side
_DEV_DSN_W = "host=localhost port=5433 dbname=rfqtest user=rfq_ingest_login password=ingest connect_timeout=5"
_DEV_DSN_R = "host=localhost port=5433 dbname=rfqtest user=rfq_app_login password=app connect_timeout=5"
WRITE_DSN = os.environ.get("RFQ_WRITE_DSN", _DEV_DSN_W)         # B3: non-superuser login roles
READ_DSN  = os.environ.get("RFQ_READ_DSN",  _DEV_DSN_R)

# B1: fail-closed — ต้องมี auth config เว้นแต่ประกาศ dev mode ชัดเจน
if not DEV_MODE and not ENQ_API_KEY:
    raise RuntimeError("fail-closed: ต้องตั้ง ENQ_API_KEY (หรือ ENQ_DEV_MODE=1 สำหรับ loopback dev)")


# ---- B2: ASGI middleware จำกัด raw body (นับ bytes จริง ก่อน JSON parse; รองรับ chunked) ----
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
                    await send({"type": "http.response.body", "body": b'{"detail":"payload too large"}'})
                    return
            elif msg["type"] == "http.disconnect":
                await self.app(scope, receive, send); return
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}
        await self.app(scope, replay, send)


app = FastAPI(title="ENQ API (RFQ draft)", version="0.2.0")
app.add_middleware(LimitBodyMiddleware, max_bytes=MAX_BODY_BYTES)


# ---- B1: auth dependency ใช้กับทุก /enq/* route ----
def require_actor(x_api_key: str | None = Header(default=None)) -> str:
    if DEV_MODE:
        return "enq-api-dev"
    if not ENQ_API_KEY or x_api_key != ENQ_API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    return "enq-api"                                           # prod: map JWT/registry → principal (ยังไม่ทำ)


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


# ---- H4: pgcode → stable public status/message (ไม่ส่ง raw DB message) ----
_PG_STATUS = {"22023": 422, "23503": 422, "23505": 409, "54000": 413}
_PG_MSG    = {"22023": "invalid payload", "23503": "referenced entity not found",
              "23505": "conflict (idempotency key reused or duplicate)", "54000": "payload limit exceeded"}

def _raise_db(e: psycopg2.Error, request_id: str):
    code = getattr(e, "pgcode", None)
    real = (e.diag.message_primary if e.diag else str(e)) or "db error"
    log.warning("db error req_id=%s pgcode=%s: %s", request_id, code, real)   # detail → server log เท่านั้น
    raise HTTPException(status_code=_PG_STATUS.get(code, 500), detail=_PG_MSG.get(code, "internal error"))


@app.get("/health")
def health():
    try:
        with db(READ_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
            is_super = cur.fetchone()[0]
        return {"status": "ok", "db": "up", "service": SERVICE, "read_role_superuser": is_super}
    except Exception:
        raise HTTPException(status_code=503, detail="db down")


@app.post("/enq/draft")
def create_draft(
    payload: DraftPayload,
    actor: str = Depends(require_actor),
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
        _raise_db(e, request_id)
    return {"rfq_id": str(rfq_id), "request_id": request_id, "actor": actor}


@app.get("/enq/rfq/{rfq_id}")
def get_rfq(rfq_id: str, actor: str = Depends(require_actor)):   # B1: GET ก็ต้อง auth
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
