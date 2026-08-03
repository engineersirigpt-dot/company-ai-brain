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
import os, json, time, uuid, logging, math
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


def _validate_config():
    """fail-fast config (Codex backlog (b) + F1) — ค่าที่ผิดทำ retry loop/sleep/lease budget พังแบบเงียบ
    - ENQ_APPLY_RETRIES < 1 → `for attempt in range(0)` ข้าม loop → `raise last` ที่ last=None → raise None
    - F1: NaN/±Inf ผ่าน comparison `< 0` (nan<0 และ inf<0 = False) → time.sleep(NaN)→ValueError / sleep(Inf)→OverflowError /
      margin NaN → lease budget=NaN bypass `budget<=0` / margin +Inf → ทุกงาน lease_too_short วน reclaim → ต้อง finite ก่อน"""
    problems = []
    for name, val in (("ENQ_APPLY_BACKOFF_S", APPLY_BACKOFF_S), ("ENQ_WORKER_INTERVAL", POLL_INTERVAL_S),
                      ("ENQ_PROVIDER_MARGIN_S", PROVIDER_MARGIN_S)):
        if not math.isfinite(val):    problems.append("%s ต้องเป็น finite (ตอนนี้ %r → sleep/lease budget พัง)" % (name, val))
    if APPLY_RETRIES < 1:     problems.append("ENQ_APPLY_RETRIES ต้อง >= 1 (ตอนนี้ %r → retry loop ไม่รัน → raise None)" % APPLY_RETRIES)
    if APPLY_BACKOFF_S < 0:   problems.append("ENQ_APPLY_BACKOFF_S ต้อง >= 0 (ตอนนี้ %r → time.sleep ค่าติดลบ)" % APPLY_BACKOFF_S)
    if POLL_LIMIT < 1:        problems.append("ENQ_WORKER_POLL_LIMIT ต้อง >= 1 (ตอนนี้ %r → ไม่ claim งานเลย)" % POLL_LIMIT)
    if POLL_INTERVAL_S < 0:   problems.append("ENQ_WORKER_INTERVAL ต้อง >= 0 (ตอนนี้ %r)" % POLL_INTERVAL_S)
    if PROVIDER_MARGIN_S < 0: problems.append("ENQ_PROVIDER_MARGIN_S ต้อง >= 0 (ตอนนี้ %r)" % PROVIDER_MARGIN_S)
    if problems:
        raise ValueError("worker config ไม่ถูกต้อง: " + " ; ".join(problems))


_validate_config()                                             # import-time fail-fast (default ทุกค่า valid — ไม่กระทบ test)


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
                                  timeout_s=budget, idempotency_key=f"{run_id}:{sha}")   # M3: stable ข้าม claim/reclaim ของ run
    except provider.ProviderTransient as e:
        # M3: ชั่วคราว — ไม่ fail run (ไม่ burn) ; ปล่อย RUNNING → lease หมด → worker ใหม่ reclaim
        return {"run_id": str(run_id), "action": "provider_transient", "reason": str(e)}
    except provider.ProviderError as e:
        return _do_fail(dsn, run_id, lease, "PROVIDER_ERROR", worker_id, fail_req, reason=str(e))
    return _do_apply(dsn, run_id, lease, result, worker_id, apply_req, fail_req)


# M3: transient (retry ได้ ด้วย request_id เดิม) — serialize/deadlock/lock/canceled/insufficient-resource/connection 08/57
_TRANSIENT_PG = {"40001", "40P01", "55P03", "57014", "53300", "53400",
                 "08000", "08003", "08006", "08001", "08004", "57P01", "57P02", "57P03"}
# M3-F1: provider คืน payload แต่ DB validator reject = invalid provider result → escalate เป็น FAILED (ไม่ค้าง RUNNING)
# B1: 54000 (ProgramLimitExceeded) สืบทอด OperationalError → ต้อง classify เป็น invalid ไม่ใช่ transient
_INVALID_RESULT_PG = {"RFR01", "23502", "23503", "23505", "23514", "54000"}   # + class '22'

def _is_invalid_result(code) -> bool:
    return code in _INVALID_RESULT_PG or (code is not None and code[:2] == "22")

def _classify(e) -> str:
    """operation-aware classifier เดียว (ใช้ทั้ง retry + final dispatch) — คืน INVALID_RESULT|FENCED|NOT_FOUND|TRANSIENT|OPERATIONAL
    B1: invalid-result ต้องมา **ก่อน** generic OperationalError — 54000 สืบทอด OperationalError แต่เป็น payload ผิด contract
        → terminal FAILED ไม่ใช่ retry/reclaim ; precedence = INVALID_RESULT > FENCED > NOT_FOUND > TRANSIENT > OPERATIONAL"""
    code = getattr(e, "pgcode", None)
    if _is_invalid_result(code):     # 220xx/RFR01/23502/23503/23505/23514/54000 = payload ผิด contract → terminal
        return "INVALID_RESULT"
    if code == "RFS01":              # lease หมด/ถูก fence → ทิ้งผล lease เก่า (ห้าม fail)
        return "FENCED"
    if code == "RFN01":              # run หาย → alert (state lost)
        return "NOT_FOUND"
    if isinstance(e, psycopg2.OperationalError) or code in _TRANSIENT_PG:
        return "TRANSIENT"           # connection/serialize/deadlock/lock/canceled → retry (idempotent replay)
    return "OPERATIONAL"             # RFI01/permission/programming → operational alert (ไม่ใช่ provider transient)

def _call_retry(dsn, sql, params):
    """transient-retry core (request_id เดิม = idempotent replay) ; raise psycopg2.Error ถ้า terminal/invalid หรือ retry หมด
    B1: retry **เฉพาะ** TRANSIENT — invalid-result (รวม 54000) raise กลับทันที ไม่ retry"""
    last = None
    for attempt in range(APPLY_RETRIES):
        try:
            return (_call_json(dsn, sql, params) or {}), attempt + 1
        except psycopg2.Error as e:
            if _classify(e) != "TRANSIENT":
                raise
            last = e
            log.warning("transient (%s) attempt %d/%d — retry (idempotent)", getattr(e, "pgcode", None), attempt + 1, APPLY_RETRIES)
            time.sleep(APPLY_BACKOFF_S * (attempt + 1))
    raise last                                                 # retry หมด (ยัง transient) → propagate

def _do_fail(dsn, run_id, lease, error_code, worker_id, fail_req, reason=None) -> dict:
    """fail_rfq_extraction แบบ durable retry (stable fail_req) → run FAILED (terminal)"""
    try:
        r, att = _call_retry(dsn, "SELECT fail_rfq_extraction(%s,%s,%s,%s,%s,%s)",
                             (run_id, lease, error_code, worker_id, SERVICE, fail_req))
        return {"run_id": str(run_id), "action": "fail", "status": r.get("status"), "reason": reason, "attempts": att}
    except psycopg2.Error as e:
        kind = "fail_retry_exhausted" if _classify(e) == "TRANSIENT" else "fail_rejected"   # transient หมด → RUNNING/reclaim
        return {"run_id": str(run_id), "action": kind, "pgcode": getattr(e, "pgcode", None)}

def _do_apply(dsn, run_id, lease, result, worker_id, apply_req, fail_req) -> dict:
    """apply_rfq_extraction แบบ durable retry ; invalid provider result → escalate เป็น FAILED (M3-F1 + B1 54000 + B2 serialize)"""
    # B2: serialize **ก่อน** ถึง DB — provider result ที่ไม่ JSON-safe (Decimal/bytes/NaN/Infinity/circular/lone-surrogate)
    #     = invalid provider result → FAILED (ไม่หลุดเป็น unhandled exception → run ไม่ค้าง RUNNING) ; ห้าม log payload ดิบ
    #     ensure_ascii=True → non-BMP/lone-surrogate ถูก escape แล้วให้ DB jsonb reject (class 22) ; allow_nan=False → reject NaN/Inf
    try:
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as e:       # ValueError ครอบ UnicodeError/allow_nan ด้วย
        return _do_fail(dsn, run_id, lease, "INVALID_EXTRACTION_RESULT", worker_id, fail_req,
                        reason="provider result not JSON-serializable: " + type(e).__name__)
    try:
        r, att = _call_retry(dsn, "SELECT apply_rfq_extraction(%s,%s,%s::jsonb,%s,%s,%s)",
                             (run_id, lease, encoded, worker_id, SERVICE, apply_req))
        return {"run_id": str(run_id), "action": "apply", "status": r.get("status"), "rfq_id": r.get("rfq_id"), "attempts": att}
    except psycopg2.Error as e:
        cat = _classify(e); code = getattr(e, "pgcode", None)
        if cat == "INVALID_RESULT":                            # M3-F1/B1: payload ผิด contract (รวม 54000) → fail run (terminal)
            return _do_fail(dsn, run_id, lease, "INVALID_EXTRACTION_RESULT", worker_id, fail_req, reason="invalid provider result " + str(code))
        if cat == "TRANSIENT":                                 # transient หมด — run ยัง RUNNING → reclaim (ไม่ burn)
            return {"run_id": str(run_id), "action": "apply_retry_exhausted", "pgcode": code}
        if cat == "FENCED":                                    # lease หมด/ถูก fence — ทิ้งผล ปล่อย owner ใหม่
            return {"run_id": str(run_id), "action": "apply_fenced", "pgcode": code}
        if cat == "NOT_FOUND":                                 # run หาย — alert (state lost)
            return {"run_id": str(run_id), "action": "apply_run_not_found", "pgcode": code}
        return {"run_id": str(run_id), "action": "apply_rejected", "pgcode": code}   # OPERATIONAL: RFI01/permission


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
