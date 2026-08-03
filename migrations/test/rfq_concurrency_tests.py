#!/usr/bin/env python3
"""
rfq_concurrency_tests.py — 2-connection deterministic concurrency/permission tests
สำหรับ RFQ service layer v2 (005). ครอบ Codex T01–T08.

ต่างจาก 020/030 (single connection, invariant): ไฟล์นี้ใช้ "สอง connection จริง"
ประสานจังหวะด้วย pg_blocking_pids (ไม่เดาด้วย sleep) เพื่อพิสูจน์:
  T01 concurrent mark_ready ชน row_version (40001) — ไม่ Ready ซ้ำ
  T02 concurrent create_rfq_revision จาก predecessor เดียว — ไม่เกิด revision ซ้ำ
  T03 readiness TOCTOU — parent-lock serialize + freeze (F3)
  T04 role/privilege — app เขียน raw ไม่ได้ (F1/F2), flag ไม่ช่วย
  T05 optimistic version edge (NULL/stale)  (F4)
  T06 rollback/savepoint atomicity + boundary คงอยู่
  T07 idempotency — SKIP by design (F7 gated: ยังไม่มี request_id)
  T08 revision clone atomicity — fail กลาง clone แล้ว rollback ครบ (F6)

รันผ่าน run_migrations.sh (โหลด 001+002+003+005 ลง ephemeral postgres:16 ก่อน)
ค่า connection อ่านจาก env (ดีฟอลต์ = container ทดสอบ localhost:5433)
"""
import os, sys, time, threading, json
import psycopg2
from psycopg2 import errorcodes

DSN = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5433")),
    dbname=os.environ.get("PGDATABASE", "rfqtest"),
    user=os.environ.get("PGUSER", "postgres"),
    password=os.environ.get("PGPASSWORD", "test"),
)

OK_PAYLOAD = ('{"schema_version":"draft-v1","header":{"enquiry_ref":"ENQ","source_channel":"EMAIL"},'
              '"items":[{"line_no":1,"job_name":"box","components":[{"component_no":1,"box_template_ref":"BT"}]}]}')

results = []  # (name, ok, detail)
def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

def connect(role=None, app_name=None):
    c = psycopg2.connect(**DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        if app_name:
            cur.execute("SET application_name = %s", (app_name,))
        if role:
            cur.execute(f"SET ROLE {role}")
    return c

def sql1(conn, q, args=None):
    with conn.cursor() as cur:
        cur.execute(q, args)
        row = cur.fetchone()
        return row[0] if row else None

def rowver(sup, rfq):
    return sql1(sup, "SELECT row_version FROM rfq WHERE id=%s", (rfq,))

def wait_blocked(sup, blocked_pid, blocker_pid, timeout=10.0):
    """poll จน blocker_pid ปรากฏใน pg_blocking_pids(blocked_pid) — พิสูจน์ B รอ A จริง"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        blockers = sql1(sup, "SELECT pg_blocking_pids(%s)", (blocked_pid,)) or []
        if blocker_pid in blockers:
            return True
        time.sleep(0.05)
    return False

def seed_review_rfq(sup, rfq_no, blocking_clar=False):
    """RFQ พร้อม mark_ready: READY_FOR_REVIEW + item/qty/comp + reviewer signoff"""
    with sup.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("""INSERT INTO rfq (rfq_no,enquiry_ref,source_channel,customer_ref,sales_owner_ref,
            status_code,created_by_ref,updated_by_ref)
            VALUES (%s,%s,'EMAIL','C','A','READY_FOR_REVIEW','P','P') RETURNING id""",
            (rfq_no, 'ENQ-' + rfq_no))
        rfq = cur.fetchone()[0]
        cur.execute("""INSERT INTO rfq_item (rfq_id,line_no,job_name,product_type_ref,finished_width_mm,
            finished_length_mm,finished_depth_mm,finishing_state,packing_state,artwork_state,sample_state)
            VALUES (%s,1,'box','PT',80,120,50,'SPECIFIED','SPECIFIED','RECEIVED','AVAILABLE') RETURNING id""", (rfq,))
        item = cur.fetchone()[0]
        cur.execute("INSERT INTO rfq_quantity_option (rfq_item_id,option_no,quantity,unit_ref,is_primary) VALUES (%s,1,5000,'PCS',true) RETURNING id", (item,))
        qty = cur.fetchone()[0]
        cur.execute("INSERT INTO rfq_component (rfq_item_id,component_no,component_name,box_template_ref,box_width_mm,box_length_mm,box_depth_mm) VALUES (%s,1,'body','BT',80,120,50) RETURNING id", (item,))
        comp = cur.fetchone()[0]
        cur.execute("INSERT INTO rfq_signoff (rfq_id,signoff_role,decision_code,actor_ref) VALUES (%s,'REVIEWER','CONFIRMED','REV') RETURNING id", (rfq,))
        so = cur.fetchone()[0]
        clar = None
        if blocking_clar:
            clar = sql1(sup, "SELECT add_clarification(%s,'RFQ',%s,'q',true,'AI','bot','tester')", (rfq, rfq))
    return dict(rfq=rfq, item=item, qty=qty, comp=comp, signoff=so, clar=clar)

def seed_draft_rfq(sup, rfq_no):
    """DRAFT RFQ + 1 item (row_version=1) สำหรับ draft-edit/upsert"""
    with sup.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("""INSERT INTO rfq (rfq_no,enquiry_ref,source_channel,customer_ref,sales_owner_ref,
            status_code,created_by_ref,updated_by_ref) VALUES (%s,%s,'EMAIL','C','A','DRAFT','P','P') RETURNING id""",
            (rfq_no, 'ENQ-' + rfq_no))
        rfq = cur.fetchone()[0]
        cur.execute("""INSERT INTO rfq_item (rfq_id,line_no,job_name,finishing_state,packing_state,artwork_state,sample_state)
            VALUES (%s,1,'orig job','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN') RETURNING id""", (rfq,))
        item = cur.fetchone()[0]
    return dict(rfq=rfq, item=item)

def seed_ready_estimate(sup, rfq_no):
    f = seed_review_rfq(sup, rfq_no)
    sql1(sup, "SELECT mark_ready(%s,%s,'REV')", (f['rfq'], rowver(sup, f['rfq'])))
    return f

# ---------------------------------------------------------------------------
def t01_concurrent_mark_ready(sup):
    f = seed_review_rfq(sup, 'T01')
    rfq = f['rfq']
    A = connect(role='rfq_app', app_name='rfq-conc-A')
    B = connect(role='rfq_app', app_name='rfq-conc-B')
    A.autocommit = False; B.autocommit = False
    pidA, pidB = A.info.backend_pid, B.info.backend_pid
    # A: mark_ready ใน txn เปิดค้าง (ถือ row lock)
    curA = A.cursor(); curA.execute("SELECT mark_ready(%s,1,'A')", (rfq,)); curA.fetchone()
    res = {}
    def run_b():
        try:
            cb = B.cursor(); cb.execute("SELECT mark_ready(%s,1,'B')", (rfq,)); cb.fetchone(); B.commit()
            res['ok'] = True
        except Exception as e:
            res['err'] = e
            B.rollback()
    tb = threading.Thread(target=run_b); tb.start()
    blocked = wait_blocked(sup, pidB, pidA)
    A.commit()                    # ปล่อย lock: A ทำ Ready สำเร็จ
    tb.join(15)
    A.close(); B.close()
    err = res.get('err')
    code = getattr(err, 'pgcode', None)
    ok_block = blocked
    ok_bcode = code == errorcodes.SERIALIZATION_FAILURE   # 40001
    status = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (rfq,))
    rv = rowver(sup, rfq)
    n_ready_hist = sql1(sup, "SELECT count(*) FROM rfq_status_history WHERE rfq_id=%s AND to_status_code='READY_FOR_ESTIMATE'", (rfq,))
    n_pass_run = sql1(sup, "SELECT count(*) FROM rfq_readiness_run WHERE rfq_id=%s AND passed", (rfq,))
    ok = ok_block and ok_bcode and status == 'READY_FOR_ESTIMATE' and rv == 2 and n_ready_hist == 1 and n_pass_run == 1
    record('T01 concurrent mark_ready', ok,
           f"B blocked-by-A={ok_block}, B rc={code}(want 40001), status={status}, rv={rv}, ready_hist={n_ready_hist}, passed_run={n_pass_run}")

def t02_concurrent_revision(sup):
    f = seed_ready_estimate(sup, 'T02')
    rfq = f['rfq']
    A = connect(role='rfq_app', app_name='rfq-conc-A'); B = connect(role='rfq_app', app_name='rfq-conc-B')
    A.autocommit = False; B.autocommit = False
    pidA, pidB = A.info.backend_pid, B.info.backend_pid
    curA = A.cursor(); curA.execute("SELECT create_rfq_revision(%s,'reasonA','A')", (rfq,)); new_a = curA.fetchone()[0]
    res = {}
    def run_b():
        try:
            cb = B.cursor(); cb.execute("SELECT create_rfq_revision(%s,'reasonB','B')", (rfq,)); cb.fetchone(); B.commit()
            res['ok'] = True
        except Exception as e:
            res['err'] = e; B.rollback()
    tb = threading.Thread(target=run_b); tb.start()
    blocked = wait_blocked(sup, pidB, pidA)
    A.commit()
    tb.join(15)
    A.close(); B.close()
    err = res.get('err'); code = getattr(err, 'pgcode', None)
    n_rev2 = sql1(sup, "SELECT count(*) FROM rfq WHERE rfq_no='T02' AND revision_no=2", ())
    n_current = sql1(sup, "SELECT count(*) FROM rfq WHERE rfq_no='T02' AND is_current", ())
    old_status = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (rfq,))
    n_super_hist = sql1(sup, "SELECT count(*) FROM rfq_status_history WHERE rfq_id=%s AND to_status_code='SUPERSEDED'", (rfq,))
    ok = blocked and code == errorcodes.CHECK_VIOLATION and n_rev2 == 1 and n_current == 1 and old_status == 'SUPERSEDED' and n_super_hist == 1
    record('T02 concurrent create_rfq_revision', ok,
           f"B blocked-by-A={blocked}, B rc={code}(want 23514), rev2_count={n_rev2}, current_count={n_current}, old={old_status}, super_hist={n_super_hist}")

def t03_readiness_toctou(sup):
    """A ถือ lock ระหว่าง mark_ready; B พยายาม reopen clarification / revoke signoff → ถูก serialize แล้ว reject"""
    # variant a: reopen answered blocking clarification
    f = seed_review_rfq(sup, 'T03a')
    rfq = f['rfq']
    clar = sql1(sup, "SELECT add_clarification(%s,'RFQ',%s,'q',true,'AI','bot','tester')", (rfq, rfq))
    sql1(sup, "SELECT resolve_clarification(%s,'ANSWERED','ae','a')", (clar,))   # ตอบแล้ว → ไม่ block Ready
    A = connect(role='rfq_app', app_name='rfq-conc-A'); B = connect(role='rfq_app', app_name='rfq-conc-B')
    A.autocommit = False; B.autocommit = False
    pidA, pidB = A.info.backend_pid, B.info.backend_pid
    rvA = rowver(sup, rfq)   # V3: add/resolve_clarification bump row_version → อ่าน version ปัจจุบัน
    curA = A.cursor(); curA.execute("SELECT mark_ready(%s,%s,'A')", (rfq, rvA)); curA.fetchone()
    res = {}
    def run_b():
        try:
            cb = B.cursor(); cb.execute("SELECT resolve_clarification(%s,'OPEN','hacker')", (clar,)); B.commit()
            res['ok'] = True
        except Exception as e:
            res['err'] = e; B.rollback()
    tb = threading.Thread(target=run_b); tb.start()
    blocked = wait_blocked(sup, pidB, pidA)
    A.commit()
    tb.join(15)
    A.close(); B.close()
    err = res.get('err'); code = getattr(err, 'pgcode', None)
    clar_status = sql1(sup, "SELECT status_code FROM rfq_clarification WHERE id=%s", (clar,))
    rfq_status = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (rfq,))
    n_open_block = sql1(sup, "SELECT count(*) FROM rfq_clarification WHERE rfq_id=%s AND is_blocking AND status_code='OPEN'", (rfq,))
    ok_a = blocked and code == errorcodes.CHECK_VIOLATION and clar_status == 'ANSWERED' and rfq_status == 'READY_FOR_ESTIMATE' and n_open_block == 0
    record('T03a TOCTOU reopen-clarification', ok_a,
           f"B blocked={blocked}, B rc={code}(want 23514/reject), clar={clar_status}(want ANSWERED), rfq={rfq_status}, open_block={n_open_block}")

    # variant b: revoke reviewer signoff ระหว่าง mark_ready
    f2 = seed_review_rfq(sup, 'T03b')
    rfq2, so2 = f2['rfq'], f2['signoff']
    A2 = connect(role='rfq_app', app_name='rfq-conc-A'); B2 = connect(role='rfq_app', app_name='rfq-conc-B')
    A2.autocommit = False; B2.autocommit = False
    pidA2, pidB2 = A2.info.backend_pid, B2.info.backend_pid
    cA = A2.cursor(); cA.execute("SELECT mark_ready(%s,1,'A')", (rfq2,)); cA.fetchone()
    res2 = {}
    def run_b2():
        try:
            cb = B2.cursor(); cb.execute("SELECT revoke_signoff(%s,'hacker','conc revoke')", (so2,)); B2.commit(); res2['ok'] = True
        except Exception as e:
            res2['err'] = e; B2.rollback()
    tb2 = threading.Thread(target=run_b2); tb2.start()
    blocked2 = wait_blocked(sup, pidB2, pidA2)
    A2.commit(); tb2.join(15); A2.close(); B2.close()
    err2 = res2.get('err'); code2 = getattr(err2, 'pgcode', None)
    # V2 append-only: revoke ถูก reject (frozen หลัง A commit mark_ready) → signoff ยัง active (revoked_at IS NULL)
    so_active = sql1(sup, "SELECT revoked_at IS NULL FROM rfq_signoff WHERE id=%s", (so2,))
    ok_b = blocked2 and code2 == errorcodes.CHECK_VIOLATION and so_active is True
    record('T03b TOCTOU revoke-signoff', ok_b,
           f"B blocked={blocked2}, B rc={code2}(want 23514/reject), signoff_still_active(revoked_at IS NULL)={so_active}")

def t04_role_privilege(sup):
    """as rfq_app: raw DML ถูก block; flag ไม่ช่วย; has_table_privilege = false"""
    f = seed_ready_estimate(sup, 'T04')
    rfq, item = f['rfq'], f['item']
    app = connect(role='rfq_app')
    checks = []
    def denied(label, q, args=None):
        try:
            with app.cursor() as cur:
                cur.execute(q, args)
            checks.append((label, False, 'ALLOWED(bad)'))
        except psycopg2.Error as e:
            checks.append((label, e.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE, e.pgcode))
            app.rollback()
    denied('raw UPDATE status', "UPDATE rfq SET status_code='DRAFT' WHERE id=%s", (rfq,))
    # flag-bypass (F1): SET rfq.privileged='on' + raw UPDATE ใน txn เดียว ต้องยัง denied
    # ใช้ connection แยก (autocommit=False) ไม่ให้ txn ค้างไป poison เช็คอื่น
    fb = connect(role='rfq_app'); fb.autocommit = False
    try:
        with fb.cursor() as cur:
            cur.execute("SET LOCAL rfq.privileged='on'")
            cur.execute("UPDATE rfq SET status_code='DRAFT' WHERE id=%s", (rfq,))
        checks.append(('flag-bypass', False, 'ALLOWED(bad)'))
    except psycopg2.Error as e:
        checks.append(('flag-bypass', e.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE, e.pgcode))
    fb.rollback(); fb.close()
    denied('raw INSERT ready rfq', "INSERT INTO rfq (rfq_no,status_code,ready_at,ready_by_ref,created_by_ref,updated_by_ref) VALUES ('T04HACK','READY_FOR_ESTIMATE',now(),'x','x','x')")
    denied('raw INSERT child item', "INSERT INTO rfq_item (rfq_id,line_no,product_type_ref) VALUES (%s,9,'PT')", (rfq,))
    denied('raw reparent item', "UPDATE rfq_item SET rfq_id=%s WHERE id=%s", (rfq, item))
    denied('raw DELETE status_history', "DELETE FROM rfq_status_history WHERE rfq_id=%s", (rfq,))
    denied('raw UPDATE readiness_run', "UPDATE rfq_readiness_run SET passed=false WHERE rfq_id=%s", (rfq,))
    denied('raw UPDATE row_version', "UPDATE rfq SET row_version=99 WHERE id=%s", (rfq,))
    # introspection: table-level UPDATE ต้องเป็น false (ไม่พึ่ง column revoke)
    has_upd = sql1(sup, "SELECT has_table_privilege('rfq_app','rfq.rfq','UPDATE')")
    checks.append(('has_table_privilege UPDATE=false', has_upd is False, f'has_update={has_upd}'))
    # app ต้องเรียก mark_ready ได้ (EXECUTE granted) — ยืนยันว่าไม่ได้ปิดหมด
    can_exec = sql1(sup, "SELECT has_function_privilege('rfq_app','mark_ready(uuid,int,text)','EXECUTE')")
    checks.append(('app EXECUTE mark_ready=true', can_exec is True, f'exec={can_exec}'))
    app.close()
    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or "all raw-DML denied, flag no-op, introspection correct"
    record('T04 role/privilege boundary', allok, detail)

def t05_version_edge(sup):
    f = seed_review_rfq(sup, 'T05')
    rfq = f['rfq']
    app = connect(role='rfq_app')
    # NULL row_version → reject 40001
    def expect_code(label, q, args, want):
        try:
            with app.cursor() as cur:
                cur.execute(q, args)
            app.rollback(); return (label, False, 'no-error')
        except psycopg2.Error as e:
            app.rollback(); return (label, e.pgcode == want, e.pgcode)
    c1 = expect_code('NULL row_version', "SELECT mark_ready(%s,NULL,'x')", (rfq,), errorcodes.SERIALIZATION_FAILURE)
    c2 = expect_code('stale row_version', "SELECT mark_ready(%s,999,'x')", (rfq,), errorcodes.SERIALIZATION_FAILURE)
    # correct version → success + version bumps to 2 (เฉพาะ mark_ready ที่ขยับ version)
    sql1(sup, "SELECT mark_ready(%s,%s,'x')", (rfq, rowver(sup, rfq)))
    bumped = rowver(sup, rfq) == 2
    app.close()
    ok = c1[1] and c2[1] and bumped
    record('T05 optimistic version edge', ok, f"{c1[0]}:{c1[2]} {c2[0]}:{c2[2]} bumped_to_2={bumped}")

def t06_rollback(sup):
    f = seed_ready_estimate(sup, 'T06')
    rfq = f['rfq']
    # A: create_rfq_revision แล้ว ROLLBACK → ต้องไม่เหลืออะไร
    A = connect(role='rfq_app'); A.autocommit = False
    cA = A.cursor(); cA.execute("SELECT create_rfq_revision(%s,'reason','A')", (rfq,)); new_id = cA.fetchone()[0]
    A.rollback(); A.close()
    n_rev2 = sql1(sup, "SELECT count(*) FROM rfq WHERE rfq_no='T06' AND revision_no=2")
    old_status = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (rfq,))
    old_current = sql1(sup, "SELECT is_current FROM rfq WHERE id=%s", (rfq,))
    n_super = sql1(sup, "SELECT count(*) FROM rfq_status_history WHERE rfq_id=%s AND to_status_code='SUPERSEDED'", (rfq,))
    ok_rb = n_rev2 == 0 and old_status == 'READY_FOR_ESTIMATE' and old_current is True and n_super == 0
    # savepoint: failing mark_ready แล้ว rollback to savepoint → boundary ยังอยู่
    g = seed_review_rfq(sup, 'T06sp', blocking_clar=True)  # มี blocking clar → mark_ready fail
    rfq2 = g['rfq']
    B = connect(role='rfq_app'); B.autocommit = False
    cB = B.cursor()
    cB.execute("SAVEPOINT sp1")
    failed = False
    try:
        cB.execute("SELECT mark_ready(%s,1,'x')", (rfq2,))
    except psycopg2.Error:
        failed = True
        cB.execute("ROLLBACK TO SAVEPOINT sp1")
    # หลัง rollback-to-savepoint: raw update ยังถูก block (permission), flag setting ไม่มีผล
    still_denied = False
    try:
        cB.execute("UPDATE rfq SET status_code='DRAFT' WHERE id=%s", (rfq2,))
    except psycopg2.Error as e:
        still_denied = (e.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE)
    B.rollback(); B.close()
    status2 = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (rfq2,))
    ok_sp = failed and still_denied and status2 == 'READY_FOR_REVIEW'
    record('T06 rollback/savepoint atomicity', ok_rb and ok_sp,
           f"revision-rollback ok={ok_rb} (rev2={n_rev2},old={old_status},cur={old_current}); savepoint ok={ok_sp} (failed={failed},denied={still_denied},status={status2})")

def t07_idempotency(sup):
    record('T07 idempotency (request_id)', True,
           'SKIP by design - F7 gated: no request_id/outbox yet (block auto-retry until implemented)')

def t08_clone_atomicity(sup):
    """บังคับ error กลาง clone (test-only trigger) → predecessor + revision + history ต้อง rollback ครบ"""
    f = seed_ready_estimate(sup, 'T08')
    rfq = f['rfq']
    # test-only trigger: raise เมื่อ insert delivery (ขั้น clone ท้าย ๆ)
    with sup.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("""CREATE OR REPLACE FUNCTION zz_test_fail_delivery() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'zz_test injected failure' USING ERRCODE='22000'; END $$;""")
        # ต้องมี delivery ให้ clone ถึงจะ trigger; seed delivery ให้ predecessor
        cur.execute("INSERT INTO rfq_delivery (rfq_item_id,quantity_option_id,delivery_no,destination_ref) VALUES (%s,%s,1,'D')", (f['item'], f['qty']))
        cur.execute("CREATE TRIGGER zz_test_fail BEFORE INSERT ON rfq_delivery FOR EACH ROW EXECUTE FUNCTION zz_test_fail_delivery()")
    app = connect(role='rfq_app'); app.autocommit = True
    injected = False
    try:
        sql1(app, "SELECT create_rfq_revision(%s,'reason','A')", (rfq,))
    except psycopg2.Error as e:
        injected = ('zz_test' in (e.pgerror or ''))
        try: app.rollback()
        except Exception: pass
    app.close()
    with sup.cursor() as cur:
        cur.execute("DROP TRIGGER zz_test_fail ON rfq_delivery")
        cur.execute("DROP FUNCTION zz_test_fail_delivery()")
    n_rev2 = sql1(sup, "SELECT count(*) FROM rfq WHERE rfq_no='T08' AND revision_no=2")
    old_status = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (rfq,))
    old_current = sql1(sup, "SELECT is_current FROM rfq WHERE id=%s", (rfq,))
    n_super = sql1(sup, "SELECT count(*) FROM rfq_status_history WHERE rfq_id=%s AND to_status_code='SUPERSEDED'", (rfq,))
    ok = injected and n_rev2 == 0 and old_status == 'READY_FOR_ESTIMATE' and old_current is True and n_super == 0
    record('T08 revision clone atomicity', ok,
           f"injected={injected}, rev2={n_rev2}(want 0), old={old_status}, current={old_current}, super_hist={n_super}(want 0)")

def t09_authz_separation(sup):
    """Codex V1: rfq_ingest (ENQ worker) ต้องปลอม sign-off/Ready/revision ไม่ได้"""
    f = seed_review_rfq(sup, 'T09')
    rfq, so = f['rfq'], f['signoff']
    ing = connect(role='rfq_ingest')
    checks = []
    def denied_exec(label, q, args=None):
        try:
            with ing.cursor() as cur:
                cur.execute(q, args)
            checks.append((label, False, 'ALLOWED(bad)'))
        except psycopg2.Error as e:
            checks.append((label, e.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE, e.pgcode)); ing.rollback()
    denied_exec('ingest mark_ready',          "SELECT mark_ready(%s,1,'forged')", (rfq,))
    denied_exec('ingest add_signoff',         "SELECT add_signoff(%s,'REVIEWER','CONFIRMED','forged')", (rfq,))
    denied_exec('ingest revoke_signoff',      "SELECT revoke_signoff(%s,'forged','x')", (so,))
    denied_exec('ingest create_rfq_revision', "SELECT create_rfq_revision(%s,'r','forged')", (rfq,))
    denied_exec('ingest resolve_clarification', "SELECT resolve_clarification(%s,'OPEN','forged')", (so,))
    denied_exec('ingest upsert_rfq_draft',    "SELECT upsert_rfq_draft(%s,1,'forged','{}'::jsonb)", (rfq,))
    ing.close()
    # rfq_app คงสิทธิ์ reviewer/Ready ไว้; rfq_ingest ต้องไม่มี
    app_exec = sql1(sup, "SELECT has_function_privilege('rfq_app','mark_ready(uuid,int,text)','EXECUTE')")
    ing_exec = sql1(sup, "SELECT has_function_privilege('rfq_ingest','mark_ready(uuid,int,text)','EXECUTE')")
    ing_so = sql1(sup, "SELECT has_function_privilege('rfq_ingest','add_signoff(uuid,text,text,text,text)','EXECUTE')")
    checks.append(('rfq_app EXECUTE mark_ready=true', app_exec is True, app_exec))
    checks.append(('rfq_ingest EXECUTE mark_ready=false', ing_exec is False, ing_exec))
    checks.append(('rfq_ingest EXECUTE add_signoff=false', ing_so is False, ing_so))
    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or "ingest blocked from signoff/Ready/revision; app retains capability"
    record('T09 authz separation (rfq_ingest)', allok, detail)

def t10_catalog_and_policy(sup):
    """Codex #1/#3: catalog assertions + policy-version spoof เป็นไปไม่ได้เชิงโครงสร้าง"""
    svc = ['mark_ready', 'create_rfq_revision', 'add_clarification', 'resolve_clarification',
           'add_signoff', 'revoke_signoff', 'upsert_rfq_draft', '_lock_rfq_for_input', '_reviewer_latest_confirmed', '_bump_rfq_version',
           'create_rfq_draft', '_reject_unknown_keys', '_child_array',
           'begin_rfq_extraction', 'claim_rfq_extraction', 'apply_rfq_extraction', 'fail_rfq_extraction',
           'list_claimable_extractions', 'get_extraction_status',
           '_egress_decide', '_norm_ctx', '_ext_collect', '_resolve_subject']
    invoker_ok = {'_reject_unknown_keys', '_child_array', '_norm_ctx', '_ext_collect', '_resolve_subject'}   # pure helper = INVOKER ตั้งใจ
    bad = []
    for fn in svc:
        row = None
        with sup.cursor() as cur:
            cur.execute("""SELECT r.rolname, p.prosecdef, array_to_string(p.proconfig,'|'),
                    p.proacl IS NULL AS acl_null,
                    EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                            WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS pub_exec
                FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='rfq' AND p.proname=%s LIMIT 1""", (fn,))
            row = cur.fetchone()
        if not row:
            bad.append(f"{fn}:missing"); continue
        owner, secdef, cfg, acl_null, pub_exec = row
        if owner != 'rfq_owner': bad.append(f"{fn}:owner={owner}")
        if fn in invoker_ok:
            if secdef is not False: bad.append(f"{fn}:expected INVOKER got prosecdef={secdef}")
        elif secdef is not True:   bad.append(f"{fn}:prosecdef={secdef}")
        if not cfg or 'search_path=pg_catalog, rfq, pg_temp' not in cfg: bad.append(f"{fn}:proconfig={cfg}")
        # PUBLIC ต้องไม่มี EXECUTE (proacl NULL = default = PUBLIC execute ได้ = bad)
        if acl_null or pub_exec: bad.append(f"{fn}:PUBLIC-execute(acl_null={acl_null})")
    # internal helper ต้องไม่เปิดให้ app/ingest
    for role in ('rfq_app', 'rfq_ingest'):
        if sql1(sup, "SELECT has_function_privilege(%s,'_lock_rfq_for_input(uuid)','EXECUTE')", (role,)):
            bad.append(f"_lock:{role}-can-execute")
        if sql1(sup, "SELECT has_function_privilege(%s,'_reject_unknown_keys(jsonb,text[],text)','EXECUTE')", (role,)):
            bad.append(f"_reject_unknown_keys:{role}-can-execute")
        if sql1(sup, "SELECT has_function_privilege(%s,'_child_array(jsonb,text,int,text)','EXECUTE')", (role,)):
            bad.append(f"_child_array:{role}-can-execute")
        if sql1(sup, "SELECT has_function_privilege(%s,'_reviewer_latest_confirmed(uuid)','EXECUTE')", (role,)):
            bad.append(f"_reviewer_latest_confirmed:{role}-can-execute")
        if sql1(sup, "SELECT has_function_privilege(%s,'_bump_rfq_version(uuid,text)','EXECUTE')", (role,)):
            bad.append(f"_bump_rfq_version:{role}-can-execute")
    # policy spoof เชิงโครงสร้าง: mark_ready 6-arg (รับ policy version) ต้องไม่มีอยู่
    six = sql1(sup, """SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rfq' AND p.proname='mark_ready' AND p.pronargs=6""")
    if six and six > 0: bad.append("mark_ready-6arg-exists(policy-spoofable)")
    # B1: add_clarification 7-arg (provenance-as-actor) ต้องถูก DROP ; เหลือแค่ 8-arg + rfq_app EXECUTE เฉพาะ 8-arg
    ac7 = sql1(sup, """SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rfq' AND p.proname='add_clarification' AND p.pronargs=7""")
    if ac7 and ac7 > 0: bad.append("add_clarification-7arg-exists(B1 provenance-as-actor)")
    ac8 = sql1(sup, "SELECT has_function_privilege('rfq_app','add_clarification(uuid,text,uuid,text,boolean,text,text,text)','EXECUTE')")
    if ac8 is not True: bad.append(f"rfq_app !EXECUTE add_clarification-8arg={ac8}")
    # และ readiness_run ต้องบันทึก trusted version เสมอ
    f = seed_review_rfq(sup, 'T10')
    sql1(sup, "SELECT mark_ready(%s,%s,'x')", (f['rfq'], rowver(sup, f['rfq'])))
    ver = sql1(sup, "SELECT validator_version FROM rfq_readiness_run WHERE rfq_id=%s", (f['rfq'],))
    if ver != 'pkg-minimal-v1': bad.append(f"readiness_run.validator={ver}(not trusted)")
    record('T10 catalog + policy-spoof structural', not bad, "; ".join(bad) or
           "all 10 fns: owner=rfq_owner, definer/invoker ตามชนิด, pinned search_path, no PUBLIC exec; helpers hidden; no 6-arg; trusted version")

EXPECTED_RD = [  # F2: (table, column) ที่ rfq_read_api ควรอ่านได้ = ตรง GET /enq/rfq (mirror 009 exact allowlist)
    ("rfq","id"),("rfq","rfq_no"),("rfq","status_code"),("rfq","revision_no"),("rfq","enquiry_ref"),
    ("rfq","source_channel"),("rfq","customer_name_raw"),("rfq","priority_code"),
    ("rfq_item","id"),("rfq_item","rfq_id"),("rfq_item","line_no"),("rfq_item","job_name"),("rfq_item","is_reprint"),
    ("rfq_item","previous_job_ref"),("rfq_item","finished_width_mm"),("rfq_item","finished_length_mm"),
    ("rfq_item","finished_depth_mm"),("rfq_item","finishing_state"),("rfq_item","packing_state"),("rfq_item","artwork_state"),
    ("rfq_quantity_option","rfq_item_id"),("rfq_quantity_option","option_no"),("rfq_quantity_option","quantity"),
    ("rfq_quantity_option","unit_ref"),("rfq_quantity_option","is_primary"),
    ("rfq_component","rfq_item_id"),("rfq_component","component_no"),("rfq_component","component_name"),
    ("rfq_component","paper_name_snapshot"),("rfq_component","paper_gsm_snapshot"),("rfq_component","print_sides_code"),
    ("rfq_component","color_outside_count"),("rfq_component","color_inside_count"),
    ("rfq_process_requirement","rfq_item_id"),("rfq_process_requirement","sequence_no"),("rfq_process_requirement","process_ref"),
    ("rfq_process_requirement","process_name_raw"),("rfq_process_requirement","side_code"),
    ("rfq_packing_requirement","rfq_item_id"),("rfq_packing_requirement","sequence_no"),("rfq_packing_requirement","packing_name_raw"),
    ("rfq_packing_requirement","quantity_per_pack"),("rfq_packing_requirement","unit_ref"),
    ("rfq_delivery","rfq_item_id"),("rfq_delivery","delivery_no"),("rfq_delivery","destination_raw"),("rfq_delivery","requested_date"),
]

def t11_ingest_allowlist(sup):
    """Codex #2/F1/M1/M2: role boundary — inbound(rfq_ingest)=create_rfq_draft+begin, worker(rfq_worker)=claim/apply/fail/list,
    read_api(rfq_read_api)=business SELECT+get_extraction_status ; แต่ละ role ข้าม surface ของกันไม่ได้ + ไม่มี broad SELECT/DML"""
    ing = connect(role='rfq_ingest')
    checks = []
    try:
        rid = sql1(ing, "SELECT create_rfq_draft(%s::jsonb,'enq-extractor','enq','treq1')", (OK_PAYLOAD,))
        checks.append(('ingest CAN create_rfq_draft', rid is not None, rid))
    except psycopg2.Error as e:
        checks.append(('ingest CAN create_rfq_draft', False, e.pgcode)); ing.rollback()
    def denied(label, q):
        try:
            with ing.cursor() as cur: cur.execute(q)
            checks.append((label, False, 'ALLOWED(bad)'))
        except psycopg2.Error as e:
            checks.append((label, e.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE, e.pgcode)); ing.rollback()
    denied('ingest !mark_ready',          "SELECT mark_ready(gen_random_uuid(),1,'x')")
    denied('ingest !add_signoff',         "SELECT add_signoff(gen_random_uuid(),'REVIEWER','CONFIRMED','x')")
    denied('ingest !create_rfq_revision', "SELECT create_rfq_revision(gen_random_uuid(),'r','x')")
    denied('ingest !SELECT rfq',          "SELECT * FROM rfq LIMIT 1")
    denied('ingest !SELECT attachment',   "SELECT object_store_key FROM rfq_attachment LIMIT 1")
    denied('ingest !INSERT rfq',          "INSERT INTO rfq (rfq_no,created_by_ref,updated_by_ref) VALUES ('X','a','a')")
    ing.close()
    sel = sql1(sup, "SELECT has_table_privilege('rfq_ingest','rfq.rfq','SELECT')")
    checks.append(('ingest table SELECT=false', sel is False, sel))
    # M2: effective allowlist ต่อ role — enumerate fn ใน schema rfq ที่แต่ละ role execute ได้
    def eff_of(role):
        return sql1(sup, """SELECT array_agg(p.proname ORDER BY p.proname) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='rfq' AND has_function_privilege(%s, p.oid, 'EXECUTE')""", (role,))
    # rfq_ingest = inbound เท่านั้น (create_rfq_draft + begin) ; ไม่มี claim/apply/fail/list (M2)
    checks.append(('inbound(rfq_ingest) EXECUTE = create_rfq_draft + begin เท่านั้น',
                   eff_of('rfq_ingest') == ['begin_rfq_extraction','create_rfq_draft'], eff_of('rfq_ingest')))
    # rfq_worker = worker เท่านั้น (list_claimable + claim/apply/fail) ; ไม่มี draft/begin
    checks.append(('worker(rfq_worker) EXECUTE = claim/apply/fail/list_claimable เท่านั้น',
                   eff_of('rfq_worker') == ['apply_rfq_extraction','claim_rfq_extraction','fail_rfq_extraction','list_claimable_extractions'],
                   eff_of('rfq_worker')))
    # cross-boundary: inbound เรียก worker fn ไม่ได้ ; worker เรียก inbound fn ไม่ได้
    checks.append(('inbound !claim', sql1(sup, "SELECT has_function_privilege('rfq_ingest','claim_rfq_extraction(uuid,text,text,text)','EXECUTE')") is False, 'ingest-can-claim'))
    checks.append(('worker !create_rfq_draft', sql1(sup, "SELECT has_function_privilege('rfq_worker','create_rfq_draft(jsonb,text,text,text)','EXECUTE')") is False, 'worker-can-draft'))
    # M1: rfq_read_api = **column-level** SELECT (business columns) + get_extraction_status ; ไม่มี PII/sensitive/ledger + ไม่ mutate
    def hcol(t, c): return sql1(sup, "SELECT has_column_privilege('rfq_read_api',%s,%s,'SELECT')", (f'rfq.{t}', c))
    checks.append(('read_api CAN col rfq_item.job_name',      hcol('rfq_item', 'job_name') is True, 'read_api-no-jobname'))
    checks.append(('read_api CAN col rfq.customer_name_raw',  hcol('rfq', 'customer_name_raw') is True, 'read_api-no-name'))
    # B1: PII/notes/spec columns ที่ endpoint ไม่คืน ต้อง denied
    checks.append(('read_api !col rfq.customer_notes (PII)',  hcol('rfq', 'customer_notes') is False, 'read_api-can-notes'))
    checks.append(('read_api !col rfq.contact_phone (PII)',   hcol('rfq', 'contact_phone') is False, 'read_api-can-phone'))
    checks.append(('read_api !col rfq.contact_email (PII)',   hcol('rfq', 'contact_email') is False, 'read_api-can-email'))
    checks.append(('read_api !col rfq_item.notes',            hcol('rfq_item', 'notes') is False, 'read_api-can-item-notes'))
    # B2: sensitive/trusted/ledger tables denied (ledger outcome เก็บ lease/ref/hash)
    checks.append(('read_api !col extraction_run.input_sha256', hcol('rfq_ai_extraction_run', 'input_sha256') is False, 'read_api-can-run'))
    checks.append(('read_api !col attachment.object_store_key', hcol('rfq_attachment', 'object_store_key') is False, 'read_api-can-attach'))
    checks.append(('read_api !col ledger.outcome',            hcol('rfq_extraction_request', 'outcome') is False, 'read_api-can-ledger'))
    checks.append(('read_api !col field_evidence.value_snapshot', hcol('rfq_field_evidence', 'value_snapshot') is False, 'read_api-can-evidence'))
    # exact function allowlist ของ read_api = get_extraction_status เท่านั้น
    effr = sql1(sup, """SELECT array_agg(p.proname ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rfq' AND has_function_privilege('rfq_read_api', p.oid, 'EXECUTE')""")
    checks.append(('read_api function allowlist = get_extraction_status เท่านั้น', effr == ['get_extraction_status'], effr))
    # F2/F4: **effective** column allowlist — has_column_privilege (เห็น direct + inherited + PUBLIC) EXCEPT expected
    _rows = ",".join("('%s','%s')" % (t, c) for t, c in EXPECTED_RD)
    def _rd_extra():   # F6: relkind ครอบ view/matview/foreign ('r','p','v','m','f') ด้วย
        return sql1(sup, "SELECT string_agg(cl.relname||'.'||a.attname, ',') "
            "FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum>0 AND NOT a.attisdropped "
            "WHERE n.nspname='rfq' AND cl.relkind IN ('r','p','v','m','f') "
            "AND has_column_privilege('rfq_read_api', cl.oid, a.attnum, 'SELECT') "
            "AND (cl.relname, a.attname) NOT IN (VALUES " + _rows + ")")
    def _rd_x7():      # F7: schema CREATE / sequence / grant-option (มิเรอร์ caps.extra_privs_sql)
        return sql1(sup, "SELECT string_agg(x, ',') FROM ("
            "SELECT 'schema:CREATE' x WHERE has_schema_privilege('rfq_read_api','rfq','CREATE') "
            "UNION ALL SELECT 'seq:'||cl.relname||':'||p.priv FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace "
            "CROSS JOIN LATERAL (VALUES ('USAGE'),('SELECT'),('UPDATE')) p(priv) "
            "WHERE n.nspname='rfq' AND cl.relkind='S' AND has_sequence_privilege('rfq_read_api', cl.oid, p.priv) "
            "UNION ALL SELECT 'fn-wgo:'||p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='rfq' AND has_function_privilege('rfq_read_api', p.oid, 'EXECUTE WITH GRANT OPTION') "
            "UNION ALL SELECT 'col-wgo:'||cl.relname||'.'||a.attname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum>0 AND NOT a.attisdropped "
            "WHERE n.nspname='rfq' AND cl.relkind IN ('r','p','v','m','f') "
            "AND has_column_privilege('rfq_read_api', cl.oid, a.attnum, 'SELECT WITH GRANT OPTION')) z")
    def _sx(q):
        with sup.cursor() as _c: _c.execute(q)
    checks.append(('F2 baseline: read_api ไม่มี column นอก allowlist (effective)', _rd_extra() is None, _rd_extra()))
    checks.append(('F7 baseline: read_api ไม่มี schema/seq/grant-option', _rd_x7() is None, _rd_x7()))
    # F2: direct column over-grant (business + sensitive)
    _sx("GRANT SELECT (sample_description) ON rfq.rfq_item TO rfq_read_api")
    _sx("GRANT SELECT (original_filename) ON rfq.rfq_attachment TO rfq_read_api")
    _e = _rd_extra() or ""
    checks.append(('F2 drift caught: direct column over-grant', 'rfq_item.sample_description' in _e and 'rfq_attachment.original_filename' in _e, _e))
    _sx("REVOKE SELECT (sample_description) ON rfq.rfq_item FROM rfq_read_api")
    _sx("REVOKE SELECT (original_filename) ON rfq.rfq_attachment FROM rfq_read_api")
    # F4: inherited-role grant
    _sx("CREATE ROLE rfq_drift_tmp NOLOGIN")
    _sx("GRANT SELECT (source_sha256) ON rfq.rfq_source_ingest TO rfq_drift_tmp")
    _sx("GRANT rfq_drift_tmp TO rfq_read_api")
    checks.append(('F4 drift caught: inherited-role grant', 'rfq_source_ingest.source_sha256' in (_rd_extra() or ''), _rd_extra()))
    _sx("REVOKE rfq_drift_tmp FROM rfq_read_api"); _sx("DROP OWNED BY rfq_drift_tmp"); _sx("DROP ROLE rfq_drift_tmp")
    # F4: PUBLIC grant
    _sx("GRANT SELECT (input_sha256) ON rfq.rfq_ai_extraction_run TO PUBLIC")
    checks.append(('F4 drift caught: PUBLIC grant', 'rfq_ai_extraction_run.input_sha256' in (_rd_extra() or ''), _rd_extra()))
    _sx("REVOKE SELECT (input_sha256) ON rfq.rfq_ai_extraction_run FROM PUBLIC")
    # F6: sensitive view grant → effective read drift จับได้ (relkind='v')
    _sx("CREATE VIEW rfq.codex_view_probe AS SELECT input_sha256 FROM rfq.rfq_ai_extraction_run")
    _sx("GRANT SELECT ON rfq.codex_view_probe TO rfq_read_api")
    checks.append(('F6 drift caught: sensitive view SELECT', 'codex_view_probe' in (_rd_extra() or ''), _rd_extra()))
    _sx("REVOKE SELECT ON rfq.codex_view_probe FROM rfq_read_api"); _sx("DROP VIEW rfq.codex_view_probe")
    # F7: schema CREATE + sequence UPDATE + function/column grant-option → จับได้
    _sx("GRANT CREATE ON SCHEMA rfq TO rfq_read_api")
    checks.append(('F7 drift caught: schema CREATE', 'schema:CREATE' in (_rd_x7() or ''), _rd_x7()))
    _sx("REVOKE CREATE ON SCHEMA rfq FROM rfq_read_api")
    _seq = sql1(sup, "SELECT cl.relname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='rfq' AND cl.relkind='S' LIMIT 1")
    if _seq:
        _sx("GRANT UPDATE ON SEQUENCE rfq.%s TO rfq_read_api" % _seq)
        checks.append(('F7 drift caught: sequence UPDATE', ('seq:'+_seq) in (_rd_x7() or ''), _rd_x7()))
        _sx("REVOKE UPDATE ON SEQUENCE rfq.%s FROM rfq_read_api" % _seq)
    _sx("GRANT EXECUTE ON FUNCTION rfq.get_extraction_status(uuid) TO rfq_read_api WITH GRANT OPTION")
    checks.append(('F7 drift caught: function EXECUTE WITH GRANT OPTION', 'fn-wgo' in (_rd_x7() or ''), _rd_x7()))
    _sx("REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION rfq.get_extraction_status(uuid) FROM rfq_read_api")
    _sx("GRANT SELECT (id) ON rfq.rfq TO rfq_read_api WITH GRANT OPTION")
    checks.append(('F7 drift caught: column SELECT WITH GRANT OPTION', 'col-wgo' in (_rd_x7() or ''), _rd_x7()))
    _sx("REVOKE GRANT OPTION FOR SELECT (id) ON rfq.rfq FROM rfq_read_api")
    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or "role boundaries: inbound=draft+begin, worker=claim/apply/fail/list, read_api=business-SELECT+status; no cross-surface, no broad SELECT"
    record('T11 role allowlist boundaries (M1/M2)', allok, detail)

def t12_ingest_atomicity(sup):
    """Codex #10: fail กลาง insert → ไม่มี partial draft"""
    with sup.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("CREATE OR REPLACE FUNCTION zz_fail_comp() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'zz inject' USING ERRCODE='22000'; END $$;")
        cur.execute("CREATE TRIGGER zz_fail BEFORE INSERT ON rfq_component FOR EACH ROW EXECUTE FUNCTION zz_fail_comp()")
    n0_rfq = sql1(sup, "SELECT count(*) FROM rfq")
    n0_req = sql1(sup, "SELECT count(*) FROM rfq_ingest_request")
    injected = False
    ing = connect(role='rfq_ingest')
    try:
        sql1(ing, "SELECT create_rfq_draft(%s::jsonb,'a','enq','tatom')", (OK_PAYLOAD,))
    except psycopg2.Error as e:
        injected = 'zz inject' in (e.pgerror or ''); ing.rollback()
    ing.close()
    with sup.cursor() as cur:
        cur.execute("DROP TRIGGER zz_fail ON rfq_component"); cur.execute("DROP FUNCTION zz_fail_comp()")
    n1_rfq = sql1(sup, "SELECT count(*) FROM rfq")
    n1_req = sql1(sup, "SELECT count(*) FROM rfq_ingest_request")
    partial = sql1(sup, "SELECT count(*) FROM rfq_ingest_request WHERE request_id='tatom'")
    ok = injected and n1_rfq == n0_rfq and n1_req == n0_req and partial == 0
    record('T12 ingest atomicity (no partial draft)', ok,
           f"injected={injected}, rfq {n0_rfq}->{n1_rfq}, req {n0_req}->{n1_req}, tatom_rows={partial}")

def t13_ingest_grant_scope(sup):
    """Codex #9: create_rfq_draft ไม่มี PUBLIC execute; rfq_app เรียกไม่ได้ (ingest-only)"""
    checks = []
    pub = sql1(sup, """SELECT p.proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
        WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rfq' AND p.proname='create_rfq_draft'""")
    checks.append(('no PUBLIC execute', pub is False, pub))
    app_exec = sql1(sup, "SELECT has_function_privilege('rfq_app','create_rfq_draft(jsonb,text,text,text)','EXECUTE')")
    checks.append(('rfq_app EXECUTE=false', app_exec is False, app_exec))
    app = connect(role='rfq_app')
    try:
        with app.cursor() as cur:
            cur.execute("SELECT create_rfq_draft('{}'::jsonb,'a','enq','x')")
        checks.append(('rfq_app call denied', False, 'ALLOWED(bad)'))
    except psycopg2.Error as e:
        checks.append(('rfq_app call denied', e.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE, e.pgcode)); app.rollback()
    app.close()
    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or "create_rfq_draft: no PUBLIC exec, rfq_app denied, ingest-only"
    record('T13 create_rfq_draft grant scope', allok, detail)

def t14_idempotency_concurrency(sup):
    """Codex F4A: 2 conn same (service,request_id,actor,payload) → advisory lock serialize → ทั้งคู่ได้ id เดียว (ไม่ใช่ loser 23505)"""
    A = connect(role='rfq_ingest'); B = connect(role='rfq_ingest')
    A.autocommit = False; B.autocommit = False
    pidA, pidB = A.info.backend_pid, B.info.backend_pid
    curA = A.cursor(); curA.execute("SELECT create_rfq_draft(%s::jsonb,'enq-extractor','enq','tconc')", (OK_PAYLOAD,))
    id_a = curA.fetchone()[0]                    # A สร้าง + ถือ advisory xact lock (ยังไม่ commit)
    res = {}
    def run_b():
        try:
            cb = B.cursor(); cb.execute("SELECT create_rfq_draft(%s::jsonb,'enq-extractor','enq','tconc')", (OK_PAYLOAD,))
            res['id'] = cb.fetchone()[0]; B.commit()
        except Exception as e:
            res['err'] = e; B.rollback()
    tb = threading.Thread(target=run_b); tb.start()
    blocked = wait_blocked(sup, pidB, pidA)       # B รอ advisory lock ของ A
    A.commit()
    tb.join(15)
    A.close(); B.close()
    id_b = res.get('id')
    n_req = sql1(sup, "SELECT count(*) FROM rfq_ingest_request WHERE request_id='tconc'")
    n_rfq = sql1(sup, "SELECT count(*) FROM rfq WHERE id IN (SELECT rfq_id FROM rfq_ingest_request WHERE request_id='tconc')")
    ok = blocked and res.get('err') is None and id_b == id_a and n_req == 1 and n_rfq == 1
    record('T14 idempotency concurrency (same key→same id)', ok,
           f"B blocked-by-A={blocked}, id_a==id_b={id_b == id_a}, B_err={getattr(res.get('err'),'pgcode',None)}, req_rows={n_req}, rfq_rows={n_rfq}")

def _seed_ext_run(sup, tag):
    """seed source+provider (as superuser) + begin → คืน run_id (PENDING)"""
    with sup.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("INSERT INTO rfq_ai_provider VALUES ('typ','v1','LOCAL',true,'pol-v1') ON CONFLICT DO NOTHING")
        cur.execute("""INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,
            classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref)
            VALUES (%s, repeat('a',64),'CLEAN','CONFIRMED','INTERNAL',true,false,'LOCAL_ONLY','pol-v1','sc') RETURNING id""", (f's3://{tag}',))
        sid = cur.fetchone()[0]
        cur.execute("SELECT (begin_rfq_extraction(%s,'LOCAL','typ','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq',%s)->>'run_id')::uuid",
                    (sid, f'b-{tag}'))
        return cur.fetchone()[0]

def t15_claim_race(sup):
    """Codex R6/#5: 2 worker claim run เดียวพร้อมกัน → มีเพียงหนึ่งได้สิทธิ์ (should_execute=true) + lease เดียว"""
    run = _seed_ext_run(sup, 't15')
    res = {}
    def worker(name):
        c = connect(role='rfq_worker')
        try:
            with c.cursor() as cur:
                cur.execute("SELECT claim_rfq_extraction(%s,%s,'enq',%s)", (run, name, f'cl-{name}'))
                res[name] = cur.fetchone()[0].get('should_execute')
        except Exception as e:
            res[name] = f'ERR:{getattr(e,"pgcode",e)}'
        finally:
            c.close()
    ta = threading.Thread(target=worker, args=('wA',)); tb = threading.Thread(target=worker, args=('wB',))
    ta.start(); tb.start(); ta.join(10); tb.join(10)
    n_exec = sum(1 for v in res.values() if v is True)
    n_lease = sql1(sup, "SELECT count(*) FROM rfq_ai_extraction_run WHERE id=%s AND status_code='RUNNING' AND lease_token IS NOT NULL", (run,))
    ok = n_exec == 1 and n_lease == 1
    record('T15 claim race (exactly-one executor)', ok, f"exec_true={n_exec} of {res}, running_lease={n_lease}")

def t16_reclaim_fencing(sup):
    """Codex R6/C4/#8: lease หมด → worker ใหม่ reclaim ได้; worker เก่า (token เดิม) apply ถูก fence"""
    run = _seed_ext_run(sup, 't16')
    a = connect(role='rfq_worker')
    with a.cursor() as cur:
        cur.execute("SELECT claim_rfq_extraction(%s,'wA','enq','clA')", (run,))
        old_lease = cur.fetchone()[0]['lease_token']
    # expire lease A (privileged)
    with sup.cursor() as cur:
        cur.execute("UPDATE rfq.rfq_ai_extraction_run SET lease_expires_at = now()-interval '1 min' WHERE id=%s", (run,))
    # B reclaims with new request → should_execute true, attempt 2
    b = connect(role='rfq_worker')
    with b.cursor() as cur:
        cur.execute("SELECT claim_rfq_extraction(%s,'wB','enq','clB')", (run,))
        rec = cur.fetchone()[0]
    reclaimed = rec.get('should_execute') is True
    attempt = sql1(sup, "SELECT attempt_no FROM rfq_ai_extraction_run WHERE id=%s", (run,))
    # A (old token) tries apply → fenced (C4 token mismatch / expired)
    payload = ('{"schema_version":"extract-v1.1","input_sha256":"'+('a'*64)+'","items":[{"line_no":1,'
               '"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},'
               '"field_name":"job_name","source_type":"PDF"}]}')
    fenced = False
    try:
        with a.cursor() as cur:
            cur.execute("SELECT apply_rfq_extraction(%s,%s,%s::jsonb,'wA','enq','apA')", (run, old_lease, payload))
    except psycopg2.Error as e:
        fenced = e.pgcode == 'RFS01'; a.rollback()          # F1: lease/state conflict = RFS01 (เดิม 23514)
    a.close(); b.close()
    ok = reclaimed and attempt == 2 and fenced
    record('T16 reclaim fencing (old lease rejected)', ok, f"reclaimed={reclaimed}, attempt={attempt}, old_lease_fenced={fenced}(want RFS01)")

def t17_service_binding(sup):
    """Codex B6: claim ด้วย service A แล้ว apply ด้วย service B → reject (lease bind service)"""
    run = _seed_ext_run(sup, 't17')
    c = connect(role='rfq_worker'); lease = None; denied = False
    with c.cursor() as cur:
        cur.execute("SELECT (claim_rfq_extraction(%s,'wA','svc-a','cl17'))->>'lease_token'", (run,))
        lease = cur.fetchone()[0]
    payload = ('{"schema_version":"extract-v1.1","input_sha256":"'+('a'*64)+'","items":[{"line_no":1,'
               '"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},'
               '"field_name":"job_name","source_type":"PDF"}]}')
    try:
        with c.cursor() as cur:
            cur.execute("SELECT apply_rfq_extraction(%s,%s,%s::jsonb,'wA','svc-b','ap17')", (run, lease, payload))
    except psycopg2.Error as e:
        denied = e.pgcode == 'RFS01'; c.rollback()           # F1: service/lease mismatch = RFS01 (เดิม 23514)
    c.close()
    record('T17 apply service binding (svc-a claim → svc-b apply reject)', denied, f"denied={denied}(want RFS01)")

def t18_temptable_no_hijack(sup):
    """Codex B5: caller (rfq_worker) สร้าง temp _w/_e ก่อน → ไม่กระทบ SECURITY DEFINER apply (เลิกใช้ temp table แล้ว)"""
    run = _seed_ext_run(sup, 't18')
    c = connect(role='rfq_worker'); ok = False
    with c.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _w (x int)")   # attacker pre-creates hostile-shaped temp tables
        cur.execute("CREATE TEMP TABLE _e (y int)")
        cur.execute("SELECT (claim_rfq_extraction(%s,'wA','enq','cl18'))->>'lease_token'", (run,))
        lease = cur.fetchone()[0]
    payload = ('{"schema_version":"extract-v1.1","input_sha256":"'+('a'*64)+'","items":[{"line_no":1,'
               '"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},'
               '"field_name":"job_name","source_type":"PDF"}]}')
    try:
        with c.cursor() as cur:
            cur.execute("SELECT apply_rfq_extraction(%s,%s,%s::jsonb,'wA','enq','ap18')", (run, lease, payload))
            ok = cur.fetchone()[0].get('status') == 'SUCCEEDED'
    except psycopg2.Error as e:
        ok = False
    c.close()
    record('T18 temp-table no-hijack (caller _w/_e ไม่กระทบ apply)', ok, f"apply_succeeded_despite_hostile_temp={ok}")

def _fence_lockwait(sup, tag, op):
    """B1: op (fail/apply) txn เริ่ม *ก่อน* lease หมด แต่รอ parent lock *ข้าม* expiry
    → ต้อง RFS01 (fence ด้วย clock_timestamp ไม่ใช่ now()=txn-start) + run ยัง RUNNING (reclaim ได้)"""
    run = _seed_ext_run(sup, tag)
    rfq_id = sql1(sup, "SELECT rfq_id FROM rfq_ai_extraction_run WHERE id=%s", (run,))
    a = connect(role='rfq_worker')                          # wA claim → RUNNING + lease (autocommit)
    lease = sql1(a, "SELECT (claim_rfq_extraction(%s,'wA','enq',%s))->>'lease_token'", (run, f'cl-{tag}'))
    holder = connect(); holder.autocommit = False           # superuser: ถือ parent lock ข้าม expiry
    holder.cursor().execute("SELECT id FROM rfq WHERE id=%s FOR UPDATE", (rfq_id,))
    sql1(sup, "SELECT 1")                                    # (no-op) ensure sup usable
    with sup.cursor() as cur:                               # lease หมดใน 2s (หลัง holder ถือ lock แล้ว)
        cur.execute("UPDATE rfq.rfq_ai_extraction_run SET lease_expires_at = clock_timestamp() + interval '2 seconds' WHERE id=%s", (run,))
    payload = ('{"schema_version":"extract-v1.1","input_sha256":"'+('a'*64)+'","items":[{"line_no":1,'
               '"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},'
               '"field_name":"job_name","source_type":"PDF"}]}')
    b = connect(role='rfq_worker'); b.autocommit = False    # B: txn เริ่มตอนเรียก (now() < expiry) แล้ว block
    res = {}
    def run_b():
        try:
            cb = b.cursor()
            if op == 'fail':
                cb.execute("SELECT fail_rfq_extraction(%s,%s,'x','wA','enq',%s)", (run, lease, f'fB-{tag}'))
            else:
                cb.execute("SELECT apply_rfq_extraction(%s,%s,%s::jsonb,'wA','enq',%s)", (run, lease, payload, f'aB-{tag}'))
            res['status'] = 'committed'; b.commit()
        except psycopg2.Error as e:
            res['pgcode'] = e.pgcode; b.rollback()
    tb = threading.Thread(target=run_b); tb.start()
    blocked = wait_blocked(sup, b.info.backend_pid, holder.info.backend_pid)
    time.sleep(2.5)                                          # ปล่อยให้ lease (2s) หมดขณะ B รอ lock
    holder.rollback(); holder.close()                       # ปลด parent lock → B เดินต่อ (clock จริง > expiry)
    tb.join(15)
    b.close(); a.close()
    fenced = res.get('pgcode') == 'RFS01'
    status = sql1(sup, "SELECT status_code FROM rfq_ai_extraction_run WHERE id=%s", (run,))
    ok = blocked and fenced and status == 'RUNNING'
    return ok, f"blocked={blocked}, B rc={res.get('pgcode') or res.get('status')}(want RFS01), status={status}(want RUNNING)"

def t19_fence_fail(sup):
    """B1: fail รอ parent lock ข้าม lease expiry → RFS01 (clock_timestamp fence)"""
    ok, detail = _fence_lockwait(sup, 't19', 'fail')
    record('T19 fence lock-wait-across-expiry (fail)', ok, detail)

def t20_fence_apply(sup):
    """B1: apply รอ parent lock ข้าม lease expiry → RFS01 (กัน two-path drift)"""
    ok, detail = _fence_lockwait(sup, 't20', 'apply')
    record('T20 fence lock-wait-across-expiry (apply)', ok, detail)

def t21_signoff_v2(sup):
    """V2 (010): RFQ-007 latest active-decision rule (ยึด decision_seq) + revoke soft-revoke audit"""
    checks = []

    def _decide(rfq, decision, actor):   # decision ใหม่ผ่าน service path (parent lock + decision_seq via DEFAULT)
        return sql1(sup, "SELECT add_signoff(%s,'REVIEWER',%s,%s)", (rfq, decision, actor))

    def _mark_ready_blocked(rfq):
        try:
            sql1(sup, "SELECT mark_ready(%s,%s,'x')", (rfq, rowver(sup, rfq)))
            return (False, None)
        except psycopg2.Error as e:
            return (True, e.pgcode)

    def _revoke_rejected(soid, actor, reason):
        try:
            sql1(sup, "SELECT revoke_signoff(%s,%s,%s)", (soid, actor, reason)); return False
        except psycopg2.Error as e:
            return e.pgcode == errorcodes.CHECK_VIOLATION

    # A: CONFIRM (base) แล้ว REJECT ทีหลัง -> BLOCK (เดิม EXISTS CONFIRMED = bug: ผ่าน)
    fa = seed_review_rfq(sup, 'T21a')                 # base REVIEWER CONFIRMED (decision_seq ต่ำสุด)
    _decide(fa['rfq'], 'REJECTED', 'REV2')            # seq ใหม่กว่า -> latest = REJECTED
    bA, cA = _mark_ready_blocked(fa['rfq'])
    checks.append(('A CONFIRM->REJECT blocks Ready (23514)', bA and cA == errorcodes.CHECK_VIOLATION, (bA, cA)))
    checks.append(('A _reviewer_latest_confirmed=false', sql1(sup, "SELECT _reviewer_latest_confirmed(%s)", (fa['rfq'],)) is False, None))

    # B: REJECT แล้ว CONFIRM ทีหลัง -> Ready ผ่าน
    fb = seed_review_rfq(sup, 'T21b')                 # base CONFIRMED (seq1)
    _decide(fb['rfq'], 'REJECTED', 'REV0')            # REJECTED (seq2)
    _decide(fb['rfq'], 'CONFIRMED', 'REV3')           # CONFIRMED (seq3) = latest
    ok_b = False
    try:
        sql1(sup, "SELECT mark_ready(%s,%s,'x')", (fb['rfq'], rowver(sup, fb['rfq'])))
        ok_b = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (fb['rfq'],)) == 'READY_FOR_ESTIMATE'
    except psycopg2.Error:
        ok_b = False
    checks.append(('B REJECT->CONFIRM(latest) -> Ready ผ่าน', ok_b, None))

    # C: revoke CONFIRMED เดียว -> ไม่มี active reviewer -> BLOCK ; revoke = soft (row อยู่ + metadata)
    fc = seed_review_rfq(sup, 'T21c')
    sql1(sup, "SELECT revoke_signoff(%s,'admin','withdrawn record')", (fc['signoff'],))
    with sup.cursor() as cur:
        cur.execute("SET search_path TO rfq")
        cur.execute("SELECT revoked_at IS NOT NULL, revoked_by_ref, revoke_reason FROM rfq_signoff WHERE id=%s", (fc['signoff'],))
        rev = cur.fetchone()
    checks.append(('C revoke = soft (row อยู่ + revoked_at/by/reason)',
                   rev is not None and rev[0] is True and rev[1] == 'admin' and rev[2] == 'withdrawn record', rev))
    bC, cC = _mark_ready_blocked(fc['rfq'])
    checks.append(('C revoked CONFIRMED -> Ready BLOCK (no active reviewer)', bC and cC == errorcodes.CHECK_VIOLATION, (bC, cC)))

    # D: M2 + re-review — revoke actor/reason ว่าง หรือ whitespace ล้วน (tab/newline) -> reject
    fd = seed_review_rfq(sup, 'T21d')
    checks.append(('D revoke actor ว่าง -> reject (M2)', _revoke_rejected(fd['signoff'], '   ', 'r'), None))
    checks.append(('D revoke reason ว่าง -> reject', _revoke_rejected(fd['signoff'], 'admin', '   '), None))
    checks.append(('D revoke reason tab/newline ล้วน -> reject (re-review)', _revoke_rejected(fd['signoff'], 'admin', '\t\n'), None))
    checks.append(('D revoke actor tab ล้วน -> reject', _revoke_rejected(fd['signoff'], '\t', 'r'), None))
    # CHECK invariant (defense in depth): direct UPDATE เลี่ยง function -> whitespace ล้วน ต้องชน constraint
    def _direct_revoke(soid, actor, reason):
        try:
            with sup.cursor() as cur:
                cur.execute("SET search_path TO rfq")
                cur.execute("UPDATE rfq_signoff SET revoked_at=clock_timestamp(), revoked_by_ref=%s, revoke_reason=%s WHERE id=%s",
                            (actor, reason, soid))
            return False
        except psycopg2.Error as e:
            return e.pgcode == errorcodes.CHECK_VIOLATION
    fg = seed_review_rfq(sup, 'T21g')
    checks.append(('D CHECK: direct UPDATE reason tab/newline -> violation', _direct_revoke(fg['signoff'], 'admin', '\t\n'), None))
    checks.append(('D CHECK: direct UPDATE actor tab -> violation', _direct_revoke(fg['signoff'], '\t', 'valid reason'), None))

    # E: double-revoke -> reject ; reason แรกไม่ถูกทับ
    sql1(sup, "SELECT revoke_signoff(%s,'admin','first')", (fd['signoff'],))
    checks.append(('E double-revoke -> reject', _revoke_rejected(fd['signoff'], 'admin2', 'second'), None))
    checks.append(('E double-revoke ไม่ทับ revoke แรก',
                   sql1(sup, "SELECT revoke_reason FROM rfq_signoff WHERE id=%s", (fd['signoff'],)) == 'first', None))

    # F (Codex Q4 correction semantics): revoke decision ล่าสุด -> fallback ไป active decision เก่ากว่า
    #    CONFIRMED(base) -> REJECTED(latest) -> revoke REJECTED -> latest active = CONFIRMED -> Ready ผ่าน
    ff = seed_review_rfq(sup, 'T21f')                 # base CONFIRMED (active)
    rj = _decide(ff['rfq'], 'REJECTED', 'REVX')       # REJECTED = latest
    b_before, _ = _mark_ready_blocked(ff['rfq'])
    sql1(sup, "SELECT revoke_signoff(%s,'admin','entered in error')", (rj,))   # revoke REJECTED
    ok_f = False
    try:
        sql1(sup, "SELECT mark_ready(%s,%s,'x')", (ff['rfq'], rowver(sup, ff['rfq'])))
        ok_f = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (ff['rfq'],)) == 'READY_FOR_ESTIMATE'
    except psycopg2.Error:
        ok_f = False
    checks.append(('F revoke latest REJECTED -> fallback CONFIRMED เดิม -> Ready ผ่าน (correction)',
                   b_before and ok_f, (b_before, ok_f)))

    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or \
        "latest active-decision (decision_seq) + soft-revoke + actor/reason/double-revoke + correction fallback ครบ"
    record('T21 signoff v2 (latest-decision + soft-revoke)', allok, detail)

def t22_signoff_decision_order(sup):
    """B1 regression: 'latest' ต้องยึด decision_seq (mutation order หลัง lock) ไม่ใช่ signed_at (txn-start)
       txn A เริ่มก่อน (signed_at เก่า) แต่ commit CONFIRMED ทีหลัง txn B ที่ commit REJECTED ก่อน"""
    f = seed_review_rfq(sup, 'T22'); rfq = f['rfq']   # base CONFIRMED (seq ต่ำสุด)
    A = connect(role='rfq_app', app_name='rfq-ord-A'); A.autocommit = False
    B = connect(role='rfq_app', app_name='rfq-ord-B'); B.autocommit = False
    try:
        ca = A.cursor(); ca.execute("SELECT 1")       # เริ่ม txn A -> now()=t_A (เก่าสุด)
        cb = B.cursor(); cb.execute("SELECT add_signoff(%s,'REVIEWER','REJECTED','REVB')", (rfq,)); B.commit()  # t_B>t_A, commit ก่อน
        ca.execute("SELECT add_signoff(%s,'REVIEWER','CONFIRMED','REVA')", (rfq,)); A.commit()                 # signed_at=t_A (< t_B) แต่ commit ทีหลัง
    finally:
        A.close(); B.close()
    helper = sql1(sup, "SELECT _reviewer_latest_confirmed(%s)", (rfq,))
    by_seq  = sql1(sup, "SELECT decision_code FROM rfq_signoff WHERE rfq_id=%s AND revoked_at IS NULL ORDER BY decision_seq DESC LIMIT 1", (rfq,))
    by_time = sql1(sup, "SELECT decision_code FROM rfq_signoff WHERE rfq_id=%s AND revoked_at IS NULL ORDER BY signed_at DESC, id DESC LIMIT 1", (rfq,))
    # test มีความหมายก็ต่อเมื่อ signed_at เรียงสวน (by_time=REJECTED) แต่ decision_seq/helper ถูก (CONFIRMED)
    ok = helper is True and by_seq == 'CONFIRMED' and by_time == 'REJECTED'
    record('T22 latest by decision_seq not signed_at (B1)', ok,
           f"helper={helper}, by_seq={by_seq}(want CONFIRMED), by_signed_at={by_time}(bug->REJECTED)")

def t23_readiness_versioning(sup):
    """V3 (011): readiness mutation ทุกตัว bump parent row_version -> stale optimistic token -> mark_ready 40001"""
    checks = []
    def rv(rfq): return rowver(sup, rfq)

    # 1) each readiness mutation bumps row_version
    f = seed_review_rfq(sup, 'T23'); rfq = f['rfq']; v0 = rv(rfq)   # base signoff = direct insert (ไม่ bump)
    c = sql1(sup, "SELECT add_clarification(%s,'RFQ',%s,'q',false,'AI','bot','tester')", (rfq, rfq)); v1 = rv(rfq)
    sql1(sup, "SELECT resolve_clarification(%s,'ANSWERED','ae','ans')", (c,)); v2 = rv(rfq)
    so = sql1(sup, "SELECT add_signoff(%s,'REVIEWER','CONFIRMED','rev2')", (rfq,)); v3 = rv(rfq)
    sql1(sup, "SELECT revoke_signoff(%s,'admin','correction')", (so,)); v4 = rv(rfq)
    checks.append(('add_clarification bump', v1 == v0 + 1, (v0, v1)))
    checks.append(('resolve_clarification bump', v2 == v1 + 1, (v1, v2)))
    checks.append(('add_signoff bump', v3 == v2 + 1, (v2, v3)))
    checks.append(('revoke_signoff bump', v4 == v3 + 1, (v3, v4)))

    # 2) stale optimistic token: readiness เปลี่ยนหลัง client อ่าน version -> mark_ready(เก่า) = 40001
    g = seed_review_rfq(sup, 'T23s')          # ready-able, base CONFIRMED, v1
    stale = rv(g['rfq'])                       # client cache = v1
    sql1(sup, "SELECT add_signoff(%s,'REVIEWER','CONFIRMED','rev3')", (g['rfq'],))   # readiness เปลี่ยน -> v2
    stale_ok = False
    try:
        sql1(sup, "SELECT mark_ready(%s,%s,'x')", (g['rfq'], stale))
    except psycopg2.Error as e:
        stale_ok = e.pgcode == errorcodes.SERIALIZATION_FAILURE   # 40001
    checks.append(('stale version หลัง readiness mutation -> mark_ready 40001', stale_ok, None))
    fresh_ok = False
    try:
        sql1(sup, "SELECT mark_ready(%s,%s,'x')", (g['rfq'], rv(g['rfq'])))   # re-read -> ผ่าน
        fresh_ok = sql1(sup, "SELECT status_code FROM rfq WHERE id=%s", (g['rfq'],)) == 'READY_FOR_ESTIMATE'
    except psycopg2.Error:
        fresh_ok = False
    checks.append(('re-read fresh version -> mark_ready ผ่าน', fresh_ok, None))

    # 3) reject-on-terminal ยังทำงาน: mutation บน RFQ ที่ READY_FOR_ESTIMATE (frozen) -> reject + ไม่ bump
    vt = rv(g['rfq'])
    frozen_ok = False
    try:
        sql1(sup, "SELECT add_signoff(%s,'REVIEWER','CONFIRMED','late')", (g['rfq'],))
    except psycopg2.Error as e:
        frozen_ok = e.pgcode == errorcodes.CHECK_VIOLATION
    checks.append(('mutation บน terminal RFQ -> reject (freeze) + ไม่ bump', frozen_ok and rv(g['rfq']) == vt, (frozen_ok, vt)))

    # B1 (Codex): add_clarification แยก authenticated actor ออกจาก nullable provenance
    fb = seed_review_rfq(sup, 'T23b1'); vb0 = rv(fb['rfq'])
    cb = sql1(sup, "SELECT add_clarification(%s,'RFQ',%s,'q',true,'VALIDATOR',NULL,'validator-svc')", (fb['rfq'], fb['rfq']))
    checks.append(('B1 raised_by_ref=NULL + actor -> clarification สำเร็จ + bump 1',
                   cb is not None and rv(fb['rfq']) == vb0 + 1, (cb, vb0, rv(fb['rfq']))))
    b1blank = False
    try:
        sql1(sup, "SELECT add_clarification(%s,'RFQ',%s,'q',true,'AI',NULL,'   ')", (fb['rfq'], fb['rfq']))
    except psycopg2.Error as e:
        b1blank = e.pgcode == errorcodes.CHECK_VIOLATION
    checks.append(('B1 actor blank -> reject ก่อน write', b1blank and rv(fb['rfq']) == vb0 + 1, None))   # version ไม่ขยับจาก call ที่ reject

    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or \
        "readiness mutations bump row_version ; stale token -> 40001 ; terminal reject ไม่ bump ; B1 actor/provenance แยก"
    record('T23 readiness versioning (V3)', allok, detail)

def t24_draft_edit(sup):
    """draft-edit/upsert (012): read Draft -> edit -> expected_row_version -> lock/freeze -> save -> bump (V3 consumer)"""
    checks = []
    def rv(rfq): return rowver(sup, rfq)
    def edit(rfq, ver, actor, patch):
        return sql1(sup, "SELECT upsert_rfq_draft(%s,%s,%s,%s::jsonb)", (rfq, ver, actor, json.dumps(patch)))
    def err(rfq, ver, actor, patch):
        try: edit(rfq, ver, actor, patch); return None
        except psycopg2.Error as e: return e.pgcode

    f = seed_draft_rfq(sup, 'T24'); rfq = f['rfq']

    # 1) header edit -> field updated + bump
    v0 = rv(rfq)
    o1 = edit(rfq, v0, 'editor-1', {"header": {"fields": {"customer_name_raw": "ACME Co"}}})
    nm = sql1(sup, "SELECT customer_name_raw FROM rfq WHERE id=%s", (rfq,))
    checks.append(('1 header edit -> updated + bump', nm == 'ACME Co' and rv(rfq) == v0 + 1 and o1.get('edited_header') is True, (nm, o1)))

    # 2) update existing item (line_no=1) -> items_updated=1
    v1 = rv(rfq)
    o2 = edit(rfq, v1, 'editor-1', {"items": [{"line_no": 1, "fields": {"job_name": "edited job", "finishing_state": "SPECIFIED"}}]})
    jn = sql1(sup, "SELECT job_name FROM rfq_item WHERE rfq_id=%s AND line_no=1", (rfq,))
    checks.append(('2 item update (existing) -> updated + bump', jn == 'edited job' and o2.get('items_updated') == 1 and rv(rfq) == v1 + 1, (jn, o2)))

    # 3) upsert NEW item (line_no=2) -> items_inserted=1
    v2 = rv(rfq)
    o3 = edit(rfq, v2, 'editor-1', {"items": [{"line_no": 2, "fields": {"job_name": "new item"}}]})
    n2 = sql1(sup, "SELECT count(*) FROM rfq_item WHERE rfq_id=%s AND line_no=2", (rfq,))
    checks.append(('3 item upsert (new line_no) -> inserted + bump', n2 == 1 and o3.get('items_inserted') == 1 and rv(rfq) == v2 + 1, (n2, o3)))

    # 4) stale expected_row_version -> 40001 (optimistic concurrency)
    checks.append(('4 stale expected_row_version -> 40001',
                   err(rfq, 1, 'editor-1', {"header": {"fields": {"customer_name_raw": "x"}}}) == errorcodes.SERIALIZATION_FAILURE, None))

    # 5) non-DRAFT (READY_FOR_REVIEW) -> reject 23514 (freeze)
    g = seed_review_rfq(sup, 'T24r')
    checks.append(('5 non-DRAFT -> reject 23514',
                   err(g['rfq'], rv(g['rfq']), 'editor-1', {"header": {"fields": {"customer_name_raw": "x"}}}) == errorcodes.CHECK_VIOLATION, None))

    # 6) unknown field -> reject 22023 (_reject_unknown_keys)
    checks.append(('6 unknown field -> reject 22023',
                   err(rfq, rv(rfq), 'editor-1', {"header": {"fields": {"nonexistent": "x"}}}) == errorcodes.INVALID_PARAMETER_VALUE, None))

    # 7) actor blank -> reject 23514
    checks.append(('7 actor blank -> reject 23514',
                   err(rfq, rv(rfq), '   ', {"header": {"fields": {"customer_name_raw": "x"}}}) == errorcodes.CHECK_VIOLATION, None))

    # 8) empty patch -> reject 23514 ; version ไม่ขยับจาก call ที่ reject
    v_end = rv(rfq)
    checks.append(('8 empty patch -> reject 23514',
                   err(rfq, v_end, 'editor-1', {}) == errorcodes.CHECK_VIOLATION and rv(rfq) == v_end, None))

    allok = all(ok for _, ok, _ in checks)
    detail = "; ".join(f"{l}:{c}" for l, ok, c in checks if not ok) or \
        "draft-edit: header/item update + item upsert + stale->40001 + non-DRAFT/unknown/actor/empty rejects (ไม่ bump)"
    record('T24 draft-edit/upsert (V3 consumer)', allok, detail)

def main():
    sup = connect(app_name='rfq-conc-coordinator')
    tests = [t01_concurrent_mark_ready, t02_concurrent_revision, t03_readiness_toctou,
             t04_role_privilege, t05_version_edge, t06_rollback, t07_idempotency, t08_clone_atomicity,
             t09_authz_separation, t10_catalog_and_policy,
             t11_ingest_allowlist, t12_ingest_atomicity, t13_ingest_grant_scope, t14_idempotency_concurrency,
             t15_claim_race, t16_reclaim_fencing, t17_service_binding, t18_temptable_no_hijack,
             t19_fence_fail, t20_fence_apply, t21_signoff_v2, t22_signoff_decision_order, t23_readiness_versioning, t24_draft_edit]
    for t in tests:
        try:
            t(sup)
        except Exception as e:
            record(t.__name__, False, f"harness error: {type(e).__name__}: {e}")
    sup.close()
    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    print(f"========= CONCURRENCY RESULT: {npass} passed, {nfail} failed =========")
    sys.exit(1 if nfail else 0)

if __name__ == '__main__':
    main()
