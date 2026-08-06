"""
P2 operational provenance ledger (Codex constraints 3 + review 0e04eb7 M1/B3) — **pure/offline**

event ledger แบบ STARTED → terminal (PUBLISHED|DEGRADED|FAILED) ต่อ attempt_id ที่ **อยู่รอดข้าม process**:
  - single-writer lock (O_EXCL lock file, bounded retry) — reject writer ที่สอง (M1 concurrency)
  - full-write ตรวจ byte ครบ (short write = ProvenanceError, ไม่รายงาน success) ; record size จำกัด ; canonical JSON allow_nan=False
  - fsync ไฟล์ + fsync parent directory ตอนสร้าง log ใหม่ (POSIX) — ล้ม = ProvenanceError
  - read: recover partial tail (บรรทัดสุดท้ายพัง = truncated → drop) ; interior พัง = corruption (raise)
  - reconcile: STARTED ที่ไม่มี terminal = INCOMPLETE
"""
from __future__ import annotations
import json
import os
import time

import p2_atomic as AT

MAX_RECORD_BYTES = 65536
_TERMINALS = ("PUBLISHED", "DEGRADED", "FAILED")


class ProvenanceError(Exception):
    """เขียน provenance ไม่สำเร็จ (short write / oversize / fsync fail / log corruption)"""


class ProvenanceLocked(Exception):
    """acquire single-writer lock ไม่ได้ (มี writer อื่นถืออยู่)"""


def _acquire_lock(lock_path: str, retries: int = 200, delay: float = 0.005) -> None:
    for _ in range(retries):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            return
        except FileExistsError:
            time.sleep(delay)
    raise ProvenanceLocked(f"provenance lock ถูกถือค้าง: {lock_path}")


def _release_lock(lock_path: str) -> None:
    try:
        os.unlink(lock_path)
    except OSError:
        pass


def append_provenance(log_path: str, record: dict) -> None:
    """append 1 event record (JSONL) แบบ single-writer, full-write, fsync ; ล้ม = ProvenanceError/ProvenanceLocked"""
    if not isinstance(record, dict):
        raise TypeError("provenance record ต้องเป็น dict")
    try:
        line = (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as e:
        raise ProvenanceError(f"record serialize ไม่ได้ (allow_nan=False): {e!r}") from e
    if len(line) > MAX_RECORD_BYTES:
        raise ProvenanceError(f"record ใหญ่เกิน {MAX_RECORD_BYTES} bytes")
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    lock = log_path + ".lock"
    _acquire_lock(lock)
    try:
        existed = os.path.exists(log_path)
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            mv = memoryview(line)
            while mv:
                n = os.write(fd, mv)
                if n <= 0:
                    raise ProvenanceError("os.write คืน 0 (เขียนไม่คืบ)")
                mv = mv[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed and d and os.name == "posix":
            try:
                AT._fsync_dir(d)               # fsync parent เมื่อสร้าง log ใหม่ (POSIX durability)
            except OSError as e:
                raise ProvenanceError(f"fsync parent dir ของ log ใหม่ล้ม: {e!r}") from e
    finally:
        _release_lock(lock)


def read_provenance(log_path: str) -> list:
    """อ่าน records ทั้งหมด — recover truncated tail (บรรทัดสุดท้ายพัง = drop) ; interior พัง = ProvenanceError"""
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8") as f:
        lines = [ln for ln in f.read().split("\n") if ln != ""]
    out = []
    for i, ln in enumerate(lines):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break                          # partial tail (process ตายกลางเขียน) → drop
            raise ProvenanceError(f"provenance log corrupt ที่บรรทัด {i}")
    return out


def reconcile(records: list) -> dict:
    """map attempt_id → terminal status ; STARTED ที่ไม่มี terminal = INCOMPLETE (ตายกลางทาง)"""
    by = {}
    for r in records:
        aid, ev = r.get("attempt_id"), r.get("event")
        if not isinstance(aid, str):
            continue
        st = by.setdefault(aid, {"started": False, "terminal": None})
        if ev == "STARTED":
            st["started"] = True
        elif ev in _TERMINALS:
            st["terminal"] = ev
    return {aid: (st["terminal"] or ("INCOMPLETE" if st["started"] else "UNKNOWN")) for aid, st in by.items()}
