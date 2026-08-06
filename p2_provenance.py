"""
P2 operational provenance log (Codex constraint 3) — **pure/offline**

บันทึกผล run + durability mode + cleanup/durability exception ลง log ที่ **อยู่รอดข้าม process**
(return dict อย่างเดียวไม่พอสำหรับ run จริง). append-only JSONL, atomic ต่อ record (O_APPEND + fsync)
"""
from __future__ import annotations
import json
import os


def append_provenance(log_path: str, record: dict) -> None:
    """append 1 record (JSON line) ลง operational provenance log — O_APPEND (atomic ต่อ write เล็ก) + fsync"""
    if not isinstance(record, dict):
        raise TypeError("provenance record ต้องเป็น dict")
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_provenance(log_path: str) -> list:
    """อ่าน provenance records ทั้งหมด (JSONL) — ข้ามบรรทัดว่าง"""
    if not os.path.exists(log_path):
        return []
    out = []
    with open(log_path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out
