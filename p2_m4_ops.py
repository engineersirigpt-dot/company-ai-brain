"""
P2 M4a operational wrapper (Codex constraints 1/2/3) — **pure/offline** ; รัน M4a จริง = ยัง NO-GO

รวม 3 constraint ให้ run จริงปลอดภัย:
  1. probe output filesystem **ก่อน** provision/model (fs ไม่รองรับ hard-link no-clobber → FAILED ทันที)
  2. **treat `CleanupUnconfirmed`/`DurabilityUnconfirmed` เป็น authority** — status DEGRADED มาจาก exception
     ไม่ใช่จากการเจอ `<run_id>.bundle.json` (ห้ามตีความว่า clean success)
  3. persist ทุกผล (durability mode + cleanup/durability exception) ลง operational provenance ที่อยู่รอดข้าม process

คืน {status: PUBLISHED|DEGRADED|FAILED, ...} — caller (CLI/adapter) map status → exit code / decision
"""
from __future__ import annotations
import os

import p2_atomic as AT
import p2_fs_probe as FS
import p2_provenance as PV
import p2_m4_runner as RUN


def _bundle_path(out_dir: str, run_id) -> str:
    return os.path.join(out_dir, str(run_id) + ".bundle.json")


def run_m4a_operational(*, provenance_log, now, out_dir, plan, frozen, cases, corpus, marker,
                        ports, argv, stdout, stderr) -> dict:
    run_id = plan.get("run_id") if isinstance(plan, dict) else None
    base = {"run_id": run_id, "recorded_at": now}

    def _rec(record):
        PV.append_provenance(provenance_log, record)   # constraint 3: persist ทุกผลข้าม process
        return record

    # constraint 1: capability probe บน output fs จริง ก่อน provision/model
    try:
        cap = FS.probe_output_fs(out_dir)
    except FS.CapabilityError as e:
        return _rec({**base, "status": "FAILED", "phase": "fs_probe", "error_type": type(e).__name__, "error": repr(e)})

    try:
        result = RUN.run_m4a(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker=marker,
                             ports=ports, out_dir=out_dir, argv=argv, stdout=stdout, stderr=stderr)
    except (AT.CleanupUnconfirmed, AT.DurabilityUnconfirmed) as e:
        # constraint 2: artifact อาจปรากฏแล้ว แต่ **ไม่ clean** — DEGRADED จาก exception (ไม่ใช่จากการเจอไฟล์)
        return _rec({**base, "status": "DEGRADED", "phase": "publish", "durability_mode": cap["durability_mode"],
                     "artifact": _bundle_path(out_dir, run_id), "error_type": type(e).__name__, "error": repr(e)})
    except Exception as e:
        # RunnerError/PublishRefused/PermissionError/อื่น ๆ = ไม่มี PASS artifact
        return _rec({**base, "status": "FAILED", "phase": "run", "error_type": type(e).__name__, "error": repr(e)})

    rec = _rec({**base, "status": "PUBLISHED", "phase": "complete", "durability_mode": result.get("durability"),
                "path": result["path"], "capability": cap})
    return {**rec, "evidence": result["evidence"], "receipt": result["receipt"]}
