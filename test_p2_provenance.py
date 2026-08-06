"""
Unit test ของ p2_provenance — operational provenance log ข้าม process (pure/offline)

    python test_p2_provenance.py
"""
import io
import os
import shutil
import sys
import tempfile

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
LOG = os.path.join(BASE, "sub", "prov.jsonl")   # dir ยังไม่มี → ต้องสร้างเอง

PV.append_provenance(LOG, {"run_id": "r1", "status": "PUBLISHED", "durability_mode": "durable"})
PV.append_provenance(LOG, {"run_id": "r1", "status": "DEGRADED", "error_type": "DurabilityUnconfirmed"})
recs = PV.read_provenance(LOG)
check("append + read 2 records ตามลำดับ", len(recs) == 2 and recs[0]["status"] == "PUBLISHED" and recs[1]["status"] == "DEGRADED")
check("record มี field ครบ", recs[0]["durability_mode"] == "durable" and recs[1]["error_type"] == "DurabilityUnconfirmed")
check("dir ถูกสร้างอัตโนมัติ + log เป็นไฟล์", os.path.isfile(LOG))
check("read log ที่ไม่มี -> []", PV.read_provenance(os.path.join(BASE, "nope.jsonl")) == [])
check("record ไม่ใช่ dict -> TypeError", raises(lambda: PV.append_provenance(LOG, ["x"]), TypeError))
# อ่านซ้ำจากไฟล์ (จำลอง process ใหม่) ต้องได้ครบ
check("อ่านซ้ำ (ข้าม process) -> ครบ 2", len(PV.read_provenance(LOG)) == 2)

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
