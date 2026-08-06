"""
P2 atomic fail-closed artifact publisher — publish M4 evidence+receipt เป็นหน่วยเดียว (all-or-nothing)

Codex load-bearing check #6: atomic writer ต้อง **ไม่ทิ้ง PASS artifact** เมื่อ exception/non-zero/partial write
และต้อง **validate public bundle ก่อน publish** final receipt/evidence

contract:
  - validate() ถูกเรียก **ก่อน** เขียนอะไรทั้งสิ้น ; ไม่ว่าง = PublishRefused (ไม่มีไฟล์ถูกสร้าง)
  - เขียนลง temp dir → fsync ไฟล์+dir → `os.replace` temp→final (atomic rename) ; partial write อยู่ใน temp เท่านั้น
  - exception ระหว่างเขียน → ลบ temp ทิ้ง ; final ไม่เคยถูกสร้าง
  - run_id ที่มี artifact อยู่แล้ว → PublishRefused (immutable — ไม่ overwrite run เดิม)
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile


class PublishRefused(Exception):
    """bundle invalid / run มีอยู่แล้ว / อินพุตผิด — ไม่มี PASS artifact ถูกเขียน"""


def _write_json_fsync(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass          # Windows: fsync บน dir handle ไม่รองรับ — best-effort
    finally:
        os.close(fd)


def publish_m4_bundle(*, out_dir: str, run_id: str, evidence: dict, receipt: dict, validate) -> str:
    """
    validate: callable คืน list ของ error (ว่าง = bundle ผ่าน public gate). ต้องเป็น bound closure
              ที่ตรวจ (plan, frozen, evidence, receipt) ให้ครบก่อน publish
    เขียน out_dir/<run_id>/{evidence.json, receipt.json} แบบ atomic. คืน path ของ final dir.
    ล้มเหลว/ปฏิเสธ → raise PublishRefused (หรือ propagate exception) โดย **ไม่ทิ้ง final artifact**
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise PublishRefused("run_id ว่าง/ผิดชนิด")
    if not isinstance(evidence, dict) or not isinstance(receipt, dict):
        raise PublishRefused("evidence/receipt ต้องเป็น dict")
    if not callable(validate):
        raise PublishRefused("validate ต้องเป็น callable (public bundle gate)")

    errs = validate()                                   # ตรวจ "ก่อน" เขียน (fail-closed)
    if not isinstance(errs, list):
        raise PublishRefused("validate ต้องคืน list ของ error")
    if errs:
        raise PublishRefused(f"public bundle invalid ({len(errs)} errors) — ไม่ publish PASS artifact: {errs[:3]}")

    final = os.path.join(out_dir, run_id)
    if os.path.exists(final):
        raise PublishRefused(f"run {run_id!r} มี artifact อยู่แล้ว (immutable — ไม่ overwrite)")
    os.makedirs(out_dir, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix=f".{run_id}.", dir=out_dir)   # temp อยู่ filesystem เดียวกับ final (rename atomic)
    try:
        _write_json_fsync(os.path.join(tmp, "evidence.json"), evidence)
        _write_json_fsync(os.path.join(tmp, "receipt.json"), receipt)
        _fsync_dir(tmp)
        if os.path.exists(final):                       # กัน race: run โผล่มาระหว่างเขียน
            raise PublishRefused(f"run {run_id!r} โผล่ระหว่าง publish (immutable)")
        os.replace(tmp, final)                          # atomic: final ปรากฏครบทั้ง bundle หรือไม่ปรากฏเลย
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)          # partial write ถูกลบ — ไม่มี PASS artifact ตกค้าง
        raise
    return final
