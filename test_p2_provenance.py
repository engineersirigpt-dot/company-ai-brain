"""
Unit test ของ p2_provenance — event ledger ข้าม process (pure/offline)
STARTED→terminal · single-writer lock · full-write/short-write · allow_nan/oversize · truncated-tail recovery ·
reconcile INCOMPLETE · concurrent writers serialize

    python test_p2_provenance.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading

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
LOG = os.path.join(BASE, "sub", "prov.jsonl")

# ── STARTED → terminal ledger + reconcile ─────────────────────────────────────
PV.append_provenance(LOG, {"attempt_id": "att-1", "run_id": "run-1", "event": "STARTED"})
PV.append_provenance(LOG, {"attempt_id": "att-1", "run_id": "run-1", "event": "PUBLISHED", "status": "PUBLISHED"})
PV.append_provenance(LOG, {"attempt_id": "att-2", "run_id": "run-2", "event": "STARTED"})   # ตายกลางทาง (ไม่มี terminal)
recs = PV.read_provenance(LOG)
check("append + read ตามลำดับ (3 events)", [r["event"] for r in recs] == ["STARTED", "PUBLISHED", "STARTED"])
check("dir สร้างเอง + log เป็นไฟล์", os.path.isfile(LOG))
rc = PV.reconcile(recs)
check("reconcile: att-1 -> PUBLISHED", rc["att-1"] == "PUBLISHED")
check("reconcile: att-2 (STARTED ไม่มี terminal) -> INCOMPLETE", rc["att-2"] == "INCOMPLETE")
check("read log ที่ไม่มี -> []", PV.read_provenance(os.path.join(BASE, "nope.jsonl")) == [])
check("record ไม่ใช่ dict -> TypeError", raises(lambda: PV.append_provenance(LOG, ["x"]), TypeError))

# ── M1: allow_nan / oversize / short-write handling ───────────────────────────
check("NaN ใน record -> ProvenanceError (allow_nan=False)", raises(lambda: PV.append_provenance(LOG, {"x": float("nan")}), PV.ProvenanceError))
check("record ใหญ่เกิน -> ProvenanceError", raises(lambda: PV.append_provenance(LOG, {"x": "y" * (PV.MAX_RECORD_BYTES + 10)}), PV.ProvenanceError))
LOG2 = os.path.join(BASE, "sw.jsonl")
_rw = os.write
os.write = lambda fd, b: _rw(fd, b[:3])          # partial write ทีละ 3 byte — loop ต้องเขียนครบ
try:
    PV.append_provenance(LOG2, {"attempt_id": "a", "event": "STARTED"})
finally:
    os.write = _rw
check("short write (partial) -> loop เขียนครบ + record อ่านได้ปกติ", PV.read_provenance(LOG2)[0]["attempt_id"] == "a")
LOG3 = os.path.join(BASE, "zero.jsonl")
os.write = lambda fd, b: 0                        # ไม่คืบ
try:
    zero = raises(lambda: PV.append_provenance(LOG3, {"a": 1}), PV.ProvenanceError)
finally:
    os.write = _rw
check("os.write คืน 0 (ไม่คืบ) -> ProvenanceError", zero)

# ── lock: reject writer ที่สอง ────────────────────────────────────────────────
_lk = LOG + ".lock"
fd = os.open(_lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd)   # จำลอง lock ถูกถือ
check("lock ถูกถือ -> ProvenanceLocked (bounded retry)", raises(lambda: PV._acquire_lock(_lk, retries=2, delay=0.001), PV.ProvenanceLocked))
os.unlink(_lk)
check("ปล่อย lock แล้ว acquire ได้", (PV._acquire_lock(_lk, retries=2, delay=0.001), PV._release_lock(_lk)) and not os.path.exists(_lk))

# ── truncated tail recovery / interior corruption ────────────────────────────
LOG4 = os.path.join(BASE, "tail.jsonl")
PV.append_provenance(LOG4, {"attempt_id": "t1", "event": "STARTED"})
with open(LOG4, "a", encoding="utf-8") as f:
    f.write('{"attempt_id": "t1", "event": "PUBL')      # partial tail (process ตายกลางเขียน)
check("truncated tail -> drop, คืน record ที่สมบูรณ์", [r["attempt_id"] for r in PV.read_provenance(LOG4)] == ["t1"])
LOG5 = os.path.join(BASE, "corrupt.jsonl")
with open(LOG5, "w", encoding="utf-8") as f:
    f.write('{"a":1}\nGARBAGE-INTERIOR\n{"b":2}\n')
check("interior corruption -> ProvenanceError", raises(lambda: PV.read_provenance(LOG5), PV.ProvenanceError))

# ── concurrent writers serialize (lock mutual exclusion) ─────────────────────
LOG6 = os.path.join(BASE, "conc.jsonl")
def _worker(i):
    for j in range(5):
        PV.append_provenance(LOG6, {"attempt_id": f"w{i}-{j}", "event": "STARTED"})
ts = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join()
_cr = PV.read_provenance(LOG6)
check("concurrent 4x5 writers -> 20 records ครบ + ไม่มี corruption", len(_cr) == 20 and all(isinstance(r, dict) for r in _cr))

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
