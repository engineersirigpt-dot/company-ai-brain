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

# F1 mapper taxonomy — operation-aware, safe SQLSTATE (extraction state-conflict ≠ invalid-result ≠ not-found)
mp = m._http_for_pg
check("map RFS01 → 409 state_conflict", mp("apply", "RFS01") == (409, "state_conflict"))
check("map RFN01 → 404 run_not_found", mp("claim", "RFN01") == (404, "run_not_found"))
check("map RFR01 → 422 invalid_extraction_result", mp("apply", "RFR01") == (422, "invalid_extraction_result"))
check("map RFI01 → 409 idempotency_conflict", mp("apply", "RFI01") == (409, "idempotency_conflict"))
check("map draft 23505 (dup key) → 422 invalid_payload", mp("draft", "23505") == (422, "invalid_payload"))
check("map apply 23505 (dup key) → 422 invalid_extraction_result", mp("apply", "23505") == (422, "invalid_extraction_result"))
check("map begin 23505 (unexpected) → 500 internal_error", mp("begin", "23505") == (500, "internal_error"))
check("F1.1 RFI01(409) ≠ 23505 dup-key(422)", mp("apply", "RFI01")[0] == 409 and mp("apply", "23505")[0] == 422)
check("map begin 23503 → 422 invalid_request", mp("begin", "23503") == (422, "invalid_request"))
check("map draft 22023 → 422 invalid_payload", mp("draft", "22023") == (422, "invalid_payload"))
check("map apply 22023 → 422 invalid_extraction_result", mp("apply", "22023") == (422, "invalid_extraction_result"))
check("map 54000 → 413 payload_too_large", mp("apply", "54000") == (413, "payload_too_large"))
check("map 57014 → 503 (transient)", mp("apply", "57014") == (503, "database_unavailable"))
check("map 55P03 → 503 (transient class-55)", mp("apply", "55P03") == (503, "database_unavailable"))
check("map unknown → 500", mp("apply", "XX000") == (500, "internal_error"))
check("F1 apply RFS01≠RFR01 (409 state vs 422 result)", mp("apply", "RFS01")[0] == 409 and mp("apply", "RFR01")[0] == 422)

# H1 non-ASCII API key → 401 (ไม่ใช่ 500/traceback)
try:
    m.require_actor("caf\xe9"); _h1 = False
except m.HTTPException as _e:
    _h1 = (_e.status_code == 401)
except Exception:
    _h1 = False
check("H1 non-ASCII key (dependency) → 401 not 500", _h1)

# raw ASGI probe — ส่ง header byte 0xE9 ตรงเข้า app (httpx client-side encode ASCII เท่านั้น จึงส่งไม่ได้)
def asgi_status(method, path, headers):
    st = {"code": None}
    async def run():
        started = {"done": False}
        async def receive():
            if not started["done"]:
                started["done"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}
        async def send(msg):
            if msg["type"] == "http.response.start":
                st["code"] = msg["status"]
        scope = {"type": "http", "http_version": "1.1", "method": method, "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"", "headers": headers,
                 "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 5555)}
        await m.app(scope, receive, send)
    asyncio.run(run())
    return st["code"]
na = asgi_status("GET", "/enq/rfq/00000000-0000-0000-0000-000000000000", [(b"x-api-key", b"caf\xe9")])
check("H1 non-ASCII key (raw ASGI) → 401 not 500", na == 401, na)

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
    check("inbound login CAN call create_rfq_draft", ok is True, code)
    # M2: inbound (rfq_ingest) เรียก worker fn (claim/apply/fail) ไม่ได้
    ok, code = attempt(WDSN, "SELECT claim_rfq_extraction('00000000-0000-0000-0000-000000000000'::uuid,'w','enq','cap-w-claim')")
    check("M2 inbound login CANNOT claim (42501)", ok is False and code == "42501", code)
    ok, code = attempt(WDSN, "SELECT list_claimable_extractions(1)")
    check("M2 inbound login CANNOT list_claimable (42501)", ok is False and code == "42501", code)
    # read (rfq_read_api): business SELECT ได้ แต่ sensitive tables + create ไม่ได้
    ok, code = attempt(RDSN, "SELECT id FROM rfq LIMIT 1")
    check("read login CAN direct SELECT rfq (business)", ok is True, code)
    ok, code = attempt(RDSN, "INSERT INTO rfq(rfq_no) VALUES ('x')")
    check("read login direct INSERT denied (42501)", ok is False and code == "42501", code)
    ok, code = attempt(RDSN, DRAFT_CALL.replace("%s, 'enq', %s", "'cap-r', 'enq', 'cap-r-1'"))
    check("read login CANNOT call create_rfq_draft (42501)", ok is False and code == "42501", code)
    # M1: read login อ่าน sensitive tables ตรง ๆ ไม่ได้ (safe projection ไม่ถูก bypass ด้วย DB privilege)
    ok, code = attempt(RDSN, "SELECT * FROM rfq_ai_extraction_run LIMIT 1")
    check("M1 read login CANNOT SELECT rfq_ai_extraction_run (42501)", ok is False and code == "42501", code)
    ok, code = attempt(RDSN, "SELECT * FROM rfq_attachment LIMIT 1")
    check("M1 read login CANNOT SELECT rfq_attachment (42501)", ok is False and code == "42501", code)
    # B1: PII/notes columns ที่ endpoint ไม่คืน ต้อง denied (column-level grant, ไม่ใช่ table-level)
    ok, code = attempt(RDSN, "SELECT customer_notes FROM rfq LIMIT 1")
    check("B1 read login CANNOT SELECT PII customer_notes (42501)", ok is False and code == "42501", code)
    ok, code = attempt(RDSN, "SELECT contact_phone FROM rfq LIMIT 1")
    check("B1 read login CANNOT SELECT PII contact_phone (42501)", ok is False and code == "42501", code)
    ok, code = attempt(RDSN, "SELECT notes FROM rfq_item LIMIT 1")
    check("B1 read login CANNOT SELECT rfq_item.notes (42501)", ok is False and code == "42501", code)
    # B2: ledger outcome (lease/ref/hash) ต้อง denied
    ok, code = attempt(RDSN, "SELECT outcome FROM rfq_extraction_request LIMIT 1")
    check("B2 read login CANNOT SELECT ledger outcome (42501)", ok is False and code == "42501", code)
    # positive: business column ที่ endpoint คืน อ่านได้ + get_extraction_status
    ok, code = attempt(RDSN, "SELECT customer_name_raw FROM rfq LIMIT 1")
    check("read login CAN SELECT business col customer_name_raw", ok is True, code)
    ok, code = attempt(RDSN, "SELECT * FROM get_extraction_status('00000000-0000-0000-0000-000000000000'::uuid)")
    check("M1 read login CAN EXECUTE get_extraction_status", ok is True, code)
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
# B3: DSN ชี้ role ผิด surface (READ_DSN=rfq_app มี SELECT ALL) → startup fail (ไม่ใช่แค่รายงาน)
if os.environ.get("RFQ_READ_DSN"):
    app_dsn = os.environ["RFQ_READ_DSN"].replace("rfq_read_api_login", "rfq_app_login").replace("password=readapi", "password=app")
    rccode, err = import_with({"ENQ_API_KEY": "test-key", "RFQ_READ_DSN": app_dsn})
    check("B3 startup fail-closed: READ_DSN=rfq_app (SELECT ALL) → error", rccode != 0 and "fail-closed" in err and "READ_DSN" in err, err[-200:])

# F1: exact function-surface — capability drift ภายใน role เดิม → startup fail (Codex reproduction)
if os.environ.get("SUPER_DSN"):
    _sup = psycopg2.connect(os.environ["SUPER_DSN"]); _sup.autocommit = True
    def _sx(q):
        with _sup.cursor() as c: c.execute(q)
    APPLY_SIG = "rfq.apply_rfq_extraction(uuid,uuid,jsonb,text,text,text)"
    # inbound over-granted apply → WRITE function set มี extra → startup fail
    _sx(f"GRANT EXECUTE ON FUNCTION {APPLY_SIG} TO rfq_ingest")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx(f"REVOKE EXECUTE ON FUNCTION {APPLY_SIG} FROM rfq_ingest")
    check("F1 inbound over-grant (apply) → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # read over-granted list_claimable → READ function set มี extra → startup fail
    _sx("GRANT EXECUTE ON FUNCTION rfq.list_claimable_extractions(int) TO rfq_read_api")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE EXECUTE ON FUNCTION rfq.list_claimable_extractions(int) FROM rfq_read_api")
    check("F1 read over-grant (list_claimable) → startup fail", rccode != 0 and "fail-closed" in err and "READ_DSN" in err, err[-200:])
    # inbound under-granted (revoke create_rfq_draft) → WRITE function set ขาด → startup fail
    _sx("REVOKE EXECUTE ON FUNCTION rfq.create_rfq_draft(jsonb,text,text,text) FROM rfq_ingest")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("GRANT EXECUTE ON FUNCTION rfq.create_rfq_draft(jsonb,text,text,text) TO rfq_ingest")
    check("F1 inbound under-grant (revoke create_rfq_draft) → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F3: inbound มี direct data access — table SELECT → startup fail (เดิม DML-only check มองไม่เห็น SELECT)
    _sx("GRANT SELECT ON rfq.rfq_ai_extraction_run TO rfq_ingest")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE SELECT ON rfq.rfq_ai_extraction_run FROM rfq_ingest")
    check("F3 inbound table SELECT → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F3: inbound มี column-level UPDATE → startup fail (has_table_privilege('UPDATE') มองไม่เห็น column update)
    _sx("GRANT UPDATE (customer_name_raw) ON rfq.rfq TO rfq_ingest")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE UPDATE (customer_name_raw) ON rfq.rfq FROM rfq_ingest")
    check("F3 inbound column UPDATE → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F3: read มี sensitive column SELECT → startup fail (read ตรวจ exact column set ตอน startup)
    _sx("GRANT SELECT (input_sha256) ON rfq.rfq_ai_extraction_run TO rfq_read_api")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE SELECT (input_sha256) ON rfq.rfq_ai_extraction_run FROM rfq_read_api")
    check("F3 read sensitive-column SELECT → startup fail", rccode != 0 and "fail-closed" in err and "READ_DSN" in err, err[-200:])
    # F5: overload ชื่อเดียวกัน — revoke real signature + grant dummy overload → startup fail (OID ต่างกัน)
    _sx("REVOKE EXECUTE ON FUNCTION rfq.create_rfq_draft(jsonb,text,text,text) FROM rfq_ingest")
    _sx("CREATE FUNCTION rfq.create_rfq_draft() RETURNS void LANGUAGE sql AS 'SELECT 1'")
    _sx("REVOKE EXECUTE ON FUNCTION rfq.create_rfq_draft() FROM PUBLIC")   # กัน role อื่นเห็น dummy
    _sx("GRANT EXECUTE ON FUNCTION rfq.create_rfq_draft() TO rfq_ingest")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("DROP FUNCTION rfq.create_rfq_draft()")
    _sx("GRANT EXECUTE ON FUNCTION rfq.create_rfq_draft(jsonb,text,text,text) TO rfq_ingest")
    check("F5 overload (dummy create_rfq_draft()) แทน real sig → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F6: read มี sensitive-VIEW SELECT → startup fail (relkind='v' ถูก scan)
    _sx("CREATE VIEW rfq.codex_apiview AS SELECT input_sha256 FROM rfq.rfq_ai_extraction_run")
    _sx("GRANT SELECT ON rfq.codex_apiview TO rfq_read_api")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE SELECT ON rfq.codex_apiview FROM rfq_read_api"); _sx("DROP VIEW rfq.codex_apiview")
    check("F6 read sensitive-view SELECT → startup fail", rccode != 0 and "fail-closed" in err and "READ_DSN" in err, err[-200:])
    # F7: inbound มี CREATE ON SCHEMA rfq → startup fail
    _sx("GRANT CREATE ON SCHEMA rfq TO rfq_ingest")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE CREATE ON SCHEMA rfq FROM rfq_ingest")
    check("F7 inbound schema CREATE → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F7: inbound มี sequence UPDATE → startup fail
    with _sup.cursor() as _c:
        _c.execute("SELECT cl.relname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='rfq' AND cl.relkind='S' LIMIT 1")
        _r = _c.fetchone(); _seq = _r[0] if _r else None
    if _seq:
        _sx("GRANT UPDATE ON SEQUENCE rfq.%s TO rfq_ingest" % _seq)
        rccode, err = import_with({"ENQ_API_KEY": "test-key"})
        _sx("REVOKE UPDATE ON SEQUENCE rfq.%s FROM rfq_ingest" % _seq)
        check("F7 inbound sequence UPDATE → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F7: inbound function EXECUTE WITH GRANT OPTION → startup fail
    BEGIN_SIG = "rfq.begin_rfq_extraction(uuid,text,text,text,text,uuid,uuid,jsonb,text,text,text)"
    _sx(f"GRANT EXECUTE ON FUNCTION {BEGIN_SIG} TO rfq_ingest WITH GRANT OPTION")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx(f"REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION {BEGIN_SIG} FROM rfq_ingest")
    check("F7 inbound EXECUTE WITH GRANT OPTION → startup fail", rccode != 0 and "fail-closed" in err and "WRITE_DSN" in err, err[-200:])
    # F7: read column SELECT WITH GRANT OPTION → startup fail
    _sx("GRANT SELECT (id) ON rfq.rfq TO rfq_read_api WITH GRANT OPTION")
    rccode, err = import_with({"ENQ_API_KEY": "test-key"})
    _sx("REVOKE GRANT OPTION FOR SELECT (id) ON rfq.rfq FROM rfq_read_api")
    check("F7 read column SELECT WITH GRANT OPTION → startup fail", rccode != 0 and "fail-closed" in err and "READ_DSN" in err, err[-200:])
    _sup.close()

nfail = res.count(False)
print(f"===== API TEST: {res.count(True)} passed, {nfail} failed =====")
sys.exit(1 if nfail else 0)
