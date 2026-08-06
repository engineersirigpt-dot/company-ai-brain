"""
P2 M4a operational wrapper (Codex constraints 1/2/3 + reviews 0e04eb7/8066f0e) — **pure/offline** ; รัน M4a จริง = ยัง NO-GO

event-ledger + exception-authority + content binding:
  - attempt_id: wrapper **สร้างเอง (crypto-random)** หรือ validate token ที่ส่งมา — ห้าม None/blank/control/oversize (B3.1)
  - append **STARTED** (bind immutable RunPlan metadata หลัง pure validate) → run → **terminal** (attempt_id เดียว) ผ่าน
    `append_event` state machine (create-once/first, terminal ครั้งเดียวตาม STARTED) ; STARTED เขียนไม่ได้ = abort (B3)
  - terminal เขียนไม่ได้ = **PROVENANCE_UNCONFIRMED** (ไม่ report clean) ; Cleanup/Durability → DEGRADED (constraint 2)
  - M3: STARTED bind run_manifest/m4_case_manifest/model/image + out_dir realpath ; terminal bind capability +
    artifact/evidence/receipt digest (recompute ได้) ; started_at/finished_at แยกจาก trusted clock
  - M2: provenance เก็บเฉพาะ sanitized (error_type) — ไม่เก็บ raw exception text
"""
from __future__ import annotations
import hashlib
import os
import re
import secrets

import p2_atomic as AT
import p2_fs_probe as FS
import p2_provenance as PV
import p2_runplan as RP
import p2_m4_runner as RUN

_ATTEMPT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")


def _resolve_attempt_id(attempt_id):
    if attempt_id is None:
        return "att-" + secrets.token_hex(16)               # crypto-random (B3.1)
    if not isinstance(attempt_id, str) or not _ATTEMPT_RE.fullmatch(attempt_id):
        raise ValueError("attempt_id ต้องเป็น safe token (>=8 ASCII [A-Za-z0-9._:-], ไม่ control/newline/oversize)")
    return attempt_id


def _bundle_path(out_dir: str, run_id) -> str:
    return os.path.join(out_dir, str(run_id) + ".bundle.json")


def _sha256_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def run_m4a_operational(*, provenance_log, out_dir, plan, frozen, cases, corpus, marker, ports, argv, stdout, stderr,
                        started_at, finished_at, attempt_id=None) -> dict:
    aid = _resolve_attempt_id(attempt_id)
    run_id = plan.get("run_id") if isinstance(plan, dict) else None
    plan_errs = RP.validate_run_plan(plan) if isinstance(plan, dict) else ["plan ไม่ใช่ dict"]

    # B3: STARTED ก่อน run + M3: bind immutable RunPlan metadata (เมื่อ plan valid) + out_dir realpath + started_at
    started = {"attempt_id": aid, "run_id": run_id, "event": "STARTED", "started_at": started_at,
               "out_dir_realpath": os.path.realpath(out_dir), "plan_valid": not plan_errs}
    if not plan_errs:
        started.update({"run_manifest_sha256": RP.run_manifest_sha256(plan),
                        "m4_case_manifest_sha256": plan["m4_case_manifest_sha256"],
                        "model_revision": plan["model_commit"], "image_digest": plan["image_digest"]})
    try:
        PV.append_event(provenance_log, started)
    except Exception as e:
        return {"attempt_id": aid, "run_id": run_id, "status": "FAILED", "phase": "provenance_started",
                "error_type": type(e).__name__}

    def _terminal(status, phase, **extra):
        rec = {"attempt_id": aid, "run_id": run_id, "event": status, "status": status,
               "phase": phase, "finished_at": finished_at, **extra}          # M2: sanitized fields เท่านั้น
        try:
            PV.append_event(provenance_log, rec)
        except Exception as pe:
            return {**rec, "status": "PROVENANCE_UNCONFIRMED", "recorded": False,
                    "provenance_error_type": type(pe).__name__}
        return rec

    if plan_errs:
        return _terminal("FAILED", "plan_invalid")

    try:
        cap = FS.probe_output_fs(out_dir)
    except Exception as e:
        return _terminal("FAILED", "fs_probe", error_type=type(e).__name__)
    cap_sum = {"hardlink_no_clobber": cap["hardlink_no_clobber"], "cleanup_ok": cap["cleanup_ok"],
               "durability_mode": cap["durability_mode"]}

    try:
        result = RUN.run_m4a(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker=marker,
                             ports=ports, out_dir=out_dir, argv=argv, stdout=stdout, stderr=stderr)
    except (AT.CleanupUnconfirmed, AT.DurabilityUnconfirmed) as e:
        art = _bundle_path(out_dir, run_id)
        return _terminal("DEGRADED", "publish", capability=cap_sum, artifact=art,
                         artifact_sha256=_sha256_file(art), error_type=type(e).__name__)
    except Exception as e:
        return _terminal("FAILED", "run", capability=cap_sum, error_type=type(e).__name__)

    ev = result["evidence"]
    term = _terminal("PUBLISHED", "complete", capability=cap_sum, durability_mode=result.get("durability"),
                     path=result["path"], artifact_sha256=_sha256_file(result["path"]),
                     evidence_body_sha256=ev.get("evidence_body_sha256"), run_receipt_sha256=ev.get("run_receipt_sha256"))
    if term.get("status") != "PUBLISHED":
        return term
    return {**term, "evidence": ev, "receipt": result["receipt"]}
