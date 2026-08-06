"""
P2 operational provenance ledger — **SQLite authority** (Codex round-7 REWORK `6721721`) — pure/offline

authoritative append-only event ledger บน `sqlite3` (stdlib) แทน hand-rolled JSONL WAL:
  - `PRAGMA synchronous=FULL` + rollback journal → **durable ต่อ event** ; SQLite จัดการ locking / rollback /
    crash recovery เอง (แทน sidecar `.lock`/`.intent` + short-file recovery + tail-truncate + alias-lock ที่รีวิววนหลายรอบ)
  - `BEGIN IMMEDIATE` ต่อ event → read state + validate transition + INSERT อยู่ใน **transaction เดียว** (writers serialize)
  - **state machine** (reducer เดียวกับ `reconcile`, B3.1/M3.2-A): STARTED create-once + first ; terminal ครั้งเดียว +
    ตาม STARTED + run_id ตรง + `status == event` + terminal schema ครบ ; ย้ำด้วย **UNIQUE partial index** (defense-in-depth)
  - **file identity**: SQLite ล็อกที่ inode ของ db → hard-link/symlink alias ล็อกตัวเดียวกัน (ปิด alias-bypass ของ path sidecar เดิม)
  - **evidence**: `export_jsonl()` หลัง commit — external M4 evidence contract (JSONL bundle) ไม่เปลี่ยน
  - `reconcile`: STARTED ไม่มี terminal = INCOMPLETE ; transition ที่เป็นไปไม่ได้ = ProvenanceError
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime

MAX_RECORD_BYTES = 65536
SCHEMA_VERSION = 1
_TERMINALS = ("PUBLISHED", "DEGRADED", "FAILED")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_BUSY_TIMEOUT_MS = 5000                          # writers serialize ใต้ write lock ; เกินเวลา = ProvenanceLocked


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


class ProvenanceError(Exception):
    """เขียน/อ่าน provenance ไม่สำเร็จ (bad transition / schema / serialize / db corruption / integrity)"""


class ProvenanceLocked(Exception):
    """acquire write lock ไม่ได้ในเวลาที่กำหนด (writer อื่นถือ transaction อยู่)"""


class ProvenanceIndeterminate(Exception):
    """commit/rollback ยืนยันไม่ได้ (เก็บไว้เพื่อ API compat) — SQLite transaction atomic ทำให้ไม่มี indeterminate
    window ในทางปฏิบัติ ; ยังใช้กับ db-level durability failure ที่แยกผลไม่ได้"""


def _parent_dir(log_path: str) -> str:
    return os.path.dirname(os.path.abspath(log_path))


def _canonical(record: dict) -> str:
    """canonical JSON (sorted-key, allow_nan=False, size cap) — non-dict/NaN/oversize = error ก่อนแตะ db"""
    if not isinstance(record, dict):
        raise TypeError("provenance record ต้องเป็น dict")
    try:
        s = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ProvenanceError(f"record serialize ไม่ได้ (allow_nan=False): {e!r}") from e
    if len(s.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ProvenanceError(f"record ใหญ่เกิน {MAX_RECORD_BYTES} bytes")
    return s


def _serialize(record: dict) -> bytes:
    """canonical JSON bytes + `\\n` (คงไว้เพื่อ compat/ขนาด) — ledger body ใช้ `_canonical` (ไม่มี newline)"""
    return (_canonical(record) + "\n").encode("utf-8")


def _connect(log_path: str) -> sqlite3.Connection:
    parent = _parent_dir(log_path)
    if not os.path.isdir(parent):                # durability boundary: ไม่ auto-create directory
        raise ProvenanceError(f"provenance parent directory ต้องมีอยู่ก่อนเขียน: {parent}")
    try:
        conn = sqlite3.connect(log_path, timeout=_BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=DELETE")   # rollback journal + synchronous=FULL = durable ต่อ commit
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id  TEXT,
            run_id      TEXT,
            event       TEXT NOT NULL,
            body        TEXT NOT NULL,
            body_sha256 TEXT NOT NULL)""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_started ON events(attempt_id) WHERE event='STARTED'")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_terminal ON events(attempt_id) "
                     "WHERE event IN ('PUBLISHED','DEGRADED','FAILED')")
    except sqlite3.Error as e:
        raise ProvenanceError(f"เปิด provenance db ไม่ได้: {e}") from e
    return conn


def _attempt_records(conn: sqlite3.Connection, aid) -> list:
    cur = conn.execute("SELECT body FROM events WHERE attempt_id IS ? ORDER BY seq", (aid,))
    return [json.loads(b) for (b,) in cur.fetchall()]


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
        if r.get("clock_anomaly"):                          # M3.1: clock anomaly load-bearing — PUBLISHED ต้อง clean
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
    _reduce(committed + [record])                # reuse reducer เดียวกับ reconcile (per-attempt state)


def _locked_write(log_path: str, record: dict, validate_state) -> None:
    """
    append 1 event ใน transaction เดียว (durable) — validate_state=None = raw (ไม่บังคับ state machine, private) ;
    validate_state=_validate_transition = event ledger (บังคับ state machine ที่ ledger boundary)
    """
    body = _canonical(record)                    # raises TypeError/ProvenanceError ก่อนแตะ db
    bsha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    aid = record.get("attempt_id")
    conn = _connect(log_path)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")      # write lock ทันที → read-validate-insert atomic (serialize writers)
        except sqlite3.OperationalError as e:
            raise ProvenanceLocked(f"provenance db ถูก lock: {log_path} :: {e}") from e
        try:
            if validate_state is not None:
                validate_state(_attempt_records(conn, aid), record)   # bad transition = ProvenanceError (ก่อน insert)
            try:
                conn.execute("INSERT INTO events(attempt_id, run_id, event, body, body_sha256) VALUES(?,?,?,?,?)",
                             (aid, record.get("run_id"), record.get("event"), body, bsha))
            except sqlite3.IntegrityError as e:  # UNIQUE STARTED/terminal (กันเชิงลึก เผื่อ logic รั่ว)
                raise ProvenanceError(f"ledger integrity (duplicate STARTED/terminal): {e}") from e
            conn.execute("COMMIT")               # durable (synchronous=FULL)
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()


def append_event(log_path: str, record: dict) -> None:
    """append event ledger (STARTED/terminal) พร้อม state machine — bad transition = ProvenanceError ; durable ต่อ event"""
    _locked_write(log_path, record, _validate_transition)


def _append_raw(log_path: str, record: dict) -> None:
    """low-level append (durable insert) — **ไม่บังคับ state machine** ; private (ไม่ใช่ authority path, M3.2-A)"""
    _locked_write(log_path, record, None)


def read_provenance(log_path: str) -> list:
    """อ่าน committed events ตามลำดับ append — db ไม่มี = [] ; db corrupt/body corrupt = ProvenanceError"""
    if not os.path.exists(log_path):
        return []
    conn = _connect(log_path)
    try:
        rows = conn.execute("SELECT body FROM events ORDER BY seq").fetchall()
    except sqlite3.DatabaseError as e:
        raise ProvenanceError(f"provenance db corrupt: {e}") from e
    finally:
        conn.close()
    out = []
    for (body,) in rows:
        try:
            out.append(json.loads(body))
        except (json.JSONDecodeError, ValueError) as e:
            raise ProvenanceError(f"provenance body corrupt: {e!r}") from e
    return out


def reconcile(records: list) -> dict:
    """map attempt_id → terminal status ; STARTED ไม่มี terminal = INCOMPLETE ; ลำดับ/run/status ผิด = ProvenanceError
    (order-sensitive — reducer เดียวกับ append validation, B3.1-R)"""
    return _reduce(records)


def _fsync_parent_dir(path: str) -> None:
    """directory-entry durability (POSIX) — Windows: dir fd fsync ไม่รองรับ → atomic-visibility only (no-op)"""
    if os.name != "posix":
        return
    dfd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def export_jsonl(log_path: str, out_path: str) -> str:
    """
    export committed events → JSONL bundle (evidence artifact) ตามลำดับ canonical — durable (fsync file+parent) ;
    external M4 evidence contract ยังเป็น JSONL แม้ authority ย้ายไป SQLite แล้ว
    """
    parent = _parent_dir(out_path)
    if not os.path.isdir(parent):
        raise ProvenanceError(f"export parent directory ต้องมีอยู่ก่อน: {parent}")
    data = "".join(_canonical(r) + "\n" for r in read_provenance(log_path)).encode("utf-8")
    fd = os.open(out_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
    try:
        mv = memoryview(data)
        while mv:
            n = os.write(fd, mv)
            if n <= 0:
                raise ProvenanceError("export write คืน 0")
            mv = mv[n:]
        os.fsync(fd)                             # file durable (write fd)
    finally:
        os.close(fd)
    _fsync_parent_dir(out_path)                  # directory-entry durable (POSIX)
    return out_path
