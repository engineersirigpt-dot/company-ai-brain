"""
Automated transport tests — FastAPI TestClient + live PostgreSQL (Codex H5 + re-review 8EB6C41)
รันผ่าน enq_api/run_api_tests.sh (สร้าง ephemeral PG + login roles + set env)
ครอบ: auth ทุก route + startup fail-closed + no dev-bypass (B1),
       operation-aware SQLSTATE mapping ไม่รั่ว (B2), middleware disconnect/multi-frame (M1),
       idempotency required/replay/conflict, oversize, schema strict, login least-privilege (M2)
"""
import os, sys, json, subprocess, asyncio

# ต้องตั้ง env ก่อน import app (main อ่าน config ตอน import + fail-closed)
os.environ.setdefault("ENQ_API_KEY", "test-key")
os.environ.pop("ENQ_DEV_MODE", None)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import psycopg2
from fastapi.testclient import TestClient
from enq_api import main as m

client = TestClient(m.app)
KEY = {"X-API-Key": "test-key"}
DRAFT = {"schema_version": "draft-v1", "header": {"enquiry_ref": "T"},
         "items": [{"line_no": 1, "job_name": "box",
                    "quantity_options": [{"option_no": 1, "quantity": 5000, "unit_ref": "PCS", "is_primary": True}]}]}
res = []
def check(name, cond, detail=""):
    res.append(cond); print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(detail)[:300]))

# happy
r = client.post("/enq/draft", json=DRAFT, headers={**KEY, "X-Request-Id": "t-happy"})
check("happy → 200 + rfq_id", r.status_code == 200 and "rfq_id" in r.json(), r.text)
rid = r.json().get("rfq_id")

# B1 auth ทุก route (รวม GET) + wrong key
check("POST no-auth → 401", client.post("/enq/draft", json=DRAFT, headers={"X-Request-Id": "z"}).status_code == 401)
check("POST wrong key → 401", client.post("/enq/draft", json=DRAFT, headers={"X-API-Key": "nope", "X-Request-Id": "z"}).status_code == 401)
check("GET no-auth → 401", client.get(f"/enq/rfq/{rid}").status_code == 401)
check("GET with auth → 200", client.get(f"/enq/rfq/{rid}", headers=KEY).status_code == 200)
check("no dev bypass global (DEV_MODE removed)", getattr(m, "DEV_MODE", None) is None)

# H1 idempotency
check("POST no X-Request-Id → 400", client.post("/enq/draft", json=DRAFT, headers=KEY).status_code == 400)
r2 = client.post("/enq/draft", json=DRAFT, headers={**KEY, "X-Request-Id": "t-happy"})
check("replay same key+payload → same rfq_id", r2.status_code == 200 and r2.json().get("rfq_id") == rid, r2.text)
rc = client.post("/enq/draft", json={**DRAFT, "header": {"enquiry_ref": "CHANGED"}}, headers={**KEY, "X-Request-Id": "t-happy"})
check("same key + diff payload → 409", rc.status_code == 409, rc.text)

# H3 schema strictness
check("missing schema_version → 422",
      client.post("/enq/draft", json={"items": [{"line_no": 1}]}, headers={**KEY, "X-Request-Id": "t-nv"}).status_code == 422)
check("empty items → 422",
      client.post("/enq/draft", json={"schema_version": "draft-v1", "items": []}, headers={**KEY, "X-Request-Id": "t-e"}).status_code == 422)

# H4/B2 unknown key → 422 + stable code (ไม่รั่ว DB message)
ru = client.post("/enq/draft", json={"schema_version": "draft-v1", "items": [{"line_no": 1, "EVIL": 1}]},
                 headers={**KEY, "X-Request-Id": "t-unk"})
check("unknown key → 422", ru.status_code == 422)
check("error ไม่รั่ว DB message", "allowlist" not in ru.text and ru.json().get("detail") == "invalid_payload", ru.text)

# B2 operation-aware SQLSTATE — invalid typed/check/range payload → 422 (ไม่ใช่ 500)
def post_item(item, key):
    return client.post("/enq/draft", json={"schema_version": "draft-v1", "items": [item]},
                       headers={**KEY, "X-Request-Id": key})
base = {"job_name": "b", "quantity_options": [{"option_no": 1, "quantity": 5000, "unit_ref": "PCS", "is_primary": True}]}
check("line_no non-int (22P02) → 422", post_item({**base, "line_no": "abc"}, "t-22P02").status_code == 422)
check("line_no 0 check (23514) → 422", post_item({**base, "line_no": 0}, "t-23514").status_code == 422)
check("line_no overflow (22003) → 422", post_item({**base, "line_no": 999999}, "t-22003").status_code == 422)
check("bad date (22007/22023) → 422",
      post_item({**base, "line_no": 1, "deliveries": [{"delivery_no": 1, "destination_raw": "x", "requested_date": "not-a-date"}]}, "t-22007").status_code == 422)

# B2 oversize
big = {"schema_version": "draft-v1", "items": [{"line_no": 1, "job_name": "x" * 1_100_000}]}
check("oversize body → 413", client.post("/enq/draft", json=big, headers={**KEY, "X-Request-Id": "t-big"}).status_code == 413)

# ---- M1: middleware disconnect/multi-frame (direct ASGI probe) ----
def asgi_probe(messages, max_bytes=1_000_000):
    state = {"called": False, "body": b"", "status": None}
    async def run():
        q = list(messages)
        async def receive():
            return q.pop(0) if q else {"type": "http.disconnect"}
        async def send(msg):
            if msg["type"] == "http.response.start":
                state["status"] = msg["status"]
        async def downstream(sc, rc, sn):
            state["called"] = True
            while True:
                mm = await rc()
                if mm["type"] == "http.request":
                    state["body"] += mm.get("body", b"")
                    if not mm.get("more_body"):
                        break
                else:
                    break
            await sn({"type": "http.response.start", "status": 200, "headers": []})
            await sn({"type": "http.response.body", "body": b"ok"})
        mw = m.LimitBodyMiddleware(downstream, max_bytes)
        await mw({"type": "http", "method": "POST", "headers": [], "path": "/enq/draft"}, receive, send)
    asyncio.run(run())
    return state

d = asgi_probe([{"type": "http.disconnect"}])
check("M1 disconnect-first → downstream NOT called", d["called"] is False and d["status"] is None, str(d))
mf = asgi_probe([{"type": "http.request", "body": b"ab", "more_body": True},
                 {"type": "http.request", "body": b"cd", "more_body": False}])
check("multi-frame ≤ cap → downstream gets full body", mf["called"] and mf["body"] == b"abcd", str(mf))
mo = asgi_probe([{"type": "http.request", "body": b"aaa", "more_body": True},
                 {"type": "http.request", "body": b"bbb", "more_body": False}], max_bytes=5)
check("multi-frame > cap → 413, downstream NOT called", mo["status"] == 413 and mo["called"] is False, str(mo))

# ---- B3/M2: login roles least privilege (connect ตรงด้วย login role) ----
WDSN, RDSN = os.environ.get("RFQ_WRITE_DSN"), os.environ.get("RFQ_READ_DSN")
def attempt(dsn, sql):
    try:
        c = psycopg2.connect(dsn); c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SET search_path TO rfq"); cur.execute(sql)
        c.close(); return True, None
    except Exception as e:
        return False, getattr(e, "pgcode", None)
def is_super(dsn):
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"); v = cur.fetchone()[0]
    c.close(); return v
DRAFT_CALL = ("SELECT create_rfq_draft('{\"schema_version\":\"draft-v1\",\"items\":"
              "[{\"line_no\":1,\"job_name\":\"c\",\"quantity_options\":[{\"option_no\":1,"
              "\"quantity\":5000,\"unit_ref\":\"PCS\",\"is_primary\":true}]}]}'::jsonb, %s, 'enq', %s)")
if WDSN and RDSN:
    check("write login non-superuser", is_super(WDSN) is False)
    check("read login non-superuser", is_super(RDSN) is False)
    ok, code = attempt(WDSN, "SELECT * FROM rfq LIMIT 1")
    check("write login direct SELECT denied (42501)", ok is False and code == "42501", code)
    ok, code = attempt(WDSN, "INSERT INTO rfq(rfq_no) VALUES ('x')")
    check("write login direct INSERT denied (42501)", ok is False and code == "42501", code)
    ok, code = attempt(WDSN, DRAFT_CALL.replace("%s, 'enq', %s", "'cap-w', 'enq', 'cap-w-1'"))
    check("write login CAN call create_rfq_draft", ok is True, code)
    ok, code = attempt(RDSN, "SELECT id FROM rfq LIMIT 1")
    check("read login CAN direct SELECT", ok is True, code)
    ok, code = attempt(RDSN, "INSERT INTO rfq(rfq_no) VALUES ('x')")
    check("read login direct INSERT denied (42501)", ok is False and code == "42501", code)
    ok, code = attempt(RDSN, DRAFT_CALL.replace("%s, 'enq', %s", "'cap-r', 'enq', 'cap-r-1'"))
    check("read login CANNOT call create_rfq_draft (42501)", ok is False and code == "42501", code)
else:
    print("SKIP login least-privilege (no RFQ_WRITE_DSN/RFQ_READ_DSN in env)")

# B3 read session via /health
h = client.get("/health").json()
check("read role ไม่ใช่ superuser (/health)", h.get("read_role_superuser") is False, str(h))

# B1 startup fail-closed: no key → error ; และ ENQ_DEV_MODE=1 ไม่ bypass อีกต่อไป
def import_with(env_over):
    env = {k: v for k, v in os.environ.items() if k not in ("ENQ_API_KEY", "ENQ_DEV_MODE")}
    env["PYTHONIOENCODING"] = "utf-8"; env.update(env_over)
    cp = subprocess.run([sys.executable, "-c", "import enq_api.main"], cwd=REPO, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    return cp.returncode, (cp.stderr or "") + (cp.stdout or "")
rccode, err = import_with({})
check("startup fail-closed (no key) → error", rccode != 0 and "fail-closed" in err, err[-200:])
rccode, err = import_with({"ENQ_DEV_MODE": "1"})
check("ENQ_DEV_MODE=1 without key → still fail-closed (no bypass)", rccode != 0 and "fail-closed" in err, err[-200:])

nfail = res.count(False)
print(f"===== API TEST: {res.count(True)} passed, {nfail} failed =====")
sys.exit(1 if nfail else 0)
