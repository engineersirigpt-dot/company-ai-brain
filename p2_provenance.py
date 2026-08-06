"""
P2 operational provenance ledger (Codex constraint 3 + reviews 0e04eb7/8066f0e) — **pure/offline**

crash-safe append-only JSONL event ledger (STARTED → terminal ต่อ attempt_id):
  - **OS advisory lock** (`fcntl`/`msvcrt`, non-blocking + bounded retry) — ปล่อยอัตโนมัติเมื่อ process ตาย (M1.1)
  - **write-ahead intent v2** (B1): durable marker bind `protocol_version`/`log_id`/`cut`/`record_sha256` ก่อนแตะ ledger ;
    หลัง crash `recover()`/reader/writer ทำ **evidence-based recovery ใต้ lock** — เทียบ bytes ณ `cut` กับ digest:
    ตรง+ปิด `\n` = COMMITTED (re-fsync) ; partial/absent/mismatch = UNCOMMITTED (truncate กลับ `cut`) ; ห้ามลบ marker แบบ blind
  - **newline = commit marker** : tail ที่ไม่ลงท้าย `\n` = uncommitted เสมอ (แม้ JSON parse ผ่าน)
  - **state machine** : STARTED create-once + first ; terminal ครั้งเดียว + ตาม STARTED + run_id ตรง (B3.1)
  - full-write (short write = error) · `allow_nan=False` + size cap · **parent ต้อง pre-exist** + fsync file+parent ใน commit boundary (B2)
  - `reconcile` : STARTED ไม่มี terminal = INCOMPLETE ; transition ที่เป็นไปไม่ได้ = ProvenanceError
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
import warnings
from datetime import datetime

MAX_RECORD_BYTES = 65536
PROTOCOL_VERSION = 2                             # write-ahead intent v2 (bind cut + record digest + log identity)
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


def _parent_dir(log_path: str) -> str:
    """B2: absolute parent เสมอ (basename → cwd) — ไม่คืน '' ที่ทำให้ข้าม directory durability เงียบ"""
    return os.path.dirname(os.path.abspath(log_path))


def _log_id(log_path: str) -> str:
    """canonical log identity ที่ intent bind ไว้ (กัน intent ของ log อื่นถูก apply ผิด)"""
    return os.path.abspath(log_path)


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


def _write_all(fd, data: bytes) -> None:
    mv = memoryview(data)
    while mv:
        n = os.write(fd, mv)
        if n <= 0:
            raise ProvenanceError("os.write คืน 0 (เขียนไม่คืบ)")
        mv = mv[n:]


def _write_intent(log_path: str, record: dict, line: bytes, cut: int) -> None:
    """
    B1: durable intent v2 ที่ bind หลักฐานพอ recover แบบ deterministic — `protocol_version`, canonical `log_id`,
    pre-append `cut`, `record_sha256` (ของ serialized line รวม `\\n`), `attempt_id`/`run_id`/`event` ;
    no-clobber (`O_EXCL`) + fsync file + fsync parent **ก่อน** mutation แรกของ ledger (B3.2-P.1a)
    """
    ip = _intent_path(log_path)
    body = json.dumps({"protocol_version": PROTOCOL_VERSION, "log_id": _log_id(log_path), "cut": cut,
                       "record_sha256": hashlib.sha256(line).hexdigest(), "attempt_id": record.get("attempt_id"),
                       "run_id": record.get("run_id"), "event": record.get("event")},
                      ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fd = os.open(ip, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY, 0o644)
    try:
        _write_all(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_parent(_parent_dir(log_path))


def _clear_intent(log_path: str) -> None:
    """
    ลบ intent เมื่อ outcome (commit/rollback) **ยืนยันแล้วเท่านั้น** — M1: แยกสองความล้ม:
      unlink ล้ม + marker ยังอยู่ → `ProvenanceIndeterminate` (intent ค้างจริง) ;
      unlink สำเร็จแต่ parent fsync ล้ม → outcome ยืนยัน durable แล้ว การลบ intent ยังไม่ durable →
        ถ้า marker กลับมาโผล่หลัง crash `_recover_locked` จะ re-confirm จาก digest (idempotent) → **warning** ไม่ใช่ indeterminate
    """
    ip = _intent_path(log_path)
    try:
        os.unlink(ip)
    except FileNotFoundError:
        return
    except OSError as e:
        raise ProvenanceIndeterminate(f"unlink write-ahead intent ล้ม (marker ยังอยู่) — ledger indeterminate: {ip}") from e
    try:
        _fsync_parent(_parent_dir(log_path))
    except OSError as e:
        warnings.warn(f"clear-intent parent fsync ล้ม (intent removal ยังไม่ durable — recovery re-confirm ได้): {ip} :: {e}",
                      RuntimeWarning, stacklevel=2)


def _read_intent(log_path: str) -> dict | None:
    """อ่าน+validate intent body ; ไม่มี = None ; corrupt/identity/binding ไม่ครบ = ProvenanceIndeterminate (recover เองไม่ได้)"""
    ip = _intent_path(log_path)
    try:
        with open(ip, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    try:
        meta = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProvenanceIndeterminate(f"intent corrupt — recover เองไม่ได้ (ต้อง operator): {ip}") from e
    if not isinstance(meta, dict) or meta.get("protocol_version") != PROTOCOL_VERSION or meta.get("log_id") != _log_id(log_path):
        raise ProvenanceIndeterminate(f"intent protocol/log identity ไม่ตรง — recover เองไม่ได้: {ip}")
    if not (isinstance(meta.get("cut"), int) and meta["cut"] >= 0 and _is_sha256(meta.get("record_sha256"))):
        raise ProvenanceIndeterminate(f"intent binding ไม่ครบ (cut/record_sha256) — recover เองไม่ได้: {ip}")
    return meta


def _recover_locked(log_path: str):
    """
    B1: evidence-based recovery **ใต้ lock** — ตรวจ bytes ณ `cut` เทียบ `record_sha256` ที่ intent bind:
      tail == intended line (digest ตรง + ปิดด้วย `\\n`) → record เขียนครบจริง → `fsync` ยืนยัน (idempotent) → COMMITTED ;
      missing/partial/mismatch → `ftruncate` กลับ `cut` + fsync → UNCOMMITTED
    เสร็จแล้วจึง `_clear_intent` ; ไม่มี intent = None (ห้ามลบ marker แบบ blind — resolution ต้องมาจากหลักฐาน)
    """
    meta = _read_intent(log_path)
    if meta is None:
        return None
    cut, rsha = meta["cut"], meta["record_sha256"]
    fd = os.open(log_path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
    try:
        data = _read_all(fd)
        if cut > len(data):
            cut = len(data)                              # ledger สั้นกว่าที่ intent บันทึก → tail ว่าง
        tail = data[cut:]
        if tail and tail.endswith(b"\n") and hashlib.sha256(tail).hexdigest() == rsha:
            os.fsync(fd)                                 # intended bytes ครบ → ยืนยัน durable (idempotent replay)
            outcome = "COMMITTED"
            if cut == 0:
                _fsync_parent(_parent_dir(log_path))
        else:
            os.ftruncate(fd, cut)                        # partial/absent/mismatch → ทิ้ง record ที่ยังไม่ยืนยัน
            os.fsync(fd)
            outcome = "UNCOMMITTED"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    _clear_intent(log_path)
    return outcome


def recover(log_path: str):
    """resolve write-ahead intent แบบ evidence-based (ใต้ lock) — คืน 'COMMITTED'/'UNCOMMITTED'/None ; corrupt intent = ProvenanceIndeterminate"""
    lockfd = _lock(log_path)
    try:
        return _recover_locked(log_path)
    finally:
        try:
            _release(lockfd)
        except OSError:
            pass


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
    write-ahead intent v2 (Codex bf.../f602329/460fe6b) — outcome ที่ survive crash แบบ evidence-based:
      0. resolve intent ที่ค้าง (`_recover_locked`) ใต้ lock ก่อน — ไม่ blind-accept (B1)
      1. validate transition (pure read) → `_write_intent` durable ที่ bind `cut`+`record_sha256` **ก่อนแตะ ledger** (B1)
      2. append record + fsync + **parent fsync (POSIX, ใน commit boundary)** ; fail → rollback ยืนยัน → UNCOMMITTED (retry) ;
         rollback ยืนยันไม่ได้ → intent ค้าง → INDETERMINATE (recover ทีหลังจาก digest ได้)
      3. `_clear_intent` เมื่อ COMMITTED หรือ rollback-confirmed เท่านั้น (M1: unlink-fail=INDETERMINATE, fsync-fail=warning)
    recover/clear intent ทำ **ใต้ lock** (B3.2-P.1b) ; close/release หลัง commit = cleanup warning (B3.2-P.2)
    """
    line = _serialize(record)
    parent = _parent_dir(log_path)                         # B2: absolute parent (ไม่มีวันเป็น '')
    if not os.path.isdir(parent):                          # B2: ต้อง pre-exist (ผ่าน capability/preflight) — ไม่ auto-create
        raise ProvenanceError(f"provenance parent directory ต้องมีอยู่ก่อนเขียน (durability boundary): {parent}")
    lockfd = _lock(log_path)
    try:
        _recover_locked(log_path)                          # B1/B3.2-P.1b: resolve intent ที่ค้าง (evidence-based) ใต้ lock ก่อน
        fd = os.open(log_path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
        try:
            data = _read_all(fd)
            cut = data.rfind(b"\n") + 1                    # committed = ถึงหลัง \n สุดท้าย (B3.2)
            if validate_state is not None:                 # pure read (ยังไม่ mutate) → reject ก่อนแตะ ledger/intent
                validate_state(_parse_committed(data[:cut]), record)
            _write_intent(log_path, record, line, cut)     # durable intent (bind cut+digest) **ก่อน** mutation แรก (B1/B3.2-P.1a)
            if cut != len(data):                           # uncommitted tail → ตัดทิ้งก่อน append
                os.ftruncate(fd, cut)
                os.fsync(fd)
            new_log = (cut == 0)
            os.lseek(fd, cut, os.SEEK_SET)
            try:
                _write_all(fd, line)
                os.fsync(fd)                               # record durable
                if new_log:
                    _fsync_parent(parent)                  # parent durability ใน commit boundary (B2/B3.2-P.2)
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
    """อ่าน records ที่ commit แล้ว **ใต้ lock + evidence-based recovery** (B1/B3.2-P.1b) — tail ไม่มี newline = drop ;
    interior corrupt = ProvenanceError ; intent corrupt/recover ไม่ได้ = ProvenanceIndeterminate (fail-closed)"""
    if not os.path.exists(log_path) and not os.path.exists(_intent_path(log_path)):
        return []
    lockfd = _lock(log_path)
    try:
        _recover_locked(log_path)                          # resolve intent ใต้ lock เดียวกับ writer (snapshot เสถียร)
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
