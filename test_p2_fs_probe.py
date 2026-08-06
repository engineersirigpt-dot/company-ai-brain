"""
Unit test ของ p2_fs_probe — output-fs capability probe (pure/offline)
hardlink no-clobber + cleanup + durability mode ; ไม่รองรับ -> CapabilityError ; ไม่ทิ้ง probe ค้าง

    python test_p2_fs_probe.py
"""
import io
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_fs_probe as FS

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True

BASE = tempfile.mkdtemp(prefix="fsprobe-")

cap = FS.probe_output_fs(BASE)
check("probe ok -> hardlink_no_clobber + cleanup_ok", cap["hardlink_no_clobber"] and cap["cleanup_ok"])
check("durability_mode ตาม platform", cap["durability_mode"] in ("durable", "atomic-visibility-only"))
check("out_dir = realpath", cap["out_dir"] == os.path.realpath(BASE))
check("ไม่มี probe dir ค้างหลัง probe สำเร็จ", not any(n.startswith(".fsprobe.") for n in os.listdir(BASE)))

_rl = os.link
os.link = lambda a, b: (_ for _ in ()).throw(OSError("no hardlink support"))
try:
    hl = raises(lambda: FS.probe_output_fs(BASE), FS.CapabilityError)
finally:
    os.link = _rl
check("hardlink ไม่รองรับ -> CapabilityError", hl)

os.link = lambda a, b: None       # ไม่เคย raise FileExistsError -> ไม่ atomic no-clobber
try:
    nc = raises(lambda: FS.probe_output_fs(BASE), FS.CapabilityError)
finally:
    os.link = _rl
check("hardlink ไม่ no-clobber (ไม่ raise FileExistsError) -> CapabilityError", nc)
check("probe ล้ม -> ไม่มี probe dir ค้าง", not any(n.startswith(".fsprobe.") for n in os.listdir(BASE)))

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
