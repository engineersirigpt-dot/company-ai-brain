"""
P2 build-time offline snapshot verify (runtime stage) — fail-closed ด้วย **SystemExit ไม่ใช่ assert**
ตรวจว่า HF cache offline มี snapshots/<SHA> ครบ + resolved commit ตรง + required files ก่อนถือว่า image ใช้ได้
(ไม่โหลด weights / ไม่รัน benchmark — แค่ path/commit/files check)

    python p2_verify_snapshot.py
"""
from __future__ import annotations
import os
import sys

import p2_pin as PIN
import p2_reranker as RK

_CACHE_REPO = "models--BAAI--bge-reranker-v2-m3"


def _fail(msg: str):
    raise SystemExit(f"SNAPSHOT VERIFY FAIL: {msg}")


def snapshot_path(hf_home: str) -> str:
    return os.path.join(hf_home, "hub", _CACHE_REPO, "snapshots", PIN.MODEL_COMMIT)


def main() -> int:
    hf = os.environ.get("HF_HOME", "/opt/hf")
    snap = snapshot_path(hf)
    if not os.path.isdir(snap):
        _fail(f"snapshot dir หาย: {snap}")
    if RK._resolved_commit(snap) != PIN.MODEL_COMMIT:
        _fail(f"resolved commit != pinned: {RK._resolved_commit(snap)!r}")
    missing = [f for f in PIN.REQUIRED_FILES if not os.path.exists(os.path.join(snap, f))]
    if missing:
        _fail(f"required files ขาดใน snapshot offline: {missing}")
    print(f"offline snapshot OK {PIN.MODEL_COMMIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
