"""
Unit test ของ p2_provenance — **SQLite authority** event ledger (pure/offline)
append_event state machine · strict reconcile · terminal schema · durable transaction ·
file identity (hard-link alias) · lock contention/crash-safe · JSONL evidence export

    python test_p2_provenance.py
"""
import hashlib
import io
import json as _json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_provenance as PV

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True

BASE = tempfile.mkdtemp(prefix="p2prov-")
def _log(n): return os.path.join(BASE, n)
def _ev(aid, rid, event, **kw):
    r = {"attempt_id": aid, "run_id": rid, "event": event}
    if event == "STARTED":
        r["started_at"] = "2026-08-06T09:00:00+07:00"
    elif event in ("PUBLISHED", "DEGRADED", "FAILED"):
        r["status"] = event
        r["finished_at"] = "2026-08-06T09:03:00+07:00"
        if event == "PUBLISHED":
            r.update({"artifact_sha256": "a" * 64, "evidence_body_sha256": "a" * 64,
                      "run_receipt_sha256": "a" * 64, "capability": {}, "path": "/x.bundle.json"})
        elif event == "DEGRADED":
            r["capability"] = {}
        elif event == "FAILED":
            r["phase"] = "run"
    r.update(kw)
    return r


# ── ledger STARTED → terminal + reconcile (append order) ──────────────────────
L = _log("led.db")
PV.append_event(L, _ev("att-00000001", "run-1", "STARTED"))
PV.append_event(L, _ev("att-00000001", "run-1", "PUBLISHED", status="PUBLISHED"))
PV.append_event(L, _ev("att-00000002", "run-2", "STARTED"))          # ตายกลางทาง (ไม่มี terminal)
recs = PV.read_provenance(L)
check("ledger read ตามลำดับ append", [r["event"] for r in recs] == ["STARTED", "PUBLISHED", "STARTED"])
rc = PV.reconcile(recs)
check("reconcile att-1 -> PUBLISHED, att-2 -> INCOMPLETE", rc["att-00000001"] == "PUBLISHED" and rc["att-00000002"] == "INCOMPLETE")
check("read_provenance(db ไม่มี) -> []", PV.read_provenance(_log("nope.db")) == [])

# ── B3.1: state machine (บังคับที่ ledger boundary ใน transaction) ─────────────
S = _log("sm.db")
PV.append_event(S, _ev("s1", "r", "STARTED"))
check("duplicate STARTED -> ProvenanceError", raises(lambda: PV.append_event(S, _ev("s1", "r", "STARTED")), PV.ProvenanceError))
check("terminal without STARTED -> ProvenanceError", raises(lambda: PV.append_event(S, _ev("s2", "r", "PUBLISHED", status="PUBLISHED")), PV.ProvenanceError))
PV.append_event(S, _ev("s1", "r", "PUBLISHED", status="PUBLISHED"))
check("duplicate terminal -> ProvenanceError", raises(lambda: PV.append_event(S, _ev("s1", "r", "FAILED", status="FAILED")), PV.ProvenanceError))
check("bad transition rollback -> ไม่มี row ปนใน ledger (ยังอ่านได้ปกติ)", [r["event"] for r in PV.read_provenance(S)] == ["STARTED", "PUBLISHED"])
R = _log("rid.db")
PV.append_event(R, _ev("a1", "r1", "STARTED"))
check("terminal run_id != STARTED -> ProvenanceError", raises(lambda: PV.append_event(R, _ev("a1", "r2", "PUBLISHED", status="PUBLISHED")), PV.ProvenanceError))
check("attempt_id reuse ข้าม run -> duplicate STARTED error", raises(lambda: PV.append_event(R, _ev("a1", "r3", "STARTED")), PV.ProvenanceError))

# ── serialize guard: not-dict / NaN / oversize (ก่อนแตะ db) ───────────────────
check("record ไม่ใช่ dict -> TypeError", raises(lambda: PV._append_raw(L, ["x"]), TypeError))
check("NaN -> ProvenanceError (allow_nan=False)", raises(lambda: PV._append_raw(_log("nan.db"), {"x": float("nan")}), PV.ProvenanceError))
check("oversize -> ProvenanceError", raises(lambda: PV._append_raw(_log("big.db"), {"x": "y" * (PV.MAX_RECORD_BYTES + 10)}), PV.ProvenanceError))

# ── UNIQUE partial index (defense-in-depth) : raw bypass state machine -> integrity error ──
UX = _log("ux.db")
PV._append_raw(UX, _ev("u1", "r", "STARTED"))
check("UNIQUE index: raw duplicate STARTED (bypass reducer) -> ProvenanceError (integrity)",
      raises(lambda: PV._append_raw(UX, _ev("u1", "r", "STARTED")), PV.ProvenanceError))
PV._append_raw(UX, _ev("u1", "r", "PUBLISHED", status="PUBLISHED"))
check("UNIQUE index: raw duplicate terminal -> ProvenanceError (integrity)",
      raises(lambda: PV._append_raw(UX, _ev("u1", "r", "FAILED", status="FAILED")), PV.ProvenanceError))

# ── strict reconcile (order-sensitive) ────────────────────────────────────────
check("reconcile STARTED-only -> INCOMPLETE", PV.reconcile([_ev("x", "r", "STARTED")]) == {"x": "INCOMPLETE"})
check("reconcile terminal-only -> ProvenanceError", raises(lambda: PV.reconcile([_ev("y", "r", "PUBLISHED", status="PUBLISHED")]), PV.ProvenanceError))
check("reconcile duplicate terminal -> ProvenanceError", raises(lambda: PV.reconcile([_ev("z", "r", "STARTED"), _ev("z", "r", "PUBLISHED", status="PUBLISHED"), _ev("z", "r", "FAILED", status="FAILED")]), PV.ProvenanceError))
check("reconcile PUBLISHED-ก่อน-STARTED -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o1", "r", "PUBLISHED", status="PUBLISHED"), _ev("o1", "r", "STARTED")]), PV.ProvenanceError))
check("reconcile terminal run_id != STARTED -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o2", "r1", "STARTED"), _ev("o2", "r2", "FAILED", status="FAILED")]), PV.ProvenanceError))
check("reconcile status != event -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o3", "r", "STARTED"), _ev("o3", "r", "PUBLISHED", status="FAILED")]), PV.ProvenanceError))
check("append_event = reducer เดียวกับ reconcile (status != event) -> ProvenanceError", raises(lambda: PV.append_event(_log("se.db"), _ev("se1", "r", "STARTED")) or PV.append_event(_log("se.db"), _ev("se1", "r", "PUBLISHED", status="FAILED")), PV.ProvenanceError))

# ── M3.2-A: ledger enforce terminal schema (PUBLISHED ต้องมี binding) ──────────
MA = _log("m32a.db")
PV.append_event(MA, _ev("m1", "r", "STARTED"))
check("M3.2-A: PUBLISHED ไม่มี binding -> ProvenanceError (ledger enforce schema)", raises(lambda: PV.append_event(MA, {"attempt_id": "m1", "run_id": "r", "event": "PUBLISHED", "status": "PUBLISHED", "finished_at": "2026-08-06T09:03:00+07:00"}), PV.ProvenanceError))
check("M3.2-A: reconcile minimal PUBLISHED (raw) -> ProvenanceError", raises(lambda: PV.reconcile([{"attempt_id": "z", "run_id": "r", "event": "STARTED", "started_at": "2026-08-06T09:00:00+07:00"}, {"attempt_id": "z", "run_id": "r", "event": "PUBLISHED", "status": "PUBLISHED", "finished_at": "2026-08-06T09:03:00+07:00"}]), PV.ProvenanceError))

# ── durability: _connect ตั้ง synchronous=FULL + journal (rollback) ────────────
DB = _log("dur.db")
PV.append_event(DB, _ev("d0", "r", "STARTED"))
_c = PV._connect(DB)
try:
    _sync = _c.execute("PRAGMA synchronous").fetchone()[0]
    _jm = _c.execute("PRAGMA journal_mode").fetchone()[0]
finally:
    _c.close()
check("durability: _connect -> synchronous=FULL(2) + journal_mode=delete", _sync == 2 and _jm == "delete")

# ── SQLite crash recovery: corrupt db file -> ProvenanceError (ไม่ crash) ──────
CR = _log("corruptdb.db")
with open(CR, "wb") as f: f.write(b"NOT-A-SQLITE-DATABASE-just-garbage-bytes-" * 8)
check("corrupt db file -> ProvenanceError", raises(lambda: PV.read_provenance(CR), PV.ProvenanceError))

# ── B1: canonical single-name authority — reject hard-link/symlink alias ──────
# SQLite rollback journal ตั้งชื่อตาม pathname → alias = undefined/corruption ตามสเปก จึงต้อง reject (ไม่ advertise)
AL = _log("alias_real.db")
PV.append_event(AL, _ev("al0", "r", "STARTED"))
ALIAS = _log("alias_link.db")
_hardlink_ok = True
try:
    os.link(AL, ALIAS)                                          # hard link -> st_nlink=2 ทั้ง AL และ ALIAS
except (OSError, NotImplementedError, AttributeError):
    _hardlink_ok = False
if _hardlink_ok:
    check("B1: hard-link (st_nlink!=1) -> append ทั้ง alias และ real ถูก reject (ProvenanceError)",
          raises(lambda: PV.append_event(ALIAS, _ev("al1", "r", "STARTED")), PV.ProvenanceError) and raises(lambda: PV.append_event(AL, _ev("al1", "r", "STARTED")), PV.ProvenanceError))
    check("B1: hard-link -> read ก็ fail-closed", raises(lambda: PV.read_provenance(AL), PV.ProvenanceError))
    os.unlink(ALIAS)                                            # เอา alias ออก -> st_nlink=1 -> ใช้งานได้อีก
    check("B1: unlink alias -> st_nlink=1 -> ledger ใช้งานได้ตามปกติ", PV.reconcile(PV.read_provenance(AL))["al0"] == "INCOMPLETE")
else:
    check("B1: hard link ไม่รองรับบน fs นี้ -> skip hard-link rejection", True)
SLINK = _log("alias_sym.db")
_symlink_ok = True
try:
    os.symlink(AL, SLINK)
except (OSError, NotImplementedError, AttributeError):
    _symlink_ok = False
if _symlink_ok:
    check("B1: symlink db -> append/read reject (ProvenanceError)",
          raises(lambda: PV.append_event(SLINK, _ev("s9", "r", "STARTED")), PV.ProvenanceError) and raises(lambda: PV.read_provenance(SLINK), PV.ProvenanceError))
    os.unlink(SLINK)
else:
    check("B1: symlink ไม่รองรับบน fs/สิทธิ์นี้ -> skip symlink rejection", True)

# ── B1/M2 (round-9): exact-schema fail-closed — foreign/weak-index/truncated db ไม่ถูก adopt/mutate ──
_rr, _rd = PV._OPEN_RETRIES, PV._OPEN_DELAY
PV._OPEN_RETRIES, PV._OPEN_DELAY = 3, 0.005                     # เร่ง fail-closed path ให้ test เร็ว
try:
    # foreign SQLite db (มี table อื่น, ไม่มี events) -> fail-closed + ไม่ถูก adopt/แก้ schema
    FG = _log("foreign.db")
    _fc = sqlite3.connect(FG, isolation_level=None); _fc.execute("CREATE TABLE other(x)"); _fc.execute("INSERT INTO other VALUES(1)"); _fc.close()
    _fgr = raises(lambda: PV.read_provenance(FG), PV.ProvenanceError)
    _fga = raises(lambda: PV.append_event(FG, _ev("f0", "r", "STARTED")), PV.ProvenanceError)
    _fc = sqlite3.connect(FG, isolation_level=None)
    _adopted = _fc.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('events','ux_started','ux_terminal')").fetchone()[0]
    _uv = _fc.execute("PRAGMA user_version").fetchone()[0]; _fc.close()
    check("B1: foreign SQLite db -> fail-closed + ไม่ถูก adopt (ไม่เพิ่ม events/index + user_version ไม่ถูก stamp)",
          _fgr and _fga and _adopted == 0 and _uv == 0)
    # same-name events + application_id ถูก แต่ index อ่อน (non-unique/non-partial) -> verify reject (ไม่ใช่แค่ชื่อ)
    WK = _log("weak.db")
    _wc = sqlite3.connect(WK, isolation_level=None)
    _wc.execute("PRAGMA journal_mode=DELETE")
    _wc.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, run_id TEXT, event TEXT NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL)")
    _wc.execute("CREATE INDEX ux_started ON events(attempt_id)")           # non-unique/non-partial
    _wc.execute("CREATE INDEX ux_terminal ON events(attempt_id)")
    _wc.execute("PRAGMA application_id=%d" % PV._APP_ID); _wc.execute("PRAGMA user_version=%d" % PV.SCHEMA_VERSION); _wc.close()
    check("B1: same-name events แต่ index อ่อน (non-unique/non-partial) -> verify reject (ProvenanceError)",
          raises(lambda: PV.read_provenance(WK), PV.ProvenanceError) and raises(lambda: PV.append_event(WK, _ev("w0", "r", "STARTED")), PV.ProvenanceError))
    # B1.2: schema ถูกหมดแต่ application_id ไม่ตรง (db เลียน schema) -> reject
    AI = _log("noappid.db")
    _ac = sqlite3.connect(AI, isolation_level=None); _ac.execute("PRAGMA journal_mode=DELETE")
    _ac.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, run_id TEXT, event TEXT NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL)")
    _ac.execute("CREATE UNIQUE INDEX ux_started ON events(attempt_id) WHERE event='STARTED'")
    _ac.execute("CREATE UNIQUE INDEX ux_terminal ON events(attempt_id) WHERE event IN ('PUBLISHED','DEGRADED','FAILED')")
    _ac.execute("PRAGMA user_version=%d" % PV.SCHEMA_VERSION); _ac.close()   # ไม่ตั้ง application_id
    check("B1.2: schema ถูกแต่ application_id ไม่ตรง (เลียน schema) -> reject",
          raises(lambda: PV.read_provenance(AI), PV.ProvenanceError) and raises(lambda: PV.append_event(AI, _ev("ai0", "r", "STARTED")), PV.ProvenanceError))
    # B1.2: extra index นอก allowlist บน events -> verify reject
    XI = _log("extraidx.db")
    PV.append_event(XI, _ev("x0", "r", "STARTED"))
    _xc = sqlite3.connect(XI, isolation_level=None); _xc.execute("CREATE INDEX extra_idx ON events(run_id)"); _xc.close()
    check("B1.2: extra index นอก allowlist -> verify reject",
          raises(lambda: PV.read_provenance(XI), PV.ProvenanceError) and raises(lambda: PV.append_event(XI, _ev("x1", "r", "STARTED")), PV.ProvenanceError))
    # M2: existing zero-length authority -> fail-closed (ไม่ re-init เป็น empty ledger)
    ZL = _log("zero.db")
    PV.append_event(ZL, _ev("z0", "r", "STARTED"))
    with open(ZL, "r+b") as f: f.truncate(0)
    check("M2: existing 0-byte db -> read/append fail-closed (ไม่ตีเป็น fresh empty)",
          raises(lambda: PV.read_provenance(ZL), PV.ProvenanceError) and raises(lambda: PV.append_event(ZL, _ev("z1", "r", "STARTED")), PV.ProvenanceError))
    # B1.1: existing db ถูก flip เป็น WAL -> reject + verify-only (ไม่ convert กลับ delete)
    FW = _log("waltamper.db")
    PV.append_event(FW, _ev("w0", "r", "STARTED"))         # valid provenance db (delete mode)
    _wc = sqlite3.connect(FW, isolation_level=None); _wc.execute("PRAGMA journal_mode=WAL"); _wc.close()   # tamper -> WAL
    _fwr = raises(lambda: PV.read_provenance(FW), PV.ProvenanceError)
    _wc = sqlite3.connect(FW, isolation_level=None); _jm_after = _wc.execute("PRAGMA journal_mode").fetchone()[0]; _wc.close()
    check("B1.1: existing db flip เป็น WAL -> reject + verify-only (ยังเป็น WAL, ไม่ถูก convert เป็น delete)",
          _fwr and str(_jm_after).lower() == "wal")
    # B1.2: trigger RAISE(IGNORE) บน events -> append fail-closed ก่อน mutation (ไม่ silent success)
    TG = _log("trigger.db")
    PV.append_event(TG, _ev("seed-0001", "r", "STARTED"))
    _gc = sqlite3.connect(TG, isolation_level=None)
    _gc.execute("CREATE TRIGGER swallow_insert BEFORE INSERT ON events BEGIN SELECT RAISE(IGNORE); END"); _gc.close()
    _tg_fail = raises(lambda: PV.append_event(TG, _ev("lost-0002", "r", "STARTED")), PV.ProvenanceError)
    _gc = sqlite3.connect(TG); _tg_cnt = _gc.execute("SELECT count(*) FROM events").fetchone()[0]; _gc.close()
    check("B1.2: trigger RAISE(IGNORE) -> append fail-closed ก่อน mutation + ledger มีแค่ seed (ไม่ silent success)",
          _tg_fail and _tg_cnt == 1)
    # B1.2 defense-in-depth: ถ้า trigger หลุด schema verify (จำลอง) -> post-commit row verify จับได้
    TG2 = _log("trigger2.db")
    PV.append_event(TG2, _ev("s1", "r", "STARTED"))
    _gc = sqlite3.connect(TG2, isolation_level=None)
    _gc.execute("CREATE TRIGGER swallow2 BEFORE INSERT ON events BEGIN SELECT RAISE(IGNORE); END"); _gc.close()
    _rvs = PV._verify_schema; PV._verify_schema = lambda conn: None          # จำลอง schema verify หลุด
    try:
        _def = raises(lambda: PV.append_event(TG2, _ev("s2", "r", "STARTED")), PV.ProvenanceError)
    finally:
        PV._verify_schema = _rvs
    check("B1.2: post-commit row verify (defense) -> trigger ที่หลุด verify ยังถูกจับ (COMMIT ok แต่ row หาย)", _def)
finally:
    PV._OPEN_RETRIES, PV._OPEN_DELAY = _rr, _rd

# ── M1: row decoder verify checksum + column identity (tamper via SQL) -> ProvenanceError ──
TM = _log("tamper.db")
PV.append_event(TM, _ev("t0", "r", "STARTED"))
_tc = sqlite3.connect(TM, isolation_level=None)
_tc.execute("UPDATE events SET attempt_id='forged' WHERE attempt_id='t0'")   # column != body identity
_tc.close()
check("M1: column tamper (attempt_id column != body identity) -> read ProvenanceError", raises(lambda: PV.read_provenance(TM), PV.ProvenanceError))
TM2 = _log("tamper2.db")
PV.append_event(TM2, _ev("t2", "r", "STARTED"))
_tc = sqlite3.connect(TM2, isolation_level=None)
_badbody = _json.dumps({"attempt_id": "t2", "run_id": "r", "event": "STARTED", "started_at": "2026-08-06T09:00:00+07:00", "x": "INJECTED"}, sort_keys=True, separators=(",", ":"))
_tc.execute("UPDATE events SET body=? WHERE attempt_id='t2'", (_badbody,))   # body เปลี่ยน แต่ body_sha256 เดิม
_tc.close()
check("M1: body tamper (body != body_sha256) -> read ProvenanceError", raises(lambda: PV.read_provenance(TM2), PV.ProvenanceError))

# ── B2: COMMIT outcome resolution (แยก pre-commit จาก ambiguous post-COMMIT) ──
_rdc = PV._do_commit
CB = _log("commit1.db")
PV.append_event(CB, _ev("cb0", "r", "STARTED"))
PV._do_commit = lambda conn: (_ for _ in ()).throw(sqlite3.OperationalError("busy at commit"))   # tx ยัง active
try:
    _b1 = raises(lambda: PV.append_event(CB, _ev("cb1", "r", "STARTED")), sqlite3.OperationalError)
finally:
    PV._do_commit = _rdc
check("B2: COMMIT fail (tx ยัง active) -> uncommitted (retryable, row ไม่ลง)",
      _b1 and [r["attempt_id"] for r in PV.read_provenance(CB)] == ["cb0"])
CB2 = _log("commit2.db")
PV.append_event(CB2, _ev("ca0", "r", "STARTED"))
def _commit_then_lose(conn):
    conn.execute("COMMIT")                                     # apply จริงแล้วค่อย ack หาย
    raise sqlite3.OperationalError("ack lost after commit")
PV._do_commit = _commit_then_lose
try:
    PV.append_event(CB2, _ev("ca1", "r", "STARTED"))          # ต้องไม่ raise (resolve จาก row = committed)
    _ackok = True
except Exception:
    _ackok = False
finally:
    PV._do_commit = _rdc
check("B2: COMMIT applied + ack lost -> verify row -> committed (append สำเร็จ)",
      _ackok and PV.reconcile(PV.read_provenance(CB2)) == {"ca0": "INCOMPLETE", "ca1": "INCOMPLETE"})
CB3 = _log("commit3.db")
PV.append_event(CB3, _ev("cc0", "r", "STARTED"))
_rre = PV._row_exists
PV._do_commit = _commit_then_lose
PV._row_exists = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("verify conn fail"))
try:
    _ind = raises(lambda: PV.append_event(CB3, _ev("cc1", "r", "STARTED")), PV.ProvenanceIndeterminate)
finally:
    PV._do_commit = _rdc; PV._row_exists = _rre
check("B2: COMMIT ambiguous + verify ไม่ได้ -> ProvenanceIndeterminate (fail-closed)", _ind)

# ── concurrent writers (NO pre-seed: init race handled by O_EXCL creator + bounded open-wait) ────
CC = _log("conc.db")
def _worker(i):
    for j in range(5):
        PV.append_event(CC, _ev(f"w{i}-{j}", "r", "STARTED"))
ts = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join()
_cc = PV.read_provenance(CC)
check("concurrent 4x5 writers (no pre-seed) -> 20 records ครบ ไม่ corrupt (init race handled)", len(_cc) == 20 and len({r["attempt_id"] for r in _cc}) == 20)

# ── lock contention: writer อื่นถือ write tx -> ProvenanceLocked (map จาก SQLITE_BUSY) ──
LC = _log("lock.db")
PV.append_event(LC, _ev("lc0", "r", "STARTED"))
holder = sqlite3.connect(LC, isolation_level=None)
holder.execute("PRAGMA busy_timeout=0")
holder.execute("BEGIN IMMEDIATE")                              # ถือ RESERVED write lock
_bt = PV._BUSY_TIMEOUT_MS; PV._BUSY_TIMEOUT_MS = 200
try:
    locked = raises(lambda: PV.append_event(LC, _ev("lc1", "r", "STARTED")), PV.ProvenanceLocked)
finally:
    PV._BUSY_TIMEOUT_MS = _bt
check("lock contention: อีก writer ถือ write tx -> ProvenanceLocked", locked)
holder.execute("ROLLBACK"); holder.close()                    # ปล่อย lock
PV.append_event(LC, _ev("lc1", "r", "STARTED"))
check("lock release: holder ปล่อย -> append สำเร็จ", PV.reconcile(PV.read_provenance(LC))["lc1"] == "INCOMPLETE")

# ── crash-safe: subprocess ถือ write tx (uncommitted) -> parent ProvenanceLocked -> kill -> OS ปล่อย lock + SQLite rollback ──
CL = _log("crash.db")
PV.append_event(CL, _ev("cl0", "r", "STARTED"))
SIG = _log("sig")
_code = ("import sys,time,sqlite3\n"
         "c=sqlite3.connect(sys.argv[1],isolation_level=None)\n"
         "c.execute('PRAGMA busy_timeout=0')\n"
         "c.execute('BEGIN IMMEDIATE')\n"
         "c.execute(\"INSERT INTO events(attempt_id,run_id,event,body,body_sha256) VALUES('h','r','STARTED','{}','x')\")\n"
         "open(sys.argv[2],'w').close()\n"
         "time.sleep(60)\n")
proc = subprocess.Popen([sys.executable, "-c", _code, CL, SIG], cwd=os.path.dirname(os.path.abspath(__file__)))
for _ in range(300):
    if os.path.exists(SIG):
        break
    time.sleep(0.02)
held = os.path.exists(SIG)
PV._BUSY_TIMEOUT_MS = 200
try:
    locked_out = raises(lambda: PV.append_event(CL, _ev("cl1", "r", "STARTED")), PV.ProvenanceLocked)
finally:
    PV._BUSY_TIMEOUT_MS = _bt
proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
reacq = True
try:
    PV.append_event(CL, _ev("cl1", "r", "STARTED"))            # OS ปล่อย lock + SQLite rollback child's tx (hot journal)
except Exception:
    reacq = False
_clr = PV.read_provenance(CL)
check("crash-safe: child ถือ write tx -> parent ProvenanceLocked", held and locked_out)
check("crash-safe: child ตาย -> OS ปล่อย lock + rollback uncommitted -> parent เขียนได้ (ไม่มี row 'h')",
      reacq and PV.reconcile(_clr)["cl1"] == "INCOMPLETE" and all(r["attempt_id"] != "h" for r in _clr))

# ── B3: export immutable JSONL evidence — atomic no-clobber + receipt (bind digest/max_seq/row_count) ──
EX = _log("ex.db"); EXOUT = _log("ex.evidence.jsonl")
PV.append_event(EX, _ev("e1", "r", "STARTED"))
PV.append_event(EX, _ev("e1", "r", "PUBLISHED", status="PUBLISHED"))
_rcpt = PV.export_jsonl(EX, EXOUT)
_lines = [l for l in open(EXOUT, encoding="utf-8").read().split("\n") if l]
check("B3: export -> JSONL ตามลำดับ + receipt (row_count/max_seq/jsonl_sha256/schema_version) ผูก source",
      len(_lines) == 2 and _rcpt["row_count"] == 2 and _rcpt["max_seq"] == 2 and _rcpt["schema_version"] == PV.SCHEMA_VERSION
      and _rcpt["jsonl_sha256"] == hashlib.sha256(open(EXOUT, "rb").read()).hexdigest()
      and PV.reconcile([_json.loads(l) for l in _lines])["e1"] == "PUBLISHED")
check("B3: export no-clobber -> ทับ artifact เดิมไม่ได้ (ProvenanceError) + ไม่มี tmp ค้าง",
      raises(lambda: PV.export_jsonl(EX, EXOUT), PV.ProvenanceError) and not any(x.startswith("ex.evidence.jsonl.tmp") for x in os.listdir(BASE)))
# export write ล้มกลางคัน -> final ไม่ถูกสร้าง (atomic via temp) + ไม่มี tmp ค้าง
EX2 = _log("ex2.db"); EXOUT2 = _log("ex2.evidence.jsonl")
PV.append_event(EX2, _ev("f1", "r", "STARTED"))
_rw = os.write
os.write = lambda fd, b: (_ for _ in ()).throw(OSError("export write fail"))
try:
    _ef = raises(lambda: PV.export_jsonl(EX2, EXOUT2), OSError)
finally:
    os.write = _rw
check("B3: export write ล้ม -> final ไม่ถูกสร้าง (atomic temp) + ไม่มี tmp ค้าง",
      _ef and not os.path.exists(EXOUT2) and not any(x.startswith("ex2.evidence.jsonl.tmp") for x in os.listdir(BASE)))

# ── B2: export receipt + JSONL จาก snapshot เดียว — writer commit หลัง freeze ไม่กระทบ ──
SS = _log("snap.db"); SSOUT = _log("snap.evidence.jsonl")
PV.append_event(SS, _ev("s1", "r", "STARTED"))
_orig_hook = PV._after_snapshot_hook
PV._after_snapshot_hook = lambda dbp: PV.append_event(dbp, _ev("s2", "r", "STARTED"))   # commit เพิ่มหลัง freeze snapshot
try:
    _rc2 = PV.export_jsonl(SS, SSOUT)
finally:
    PV._after_snapshot_hook = _orig_hook
_snlines = [l for l in open(SSOUT, encoding="utf-8").read().split("\n") if l]
check("B2: export snapshot-bound -> receipt.row_count/max_seq ตรงกับไฟล์ แม้มี commit หลัง freeze (db=2 แต่ export=1)",
      _rc2["row_count"] == 1 and _rc2["max_seq"] == 1 and len(_snlines) == 1
      and _rc2["jsonl_sha256"] == hashlib.sha256(open(SSOUT, "rb").read()).hexdigest() and len(PV.read_provenance(SS)) == 2)

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
