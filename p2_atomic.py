"""
P2 atomic fail-closed artifact publisher — publish M4 bundle เป็น **ไฟล์เดียว** (single authority)

Codex review (bfa69a0): ใช้ `<run_id>.bundle.json` = `{evidence, receipt}` ไฟล์เดียว ง่าย+เสี่ยงน้อยกว่า
two-file directory ; validate ก่อน publish (#6) · atomic no-clobber · path containment (M1) · fsync parent (M2)

contract:
  - `run_id` ต้องเป็น safe basename (ไม่มี separator/`.`/`..`/absolute/drive/UNC/reserved) — กัน path injection
  - validate() ถูกเรียก **ก่อน** เขียน ; ไม่ว่าง = PublishRefused (ไม่มีไฟล์ถูกสร้าง)
  - เขียน temp file (fsync) → `os.replace` temp→final (atomic visibility) → fsync parent dir (crash durability)
  - exception/refuse → ลบ temp ; final ไม่เคยถูกสร้าง ; final ที่มีอยู่แล้ว → PublishRefused (immutable)
"""
from __future__ import annotations
import json
import os
import re
import tempfile

SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Windows reserved device names (มี/ไม่มีนามสกุลก็สงวน) — reject case-insensitive
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class PublishRefused(Exception):
    """bundle invalid / run มีอยู่แล้ว / run_id ไม่ปลอดภัย / อินพุตผิด — ไม่มี PASS artifact ถูกเขียน"""


def _check_run_id(run_id) -> None:
    if not isinstance(run_id, str) or not run_id.strip():
        raise PublishRefused("run_id ว่าง/ผิดชนิด")
    if not SAFE_RUN_ID_RE.match(run_id):
        raise PublishRefused(f"run_id ไม่ปลอดภัย (ต้อง ^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$): {run_id!r}")
    if run_id.split(".", 1)[0].lower() in _RESERVED:
        raise PublishRefused(f"run_id ชนกับ reserved device name: {run_id!r}")


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return                # Windows: เปิด dir handle เพื่อ fsync ไม่ได้ — atomic-visibility only บน platform นี้
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def publish_m4_bundle(*, out_dir: str, run_id: str, evidence: dict, receipt: dict, validate) -> str:
    """
    validate: callable คืน list ของ error (ว่าง = ผ่าน public gate). ตรวจ (plan, frozen, evidence, receipt)
              ให้ครบก่อน publish. เขียน out_dir/<run_id>.bundle.json แบบ atomic. คืน path ของ bundle.
    ล้มเหลว/ปฏิเสธ → raise PublishRefused โดย **ไม่ทิ้ง artifact**
    """
    _check_run_id(run_id)
    if not isinstance(evidence, dict) or not isinstance(receipt, dict):
        raise PublishRefused("evidence/receipt ต้องเป็น dict")
    if not callable(validate):
        raise PublishRefused("validate ต้องเป็น callable (public bundle gate)")

    errs = validate()                                   # ตรวจ "ก่อน" เขียน (fail-closed)
    if not isinstance(errs, list):
        raise PublishRefused("validate ต้องคืน list ของ error")
    if errs:
        raise PublishRefused(f"public bundle invalid ({len(errs)} errors) — ไม่ publish PASS artifact: {errs[:3]}")

    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, run_id + ".bundle.json")
    # M1: containment — resolved final ต้องอยู่ใน out_dir พอดี (กัน symlink/edge หลงหลุด)
    if os.path.dirname(os.path.realpath(final)) != os.path.realpath(out_dir):
        raise PublishRefused(f"run_id หลุดออกนอก out_dir: {run_id!r}")
    if os.path.exists(final):
        raise PublishRefused(f"run {run_id!r} มี artifact อยู่แล้ว (immutable — ไม่ overwrite)")

    fd, tmp = tempfile.mkstemp(prefix=f".{run_id}.", suffix=".tmp", dir=out_dir)   # temp filesystem เดียวกับ final
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"evidence": evidence, "receipt": receipt}, f,
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(final):                       # กัน race: run โผล่ระหว่างเขียน
            raise PublishRefused(f"run {run_id!r} โผล่ระหว่าง publish (immutable)")
        os.replace(tmp, final)                          # atomic: bundle ปรากฏครบหรือไม่ปรากฏเลย
    except BaseException:
        try:
            os.unlink(tmp)                              # partial write ถูกลบ — ไม่มี PASS artifact ตกค้าง
        except OSError:
            pass
        raise
    _fsync_dir(out_dir)                                 # M2: parent durability หลัง rename (best-effort บน Windows)
    return final
