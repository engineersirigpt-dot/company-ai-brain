"""
P2 output-filesystem capability probe (Codex constraint 1) — **pure/offline**

รันบน output filesystem จริง **ก่อน provision/model** เพื่อยืนยันว่า publisher (p2_atomic) รับประกันได้จริง:
  - hard-link create-if-absent (immutability authority ของ publisher คือ `os.link`)
  - no-clobber (link ซ้ำ = FileExistsError)
  - temp cleanup (unlink ได้)
  - durability mode (POSIX durable / non-POSIX atomic-visibility-only)
ถ้า filesystem ไม่รองรับ (FAT/exFAT/บาง network fs) → `CapabilityError` — fail ก่อนเสีย model run ทั้งรอบ
(ไม่ fail-open : publisher จะ fail ก่อนสร้าง final อยู่แล้ว ; probe แค่ทำให้รู้ตัวก่อน provision)
"""
from __future__ import annotations
import os
import shutil
import tempfile

import p2_atomic as AT


class CapabilityError(Exception):
    """output filesystem ไม่รองรับ guarantee ของ publisher — ไม่ควร provision/รัน model บน fs นี้"""


def probe_output_fs(out_dir: str) -> dict:
    """
    ตรวจ capability บน out_dir จริง. คืน dict provenance (hardlink_no_clobber, cleanup_ok, durability_mode,
    out_dir realpath). ไม่รองรับ → `CapabilityError`. ไม่ทิ้งไฟล์ probe (ลบทุกกรณี)
    """
    os.makedirs(out_dir, exist_ok=True)
    probe_dir = tempfile.mkdtemp(prefix=".fsprobe.", dir=out_dir)
    try:
        a, b = os.path.join(probe_dir, "a"), os.path.join(probe_dir, "b")
        with open(a, "w", encoding="utf-8") as f:
            f.write("probe")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(a, b)                                   # hard link รองรับไหม
        except OSError as e:
            raise CapabilityError(f"filesystem ไม่รองรับ hard link — publisher no-clobber ใช้ไม่ได้: {e!r}") from e
        try:
            os.link(a, b)                                   # link ซ้ำต้อง FileExistsError (no-clobber จริง)
        except FileExistsError:
            pass
        else:
            raise CapabilityError("hard link ไม่ atomic no-clobber (link ซ้ำแล้วไม่ FileExistsError)")
        cleanup_ok = True
        try:
            os.unlink(a)
            os.unlink(b)
        except OSError as e:
            cleanup_ok = False
            raise CapabilityError(f"temp cleanup (unlink) ล้มเหลวบน fs นี้: {e!r}") from e
        return {"hardlink_no_clobber": True, "cleanup_ok": cleanup_ok,
                "durability_mode": AT.durability_mode(), "out_dir": os.path.realpath(out_dir)}
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
