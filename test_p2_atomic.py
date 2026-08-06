"""
Unit test ของ p2_atomic — fail-closed atomic publisher (single-file bundle, pure/offline)
validate ก่อน publish · atomic (temp→rename) · exception/refuse ไม่ทิ้ง PASS artifact · immutable ·
run_id path-injection containment (M1)

    python test_p2_atomic.py
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

import p2_atomic as AT

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def refused(fn):
    try:
        fn(); return False
    except AT.PublishRefused:
        return True

BASE = tempfile.mkdtemp(prefix="p2atomic-")
EV = {"run_receipt_sha256": "a" * 64, "schema_version": "p2-m4-v5", "n": 1}
RC = {"schema_version": "p2-m4-receipt-v1", "n": 2}
def pub(run_id, out_dir=BASE, evidence=EV, receipt=RC, validate=lambda: []):
    return AT.publish_m4_bundle(out_dir=out_dir, run_id=run_id, evidence=evidence, receipt=receipt, validate=validate)

# ── valid publish = ไฟล์เดียว <run_id>.bundle.json ────────────────────────────
p = pub("run-ok")
check("publish valid -> bundle file เดียว", os.path.isfile(p) and p.endswith("run-ok.bundle.json"))
check("bundle content = {evidence, receipt}", json.load(open(p, encoding="utf-8")) == {"evidence": EV, "receipt": RC})
check("ไม่มี temp ค้าง", [n for n in os.listdir(BASE) if n.startswith(".run-ok")] == [])

# ── validate ก่อนเขียน ; invalid -> refuse + ไม่มีไฟล์ ────────────────────────
called = {"n": 0}
def _val_fail():
    called["n"] += 1
    return ["bundle error"]
check("bundle invalid -> PublishRefused", refused(lambda: pub("run-bad", validate=_val_fail)))
check("invalid -> validate ถูกเรียก + ไม่มีไฟล์", called["n"] == 1 and not os.path.exists(os.path.join(BASE, "run-bad.bundle.json")))

# ── exception ระหว่างเขียน -> ไม่มี final + ไม่มี temp ค้าง ────────────────────
raised = False
try:
    pub("run-exc", evidence={"bad": {1, 2, 3}})     # set = ไม่ JSON-serializable → raise หลัง validate ผ่าน
except TypeError:
    raised = True
check("exception ระหว่างเขียน -> propagate", raised)
check("exception -> ไม่มี final + temp ถูกลบ", not os.path.exists(os.path.join(BASE, "run-exc.bundle.json")) and not any(n.startswith(".run-exc") for n in os.listdir(BASE)))

# ── immutable ─────────────────────────────────────────────────────────────────
check("overwrite run เดิม -> PublishRefused", refused(lambda: pub("run-ok")))
check("overwrite refuse -> ของเดิมไม่ถูกแตะ", json.load(open(p, encoding="utf-8")) == {"evidence": EV, "receipt": RC})

# ── M1: run_id path-injection containment ─────────────────────────────────────
UNSAFE = ["a/b", "a\\b", "..", ".", "../esc", "/abs", "\\abs", "C:evil", "c:\\x", "con", "PRN.txt",
          "nul", "COM1", "lpt9.log", " ", ".hidden", "a b", "x" * 129, "หนึ่ง", "run\n", "run\r", "run\x00id"]
allref = all(refused(lambda r=r: pub(r)) for r in UNSAFE)
check("M1: run_id ไม่ปลอดภัยทุกแบบ -> PublishRefused", allref)
check("M1: ไม่มีไฟล์หลุดออกนอก out_dir", not any(os.path.exists(os.path.join(os.path.dirname(BASE), n)) for n in ("escape", "esc", "abs", "evil")))
check("safe run_id (dot/dash/underscore) -> publish ได้", os.path.isfile(pub("run-1.2_3")))

# ── input guards ──────────────────────────────────────────────────────────────
check("evidence ไม่ใช่ dict -> refuse", refused(lambda: pub("r-x", evidence=[1])))
check("validate ไม่ callable -> refuse", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="r-y", evidence=EV, receipt=RC, validate="nope")))
check("validate คืนไม่ใช่ list -> refuse", refused(lambda: pub("r-z", validate=lambda: None)))
check("guard fail -> ไม่มีไฟล์ตกค้าง", not any(os.path.exists(os.path.join(BASE, r + ".bundle.json")) for r in ("r-x", "r-y", "r-z")))

# ── B1: atomic no-clobber ภายใต้ concurrent writers (exactly-one winner) ──────
def _race():
    barrier = threading.Barrier(2)
    out = []
    def w():
        barrier.wait()
        try:
            out.append(("ok", pub("run-race")))
        except AT.PublishRefused:
            out.append(("refused", None))
        except BaseException as e:
            out.append(("err", repr(e)))
    ts = [threading.Thread(target=w) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()
    return out
rr = _race()
check("B1: concurrent publish -> exactly one PUBLISHED + one PublishRefused (ไม่มี uncontrolled error)",
      sorted(k for k, _ in rr) == ["ok", "refused"], rr)
check("B1: race winner file อยู่ + ไม่มี temp ค้าง", os.path.isfile(os.path.join(BASE, "run-race.bundle.json")) and not any(n.startswith(".run-race") for n in os.listdir(BASE)))

# ── M3: durability semantics พูดตามจริง ───────────────────────────────────────
check("M3: _fsync_dir dir ปกติ -> ไม่ raise (durable/unsupported)", AT._fsync_dir(BASE) in ("durable", "unsupported"))
_orig = AT._fsync_dir
def _boom(_p): raise OSError("fsync boom")
AT._fsync_dir = _boom
try:
    dur = False
    try:
        pub("run-dur")
    except AT.DurabilityUnconfirmed:
        dur = True
finally:
    AT._fsync_dir = _orig
check("M3: parent fsync fail หลัง publish -> DurabilityUnconfirmed + final ปรากฏแล้ว", dur and os.path.isfile(os.path.join(BASE, "run-dur.bundle.json")))

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
