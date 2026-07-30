"""
ENQ API (v1.1 transport) — FastAPI หน้าร้านของ RFQ backend
==========================================================
ทำให้ create_rfq_draft (migration 006) เรียกผ่าน HTTP ได้ — synthetic/manual draft path
เป็นโมดูลแยก รันในเครื่อง dev เท่านั้น (ไม่ใช่ app/main.py ที่ deploy) — ยังไม่ deploy

ยึด Codex FastAPI guardrails:
  #1 write ผ่าน DB role rfq_ingest (read ผ่าน rfq_app) — บังคับ boundary แม้ผ่าน API
  #2 actor มาจาก authenticated server context (ไม่ใช่ request body)
  #3 hard-code service = 'enq' ฝั่ง server
  #5 validate raw-body size / structure ก่อนเรียก DB (DB = strict allowlist ชั้นสุดท้าย)
  #9 request_id deterministic (จาก header X-Request-Id หรือ generate) → idempotent retry

ยังไม่ทำ (increment ถัดไป): begin/claim/apply/fail (AI extraction path) — ต้อง seed trusted tables + mock provider
"""
from __future__ import annotations
import os, json, uuid
from typing import Any
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

# ---- config (env; default = dev DB rfq_dev บน localhost:5433) ----
RFQ_DSN = os.environ.get("RFQ_DSN", "host=localhost port=5433 dbname=rfqtest user=postgres password=test")
ENQ_API_KEY = os.environ.get("ENQ_API_KEY")          # ตั้ง = เปิด auth จริง; ไม่ตั้ง = dev mode
MAX_BODY_BYTES = 1_000_000                            # #5 raw-body cap (สอดคล้อง DB 1MB)
SERVICE = "enq"                                       # #3 hard-coded service identity

app = FastAPI(title="ENQ API (RFQ draft)", version="0.1.0")


# ---- auth: X-API-Key → server-controlled actor (#2) ----
def resolve_actor(x_api_key: str | None) -> str:
    if ENQ_API_KEY is None:
        return "enq-api-dev"                          # dev mode: no key configured
    if x_api_key != ENQ_API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    return "enq-api"                                  # prod: derive จาก key/JWT (ยังไม่ map user)


# ---- DB helpers: SET ROLE per op (บังคับ role boundary) ----
@contextmanager
def db_role(role: str):
    conn = psycopg2.connect(RFQ_DSN)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET search_path TO rfq")
            cur.execute(f"SET ROLE {role}")           # role = literal คงที่ (rfq_ingest|rfq_app) ไม่ใช่ user input
        yield conn
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


# ---- payload model (#5 structural guard; DB = strict allowlist ชั้นสุดท้าย) ----
class DraftPayload(BaseModel):
    model_config = {"extra": "forbid"}                # reject unknown top-level key (ตรงกับ DB top allowlist)
    schema_version: str = "draft-v1"
    header: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)


@app.get("/health")
def health():
    try:
        with db_role("rfq_app") as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "db": "up", "service": SERVICE}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db down: {e.__class__.__name__}")


@app.post("/enq/draft")
async def create_draft(
    payload: DraftPayload,
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    actor = resolve_actor(x_api_key)
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    if payload.schema_version != "draft-v1":
        raise HTTPException(status_code=422, detail="schema_version must be draft-v1")
    request_id = (x_request_id or "").strip() or f"api-{uuid.uuid4().hex}"   # #9 idempotency key

    try:
        with db_role("rfq_ingest") as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT create_rfq_draft(%s::jsonb, %s, %s, %s)",
                (json.dumps(payload.model_dump(), ensure_ascii=False), actor, SERVICE, request_id),
            )
            rfq_id = cur.fetchone()[0]
    except psycopg2.Error as e:
        # DB validation (unknown key / limit / bad ref ...) → 422; อื่น ๆ → 500
        code = getattr(e, "pgcode", None)
        detail = (e.diag.message_primary if e.diag else str(e)) or "db error"
        raise HTTPException(status_code=422 if code in ("22023", "54000", "23503", "23505") else 500,
                            detail=f"[{code}] {detail}")
    return {"rfq_id": str(rfq_id), "request_id": request_id, "actor": actor}


@app.get("/enq/rfq/{rfq_id}")
def get_rfq(rfq_id: str):
    try:
        uuid.UUID(rfq_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="rfq_id must be a uuid")
    with db_role("rfq_app") as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
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
        return json.loads(json.dumps(rfq, default=str))   # serialize uuid/date/numeric
