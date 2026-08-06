"""
P2 M4a operational wrapper (Codex constraints 1/2/3 + review 0e04eb7 B2/B3/M2) — **pure/offline** ; รัน M4a จริง = ยัง NO-GO

event ledger + exception-authority:
  1. probe output filesystem **ก่อน** provision/model (constraint 1) ; error ใด ๆ รอบ probe → FAILED/fs_probe (B2 defensive)
  2. **treat exception เป็น authority** — Cleanup/Durability → DEGRADED, อื่น → FAILED (ไม่ตีความจากการเจอไฟล์) (constraint 2)
  3. append **STARTED ก่อน run** ; terminal (PUBLISHED|DEGRADED|FAILED) attempt_id เดียวกัน ; STARTED เขียนไม่ได้ = abort ก่อน run ;
     terminal เขียนไม่ได้ = **PROVENANCE_UNCONFIRMED** (ไม่ report clean success) (B3, constraint 3)
  M2: provenance เก็บเฉพาะ **sanitized** (attempt_id/run_id/event/phase/error_type/durability_mode/path) — ไม่เก็บ raw exception text

คืน {status: PUBLISHED|DEGRADED|FAILED|PROVENANCE_UNCONFIRMED, ...} — caller map status → exit code / decision
"""
from __future__ import annotations
import os

import p2_atomic as AT
import p2_fs_probe as FS
import p2_provenance as PV
import p2_m4_runner as RUN


def _bundle_path(out_dir: str, run_id) -> str:
    return os.path.join(out_dir, str(run_id) + ".bundle.json")


def run_m4a_operational(*, provenance_log, attempt_id, now, out_dir, plan, frozen, cases, corpus, marker,
                        ports, argv, stdout, stderr) -> dict:
    run_id = plan.get("run_id") if isinstance(plan, dict) else None
    common = {"attempt_id": attempt_id, "run_id": run_id, "recorded_at": now}

    # B3: STARTED gate — ต้องบันทึกได้ก่อน run (ถ้าเขียน/fsync ไม่ได้ = abort, ไม่แตะ provision/model)
    try:
        PV.append_provenance(provenance_log, {**common, "event": "STARTED"})
    except Exception as e:
        return {**common, "status": "FAILED", "phase": "provenance_started", "error_type": type(e).__name__}

    def _terminal(status, phase, **extra):
        rec = {**common, "event": status, "status": status, "phase": phase, **extra}   # M2: sanitized fields เท่านั้น
        try:
            PV.append_provenance(provenance_log, rec)
        except Exception as pe:
            # B3: terminal เขียนไม่ได้ → ไม่ report clean success ; แจ้ง PROVENANCE_UNCONFIRMED + artifact path (ถ้ามี)
            return {**rec, "status": "PROVENANCE_UNCONFIRMED", "recorded": False,
                    "provenance_error_type": type(pe).__name__}
        return rec

    # constraint 1 + B2: probe boundary — CapabilityError หรือ unexpected error ใด ๆ → FAILED/fs_probe (sanitized)
    try:
        cap = FS.probe_output_fs(out_dir)
    except Exception as e:
        return _terminal("FAILED", "fs_probe", error_type=type(e).__name__)

    try:
        result = RUN.run_m4a(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker=marker,
                             ports=ports, out_dir=out_dir, argv=argv, stdout=stdout, stderr=stderr)
    except (AT.CleanupUnconfirmed, AT.DurabilityUnconfirmed) as e:
        # constraint 2: artifact อาจปรากฏแต่ **ไม่ clean** — DEGRADED จาก exception (ไม่ใช่จากการเจอไฟล์)
        return _terminal("DEGRADED", "publish", durability_mode=cap["durability_mode"],
                         artifact=_bundle_path(out_dir, run_id), error_type=type(e).__name__)
    except Exception as e:
        return _terminal("FAILED", "run", error_type=type(e).__name__)

    term = _terminal("PUBLISHED", "complete", durability_mode=result.get("durability"), path=result["path"])
    if term.get("status") != "PUBLISHED":
        return term                                        # terminal provenance ล้ม → ไม่แนบ evidence เป็น clean success
    return {**term, "evidence": result["evidence"], "receipt": result["receipt"]}
