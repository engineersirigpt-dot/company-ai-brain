"""
Unit test ของ p2_provenance — crash-safe event ledger (pure/offline)
append_event state machine · newline-commit + tail repair · OS crash-safe lock (subprocess) ·
full-write/allow_nan/oversize · strict reconcile · concurrent writers

    python test_p2_provenance.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import warnings

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


# ── ledger STARTED → terminal + reconcile ─────────────────────────────────────
L = _log("led.jsonl")
PV.append_event(L, _ev("att-00000001", "run-1", "STARTED"))
PV.append_event(L, _ev("att-00000001", "run-1", "PUBLISHED", status="PUBLISHED"))
PV.append_event(L, _ev("att-00000002", "run-2", "STARTED"))          # ตายกลางทาง (ไม่มี terminal)
recs = PV.read_provenance(L)
check("ledger read ตามลำดับ", [r["event"] for r in recs] == ["STARTED", "PUBLISHED", "STARTED"])
rc = PV.reconcile(recs)
check("reconcile att-1 -> PUBLISHED, att-2 -> INCOMPLETE", rc["att-00000001"] == "PUBLISHED" and rc["att-00000002"] == "INCOMPLETE")

# ── B3.1: state machine ───────────────────────────────────────────────────────
S = _log("sm.jsonl")
PV.append_event(S, _ev("s1", "r", "STARTED"))
check("duplicate STARTED -> ProvenanceError", raises(lambda: PV.append_event(S, _ev("s1", "r", "STARTED")), PV.ProvenanceError))
check("terminal without STARTED -> ProvenanceError", raises(lambda: PV.append_event(S, _ev("s2", "r", "PUBLISHED", status="PUBLISHED")), PV.ProvenanceError))
PV.append_event(S, _ev("s1", "r", "PUBLISHED", status="PUBLISHED"))
check("duplicate terminal -> ProvenanceError", raises(lambda: PV.append_event(S, _ev("s1", "r", "FAILED", status="FAILED")), PV.ProvenanceError))
R = _log("rid.jsonl")
PV.append_event(R, _ev("a1", "r1", "STARTED"))
check("terminal run_id != STARTED -> ProvenanceError", raises(lambda: PV.append_event(R, _ev("a1", "r2", "PUBLISHED", status="PUBLISHED")), PV.ProvenanceError))
check("attempt_id reuse ข้าม run -> duplicate STARTED error", raises(lambda: PV.append_event(R, _ev("a1", "r3", "STARTED")), PV.ProvenanceError))

# ── B3.2: newline = commit marker + tail repair ก่อน append ───────────────────
N = _log("nl.jsonl")
PV.append_event(N, _ev("n1", "r", "STARTED"))
with open(N, "a", encoding="utf-8") as f:
    f.write('{"attempt_id":"n1","run_id":"r","event":"PUBLISHED","status":"PUBLISHED"}')   # valid JSON แต่ไม่มี \n
check("valid JSON ไม่มี newline -> uncommitted -> drop", [r["event"] for r in PV.read_provenance(N)] == ["STARTED"])
T = _log("repair.jsonl")
PV.append_event(T, _ev("t1", "r", "STARTED"))
with open(T, "a", encoding="utf-8") as f:
    f.write('{"attempt_id":"t1","event":"PUBL')                                            # partial tail (crash กลางเขียน)
PV.append_event(T, _ev("t1", "r", "PUBLISHED", status="PUBLISHED"))                        # ต้องตัด tail ก่อน append
check("partial tail ถูกตัดก่อน append -> terminal อ่าน/reconcile ได้", PV.reconcile(PV.read_provenance(T))["t1"] == "PUBLISHED")
C = _log("corrupt.jsonl")
with open(C, "w", encoding="utf-8") as f:
    f.write('{"a":1}\nGARBAGE-INTERIOR\n{"b":2}\n')
check("interior corruption (committed) -> ProvenanceError", raises(lambda: PV.read_provenance(C), PV.ProvenanceError))

# ── M1: full-write / allow_nan / oversize ─────────────────────────────────────
check("record ไม่ใช่ dict -> TypeError", raises(lambda: PV._append_raw(L, ["x"]), TypeError))
check("NaN -> ProvenanceError (allow_nan=False)", raises(lambda: PV._append_raw(_log("nan.jsonl"), {"x": float("nan")}), PV.ProvenanceError))
check("oversize -> ProvenanceError", raises(lambda: PV._append_raw(_log("big.jsonl"), {"x": "y" * (PV.MAX_RECORD_BYTES + 10)}), PV.ProvenanceError))
SW = _log("sw.jsonl"); _rw = os.write
os.write = lambda fd, b: _rw(fd, b[:3])
try:
    PV._append_raw(SW, {"attempt_id": "a", "n": 1})
finally:
    os.write = _rw
check("partial write -> loop เขียนครบ", PV.read_provenance(SW)[0]["attempt_id"] == "a")
os.write = lambda fd, b: 0
try:
    zero = raises(lambda: PV._append_raw(_log("zero.jsonl"), {"a": 1}), PV.ProvenanceError)
finally:
    os.write = _rw
check("os.write คืน 0 -> ProvenanceError", zero)

# ── strict reconcile ──────────────────────────────────────────────────────────
check("reconcile STARTED-only -> INCOMPLETE", PV.reconcile([_ev("x", "r", "STARTED")]) == {"x": "INCOMPLETE"})
check("reconcile terminal-only -> ProvenanceError", raises(lambda: PV.reconcile([_ev("y", "r", "PUBLISHED", status="PUBLISHED")]), PV.ProvenanceError))
check("reconcile duplicate terminal -> ProvenanceError", raises(lambda: PV.reconcile([_ev("z", "r", "STARTED"), _ev("z", "r", "PUBLISHED", status="PUBLISHED"), _ev("z", "r", "FAILED", status="FAILED")]), PV.ProvenanceError))

# ── B3.1-R: reconcile order-sensitive (ลำดับ/run/status) ──────────────────────
check("reconcile PUBLISHED-ก่อน-STARTED -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o1", "r", "PUBLISHED", status="PUBLISHED"), _ev("o1", "r", "STARTED")]), PV.ProvenanceError))
check("reconcile terminal run_id != STARTED -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o2", "r1", "STARTED"), _ev("o2", "r2", "FAILED", status="FAILED")]), PV.ProvenanceError))
check("reconcile status != event -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o3", "r", "STARTED"), _ev("o3", "r", "PUBLISHED", status="FAILED")]), PV.ProvenanceError))
check("append_event = reducer เดียวกับ reconcile (status != event) -> ProvenanceError", raises(lambda: PV.append_event(_log("se.jsonl"), _ev("se1", "r", "STARTED")) or PV.append_event(_log("se.jsonl"), _ev("se1", "r", "PUBLISHED", status="FAILED")), PV.ProvenanceError))

# ── M3.2-A: ledger enforce terminal schema (PUBLISHED ต้องมี binding) ──────────
MA = _log("m32a.jsonl")
PV.append_event(MA, _ev("m1", "r", "STARTED"))
check("M3.2-A: PUBLISHED ไม่มี binding -> ProvenanceError (ledger enforce schema)", raises(lambda: PV.append_event(MA, {"attempt_id": "m1", "run_id": "r", "event": "PUBLISHED", "status": "PUBLISHED", "finished_at": "2026-08-06T09:03:00+07:00"}), PV.ProvenanceError))
check("M3.2-A: reconcile minimal PUBLISHED (raw) -> ProvenanceError", raises(lambda: PV.reconcile([{"attempt_id": "z", "run_id": "r", "event": "STARTED", "started_at": "2026-08-06T09:00:00+07:00"}, {"attempt_id": "z", "run_id": "r", "event": "PUBLISHED", "status": "PUBLISHED", "finished_at": "2026-08-06T09:03:00+07:00"}]), PV.ProvenanceError))

# ── B3.2-P: record commit fsync fail (intent ok, rollback confirmed) -> UNCOMMITTED -> retry ปิด attempt ได้ ─
# WAI: fsync call 1 = intent (ต้องผ่าน) · call 2 = record commit (ให้ล้ม) · call 3 = rollback (ผ่าน)
_rfs = os.fsync
F = _log("fsyncfail.jsonl")
PV.append_event(F, _ev("f1", "r", "STARTED"))
_n = {"c": 0}
def _fsync_2nd_fail(fd):
    _n["c"] += 1
    if _n["c"] == 2:
        raise OSError("record commit fsync fail")
    return _rfs(fd)
os.fsync = _fsync_2nd_fail
try:
    unc = raises(lambda: PV.append_event(F, _ev("f1", "r", "PUBLISHED", status="PUBLISHED")), OSError)
finally:
    os.fsync = _rfs
check("B3.2-P: record commit fsync fail + rollback confirmed -> UNCOMMITTED (reader เห็นแค่ STARTED, intent เคลียร์)", unc and [r["event"] for r in PV.read_provenance(F)] == ["STARTED"] and not os.path.exists(F + ".intent"))
PV.append_event(F, _ev("f1", "r", "FAILED", status="FAILED"))
check("B3.2-P: retry terminal หลัง rollback -> ปิด attempt ได้", PV.reconcile(PV.read_provenance(F))["f1"] == "FAILED")

# ── B1: write-ahead intent v2 — evidence-based recovery (ไม่ blind unlink) ─
# committed replay: crash หลัง record+\n durable ก่อน clear intent -> recover เห็น digest ตรง -> COMMITTED (re-fsync ยืนยัน)
IL = _log("intent.jsonl")
PV.append_event(IL, _ev("i1", "r", "STARTED"))
_cut = os.path.getsize(IL)
_term = _ev("i1", "r", "PUBLISHED", status="PUBLISHED"); _line = PV._serialize(_term)
with open(IL, "ab") as f: f.write(_line)                   # full line + \n อยู่ครบ (page cache) แต่ intent ยังไม่ clear
PV._write_intent(IL, _term, _line, _cut)                   # intent v2 bind cut + record digest
_sc = {"n": 0}
def _fsync_spy(fd): _sc["n"] += 1; return _rfs(fd)
os.fsync = _fsync_spy
try:
    _oc = PV.recover(IL)
finally:
    os.fsync = _rfs
check("B1: recover(committed replay, digest ตรง) -> COMMITTED + re-fsync (ยืนยัน durable ไม่ใช่ blind unlink)", _oc == "COMMITTED" and _sc["n"] >= 1)
check("B1: หลัง recover committed -> record durable + reconcile ปิด attempt + intent เคลียร์", PV.reconcile(PV.read_provenance(IL))["i1"] == "PUBLISHED" and not os.path.exists(IL + ".intent"))

# uncommitted: crash กลาง write (partial line, digest ไม่ตรง) -> recover truncate กลับ cut -> UNCOMMITTED
IL2 = _log("intent2.jsonl")
PV.append_event(IL2, _ev("j1", "r", "STARTED"))
_cut2 = os.path.getsize(IL2)
_term2 = _ev("j1", "r", "PUBLISHED", status="PUBLISHED"); _line2 = PV._serialize(_term2)
with open(IL2, "ab") as f: f.write(_line2[:len(_line2) // 2])   # เขียนไม่ครบ (ไม่ปิด \n)
PV._write_intent(IL2, _term2, _line2, _cut2)               # intent bind digest ของ full line
check("B1: recover(partial record, digest ไม่ตรง) -> UNCOMMITTED + truncate กลับ cut", PV.recover(IL2) == "UNCOMMITTED" and os.path.getsize(IL2) == _cut2)
check("B1: หลัง recover uncommitted -> reader เห็นแค่ STARTED + intent เคลียร์", [r["event"] for r in PV.read_provenance(IL2)] == ["STARTED"] and not os.path.exists(IL2 + ".intent"))

# corrupt intent (ไม่มี binding/protocol) -> recover เองไม่ได้ = ProvenanceIndeterminate (fail-closed, ไม่ blind-accept)
IL3 = _log("intent3.jsonl")
PV.append_event(IL3, _ev("k1", "r", "STARTED"))
open(IL3 + ".intent", "w").close()
check("B1: corrupt intent -> recover/read/append = ProvenanceIndeterminate (ต้อง operator, ไม่ auto-heal)",
      raises(lambda: PV.recover(IL3), PV.ProvenanceIndeterminate) and raises(lambda: PV.read_provenance(IL3), PV.ProvenanceIndeterminate) and raises(lambda: PV.append_event(IL3, _ev("k2", "r", "STARTED")), PV.ProvenanceIndeterminate))
os.unlink(IL3 + ".intent")

# auto-recover: append_event/read_provenance ถัดไป resolve intent เอง (ใต้ lock) โดยไม่ต้องเรียก recover() ตรง
IL4 = _log("intent4.jsonl")
PV.append_event(IL4, _ev("l1", "r", "STARTED"))
_cut4 = os.path.getsize(IL4)
_term4 = _ev("l1", "r", "PUBLISHED", status="PUBLISHED"); _line4 = PV._serialize(_term4)
with open(IL4, "ab") as f: f.write(_line4)
PV._write_intent(IL4, _term4, _line4, _cut4)
check("B1: read_provenance auto-recover intent (committed) ใต้ lock -> เห็น terminal + intent เคลียร์", PV.reconcile(PV.read_provenance(IL4))["l1"] == "PUBLISHED" and not os.path.exists(IL4 + ".intent"))

# ── B2: parent directory ต้อง pre-exist (durability boundary) — ไม่ auto-create ─
_missing = _log("nodir/sub/led.jsonl")
check("B2: parent ที่ยังไม่มี -> ProvenanceError (ไม่ auto-create) + ledger ไม่ถูกสร้าง", raises(lambda: PV.append_event(_missing, _ev("b1", "r", "STARTED")), PV.ProvenanceError) and not os.path.exists(_missing))

# ── M1: clear-intent parent-fsync ล้ม (หลัง unlink สำเร็จ) -> committed + warning (ไม่ indeterminate, marker หายจริง) ─
# call 1 = _write_intent parent fsync (ผ่าน) · call 2 = _clear_intent parent fsync (ให้ล้ม)
M1 = _log("m1.jsonl")
PV.append_event(M1, _ev("m1a", "r", "STARTED"))
_rfp = PV._fsync_parent; _pc = {"n": 0}
def _fp_2nd_fail(d):
    _pc["n"] += 1
    if _pc["n"] == 2:
        raise OSError("parent fsync fail (clear step)")
    return _rfp(d)
PV._fsync_parent = _fp_2nd_fail
try:
    with warnings.catch_warnings(record=True) as _caught:
        warnings.simplefilter("always")
        PV.append_event(M1, _ev("m1a", "r", "PUBLISHED", status="PUBLISHED"))   # ต้องไม่ raise (committed)
    _warned = any(issubclass(w.category, RuntimeWarning) for w in _caught)
finally:
    PV._fsync_parent = _rfp
check("M1: clear-intent parent-fsync ล้ม -> append committed (record durable) + RuntimeWarning + marker หายจริง (ไม่ indeterminate)",
      _warned and PV.reconcile(PV.read_provenance(M1))["m1a"] == "PUBLISHED" and not os.path.exists(M1 + ".intent"))

# ── B3.2-P.2: post-commit release fail -> record ยัง durable (append ไม่ report fail) ─
C2 = _log("postcommit.jsonl")
PV.append_event(C2, _ev("c1", "r", "STARTED"))
_rrel = PV._release
def _rel_fail(fd):
    _rrel(fd)                                   # ปล่อย lock จริง แล้วค่อยโยน cleanup error
    raise OSError("release cleanup fail")
PV._release = _rel_fail
try:
    PV.append_event(C2, _ev("c1", "r", "PUBLISHED", status="PUBLISHED"))   # committed แล้ว release ล้ม -> กลืน (warning)
finally:
    PV._release = _rrel
check("B3.2-P.2: post-commit release fail -> record durable (reconcile PUBLISHED)", PV.reconcile(PV.read_provenance(C2))["c1"] == "PUBLISHED")

# ── concurrent writers (lock mutual exclusion) ───────────────────────────────
CC = _log("conc.jsonl")
def _worker(i):
    for j in range(5):
        PV.append_event(CC, _ev(f"w{i}-{j}", "r", "STARTED"))
ts = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join()
check("concurrent 4x5 writers -> 20 records ครบ ไม่ corrupt", len(PV.read_provenance(CC)) == 20)

# ── M1.1: OS crash-safe lock (subprocess acquire → kill → parent acquire) ─────
CL = _log("crash.jsonl"); SIG = _log("sig")
open(CL, "w").close()
_worker_code = ("import sys,time\nsys.path.insert(0,%r)\nimport p2_provenance as PV\n"
                "fd=PV._lock(sys.argv[1])\nopen(sys.argv[2],'w').close()\ntime.sleep(60)\n" % os.path.dirname(os.path.abspath(__file__)))
proc = subprocess.Popen([sys.executable, "-c", _worker_code, CL, SIG], cwd=os.path.dirname(os.path.abspath(__file__)))
for _ in range(300):
    if os.path.exists(SIG):
        break
    time.sleep(0.02)
held = os.path.exists(SIG)
locked_out = raises(lambda: PV._lock(CL, retries=3, delay=0.01), PV.ProvenanceLocked)   # child ถือ lock อยู่
proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
_fd = None
try:
    _fd = PV._lock(CL, retries=300, delay=0.02)          # ต้อง acquire ได้หลัง child ตาย (OS ปล่อย lock เอง)
    reacquired = True
finally:
    if _fd is not None:
        PV._release(_fd)
check("M1.1: child ถือ lock -> parent ProvenanceLocked", held and locked_out)
check("M1.1: crash-safe — parent acquire ได้หลัง child ตาย โดยไม่ลบ lock manual", reacquired)

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
