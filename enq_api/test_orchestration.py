"""
Extraction orchestration tests — Codex acceptance checklist (local + synthetic)
รันผ่าน enq_api/run_orchestration_tests.sh (ephemeral PG + migrations 001-008 + login roles + seed)
ครอบ: POST→202 durable run, replay no-dup, client forge trusted fields, durable worker
       (poll→claim→provider นอก txn→apply/fail), GET safe projection (no leak), BLOCKED no-provider,
       reclaim + old-lease fencing (RFS01)
"""
import os, sys, json, uuid
from decimal import Decimal
os.environ.setdefault("ENQ_API_KEY", "test-key")
os.environ.pop("ENQ_DEV_MODE", None)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import psycopg2
from fastapi.testclient import TestClient
from enq_api import main as m, worker as w, provider as prov

client = TestClient(m.app)
KEY = {"X-API-Key": "test-key"}
WDSN = w.WORKER_DSN                                            # M2: rfq_worker_login (claim/apply/fail/list)
SUPER_DSN = os.environ["SUPER_DSN"]                            # superuser — seed trusted tables + toggles
WORKER = "w-test"
H64 = "a" * 64
res = []
def check(name, cond, detail=""):
    res.append(cond); print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(detail)[:300]))

def claim_ids():
    return [str(x) for x in w.claimable(WDSN)]

# ---- seed (superuser): LOCAL provider + CLEAN source + malware source ----
sup = psycopg2.connect(SUPER_DSN); sup.autocommit = True
with sup.cursor() as cur:
    cur.execute("SET search_path TO rfq")
    cur.execute("INSERT INTO rfq_ai_provider(provider_code,model_code,execution_target,policy_version) "
                "VALUES ('typhoon','v1','LOCAL','pol-v1') ON CONFLICT DO NOTHING")
    cur.execute("INSERT INTO rfq_source_ingest(object_store_key,source_sha256,malware_scan_status,classification_status,"
                "classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref) "
                "VALUES ('s3://ok',%s,'CLEAN','CONFIRMED','INTERNAL',true,false,'LOCAL_ONLY','pol-v1','sc') RETURNING id", (H64,))
    S_OK = cur.fetchone()[0]
    cur.execute("INSERT INTO rfq_source_ingest(object_store_key,source_sha256,malware_scan_status,classification_status,"
                "classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref) "
                "VALUES ('s3://mal',%s,'BLOCKED','CONFIRMED','INTERNAL',true,false,'LOCAL_ONLY','pol-v1','sc') RETURNING id", (H64,))
    S_MAL = cur.fetchone()[0]

# ---- T1: POST → 202 + durable PENDING run ----
r = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "x1"})
check("POST → 202 + PENDING + run_id", r.status_code == 202 and r.json().get("status") == "PENDING" and r.json().get("run_id"), r.text)
run1 = r.json().get("run_id")
check("POST response ไม่เผย lease/ref/hash", all(k not in r.text for k in ("lease_token", "input_sha256", "provider_input_ref")), r.text)

# ---- T2: replay same X-Request-Id → same run (no duplicate work) ----
r2 = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "x1"})
check("replay same key → same run_id (idempotent)", r2.status_code == 202 and r2.json().get("run_id") == run1, r2.text)

# ---- T3: auth + client can't forge trusted fields (extra=forbid) ----
check("POST no-auth → 401", client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={"X-Request-Id": "z"}).status_code == 401)
check("POST no X-Request-Id → 400", client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers=KEY).status_code == 400)
rf = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK), "provider": "evil", "actor": "root", "lease_token": "x"},
                 headers={**KEY, "X-Request-Id": "xf"})
check("forge trusted field (provider/actor/lease) → 422", rf.status_code == 422, rf.text)

# ---- T4: durable worker (poll → claim → provider นอก txn → apply) ; provider called once, server-controlled input ----
check("run1 durable in claimable (worker restart-safe)", run1 in claim_ids())
calls = []; orig = prov.extract
prov.extract = lambda **kw: (calls.append(kw), orig(**kw))[1]
try:
    out = w.process_run(run1, WORKER, WDSN)
finally:
    prov.extract = orig
check("worker apply → SUCCEEDED", out.get("action") == "apply" and out.get("status") == "SUCCEEDED", out)
check("provider called exactly once (happy path)", len(calls) == 1, len(calls))
check("provider input = server-controlled (input_sha256 จาก claim, ไม่ใช่ client)", calls and calls[0].get("input_sha256") == H64, calls)

# ---- T5: no double-processing — SUCCEEDED run ไม่ claimable ----
check("SUCCEEDED run ไม่ claimable (no double work)", run1 not in claim_ids())

# ---- T6: GET status = safe projection (no lease/ref/hash leak) ----
g = client.get(f"/enq/extractions/{run1}", headers=KEY); gj = g.json()
check("GET → SUCCEEDED + rfq_id", g.status_code == 200 and gj.get("status") == "SUCCEEDED" and gj.get("rfq_id"), g.text)
check("GET ไม่เผย lease/ref/hash/provider", all(k not in g.text for k in ("lease_token", "input_sha256", "provider_input_ref", "provider_name")), g.text)
check("GET extractions no-auth → 401", client.get(f"/enq/extractions/{run1}").status_code == 401)
check("GET unknown run → 404", client.get(f"/enq/extractions/{uuid.uuid4()}", headers=KEY).status_code == 404)

# ---- T7: extraction landed ใน RFQ tree จริง ----
tree = client.get(f"/enq/rfq/{gj['rfq_id']}", headers=KEY).json()
_qo = tree["items"][0].get("quantity_options", [])
check("extraction tree มี synthetic item + quantity",
      tree["items"][0]["job_name"] == "synthetic extracted job"
      and _qo and float(_qo[0].get("quantity")) == 1000.0, tree)

# ---- T8: should_execute=false (provider disabled หลัง begin) → skipped, provider ไม่ถูกเรียก ----
rb = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "xblk"})
run_b = rb.json()["run_id"]
with sup.cursor() as cur: cur.execute("UPDATE rfq.rfq_ai_provider SET is_active=false WHERE provider_code='typhoon'")
calls2 = []; prov.extract = lambda **kw: (calls2.append(kw), orig(**kw))[1]
try:
    outb = w.process_run(run_b, WORKER, WDSN)
finally:
    prov.extract = orig
    with sup.cursor() as cur: cur.execute("UPDATE rfq.rfq_ai_provider SET is_active=true WHERE provider_code='typhoon'")
check("should_execute=false → skipped + provider NOT called", outb.get("action") == "skipped" and len(calls2) == 0, (outb, len(calls2)))

# ---- T9: malware source → begin 202 BLOCKED, run ไม่ claimable (ไม่เข้า worker) ----
rm = client.post("/enq/extractions", json={"source_ingest_id": str(S_MAL)}, headers={**KEY, "X-Request-Id": "xmal"})
check("malware source → 202 BLOCKED", rm.status_code == 202 and rm.json().get("status") == "BLOCKED", rm.text)
check("BLOCKED run ไม่ claimable", rm.json().get("run_id") not in claim_ids())

# ---- T10: unknown source → begin 23503 → 422 invalid_request ----
ru = client.post("/enq/extractions", json={"source_ingest_id": str(uuid.uuid4())}, headers={**KEY, "X-Request-Id": "xun"})
check("unknown source → 422 invalid_request", ru.status_code == 422, ru.text)

# ---- T11: lease หมด → reclaim (attempt+1) + old-lease apply ถูก fence (RFS01) ----
rl = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "xlease"})
run_l = rl.json()["run_id"]
c1 = w._call_json(WDSN, "SELECT claim_rfq_extraction(%s,%s,%s,%s)", (run_l, "wA", "enq", "claimA"))
lease_old = c1["lease_token"]
with sup.cursor() as cur:
    cur.execute("UPDATE rfq.rfq_ai_extraction_run SET lease_expires_at=now()-interval '1 min' WHERE id=%s", (run_l,))
check("expired-lease run กลับมา claimable (reclaim)", run_l in claim_ids())
outl = w.process_run(run_l, "wB", WDSN)                        # reclaim (attempt2) → provider → apply
check("reclaim → apply SUCCEEDED", outl.get("action") == "apply" and outl.get("status") == "SUCCEEDED", outl)
fenced = False
try:
    w._call_json(WDSN, "SELECT apply_rfq_extraction(%s,%s,%s::jsonb,%s,%s,%s)",
                 (run_l, lease_old, json.dumps(orig(input_ref=None, input_sha256=H64, execution_target="LOCAL")),
                  "wA", "enq", "apAold"))
except psycopg2.Error as e:
    fenced = e.pgcode == "RFS01"
check("old-lease apply fenced → RFS01", fenced)

# ---- M3: durable apply/fail retry (idempotent) + transient vs terminal provider error ----
def _run_status(rid):
    with sup.cursor() as c:
        c.execute("SELECT status_code FROM rfq.rfq_ai_extraction_run WHERE id=%s", (rid,))
        r = c.fetchone(); return r[0] if r else None
_ocj = w._call_json
# M3-a: apply connection ขาด **ก่อน commit** → retry ด้วย request_id เดิม → apply สดสำเร็จ ; provider เรียกครั้งเดียว
ra = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "xm3a"}); run_a = ra.json()["run_id"]
c1 = []; prov.extract = lambda **kw: (c1.append(1), orig(**kw))[1]; st1 = {"hit": False}
def _flaky_pre(dsn, sql, params):
    if "apply_rfq_extraction" in sql and not st1["hit"]:
        st1["hit"] = True; raise psycopg2.OperationalError("drop before commit")
    return _ocj(dsn, sql, params)
w._call_json = _flaky_pre
try: outa = w.process_run(run_a, WORKER, WDSN)
finally: w._call_json = _ocj; prov.extract = orig
check("M3 apply transient(ก่อน commit) → retry → SUCCEEDED", outa.get("action") == "apply" and outa.get("status") == "SUCCEEDED", outa)
check("M3 provider เรียกครั้งเดียว (ไม่ re-call ตอน apply retry)", len(c1) == 1, len(c1))
check("M3 apply retried (attempts≥2)", outa.get("attempts", 1) >= 2, outa)
# M3-a2: apply **commit แล้ว** แต่ผลหาย (drop หลัง commit) → retry → ledger คืน SUCCEEDED เดิม ; provider เรียกครั้งเดียว
rb = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "xm3a2"}); run_b2 = rb.json()["run_id"]
c2 = []; prov.extract = lambda **kw: (c2.append(1), orig(**kw))[1]; st2 = {"hit": False}
def _flaky_post(dsn, sql, params):
    r = _ocj(dsn, sql, params)                              # รันจริง (commit)
    if "apply_rfq_extraction" in sql and not st2["hit"]:
        st2["hit"] = True; raise psycopg2.OperationalError("drop after commit")
    return r
w._call_json = _flaky_post
try: outb = w.process_run(run_b2, WORKER, WDSN)
finally: w._call_json = _ocj; prov.extract = orig
check("M3 apply committed+drop → retry ledger คืน SUCCEEDED เดิม", outb.get("action") == "apply" and outb.get("status") == "SUCCEEDED", outb)
check("M3 (committed case) provider เรียกครั้งเดียว", len(c2) == 1, len(c2))
# M3-b: provider **transient** → ไม่ fail run ; run คง RUNNING → reclaim ได้
rc = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "xm3b"}); run_c = rc.json()["run_id"]
prov.extract = lambda **kw: (_ for _ in ()).throw(prov.ProviderTransient("timeout"))
try: outc = w.process_run(run_c, WORKER, WDSN)
finally: prov.extract = orig
check("M3 provider transient → action=provider_transient (ไม่ fail)", outc.get("action") == "provider_transient", outc)
check("M3 provider transient → run คง RUNNING (reclaimable)", _run_status(run_c) == "RUNNING", _run_status(run_c))
# M3-c: provider **terminal** → run FAILED
rd = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": "xm3c"}); run_d = rd.json()["run_id"]
prov.extract = lambda **kw: (_ for _ in ()).throw(prov.ProviderError("bad result"))
try: outd = w.process_run(run_d, WORKER, WDSN)
finally: prov.extract = orig
check("M3 provider terminal → run FAILED", outd.get("action") == "fail" and outd.get("status") == "FAILED", outd)

# ---- M3-F1: provider คืน payload สำเร็จ แต่ DB validator reject (invalid result) → escalate เป็น FAILED (ไม่ค้าง RUNNING) ----
def _m3f1(tag, payload_fn):
    r = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": tag}); rid = r.json()["run_id"]
    cc = []; prov.extract = lambda **kw: (cc.append(1), payload_fn(kw["input_sha256"]))[1]
    try: o = w.process_run(rid, WORKER, WDSN)
    finally: prov.extract = orig
    return rid, o, len(cc)
# case 1: provider คืน {} → apply 22023 → FAILED
r1, o1, n1 = _m3f1("xm3f1a", lambda sha: {})
check("M3-F1 provider {} → apply invalid → run FAILED", o1.get("action") == "fail" and o1.get("status") == "FAILED", o1)
check("M3-F1 (empty) provider เรียกครั้งเดียว + run ไม่ claimable", n1 == 1 and r1 not in claim_ids(), (n1, o1))
# case 2: evidence ไม่ครบ → RFR01 → FAILED
r2, o2, _ = _m3f1("xm3f1b", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha,
    "items": [{"line_no": 1, "fields": {"job_name": "x", "product_type_ref": "PT"}}],
    "evidence": [{"subject_type": "ITEM", "ref": {"line_no": 1}, "field_name": "job_name", "source_type": "PDF"}]})
check("M3-F1 incomplete evidence → RFR01 → run FAILED", o2.get("action") == "fail" and o2.get("status") == "FAILED", o2)
# case 3: dup line_no → 23505 → FAILED
r3, o3, _ = _m3f1("xm3f1c", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha,
    "items": [{"line_no": 1, "fields": {"job_name": "a"}}, {"line_no": 1, "fields": {"job_name": "b"}}], "evidence": []})
check("M3-F1 dup line_no → 23505 → run FAILED", o3.get("action") == "fail" and o3.get("status") == "FAILED", o3)
check("M3-F1 FAILED runs ไม่กลับเข้า claimable", all(x not in claim_ids() for x in (r1, r2, r3)), claim_ids())

# ---- B1: 54000 (payload > 1MB) สืบทอด OperationalError แต่ต้อง classify เป็น invalid → FAILED (ไม่ใช่ retry_exhausted/RUNNING) ----
rb1, ob1, nb1 = _m3f1("xm3b1", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha, "pad": "x" * 1_100_000})
check("B1 payload >1MB → 54000 → run FAILED (ไม่ค้าง RUNNING)", ob1.get("action") == "fail" and ob1.get("status") == "FAILED", ob1)
check("B1 (54000) provider เรียกครั้งเดียว + ไม่ claimable", nb1 == 1 and rb1 not in claim_ids(), (nb1, ob1))

# ---- B2: provider result ไม่ JSON-safe (serialize fail ก่อนถึง DB) → FAILED (ไม่หลุดเป็น unhandled → RUNNING) ----
rb2, ob2, nb2 = _m3f1("xm3b2a", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha, "amount": Decimal("1.25")})
check("B2 Decimal (TypeError) → run FAILED ; provider ครั้งเดียว", ob2.get("action") == "fail" and ob2.get("status") == "FAILED" and nb2 == 1, (ob2, nb2))
rb3, ob3, _ = _m3f1("xm3b2b", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha, "blob": b"x"})
check("B2 bytes (TypeError) → run FAILED", ob3.get("action") == "fail" and ob3.get("status") == "FAILED", ob3)
rb4, ob4, _ = _m3f1("xm3b2c", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha, "n": float("nan")})
check("B2 NaN (allow_nan=False → ValueError) → run FAILED", ob4.get("action") == "fail" and ob4.get("status") == "FAILED", ob4)
check("B1/B2 FAILED runs ไม่กลับเข้า claimable", all(x not in claim_ids() for x in (rb1, rb2, rb3, rb4)), claim_ids())

# ---- backlog (a): lone-surrogate / NUL — json.dumps(ensure_ascii=True) escape ได้ แต่ DB jsonb reject (class 22) → INVALID_RESULT → FAILED ----
rls, ols, nls = _m3f1("xm3ls", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha, "note": "\ud800"})
check("(a) lone surrogate → DB class22 → run FAILED", ols.get("action") == "fail" and ols.get("status") == "FAILED", ols)
rnu, onu, _ = _m3f1("xm3nul", lambda sha: {"schema_version": "extract-v1.1", "input_sha256": sha, "note": "\u0000"})
check("(a) NUL char → DB class22 → run FAILED", onu.get("action") == "fail" and onu.get("status") == "FAILED", onu)
check("(a) surrogate/NUL provider ครั้งเดียว + ไม่ claimable", nls == 1 and rls not in claim_ids() and rnu not in claim_ids(), (nls, claim_ids()))

# ---- backlog (b): config fail-fast — ค่าผิดต้อง raise ตอน validate (กัน ENQ_APPLY_RETRIES=0 → raise None) ----
def _cfg_raises(**over):
    saved = {k: getattr(w, k) for k in over}
    try:
        for k, v in over.items(): setattr(w, k, v)
        try: w._validate_config(); return False
        except ValueError: return True
    finally:
        for k, v in saved.items(): setattr(w, k, v)
check("(b) ENQ_APPLY_RETRIES=0 → _validate_config raise (กัน raise None)", _cfg_raises(APPLY_RETRIES=0), None)
check("(b) ENQ_APPLY_BACKOFF_S<0 → raise", _cfg_raises(APPLY_BACKOFF_S=-1.0), None)
check("(b) ENQ_WORKER_POLL_LIMIT=0 → raise", _cfg_raises(POLL_LIMIT=0), None)
check("(b) default config ผ่าน validate (ไม่ raise)", w._validate_config() is None, None)

# ---- M3 fail-retry: durable fail (connection drop ก่อน/หลัง commit) ----
def _m3fail(tag, flaky_fn):
    r = client.post("/enq/extractions", json={"source_ingest_id": str(S_OK)}, headers={**KEY, "X-Request-Id": tag}); rid = r.json()["run_id"]
    cc = []
    def _perr(**kw): cc.append(1); raise prov.ProviderError("bad result")
    prov.extract = _perr; st = {"hit": False}; w._call_json = lambda d, s, p: flaky_fn(st, d, s, p)
    try: o = w.process_run(rid, WORKER, WDSN)
    finally: w._call_json = _ocj; prov.extract = orig
    return o, len(cc)
def _fail_pre(st, d, s, p):
    if "fail_rfq_extraction" in s and not st.get("hit"):
        st["hit"] = True; raise psycopg2.OperationalError("drop pre")
    return _ocj(d, s, p)
of5, nf5 = _m3fail("xm3f5", _fail_pre)
check("M3 fail transient(ก่อน commit) → retry → FAILED ; provider ครั้งเดียว", of5.get("action") == "fail" and of5.get("status") == "FAILED" and nf5 == 1, (of5, nf5))
def _fail_post(st, d, s, p):
    r = _ocj(d, s, p)
    if "fail_rfq_extraction" in s and not st.get("hit"):
        st["hit"] = True; raise psycopg2.OperationalError("drop post")
    return r
of6, nf6 = _m3fail("xm3f6", _fail_post)
check("M3 fail committed+drop → retry ledger คืน FAILED เดิม ; provider ครั้งเดียว", of6.get("action") == "fail" and of6.get("status") == "FAILED" and nf6 == 1, (of6, nf6))

# ---- B3: worker role capability fail-closed (rfq_worker ผ่าน ; inbound DSN ต้อง raise) ----
try:
    w.assert_worker_role(WDSN); _wok = True
except Exception:
    _wok = False
check("B3 worker role assert ผ่านด้วย rfq_worker DSN", _wok)
try:
    w.assert_worker_role(os.environ["RFQ_WRITE_DSN"]); _wbad = False   # inbound (rfq_ingest) → claim ไม่ได้
except RuntimeError:
    _wbad = True
except Exception:
    _wbad = False
check("B3 worker role assert FAIL ด้วย inbound DSN (surface ผิด)", _wbad)

# F1: worker capability drift ภายใน role เดิม → exact function-surface จับได้ (Codex reproduction)
def _osx(q):
    with sup.cursor() as c: c.execute(q)
APPLY_SIG = "rfq.apply_rfq_extraction(uuid,uuid,jsonb,text,text,text)"
_osx(f"REVOKE EXECUTE ON FUNCTION {APPLY_SIG} FROM rfq_worker")           # under-grant
try:
    w.assert_worker_role(WDSN); _u = False
except RuntimeError:
    _u = True
except Exception:
    _u = False
_osx(f"GRANT EXECUTE ON FUNCTION {APPLY_SIG} TO rfq_worker")
check("F1 worker under-grant (revoke apply) → assert fail", _u)
_osx("GRANT EXECUTE ON FUNCTION rfq.create_rfq_draft(jsonb,text,text,text) TO rfq_worker")   # over-grant
try:
    w.assert_worker_role(WDSN); _o = False
except RuntimeError:
    _o = True
except Exception:
    _o = False
_osx("REVOKE EXECUTE ON FUNCTION rfq.create_rfq_draft(jsonb,text,text,text) FROM rfq_worker")
check("F1 worker over-grant (create_rfq_draft) → assert fail", _o)
# F3: worker มี direct data access (table SELECT / column UPDATE) → assert fail
def _wfail(setup, teardown):
    _osx(setup)
    try:
        w.assert_worker_role(WDSN); r = False
    except RuntimeError:
        r = True
    except Exception:
        r = False
    _osx(teardown); return r
check("F3 worker table SELECT → assert fail",
      _wfail("GRANT SELECT ON rfq.rfq_attachment TO rfq_worker", "REVOKE SELECT ON rfq.rfq_attachment FROM rfq_worker"))
check("F3 worker column UPDATE → assert fail",
      _wfail("GRANT UPDATE (status_code) ON rfq.rfq_ai_extraction_run TO rfq_worker",
             "REVOKE UPDATE (status_code) ON rfq.rfq_ai_extraction_run FROM rfq_worker"))
# F5: overload ชื่อเดียวกัน — revoke real apply + grant dummy apply() → assert fail (OID ต่างกัน)
_osx(f"REVOKE EXECUTE ON FUNCTION {APPLY_SIG} FROM rfq_worker")
_osx("CREATE FUNCTION rfq.apply_rfq_extraction() RETURNS void LANGUAGE sql AS 'SELECT 1'")
_osx("REVOKE EXECUTE ON FUNCTION rfq.apply_rfq_extraction() FROM PUBLIC")
_osx("GRANT EXECUTE ON FUNCTION rfq.apply_rfq_extraction() TO rfq_worker")
try:
    w.assert_worker_role(WDSN); _f5 = False
except RuntimeError:
    _f5 = True
except Exception:
    _f5 = False
_osx("DROP FUNCTION rfq.apply_rfq_extraction()")
_osx(f"GRANT EXECUTE ON FUNCTION {APPLY_SIG} TO rfq_worker")
check("F5 worker overload (dummy apply()) แทน real sig → assert fail", _f5)
# F6/F7: worker view/schema/sequence/grant-option drift → assert fail
def _wchk(name, setups, teardowns):
    for s in setups: _osx(s)
    try:
        w.assert_worker_role(WDSN); r = False
    except RuntimeError:
        r = True
    except Exception:
        r = False
    for t in teardowns: _osx(t)
    check(name, r)
_wchk("F6 worker sensitive-view SELECT → assert fail",
      ["CREATE VIEW rfq.codex_wview AS SELECT input_sha256 FROM rfq.rfq_ai_extraction_run",
       "GRANT SELECT ON rfq.codex_wview TO rfq_worker"],
      ["REVOKE SELECT ON rfq.codex_wview FROM rfq_worker", "DROP VIEW rfq.codex_wview"])
_wchk("F7 worker schema CREATE → assert fail",
      ["GRANT CREATE ON SCHEMA rfq TO rfq_worker"], ["REVOKE CREATE ON SCHEMA rfq FROM rfq_worker"])
_wchk("F7 worker EXECUTE WITH GRANT OPTION → assert fail",
      [f"GRANT EXECUTE ON FUNCTION {APPLY_SIG} TO rfq_worker WITH GRANT OPTION"],
      [f"REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION {APPLY_SIG} FROM rfq_worker"])

sup.close()
nfail = res.count(False)
print(f"===== ORCHESTRATION TEST: {res.count(True)} passed, {nfail} failed =====")
sys.exit(1 if nfail else 0)
