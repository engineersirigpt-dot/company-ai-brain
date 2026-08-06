"""
P2 atomic fail-closed artifact publisher — publish M4 bundle เป็น **ไฟล์เดียว** (single authority)

Codex re-review (3d30b15):
  - immutability ต้องเป็น **atomic no-clobber** จริง — ใช้ `os.link` (create-if-absent) ไม่ใช่ check-then-`os.replace` (B1)
  - run_id contract = validator **ตัวเดียว** แชร์กับ RunPlan (`is_safe_run_id`, `fullmatch`, reserved, no control/newline) (M1)
  - parent fsync durability พูดตามจริง: POSIX fsync fail → raise `DurabilityUnconfirmed` ; Windows (เปิด dir fd ไม่ได้) = atomic-visibility only (M3)

contract:
  - validate() ก่อนเขียน ; ไม่ว่าง = PublishRefused (ไม่มีไฟล์)
  - เขียน temp (fsync) → `os.link(temp, final)` atomic no-clobber → unlink temp → fsync parent
  - final มีอยู่แล้ว (แม้ race) → `FileExistsError` → `PublishRefused` (immutable, exactly-one winner)
  - exception → ลบ temp ; final ที่ยังไม่ publish ไม่ถูกสร้าง
"""
from __future__ import annotations
import json
import os
import re
import tempfile

# M1: run_id = artifact path component — validator เดียวใช้ทั้ง RunPlan และ publisher (fullmatch กัน trailing newline)
_SAFE_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def is_safe_run_id(run_id) -> bool:
    """safe basename: non-empty ASCII [A-Za-z0-9._-] · fullmatch · <=128 · ไม่ชน Windows reserved device name"""
    if not isinstance(run_id, str) or not _SAFE_RUN_ID_RE.fullmatch(run_id):
        return False
    return run_id.split(".", 1)[0].lower() not in _RESERVED


class PublishRefused(Exception):
    """bundle invalid / run มีอยู่แล้ว / run_id ไม่ปลอดภัย / อินพุตผิด — ไม่มี PASS artifact ถูกเขียน"""


class DurabilityUnconfirmed(Exception):
    """bundle ถูก publish (final ปรากฏแล้ว) แต่ parent fsync ล้มเหลว → crash durability ไม่ยืนยัน (POSIX)"""


class CleanupUnconfirmed(Exception):
    """bundle ถูก publish (final ปรากฏแล้ว) แต่ลบ temp ไม่สำเร็จ → temp hard-link อาจค้าง (ต้องตรวจ manual)"""


def durability_mode() -> str:
    """M3.1: โหมด durability ตาม platform (persist ใน operational result ไม่ใช่แค่ docstring)"""
    return "durable" if os.name == "posix" else "atomic-visibility-only"


def _fsync_dir(path: str) -> str:
    """
    M3.1: POSIX → fsync parent, **propagate OSError ทุกชนิด** (ไม่เหมารวมเป็น unsupported) ; คืน 'durable'
    non-POSIX (Windows) → 'unsupported' (atomic-visibility only, ระบุ explicit ตาม platform ไม่ใช่ catch-all)
    """
    if os.name != "posix":
        return "unsupported"
    fd = os.open(path, os.O_RDONLY)   # POSIX: EACCES/EIO/ENOENT/... propagate → DurabilityUnconfirmed
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return "durable"


def _note(exc, msg) -> None:
    try:
        exc.add_note(msg)
    except Exception:
        pass


def _unlink(path: str):
    """คืน exception (ไม่ raise) เพื่อให้ caller surface temp cleanup failure (M3.2)"""
    try:
        os.unlink(path)
        return None
    except OSError as e:
        return e


def publish_m4_bundle(*, out_dir: str, run_id: str, evidence: dict, receipt: dict, validate) -> str:
    """
    validate: callable คืน list ของ error (ว่าง = ผ่าน public gate). เขียน out_dir/<run_id>.bundle.json แบบ
    atomic no-clobber. คืน path ของ bundle. collision/ปฏิเสธ → PublishRefused ; parent fsync fail (POSIX) → DurabilityUnconfirmed
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise PublishRefused("run_id ว่าง/ผิดชนิด")
    if not is_safe_run_id(run_id):
        raise PublishRefused(f"run_id ไม่ปลอดภัย (ASCII safe basename, ไม่ reserved/control/newline): {run_id!r}")
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
    if os.path.dirname(os.path.realpath(final)) != os.path.realpath(out_dir):   # M1: containment (กัน symlink edge)
        raise PublishRefused(f"run_id หลุดออกนอก out_dir: {run_id!r}")

    fd, tmp = tempfile.mkstemp(prefix=f".{run_id}.", suffix=".tmp", dir=out_dir)   # temp filesystem เดียวกับ final
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"evidence": evidence, "receipt": receipt}, f,
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, final)                         # B1: atomic no-clobber — FileExistsError ถ้า final มี (exactly-one)
        except FileExistsError:
            raise PublishRefused(f"run {run_id!r} มี artifact อยู่แล้ว (immutable — atomic no-clobber)")
    except BaseException as e:
        uerr = _unlink(tmp)                             # collision/error path: temp ถูกลบ ; ถ้าลบไม่ได้ surface (M3.2) ไม่กลบ primary
        if uerr is not None:
            _note(e, f"temp cleanup ล้ม: {uerr!r} — {tmp} อาจค้าง (ตรวจ manual)")
        raise
    uerr = _unlink(tmp)                                 # success path: final อยู่แล้วผ่าน link อีกชื่อ → เอา temp name ออก
    if uerr is not None:                                # M3.2: ห้ามคืน PUBLISHED ปกติถ้า temp cleanup ล้ม (hidden hard-link)
        raise CleanupUnconfirmed(f"bundle publish แล้วที่ {final} แต่ลบ temp {tmp} ไม่สำเร็จ: {uerr!r}")
    try:
        _fsync_dir(out_dir)                             # M3.1: parent durability ; POSIX genuine fsync fail → raise (final publish แล้ว)
    except OSError as e:
        raise DurabilityUnconfirmed(f"bundle publish แล้วที่ {final} แต่ parent fsync ล้ม — crash durability ไม่ยืนยัน") from e
    return final
