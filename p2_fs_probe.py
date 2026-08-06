"""
P2 output-filesystem capability probe (Codex constraint 1 + review 0e04eb7 B1/B2) — **pure/offline**

รันบน output filesystem จริง **ก่อน provision/model** เพื่อยืนยันว่า publisher (p2_atomic) รับประกันได้จริง:
  - hard-link create-if-absent + no-clobber (link ซ้ำ = FileExistsError) — immutability authority ของ publisher
  - temp cleanup ได้จริง (ไม่ ignore เงียบ — B2)
  - **directory durability primitive เดียวกับ publisher** (`AT._fsync_dir`) — POSIX dir fsync fail → CapabilityError (B1)
operational filesystem error ทุกชนิด (makedirs/mkdtemp/open/fsync/link/unlink/rmtree) → `CapabilityError` (B2, chained)
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
    ตรวจ capability บน out_dir จริง. คืน {hardlink_no_clobber, cleanup_ok, durability_mode, out_dir}.
    ไม่รองรับ/error ใด ๆ → `CapabilityError` (ก่อน provision/model). cleanup ล้ม (success path) = fail + ระบุ path
    """
    probe_dir = None
    ok = False
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe_dir = tempfile.mkdtemp(prefix=".fsprobe.", dir=out_dir)
        a, b = os.path.join(probe_dir, "a"), os.path.join(probe_dir, "b")
        with open(a, "w", encoding="utf-8") as f:
            f.write("probe")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(a, b)                                  # hard link รองรับไหม
        except OSError as e:
            raise CapabilityError(f"filesystem ไม่รองรับ hard link — publisher no-clobber ใช้ไม่ได้: {e!r}") from e
        clobbered = True
        try:
            os.link(a, b)                                  # link ซ้ำต้อง FileExistsError (no-clobber จริง)
        except FileExistsError:
            clobbered = False
        if clobbered:
            raise CapabilityError("hard link ไม่ atomic no-clobber (link ซ้ำแล้วไม่ FileExistsError)")
        os.unlink(a)
        os.unlink(b)
        dm = AT._fsync_dir(out_dir)                        # B1: primitive เดียวกับ publisher (POSIX fail → OSError ด้านล่าง)
        durability = "durable" if dm == "durable" else "atomic-visibility-only"
        result = {"hardlink_no_clobber": True, "cleanup_ok": True,
                  "durability_mode": durability, "out_dir": os.path.realpath(out_dir)}
        ok = True
        return result
    except CapabilityError:
        raise
    except OSError as e:                                    # B2: normalize operational fs error ทุกชนิด
        raise CapabilityError(f"filesystem operation ล้มบน output fs: {e!r}") from e
    finally:
        if probe_dir is not None:
            try:
                shutil.rmtree(probe_dir)                    # B2: ไม่ ignore_errors เป็น authority
            except OSError as ce:
                if ok:                                     # success path เท่านั้น (ไม่กลบ error เดิม)
                    raise CapabilityError(f"cleanup probe dir ล้ม (ตรวจ manual: {probe_dir}): {ce!r}") from ce
