"""
Unit test ของ p2_atomic — fail-closed atomic publisher (pure/offline)
validate ก่อน publish · atomic (temp→rename) · exception/refuse ไม่ทิ้ง PASS artifact · immutable

    python test_p2_atomic.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

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

# ── valid publish ─────────────────────────────────────────────────────────────
p = AT.publish_m4_bundle(out_dir=BASE, run_id="run-ok", evidence=EV, receipt=RC, validate=lambda: [])
check("publish valid -> final dir + ทั้งสองไฟล์", os.path.isdir(p) and os.path.isfile(os.path.join(p, "evidence.json")) and os.path.isfile(os.path.join(p, "receipt.json")))
check("evidence.json content ตรง", json.load(open(os.path.join(p, "evidence.json"), encoding="utf-8")) == EV)
check("ไม่มี temp ค้างใน out_dir", [n for n in os.listdir(BASE) if n.startswith(".run-ok")] == [])

# ── validate ถูกเรียกก่อนเขียน ; invalid -> refuse + ไม่มีไฟล์ ─────────────────
called = {"n": 0}
def _val_fail():
    called["n"] += 1
    return ["bundle error"]
check("bundle invalid -> PublishRefused", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="run-bad", evidence=EV, receipt=RC, validate=_val_fail)))
check("invalid -> validate ถูกเรียก + ไม่มี final dir", called["n"] == 1 and not os.path.exists(os.path.join(BASE, "run-bad")))

# ── exception ระหว่างเขียน -> ไม่มี final + ไม่มี temp ค้าง ────────────────────
bad_ev = {"bad": {1, 2, 3}}    # set = ไม่ JSON-serializable → json.dump raise TypeError หลัง validate ผ่าน
raised = False
try:
    AT.publish_m4_bundle(out_dir=BASE, run_id="run-exc", evidence=bad_ev, receipt=RC, validate=lambda: [])
except TypeError:
    raised = True
check("exception ระหว่างเขียน -> propagate (ไม่กลืน)", raised)
check("exception -> ไม่มี final dir + temp ถูกลบ", not os.path.exists(os.path.join(BASE, "run-exc")) and not any(n.startswith(".run-exc") for n in os.listdir(BASE)))

# ── immutable: ไม่ overwrite run เดิม ─────────────────────────────────────────
check("overwrite run เดิม -> PublishRefused", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="run-ok", evidence=EV, receipt=RC, validate=lambda: [])))
check("overwrite refuse -> ของเดิมไม่ถูกแตะ", json.load(open(os.path.join(p, "evidence.json"), encoding="utf-8")) == EV)

# ── input guards ──────────────────────────────────────────────────────────────
check("run_id ว่าง -> refuse", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="   ", evidence=EV, receipt=RC, validate=lambda: [])))
check("evidence ไม่ใช่ dict -> refuse", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="r-x", evidence=[1], receipt=RC, validate=lambda: [])))
check("validate ไม่ callable -> refuse", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="r-y", evidence=EV, receipt=RC, validate="nope")))
check("validate คืนไม่ใช่ list -> refuse", refused(lambda: AT.publish_m4_bundle(out_dir=BASE, run_id="r-z", evidence=EV, receipt=RC, validate=lambda: None)))
check("guard fail -> ไม่มี dir ตกค้าง", not any(os.path.exists(os.path.join(BASE, r)) for r in ("r-x", "r-y", "r-z")))

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
