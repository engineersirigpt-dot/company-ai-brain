"""
P2 operational provenance ledger — **SQLite authority** (Codex round-7 REWORK, round-8 hardening `4444b1c`) — pure/offline

authoritative append-only event ledger บน `sqlite3` (stdlib):
  - `PRAGMA synchronous=FULL` + rollback journal → durable ต่อ event ; SQLite จัดการ crash recovery/rollback เอง
  - **canonical single-name db** (B1): reject symlink / `st_nlink != 1` — ไม่รับ hard-link/soft-link alias
    (rollback journal ตั้งชื่อตาม pathname → alias = undefined/corruption ตามสเปก SQLite) ; ทุก op ใช้ realpath เดียว
  - `BEGIN IMMEDIATE` → read state + validate transition (reducer เดียวกับ `reconcile`) + INSERT ; **แยก COMMIT phase**
    ออกจาก pre-commit (B2): pre-commit fail → rollback/uncommitted ; COMMIT fail → resolve outcome จาก `in_transaction`
    + verify row ผ่าน fresh connection → COMMITTED / uncommitted / `ProvenanceIndeterminate`
  - **row decoder + checksum verify** (M1): อ่านทุกครั้ง verify `body_sha256`, canonical body และ column identity
    (attempt_id/run_id/event) — mismatch = ProvenanceError (evidence ผูกกับ row จริง)
  - **fail-closed open** (M2): existing zero-length / schema/version ผิด = ProvenanceError ; ตั้ง `user_version=SCHEMA_VERSION`
  - **evidence**: `export_jsonl()` = temp + fsync + **atomic no-clobber** publish + parent fsync + receipt (digest/max_seq/row_count)
  - `reconcile`: STARTED ไม่มี terminal = INCOMPLETE ; transition ที่เป็นไปไม่ได้ = ProvenanceError
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime

MAX_RECORD_BYTES = 65536
SCHEMA_VERSION = 1
_TERMINALS = ("PUBLISHED", "DEGRADED", "FAILED")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_O_BINARY = getattr(os, "O_BINARY", 0)
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
    """เขียน/อ่าน provenance ไม่สำเร็จ (bad transition / schema / serialize / corruption / integrity / alias)"""


class ProvenanceLocked(Exception):
    """acquire write lock ไม่ได้ในเวลาที่กำหนด (writer อื่นถือ transaction อยู่)"""


class ProvenanceIndeterminate(Exception):
    """COMMIT outcome พิสูจน์ไม่ได้ (ack lost + verify ไม่ได้) — caller ต้อง fail-closed จน operator/หลักฐานยืนยัน"""


def _parent_dir(path: str) -> str:
    return os.path.dirname(os.path.abspath(path))


def _fsync_parent_dir(path: str) -> None:
    """directory-entry durability (POSIX) — Windows: dir fd fsync ไม่รองรับ → atomic-visibility only (no-op)"""
    if os.name != "posix":
        return
    dfd = os.open(_parent_dir(path), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _write_all_fd(fd, data: bytes) -> None:
    mv = memoryview(data)
    while mv:
        n = os.write(fd, mv)
        if n <= 0:
            raise ProvenanceError("os.write คืน 0 (เขียนไม่คืบ)")
        mv = mv[n:]


def _resolve_db_path(log_path: str) -> str:
    """
    B1: canonical single-name authority — realpath เดียวสำหรับทุก op ; reject symlink db และ hard-link (`st_nlink != 1`)
    เพราะ SQLite rollback journal ตั้งชื่อตาม pathname (alias → journal คนละชื่อ → crash recovery undefined/corruption)
    """
    if os.path.islink(log_path):
        raise ProvenanceError(f"provenance db ห้ามเป็น symlink (canonical single-name เท่านั้น): {log_path}")
    canonical = os.path.realpath(os.path.abspath(log_path))
    if os.path.exists(canonical):
        st = os.stat(canonical)
        if getattr(st, "st_nlink", 1) != 1:
            raise ProvenanceError(f"provenance db มี hard link (st_nlink={st.st_nlink}) — ห้าม alias: {canonical}")
    return canonical


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


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA journal_mode=DELETE")


# ── B1/M2 (round-9): exact schema contract — init เฉพาะไฟล์ที่เราสร้างเอง (O_EXCL) ; existing = verify-only, ไม่แก้ ──
_DDL_UX_STARTED = "CREATE UNIQUE INDEX ux_started ON events(attempt_id) WHERE event='STARTED'"
_DDL_UX_TERMINAL = "CREATE UNIQUE INDEX ux_terminal ON events(attempt_id) WHERE event IN ('PUBLISHED','DEGRADED','FAILED')"
# PRAGMA table_info(events) → (name, type, notnull, pk) ตาม contract แบบ exact
_EXPECT_COLUMNS = [("seq", "INTEGER", 0, 1), ("attempt_id", "TEXT", 0, 0), ("run_id", "TEXT", 0, 0),
                   ("event", "TEXT", 1, 0), ("body", "TEXT", 1, 0), ("body_sha256", "TEXT", 1, 0)]
_OPEN_RETRIES, _OPEN_DELAY = 100, 0.02          # รอ concurrent creator commit schema (bounded) ก่อน fail-closed


def _norm_sql(s: str) -> str:
    return " ".join(s.split()).lower() if isinstance(s, str) else ""


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _verify_schema(conn: sqlite3.Connection) -> None:
    """B1: ตรวจ **exact** schema — columns (name/type/notnull/pk) + index uniqueness/partial-predicate (ไม่ใช่แค่ชื่อ)"""
    cols = [(c[1], c[2], c[3], c[5]) for c in conn.execute("PRAGMA table_info(events)").fetchall()]
    if cols != _EXPECT_COLUMNS:
        raise ProvenanceError(f"events columns ไม่ตรง contract: {cols}")
    for name, ddl in (("ux_started", _DDL_UX_STARTED), ("ux_terminal", _DDL_UX_TERMINAL)):
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if not row or _norm_sql(row[0]) != _norm_sql(ddl):
            raise ProvenanceError(f"index {name} ไม่ตรง contract (unique/partial/predicate/columns)")
    uv = conn.execute("PRAGMA user_version").fetchone()[0]
    if uv != SCHEMA_VERSION:
        raise ProvenanceError(f"provenance schema version ไม่ตรง: {uv} != {SCHEMA_VERSION}")


def _initialize(conn: sqlite3.Connection) -> None:
    """สร้าง exact schema + stamp version ใต้ write lock (เฉพาะไฟล์ที่เราเพิ่งสร้างด้วย O_EXCL)"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _has_table(conn, "events"):           # เผื่อ race (ไม่ควรเกิดกับไฟล์ O_EXCL) → verify แทน create
            _verify_schema(conn)
        else:
            conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, "
                         "run_id TEXT, event TEXT NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL)")
            conn.execute(_DDL_UX_STARTED)
            conn.execute(_DDL_UX_TERMINAL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _open_existing(conn: sqlite3.Connection, canonical: str) -> None:
    """
    existing file = verify-only (ไม่สร้าง/แก้ schema, B1) — foreign/truncated/wrong schema = fail-closed ;
    รอ concurrent creator ที่กำลัง init commit (bounded) ก่อนตัดสินว่า schema หาย
    """
    for _ in range(_OPEN_RETRIES):
        if _has_table(conn, "events"):
            _verify_schema(conn)
            return
        time.sleep(_OPEN_DELAY)
    raise ProvenanceError(f"existing provenance db ไม่มี provenance schema (foreign/truncated/init ค้าง) — fail-closed: {canonical}")


def _try_create(canonical: str) -> bool:
    """atomic creator detection: O_EXCL สำเร็จ = เราสร้าง (initialize) ; EEXIST = ไฟล์มีอยู่ (open+verify เท่านั้น)"""
    try:
        os.close(os.open(canonical, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
        return True
    except FileExistsError:
        return False
    except OSError as e:
        raise ProvenanceError(f"สร้าง provenance db ไม่ได้: {e}") from e


def _safe_unlink(p: str) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


def _connect(log_path: str) -> sqlite3.Connection:
    canonical = _resolve_db_path(log_path)
    parent = _parent_dir(canonical)
    if not os.path.isdir(parent):                # durability boundary: ไม่ auto-create directory
        raise ProvenanceError(f"provenance parent directory ต้องมีอยู่ก่อนเขียน: {parent}")
    created = _try_create(canonical)
    try:
        conn = sqlite3.connect(canonical, timeout=_BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        _apply_pragmas(conn)
    except sqlite3.Error as e:
        if created:
            _safe_unlink(canonical)
        raise ProvenanceError(f"เปิด provenance db ไม่ได้: {e}") from e
    try:
        if created:
            _initialize(conn)                    # เราสร้างไฟล์เอง → init exact schema
        else:
            _open_existing(conn, canonical)      # ไฟล์มีอยู่ → verify-only (fail-closed ถ้า foreign/wrong)
    except ProvenanceError:
        conn.close()
        if created:
            _safe_unlink(canonical)              # init ล้ม → ไม่ทิ้ง empty poisoned file
        raise
    except sqlite3.Error as e:
        conn.close()
        if created:
            _safe_unlink(canonical)
        raise ProvenanceError(f"provenance db ใช้ไม่ได้: {e}") from e
    except BaseException:
        conn.close()
        if created:
            _safe_unlink(canonical)
        raise
    return conn


def _decode_row(aid, rid, ev, body, bsha) -> dict:
    """
    M1: central row decoder — verify checksum, canonical body และ column identity ทุกครั้งที่อ่าน
    (body_sha256/columns ที่ index/state ใช้ ต้องผูกกับ body จริง มิฉะนั้น evidence ปลอมได้)
    """
    if not isinstance(body, str) or hashlib.sha256(body.encode("utf-8")).hexdigest() != bsha:
        raise ProvenanceError("body_sha256 ไม่ตรงกับ body (tamper/corruption)")
    try:
        rec = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        raise ProvenanceError(f"provenance body corrupt: {e!r}") from e
    if not isinstance(rec, dict):
        raise ProvenanceError("provenance body ไม่ใช่ JSON object")
    if _canonical(rec) != body:
        raise ProvenanceError("provenance body ไม่ canonical (tamper)")
    if rec.get("attempt_id") != aid or rec.get("run_id") != rid or rec.get("event") != ev:
        raise ProvenanceError("provenance body ไม่ตรงกับ column identity (attempt_id/run_id/event)")
    return rec


def _select_records(conn: sqlite3.Connection, where: str = "", params=()) -> list:
    sql = "SELECT attempt_id, run_id, event, body, body_sha256 FROM events " + where + " ORDER BY seq"
    return [_decode_row(*row) for row in conn.execute(sql, params).fetchall()]


def _attempt_records(conn: sqlite3.Connection, aid) -> list:
    return _select_records(conn, "WHERE attempt_id IS ?", (aid,))


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
    state ว่าง → รับเฉพาะ STARTED ; state STARTED → รับ terminal ครั้งเดียว (run_id ตรง + `status == event` + schema ครบ) ;
    state terminal → รับเพิ่มไม่ได้ ; คืน {attempt_id: terminal_status | 'INCOMPLETE'} ; ผิด → ProvenanceError
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


def _do_commit(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT")                       # แยกเป็น hook เดียว (test inject COMMIT failure/ack-loss ได้)


def _row_exists(canonical: str, aid, ev, bsha) -> bool:
    conn = sqlite3.connect(canonical, timeout=_BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        cur = conn.execute("SELECT 1 FROM events WHERE attempt_id IS ? AND event=? AND body_sha256=? LIMIT 1",
                           (aid, ev, bsha))
        return cur.fetchone() is not None
    finally:
        conn.close()


def _resolve_commit_outcome(conn, canonical, record, bsha) -> str:
    """B2: COMMIT ล้ม → จำแนก outcome — active tx (retryable) → uncommitted ; ack lost → verify row → committed/uncommitted/indeterminate"""
    try:
        active = bool(conn.in_transaction)
    except sqlite3.Error:
        active = False
    if active:                                   # COMMIT ไม่ apply (เช่น SQLITE_BUSY) → transaction ยังเปิด
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return "uncommitted"
    try:                                          # transaction ปิดแล้ว (ack ambiguous) → verify ผ่าน fresh connection
        found = _row_exists(canonical, record.get("attempt_id"), record.get("event"), bsha)
    except sqlite3.Error:
        return "indeterminate"
    return "committed" if found else "uncommitted"


def _locked_write(log_path: str, record: dict, validate_state) -> None:
    body = _canonical(record)                    # raises TypeError/ProvenanceError ก่อนแตะ db
    bsha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    aid = record.get("attempt_id")
    conn = _connect(log_path)
    canonical = _resolve_db_path(log_path)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")      # write lock ทันที → read-validate-insert atomic (serialize writers)
        except sqlite3.OperationalError as e:
            raise ProvenanceLocked(f"provenance db ถูก lock: {canonical} :: {e}") from e
        try:                                      # ── pre-commit phase (transaction ยัง active → rollback ได้) ──
            if validate_state is not None:
                validate_state(_attempt_records(conn, aid), record)   # bad transition = ProvenanceError
            try:
                conn.execute("INSERT INTO events(attempt_id, run_id, event, body, body_sha256) VALUES(?,?,?,?,?)",
                             (aid, record.get("run_id"), record.get("event"), body, bsha))
            except sqlite3.IntegrityError as e:  # UNIQUE STARTED/terminal (กันเชิงลึก)
                raise ProvenanceError(f"ledger integrity (duplicate STARTED/terminal): {e}") from e
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise                                 # pre-commit fail = uncommitted (ประเภท exception เดิม)
        try:                                      # ── commit phase (แยก outcome, B2) ──
            _do_commit(conn)
        except BaseException as ce:
            outcome = _resolve_commit_outcome(conn, canonical, record, bsha)
            if outcome == "committed":
                return                            # row durable จริง (ack หายเฉยๆ) → success
            if outcome == "uncommitted":
                raise                             # retryable (re-raise ประเภทเดิม)
            raise ProvenanceIndeterminate(f"COMMIT outcome พิสูจน์ไม่ได้: {canonical}") from ce
    finally:
        conn.close()


def append_event(log_path: str, record: dict) -> None:
    """append event ledger (STARTED/terminal) พร้อม state machine — bad transition = ProvenanceError ; durable ต่อ event"""
    _locked_write(log_path, record, _validate_transition)


def _append_raw(log_path: str, record: dict) -> None:
    """low-level append (durable insert) — **ไม่บังคับ state machine** ; private (ไม่ใช่ authority path, M3.2-A)"""
    _locked_write(log_path, record, None)


def read_provenance(log_path: str) -> list:
    """อ่าน committed events ตามลำดับ append (verify checksum/identity ทุก row, M1) — db ไม่มี = [] ; corrupt/tamper = ProvenanceError"""
    if os.path.islink(log_path):
        raise ProvenanceError(f"provenance db ห้ามเป็น symlink: {log_path}")
    canonical = os.path.realpath(os.path.abspath(log_path))
    if not os.path.exists(canonical):
        return []
    conn = _connect(log_path)
    try:
        return _select_records(conn)
    except sqlite3.DatabaseError as e:
        raise ProvenanceError(f"provenance db corrupt: {e}") from e
    finally:
        conn.close()


def reconcile(records: list) -> dict:
    """map attempt_id → terminal status ; STARTED ไม่มี terminal = INCOMPLETE ; ลำดับ/run/status ผิด = ProvenanceError
    (order-sensitive — reducer เดียวกับ append validation, B3.1-R)"""
    return _reduce(records)


_after_snapshot_hook = None                      # test seam: เรียกหลัง freeze snapshot (จำลอง concurrent commit)


def _read_snapshot(conn: sqlite3.Connection):
    """B2: อ่าน rows + user_version จาก **snapshot เดียว** (read transaction) → receipt/JSONL มาจากชุดเดียวกัน"""
    conn.execute("BEGIN DEFERRED")
    try:
        rows = conn.execute("SELECT attempt_id, run_id, event, body, body_sha256, seq FROM events ORDER BY seq").fetchall()
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.execute("COMMIT")
    records = [_decode_row(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    max_seq = max((r[5] for r in rows), default=0)
    return records, int(max_seq), len(rows), int(uv)


def export_jsonl(db_path: str, out_path: str) -> dict:
    """
    B3: export committed events → **immutable JSONL evidence** (atomic no-clobber) + receipt —
    อ่าน rows/max_seq/row_count/version จาก **snapshot เดียว** (B2) ; validate ทุก row (checksum/identity) ;
    temp + fsync → `os.link` no-clobber → parent fsync ; คืน receipt ผูก snapshot ที่ export จริง
    (เป็น diagnostic snapshot ของ operational ledger — ไม่ใช่ clean-publish decision evidence)
    """
    canonical = _resolve_db_path(db_path)
    out_parent = _parent_dir(out_path)
    if not os.path.isdir(out_parent):
        raise ProvenanceError(f"export parent directory ต้องมีอยู่ก่อน: {out_parent}")
    if os.path.exists(out_path):                 # immutable: ห้าม overwrite artifact เดิม
        raise ProvenanceError(f"export target มีอยู่แล้ว (no-clobber): {out_path}")
    conn = _connect(canonical)
    try:
        records, max_seq, row_count, uv = _read_snapshot(conn)   # freeze ทุกอย่างจาก snapshot เดียว
    finally:
        conn.close()
    if _after_snapshot_hook is not None:         # test: writer commit หลัง freeze → ต้องไม่กระทบ receipt/file
        _after_snapshot_hook(canonical)
    body = "".join(_canonical(r) + "\n" for r in records).encode("utf-8")   # derive จาก rows ชุดที่ freeze
    jsonl_sha = hashlib.sha256(body).hexdigest()
    tmp = out_path + ".tmp-" + jsonl_sha[:16]
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY, 0o644)
    try:                                         # temp → fsync → atomic no-clobber publish ; cleanup tmp ทุกกรณี
        try:
            _write_all_fd(fd, body)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, out_path)               # atomic no-clobber publish (final ไม่ถูกแตะจนกว่าจะ link สำเร็จ)
        except FileExistsError as e:
            raise ProvenanceError(f"export collision (no-clobber): {out_path}") from e
        _fsync_parent_dir(out_path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return {"source_db": canonical, "schema_version": uv, "max_seq": max_seq,
            "row_count": row_count, "jsonl_sha256": jsonl_sha, "path": out_path}
