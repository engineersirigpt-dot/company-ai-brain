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
import re
import time
from datetime import datetime

MAX_RECORD_BYTES = 65536
_TERMINALS = ("PUBLISHED", "DEGRADED", "FAILED")
_O_BINARY = getattr(os, "O_BINARY", 0)          # Windows: กัน CRLF translation ทำ byte offset เพี้ยน
_SHA_RE = re.compile(r"[0-9a-f]{64}")


def _is_sha256(x) -> bool:
    return isinstance(x, str) and bool(_SHA_RE.fullmatch(x))


def _valid_iso_tz(x) -> bool:
    if not isinstance(x, str):
        return False
    try:
        dt = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.utcoffset() is not None


def _intent_path(log_path: str) -> str:
    return log_path + ".intent"


def _fsync_parent(d: str) -> None:
    """POSIX: fsync directory (directory-entry durability) — fail → propagate (durability boundary, B3.2-P.2) ;
    non-POSIX (Windows): dir fd fsync ไม่รองรับ → atomic-visibility only (no-op)"""
    if not d or os.name != "posix":
        return
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _check_intent(log_path: str) -> None:
    """write-ahead intent ค้าง = append ก่อนหน้าไม่ยืนยัน outcome → ledger indeterminate (fail-closed, ต้อง operator repair)"""
    if os.path.exists(_intent_path(log_path)):
        raise ProvenanceIndeterminate(f"unresolved write-ahead intent — ledger indeterminate: {_intent_path(log_path)}")


def _write_intent(log_path: str, record: dict) -> None:
    """สร้าง durable intent (no-clobber + fsync file + fsync parent) **ก่อนแตะ ledger** (B3.2-P.1a) ; ล้ม → propagate (ledger untouched)"""
    ip = _intent_path(log_path)
    fd = os.open(ip, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY, 0o644)
    try:
        body = json.dumps({"attempt_id": record.get("attempt_id"), "event": record.get("event")},
                          ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        mv = memoryview(body)
        while mv:
            n = os.write(fd, mv)
            if n <= 0:
                raise ProvenanceError("intent write คืน 0")
            mv = mv[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_parent(os.path.dirname(log_path))


def _clear_intent(log_path: str) -> None:
    """ลบ intent + fsync parent เมื่อ outcome **ยืนยันแล้วเท่านั้น** ; ล้ม → ProvenanceIndeterminate (intent ค้าง = fail-closed)"""
    ip = _intent_path(log_path)
    try:
        os.unlink(ip)
        _fsync_parent(os.path.dirname(log_path))
    except OSError as e:
        raise ProvenanceIndeterminate(f"clear write-ahead intent ล้ม — ledger indeterminate: {ip}") from e


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


class ProvenanceIndeterminate(Exception):
    """commit หรือ rollback ยืนยันไม่ได้ → ledger poisoned ; read/reconcile/append ต้อง fail-closed จน operator repair"""


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


def _validate_terminal_schema(r: dict) -> None:
    """
    M3.2-A: enforce event-specific schema ที่ **ledger boundary** — terminal ต้องมี binding ครบ ไม่งั้น
    audit authority ยอมรับ clean terminal ที่ไม่มีหลักฐานผูก run/artifact ได้
    """
    ev = r.get("event")
    if ev == "STARTED":
        if not (isinstance(r.get("started_at"), str) and _valid_iso_tz(r["started_at"])):
            raise ProvenanceError("STARTED ต้องมี started_at (ISO+tz)")
        return
    if not _valid_iso_tz(r.get("finished_at")):
        raise ProvenanceError(f"{ev} ต้องมี finished_at (ISO+tz)")
    if ev == "PUBLISHED":
        for k in ("artifact_sha256", "evidence_body_sha256", "run_receipt_sha256"):
            if not _is_sha256(r.get(k)):
                raise ProvenanceError(f"PUBLISHED ต้องมี {k} (64-hex)")
        if not isinstance(r.get("capability"), dict):
            raise ProvenanceError("PUBLISHED ต้องมี capability (dict)")
        if not isinstance(r.get("path"), str) or not r["path"]:
            raise ProvenanceError("PUBLISHED ต้องมี path (str)")
        if r.get("clock_anomaly"):                         # M3.1: clock anomaly load-bearing — PUBLISHED ต้อง clean
            raise ProvenanceError("PUBLISHED ห้ามมี clock_anomaly (ต้อง downgrade เป็น DEGRADED)")
    elif ev == "DEGRADED":
        if not isinstance(r.get("capability"), dict):
            raise ProvenanceError("DEGRADED ต้องมี capability (dict)")
    elif ev == "FAILED":
        if not isinstance(r.get("phase"), str) or not r["phase"]:
            raise ProvenanceError("FAILED ต้องมี phase (str)")


def _reduce(records: list) -> dict:
    """
    **reducer เดียว** (order-sensitive + terminal schema) ใช้ทั้ง append validation และ reconcile (B3.1-R/M3.2-A) —
    consume record ตามลำดับ: state ว่าง → รับเฉพาะ STARTED ; state STARTED → รับ terminal ครั้งเดียว
    (run_id ตรง + `status == event` + terminal schema ครบ) ; state terminal → รับเพิ่มไม่ได้
    คืน {attempt_id: terminal_status | 'INCOMPLETE'} ; ลำดับ/run/status/schema ผิด → ProvenanceError
    """
    state = {}
    for r in records:
        if not isinstance(r, dict):
            raise ProvenanceError("record ไม่ใช่ dict")
        aid, ev, rid = r.get("attempt_id"), r.get("event"), r.get("run_id")
        if not isinstance(aid, str) or not aid:
            raise ProvenanceError("record ไม่มี attempt_id (str)")
        cur = state.get(aid)
        if ev == "STARTED":
            if cur is not None:
                raise ProvenanceError(f"STARTED ซ้ำ/ผิดลำดับ: {aid}")
            _validate_terminal_schema(r)
            state[aid] = {"run_id": rid, "terminal": None}
        elif ev in _TERMINALS:
            if cur is None:
                raise ProvenanceError(f"terminal ก่อน STARTED: {aid}")
            if cur["terminal"] is not None:
                raise ProvenanceError(f"terminal ซ้ำ: {aid}")
            if rid != cur["run_id"]:
                raise ProvenanceError(f"terminal run_id != STARTED: {aid}")
            if r.get("status") != ev:
                raise ProvenanceError(f"status != event: {aid}")
            _validate_terminal_schema(r)
            cur["terminal"] = ev
        else:
            raise ProvenanceError(f"event ไม่รู้จัก: {ev!r}")
    return {aid: (s["terminal"] or "INCOMPLETE") for aid, s in state.items()}


def _validate_transition(committed: list, record: dict) -> None:
    _reduce(committed + [record])          # reuse reducer เดียวกับ reconcile


def _locked_append(log_path: str, record: dict, validate_state) -> None:
    """
    write-ahead intent protocol (Codex bf.../f602329) — outcome 3 สถานะ ที่ survive crash แบบ fail-closed:
      1. `_write_intent` durable (fsync file+parent) **ก่อนแตะ ledger**
      2. append record + fsync + **parent fsync (POSIX, ใน commit boundary)** ; fail → rollback ยืนยัน → UNCOMMITTED (retry) ;
         rollback/parent ยืนยันไม่ได้ → intent ค้าง → INDETERMINATE
      3. `_clear_intent` (fsync parent) เมื่อ COMMITTED หรือ rollback-confirmed เท่านั้น
    check/clear intent ทำ **ใต้ lock** (B3.2-P.1b) ; close/release หลัง commit = cleanup warning (B3.2-P.2)
    """
    line = _serialize(record)
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    lockfd = _lock(log_path)
    try:
        _check_intent(log_path)                            # B3.2-P.1b: ตรวจ intent ใต้ lock
        fd = os.open(log_path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
        try:
            data = _read_all(fd)
            cut = data.rfind(b"\n") + 1                    # committed = ถึงหลัง \n สุดท้าย (B3.2)
            if validate_state is not None:                 # pure read (ยังไม่ mutate) → reject ก่อนแตะ ledger/intent
                validate_state(_parse_committed(data[:cut]), record)
            _write_intent(log_path, record)                # durable intent **ก่อน** mutation แรก (truncate/append) (B3.2-P.1a)
            if cut != len(data):                           # uncommitted tail → ตัดทิ้งก่อน append
                os.ftruncate(fd, cut)
                os.fsync(fd)
            new_log = (cut == 0)
            os.lseek(fd, cut, os.SEEK_SET)
            try:
                mv = memoryview(line)
                while mv:
                    n = os.write(fd, mv)
                    if n <= 0:
                        raise ProvenanceError("os.write คืน 0 (เขียนไม่คืบ)")
                    mv = mv[n:]
                os.fsync(fd)                               # record durable
                if new_log:
                    _fsync_parent(d)                       # parent durability ใน commit boundary (B3.2-P.2)
            except BaseException as werr:
                try:                                       # rollback ต้อง **ยืนยัน** (truncate + fsync)
                    os.ftruncate(fd, cut)
                    os.fsync(fd)
                except OSError as rberr:                   # rollback ยืนยันไม่ได้ → intent ค้าง = INDETERMINATE
                    raise ProvenanceIndeterminate("append commit ล้มและ rollback ยืนยันไม่ได้ — ledger indeterminate") from rberr
                _clear_intent(log_path)                    # rollback confirmed → outcome ยืนยัน → เคลียร์ intent
                raise werr                                 # UNCOMMITTED (retry ได้)
            _clear_intent(log_path)                        # COMMITTED (record+parent durable) → เคลียร์ intent
        finally:
            try:
                os.close(fd)
            except OSError:
                pass                                       # post-commit cleanup warning (record durable แล้ว, B3.2-P.2)
    finally:
        try:
            _release(lockfd)
        except OSError:
            pass                                           # cleanup warning


def _append_raw(log_path: str, record: dict) -> None:
    """low-level append (WAI + lock + tail-truncate + full-write) — **ไม่บังคับ state machine** ; private (ไม่ใช่ authority path, M3.2-A)"""
    _locked_append(log_path, record, None)


def append_event(log_path: str, record: dict) -> None:
    """append event ledger (STARTED/terminal) พร้อม state machine — bad transition = ProvenanceError"""
    _locked_append(log_path, record, _validate_transition)


def read_provenance(log_path: str) -> list:
    """อ่าน records ที่ commit แล้ว **ใต้ lock + intent check** (B3.2-P.1b) — tail ไม่มี newline = drop ;
    interior corrupt = ProvenanceError ; unresolved intent = ProvenanceIndeterminate (fail-closed)"""
    if not os.path.exists(log_path) and not os.path.exists(_intent_path(log_path)):
        return []
    lockfd = _lock(log_path)
    try:
        _check_intent(log_path)                            # snapshot ต้องเสถียร: อ่านใต้ lock เดียวกับ writer
        if not os.path.exists(log_path):
            return []
        with open(log_path, "rb") as f:
            data = f.read()
        cut = data.rfind(b"\n") + 1
        return _parse_committed(data[:cut])
    finally:
        try:
            _release(lockfd)
        except OSError:
            pass


def reconcile(records: list) -> dict:
    """map attempt_id → terminal status ; STARTED ไม่มี terminal = INCOMPLETE ; ลำดับ/run/status ผิด = ProvenanceError
    (order-sensitive — ใช้ reducer เดียวกับ append validation, B3.1-R)"""
    return _reduce(records)
