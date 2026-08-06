"""
P2 operational provenance ledger (Codex constraint 3 + reviews 0e04eb7/8066f0e) — **pure/offline**

crash-safe append-only JSONL event ledger (STARTED → terminal ต่อ attempt_id):
  - **OS advisory lock** (`fcntl`/`msvcrt`, non-blocking + bounded retry) — ปล่อยอัตโนมัติเมื่อ process ตาย (M1.1)
  - **newline = commit marker** : tail ที่ไม่ลงท้าย `\n` = uncommitted เสมอ (แม้ JSON parse ผ่าน) ; writer `ftruncate`
    ตัด uncommitted tail **ใต้ lock ก่อน append** (B3.2)
  - **state machine** : STARTED create-once + first ; terminal ครั้งเดียว + ตาม STARTED + run_id ตรง (B3.1)
  - full-write (short write = error) · `allow_nan=False` + size cap · fsync file + fsync parent เมื่อสร้าง log (POSIX)
  - `reconcile` : STARTED ไม่มี terminal = INCOMPLETE ; transition ที่เป็นไปไม่ได้ = ProvenanceError
"""
from __future__ import annotations
import json
import os
import time

MAX_RECORD_BYTES = 65536
_TERMINALS = ("PUBLISHED", "DEGRADED", "FAILED")
_O_BINARY = getattr(os, "O_BINARY", 0)          # Windows: กัน CRLF translation ทำ byte offset เพี้ยน


def _read_all(fd) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        b = os.read(fd, 65536)
        if not b:
            break
        chunks.append(b)
    return b"".join(chunks)

if os.name == "posix":
    import fcntl

    def _try_lock(fd) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
else:
    import msvcrt

    def _try_lock(fd) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class ProvenanceError(Exception):
    """เขียน/อ่าน provenance ไม่สำเร็จ (short write / oversize / corruption / bad transition)"""


class ProvenanceLocked(Exception):
    """acquire lock ไม่ได้ในเวลาที่กำหนด (writer อื่นถืออยู่)"""


def _lock(log_path: str, retries: int = 200, delay: float = 0.005) -> int:
    fd = os.open(log_path + ".lock", os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
    try:
        os.ftruncate(fd, 1)                 # ให้มี ≥1 byte สำหรับ byte-range lock (Windows)
    except OSError:
        pass
    for _ in range(retries):
        if _try_lock(fd):
            return fd                        # ถือ lock ผ่าน fd นี้ (OS ปล่อยเองเมื่อ process ตาย)
        time.sleep(delay)
    os.close(fd)
    raise ProvenanceLocked(f"provenance lock ถูกถือค้าง: {log_path}.lock")


def _release(fd: int) -> None:
    try:
        _unlock(fd)
    finally:
        os.close(fd)


def _serialize(record: dict) -> bytes:
    if not isinstance(record, dict):
        raise TypeError("provenance record ต้องเป็น dict")
    try:
        line = (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as e:
        raise ProvenanceError(f"record serialize ไม่ได้ (allow_nan=False): {e!r}") from e
    if len(line) > MAX_RECORD_BYTES:
        raise ProvenanceError(f"record ใหญ่เกิน {MAX_RECORD_BYTES} bytes")
    return line


def _parse_committed(committed: bytes) -> list:
    """parse เฉพาะบรรทัดที่ commit (ปิดด้วย \\n) — interior parse error = corruption (raise)"""
    out = []
    for ln in committed.split(b"\n"):
        if not ln:
            continue
        try:
            out.append(json.loads(ln.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ProvenanceError(f"provenance log corrupt (committed line parse): {e!r}") from e
    return out


def _validate_transition(records: list, record: dict) -> None:
    aid, ev = record.get("attempt_id"), record.get("event")
    if not isinstance(aid, str) or not aid:
        raise ProvenanceError("event record ต้องมี attempt_id (str)")
    started = [r for r in records if r.get("attempt_id") == aid and r.get("event") == "STARTED"]
    terminal = [r for r in records if r.get("attempt_id") == aid and r.get("event") in _TERMINALS]
    if ev == "STARTED":
        if started:
            raise ProvenanceError(f"duplicate STARTED สำหรับ attempt {aid!r} (create-once)")
    elif ev in _TERMINALS:
        if not started:
            raise ProvenanceError(f"terminal ก่อน STARTED สำหรับ attempt {aid!r}")
        if terminal:
            raise ProvenanceError(f"duplicate terminal สำหรับ attempt {aid!r}")
        if record.get("run_id") != started[0].get("run_id"):
            raise ProvenanceError(f"terminal run_id != STARTED สำหรับ attempt {aid!r}")
    else:
        raise ProvenanceError(f"event ไม่รู้จัก: {ev!r}")


def _locked_append(log_path: str, record: dict, validate_state) -> None:
    line = _serialize(record)
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    lockfd = _lock(log_path)
    try:
        fd = os.open(log_path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
        try:
            data = _read_all(fd)
            cut = data.rfind(b"\n") + 1                    # committed = ถึงหลัง \n สุดท้าย (B3.2)
            if validate_state is not None:
                validate_state(_parse_committed(data[:cut]), record)
            if cut != len(data):                           # มี uncommitted tail → ตัดทิ้งก่อน append
                os.ftruncate(fd, cut)
                os.fsync(fd)
            os.lseek(fd, cut, os.SEEK_SET)
            mv = memoryview(line)
            while mv:
                n = os.write(fd, mv)
                if n <= 0:
                    raise ProvenanceError("os.write คืน 0 (เขียนไม่คืบ)")
                mv = mv[n:]
            os.fsync(fd)
            created = (cut == 0)
        finally:
            os.close(fd)
        if created and d and os.name == "posix":
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)                              # parent durability เมื่อสร้าง log ใหม่
            finally:
                os.close(dfd)
    finally:
        _release(lockfd)


def append_provenance(log_path: str, record: dict) -> None:
    """low-level append (lock + tail-truncate + full-write + fsync) — ไม่บังคับ state machine"""
    _locked_append(log_path, record, None)


def append_event(log_path: str, record: dict) -> None:
    """append event ledger (STARTED/terminal) พร้อม state machine — bad transition = ProvenanceError"""
    _locked_append(log_path, record, _validate_transition)


def read_provenance(log_path: str) -> list:
    """อ่าน records ที่ commit แล้ว (ปิดด้วย \\n) — tail ไม่มี newline = uncommitted → drop ; interior corrupt = raise"""
    if not os.path.exists(log_path):
        return []
    with open(log_path, "rb") as f:
        data = f.read()
    cut = data.rfind(b"\n") + 1
    return _parse_committed(data[:cut])


def reconcile(records: list) -> dict:
    """map attempt_id → terminal status ; STARTED ไม่มี terminal = INCOMPLETE ; transition ผิด = ProvenanceError"""
    st = {}
    for r in records:
        aid, ev = r.get("attempt_id"), r.get("event")
        if not isinstance(aid, str):
            raise ProvenanceError("record ไม่มี attempt_id (str)")
        s = st.setdefault(aid, {"started": 0, "terminal": None, "tcount": 0})
        if ev == "STARTED":
            s["started"] += 1
        elif ev in _TERMINALS:
            s["terminal"] = ev
            s["tcount"] += 1
        else:
            raise ProvenanceError(f"event ไม่รู้จัก: {ev!r}")
    out = {}
    for aid, s in st.items():
        if s["started"] > 1:
            raise ProvenanceError(f"duplicate STARTED: {aid}")
        if s["tcount"] > 1:
            raise ProvenanceError(f"duplicate terminal: {aid}")
        if s["terminal"] and s["started"] == 0:
            raise ProvenanceError(f"terminal ก่อน STARTED: {aid}")
        out[aid] = s["terminal"] or ("INCOMPLETE" if s["started"] else "UNKNOWN")
    return out
