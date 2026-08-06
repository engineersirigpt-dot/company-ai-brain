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
def _ev(aid, rid, event, **kw): return {"attempt_id": aid, "run_id": rid, "event": event, **kw}


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
check("record ไม่ใช่ dict -> TypeError", raises(lambda: PV.append_provenance(L, ["x"]), TypeError))
check("NaN -> ProvenanceError (allow_nan=False)", raises(lambda: PV.append_provenance(_log("nan.jsonl"), {"x": float("nan")}), PV.ProvenanceError))
check("oversize -> ProvenanceError", raises(lambda: PV.append_provenance(_log("big.jsonl"), {"x": "y" * (PV.MAX_RECORD_BYTES + 10)}), PV.ProvenanceError))
SW = _log("sw.jsonl"); _rw = os.write
os.write = lambda fd, b: _rw(fd, b[:3])
try:
    PV.append_provenance(SW, {"attempt_id": "a", "n": 1})
finally:
    os.write = _rw
check("partial write -> loop เขียนครบ", PV.read_provenance(SW)[0]["attempt_id"] == "a")
os.write = lambda fd, b: 0
try:
    zero = raises(lambda: PV.append_provenance(_log("zero.jsonl"), {"a": 1}), PV.ProvenanceError)
finally:
    os.write = _rw
check("os.write คืน 0 -> ProvenanceError", zero)

# ── strict reconcile ──────────────────────────────────────────────────────────
check("reconcile STARTED-only -> INCOMPLETE", PV.reconcile([{"attempt_id": "x", "event": "STARTED"}]) == {"x": "INCOMPLETE"})
check("reconcile terminal-only -> ProvenanceError", raises(lambda: PV.reconcile([{"attempt_id": "y", "event": "PUBLISHED"}]), PV.ProvenanceError))
check("reconcile duplicate terminal -> ProvenanceError", raises(lambda: PV.reconcile([{"attempt_id": "z", "event": "STARTED"}, {"attempt_id": "z", "event": "PUBLISHED"}, {"attempt_id": "z", "event": "FAILED"}]), PV.ProvenanceError))

# ── B3.1-R: reconcile order-sensitive (ลำดับ/run/status) ──────────────────────
check("reconcile PUBLISHED-ก่อน-STARTED -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o1", "r", "PUBLISHED", status="PUBLISHED"), _ev("o1", "r", "STARTED")]), PV.ProvenanceError))
check("reconcile terminal run_id != STARTED -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o2", "r1", "STARTED"), _ev("o2", "r2", "FAILED", status="FAILED")]), PV.ProvenanceError))
check("reconcile status != event -> ProvenanceError", raises(lambda: PV.reconcile([_ev("o3", "r", "STARTED"), _ev("o3", "r", "PUBLISHED", status="FAILED")]), PV.ProvenanceError))
check("append_event = reducer เดียวกับ reconcile (status != event) -> ProvenanceError", raises(lambda: PV.append_event(_log("se.jsonl"), _ev("se1", "r", "STARTED")) or PV.append_event(_log("se.jsonl"), _ev("se1", "r", "PUBLISHED", status="FAILED")), PV.ProvenanceError))

# ── B3.2-P: terminal fsync fail -> rollback (record ไม่ปรากฏ) -> retry ปิด attempt ได้ ─
F = _log("fsyncfail.jsonl")
PV.append_event(F, _ev("f1", "r", "STARTED"))
_rfs = os.fsync
os.fsync = lambda fd: (_ for _ in ()).throw(OSError("fsync fail"))
try:
    fsync_raised = raises(lambda: PV.append_event(F, _ev("f1", "r", "PUBLISHED", status="PUBLISHED")), OSError)
finally:
    os.fsync = _rfs
check("B3.2-P: terminal fsync fail -> exception", fsync_raised)
check("B3.2-P: rolled back — reader เห็นแค่ STARTED + reconcile INCOMPLETE (result ไม่ขัด disk)", [r["event"] for r in PV.read_provenance(F)] == ["STARTED"] and PV.reconcile(PV.read_provenance(F))["f1"] == "INCOMPLETE")
PV.append_event(F, _ev("f1", "r", "FAILED", status="FAILED"))
check("B3.2-P: retry terminal หลัง rollback -> ปิด attempt ได้", PV.reconcile(PV.read_provenance(F))["f1"] == "FAILED")

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
