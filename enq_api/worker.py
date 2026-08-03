"""
ENQ extraction worker — durable poller (Codex-approved orchestration flow)
==========================================================================
process แยกจาก FastAPI (ไม่ใช่ BackgroundTasks) — งาน durable อยู่ใน run table เอง

flow ต่อ run (Codex):
  list_claimable_extractions  → run ที่ PENDING หรือ RUNNING lease หมด
  claim_rfq_extraction        → short txn, commit (จุดอนุมัติ + lease)
  provider.extract            → **นอก DB transaction** เท่านั้น, timeout < lease
  apply / fail_rfq_extraction → transaction ใหม่

boundary:
  - worker ใช้ role แยก = rfq_worker_login (M2: list_claimable/claim/apply/fail เท่านั้น ; ไม่มี draft/begin)
    poll ผ่าน list_claimable (definer, คืนแค่ run_id)
  - actor/service/lease/ref/hash เป็น server/DB-controlled — worker ไม่แต่ง
  - apply/fail request_id = stable ต่อ (run, lease) → retry idempotent
  - lease หมดระหว่าง provider → apply ถูก fence (RFS01) → worker ทิ้ง งานถูก reclaim
local + synthetic เท่านั้น — ยังไม่ deploy, ไม่เรียก cloud, ไม่ใช้ข้อมูลจริง
"""
from __future__ import annotations
import os, json, time, uuid, logging
from datetime import datetime, timezone

import psycopg2

from enq_api import provider, caps

log = logging.getLogger("enq_worker")

SERVICE           = "enq"                                       # server-controlled
# M2: worker ใช้ role แยก (rfq_worker) = list_claimable/claim/apply/fail เท่านั้น — ไม่ใช่ inbound rfq_ingest
_DEV_DSN_WK = "host=localhost port=5433 dbname=rfqtest user=rfq_worker_login password=worker connect_timeout=5"
WORKER_DSN        = os.environ.get("RFQ_WORKER_DSN", _DEV_DSN_WK)
POLL_LIMIT        = int(os.environ.get("ENQ_WORKER_POLL_LIMIT", "50"))
POLL_INTERVAL_S   = float(os.environ.get("ENQ_WORKER_INTERVAL", "2.0"))
PROVIDER_MARGIN_S = float(os.environ.get("ENQ_PROVIDER_MARGIN_S", "30"))   # provider timeout = lease_remaining - margin
APPLY_RETRIES     = int(os.environ.get("ENQ_APPLY_RETRIES", "5"))          # M3: durable apply/fail retry บน transient
APPLY_BACKOFF_S   = float(os.environ.get("ENQ_APPLY_BACKOFF_S", "0.2"))


def _conn(dsn: str):
    c = psycopg2.connect(dsn); c.autocommit = False
    with c.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("SET statement_timeout='10s'")
        cur.execute("SET lock_timeout='5s'")
        cur.execute("SET idle_in_transaction_session_timeout='15s'")
    return c


def _call_json(dsn: str, sql: str, params) -> dict | None:
    """short txn: เรียก function ที่คืน jsonb → commit → close (ไม่ถือ conn ข้าม provider)"""
    c = _conn(dsn)
    try:
        with c.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        c.commit()
        return row[0] if row and row[0] is not None else None
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def claimable(dsn: str = WORKER_DSN, limit: int = POLL_LIMIT) -> list:
    c = _conn(dsn)
    try:
        with c.cursor() as cur:
            cur.execute("SELECT run_id FROM list_claimable_extractions(%s)", (limit,))
            ids = [r[0] for r in cur.fetchall()]
        c.commit()
        return ids
    finally:
        c.close()


def _lease_budget(lease_exp_iso: str | None) -> float | None:
    """provider timeout budget = (lease_expires_at - now) - margin ; None ถ้าไม่มี lease info"""
    if not lease_exp_iso:
        return None
    try:
        remaining = (datetime.fromisoformat(lease_exp_iso) - datetime.now(timezone.utc)).total_seconds()
    except ValueError:
        return None
    return remaining - PROVIDER_MARGIN_S


def process_run(run_id, worker_id: str, dsn: str = WORKER_DSN) -> dict:
    """claim → provider (นอก txn) → apply/fail ; คืน report ไม่ raise ในกรณีปกติ"""
    run_id = str(run_id)                                       # psycopg2 mogrify string literal → uuid cast
    claim_req = "claim:" + uuid.uuid4().hex                     # claim = per-attempt (reclaim = attempt ใหม่)
    outcome = _call_json(dsn, "SELECT claim_rfq_extraction(%s,%s,%s,%s)",
                         (run_id, worker_id, SERVICE, claim_req))
    if not outcome or not outcome.get("should_execute"):
        return {"run_id": str(run_id), "action": "skipped",
                "status": (outcome or {}).get("status"), "reason": (outcome or {}).get("reason")}

    lease = outcome["lease_token"]; sha = outcome["input_sha256"]
    target = outcome["execution_target"]; ref = outcome.get("provider_input_ref")
    budget = _lease_budget(outcome.get("lease_expires_at"))
    if budget is not None and budget <= 0:                      # lease จะหมดก่อน provider เสร็จ → ปล่อย reclaim
        return {"run_id": str(run_id), "action": "lease_too_short", "budget_s": budget}

    apply_req = f"apply:{run_id}:{lease}"                       # stable ต่อ lease → retry idempotent
    fail_req  = f"fail:{run_id}:{lease}"

    # ---- provider call: นอก DB transaction เท่านั้น ----
    try:
        result = provider.extract(input_ref=ref, input_sha256=sha, execution_target=target,
                                  timeout_s=budget, idempotency_key=f"{run_id}:{sha}")   # M3: stable ต่อ attempt
    except provider.ProviderTransient as e:
        # M3: ชั่วคราว — ไม่ fail run (ไม่ burn) ; ปล่อย RUNNING → lease หมด → worker ใหม่ reclaim
        return {"run_id": str(run_id), "action": "provider_transient", "reason": str(e)}
    except provider.ProviderError as e:
        return _terminal(dsn, run_id, "fail",
                         "SELECT fail_rfq_extraction(%s,%s,%s,%s,%s,%s)",
                         (run_id, lease, "PROVIDER_ERROR", worker_id, SERVICE, fail_req), reason=str(e))

    return _terminal(dsn, run_id, "apply",
                     "SELECT apply_rfq_extraction(%s,%s,%s::jsonb,%s,%s,%s)",
                     (run_id, lease, json.dumps(result, ensure_ascii=False), worker_id, SERVICE, apply_req))


# M3: pgcode ที่ retry ได้ (transient) — serialize/deadlock/lock/canceled/insufficient-resource/connection class 08/57
_TRANSIENT_PG = {"40001", "40P01", "55P03", "57014", "53300", "53400",
                 "08000", "08003", "08006", "08001", "08004", "57P01", "57P02", "57P03"}

def _terminal(dsn, run_id, kind, sql, params, reason=None) -> dict:
    """เรียก apply/fail แบบ durable (M3): retry บน transient error ด้วย **request_id เดิม**
    → 'provider สำเร็จ + apply connection ขาด' จะ retry แล้ว ledger คืน SUCCEEDED เดิม (idempotent, ไม่เรียก provider ซ้ำ)
    business reject (RFS01 fenced ฯลฯ) = terminal → รายงานทันที ไม่ retry"""
    last = None
    for attempt in range(APPLY_RETRIES):
        try:
            r = _call_json(dsn, sql, params) or {}
            return {"run_id": str(run_id), "action": kind, "status": r.get("status"),
                    "rfq_id": r.get("rfq_id"), "reason": reason, "attempts": attempt + 1}
        except psycopg2.Error as e:
            code = getattr(e, "pgcode", None)
            if not (isinstance(e, psycopg2.OperationalError) or code in _TRANSIENT_PG):
                return {"run_id": str(run_id), "action": kind + "_rejected", "pgcode": code}   # terminal reject
            last = e
            log.warning("%s transient (%s) attempt %d/%d — retry (idempotent)", kind, code, attempt + 1, APPLY_RETRIES)
            time.sleep(APPLY_BACKOFF_S * (attempt + 1))
    # หมด retry — run อาจยัง RUNNING ; ปล่อย lease หมด → reclaim (ไม่ burn)
    return {"run_id": str(run_id), "action": kind + "_retry_exhausted", "pgcode": getattr(last, "pgcode", None)}


def poll_once(worker_id: str, dsn: str = WORKER_DSN, limit: int = POLL_LIMIT) -> list:
    results = []
    for run_id in claimable(dsn, limit):
        try:
            results.append(process_run(run_id, worker_id, dsn))
        except Exception as e:                                  # DB down กลางทาง ฯลฯ — ข้ามงานนี้ไป
            log.warning("process_run %s error: %s", run_id, type(e).__name__)
            results.append({"run_id": str(run_id), "action": "error", "error": type(e).__name__})
    return results


def assert_worker_role(dsn: str = WORKER_DSN):
    """B3/F1/F3–F7: fail-closed ถ้า WORKER_DSN ชี้ role ผิด surface — canonical effective check เดียวกับ main.py"""
    caps.assert_role(dsn, "RFQ_WORKER_DSN (worker เช่น rfq_worker)", "worker")


def main():
    worker_id = os.environ.get("ENQ_WORKER_ID", "enq-worker-1")
    assert_worker_role()                                        # B3: ยืนยัน role ก่อนเริ่ม poll
    log.info("enq worker '%s' start — poll every %.1fs (local/synthetic, no cloud)", worker_id, POLL_INTERVAL_S)
    while True:
        try:
            res = poll_once(worker_id)
            if res:
                log.info("processed %d: %s", len(res), [r.get("action") for r in res])
        except Exception as e:
            log.warning("poll error: %s", type(e).__name__)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
