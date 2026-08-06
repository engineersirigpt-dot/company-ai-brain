"""
P2 M4a operational wrapper (Codex constraints 1/2/3 + reviews 0e04eb7/8066f0e/bf0e9b7) — **pure/offline** ; รัน M4a จริง = ยัง NO-GO

event-ledger + exception-authority + trusted clock + fail-closed content binding:
  - attempt_id: wrapper สร้างเอง (crypto-random) หรือ validate token — ห้าม None/blank/control/oversize (B3.1)
  - **trusted clock port**: wrapper เรียก `clock.now_iso()` เองที่ STARTED/terminal + validate ISO-8601+tz + monotonic
    (ไม่รับ timestamp string จาก caller) (M3.1)
  - append STARTED (bind immutable RunPlan metadata) → run → terminal ผ่าน `append_event` state machine ;
    STARTED เขียนไม่ได้ = abort ; terminal เขียนไม่ได้ = PROVENANCE_UNCONFIRMED (B3)
  - **PUBLISHED fail-closed** (M3.2): reload final bundle จากดิสก์ → recompute artifact/evidence/receipt digest +
    **re-run public bundle validator** ก่อน terminal ; read/hash/validate ล้ม = ไม่ PUBLISHED (FAILED/verify_publish)
  - M2: provenance เก็บเฉพาะ sanitized (error_type) — ไม่เก็บ raw exception text
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
from datetime import datetime

import p2_atomic as AT
import p2_eval as E
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


def _now(clock) -> str:
    t = clock.now_iso()                                     # M3.1: trusted clock (ไม่รับ string จาก caller)
    if not E._valid_iso_tz(t):
        raise ValueError(f"clock.now_iso() ไม่ใช่ ISO-8601+tz: {t!r}")
    return t


def _dt(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def _finished(clock, started_at):
    """M3.1: finished จาก trusted clock ; invalid/regression → clamp = started_at + **clock_anomaly=True** (ไม่เงียบ)"""
    try:
        cand = clock.now_iso()
    except Exception:
        return started_at, True
    if not E._valid_iso_tz(cand):
        return started_at, True
    try:
        return (cand, False) if _dt(cand) >= _dt(started_at) else (started_at, True)
    except ValueError:
        return started_at, True


def _receipt_within(started_at, finished_at, receipt) -> bool:
    """PUBLISHED cross-check: receipt run interval ต้องอยู่ใน operational interval [started_at, finished_at]"""
    rst, rfn = receipt.get("started_utc"), receipt.get("finished_utc")
    if not (E._valid_iso_tz(rst) and E._valid_iso_tz(rfn)):
        return False
    try:
        return _dt(started_at) <= _dt(rst) <= _dt(rfn) <= _dt(finished_at)
    except ValueError:
        return False


def _canon(p) -> str:
    return os.path.realpath(p)                              # M2: indirection เดียว — canonicalize out_dir ครั้งเดียว (test hook ได้)


def _bundle_path(out_dir: str, run_id) -> str:
    return os.path.join(out_dir, str(run_id) + ".bundle.json")


def _sha256_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _verify_published(plan, frozen, path) -> dict:
    """
    M3.2 fail-closed: โหลด final bundle จากดิสก์ → recompute artifact/evidence/receipt digest +
    **re-run public bundle validator บน content ที่โหลด** (กัน TOCTOU/tamper/artifact หาย). ล้ม = raise
    """
    with open(path, "rb") as f:
        raw = f.read()
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    if not (isinstance(artifact_sha256, str) and len(artifact_sha256) == 64):
        raise ValueError("artifact_sha256 ไม่ใช่ 64-hex")
    bundle = json.loads(raw.decode("utf-8"))
    ev, rc = bundle.get("evidence"), bundle.get("receipt")
    if not isinstance(ev, dict) or not isinstance(rc, dict):
        raise ValueError("final bundle ไม่มี evidence/receipt dict")
    berrs = RP.validate_m4_preflight_bundle(plan, frozen, ev, rc)
    if berrs:
        raise ValueError(f"final bundle ไม่ผ่าน public gate ({len(berrs)} errors): {berrs[:2]}")
    ebs, rrs = ev.get("evidence_body_sha256"), ev.get("run_receipt_sha256")
    if not (E._is_sha256(ebs) and E._is_sha256(rrs)):
        raise ValueError("evidence_body/run_receipt digest ใน bundle ไม่ใช่ sha256")
    # M3.2-B: คืน disk-loaded evidence+receipt (caller ต้องใช้ชุดนี้ ไม่ใช่ในหน่วยความจำ)
    return {"artifact_sha256": artifact_sha256, "evidence_body_sha256": ebs, "run_receipt_sha256": rrs,
            "evidence": ev, "receipt": rc}


def run_m4a_operational(*, provenance_db, out_dir, plan, frozen, cases, corpus, marker, ports, argv, stdout, stderr,
                        attempt_id=None) -> dict:
    aid = _resolve_attempt_id(attempt_id)
    run_id = plan.get("run_id") if isinstance(plan, dict) else None
    out_dir_real = _canon(out_dir)                          # M2: canonicalize **ครั้งเดียว** ก่อน STARTED → ใช้ค่าเดียวกันทุกจุด
    clock = ports.clock                                     # M3.1: authority เดียวกับ runner (receipt)
    try:
        started_at = _now(clock)
    except Exception as e:                                  # clock ไม่น่าเชื่อ → abort ก่อน STARTED/provision
        return {"attempt_id": aid, "run_id": run_id, "status": "FAILED", "phase": "clock", "error_type": type(e).__name__}

    plan_errs = RP.validate_run_plan(plan) if isinstance(plan, dict) else ["plan ไม่ใช่ dict"]
    started = {"attempt_id": aid, "run_id": run_id, "event": "STARTED", "started_at": started_at,
               "out_dir_realpath": out_dir_real, "plan_valid": not plan_errs}
    if not plan_errs:
        started.update({"run_manifest_sha256": RP.run_manifest_sha256(plan),
                        "m4_case_manifest_sha256": plan["m4_case_manifest_sha256"],
                        "model_revision": plan["model_commit"], "image_digest": plan["image_digest"]})
    try:
        PV.append_event(provenance_db, started)
    except Exception as e:
        return {"attempt_id": aid, "run_id": run_id, "status": "FAILED", "phase": "provenance_started",
                "error_type": type(e).__name__}

    def _terminal(status, phase, receipt=None, **extra):
        finished_at, anomaly = _finished(clock, started_at)
        if status == "PUBLISHED" and isinstance(receipt, dict) and not _receipt_within(started_at, finished_at, receipt):
            anomaly = True                                 # M3.1: PUBLISHED cross-check receipt interval
        if status == "PUBLISHED" and anomaly:
            status, phase = "DEGRADED", "clock_anomaly"    # M3.1: anomaly load-bearing → ไม่ใช่ clean PUBLISHED
        rec = {"attempt_id": aid, "run_id": run_id, "event": status, "status": status, "phase": phase,
               "finished_at": finished_at, **extra}
        if anomaly:
            rec["clock_anomaly"] = True                    # explicit + status ถูก downgrade แล้ว
        try:
            PV.append_event(provenance_db, rec)
        except PV.ProvenanceIndeterminate as pe:
            return {**rec, "status": "PROVENANCE_INDETERMINATE", "provenance_error_type": type(pe).__name__}
        except Exception as pe:
            return {**rec, "status": "PROVENANCE_UNCONFIRMED", "recorded": False, "provenance_error_type": type(pe).__name__}
        # B3: export immutable JSONL evidence snapshot ของ ledger (best-effort — authority = db ; export ล้มไม่เปลี่ยน terminal)
        try:
            rec["provenance_export"] = PV.export_jsonl(provenance_db, os.path.join(out_dir_real, str(run_id) + ".provenance.jsonl"))
        except Exception as ee:
            rec["provenance_export_error"] = type(ee).__name__
        return rec

    if plan_errs:
        return _terminal("FAILED", "plan_invalid")

    try:
        cap = FS.probe_output_fs(out_dir_real)             # M2: probe บน canonical เดียวกับ STARTED/runner
    except Exception as e:
        return _terminal("FAILED", "fs_probe", error_type=type(e).__name__)
    cap_sum = {"hardlink_no_clobber": cap["hardlink_no_clobber"], "cleanup_ok": cap["cleanup_ok"],
               "durability_mode": cap["durability_mode"]}

    try:
        result = RUN.run_m4a(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker=marker,
                             ports=ports, out_dir=out_dir_real, argv=argv, stdout=stdout, stderr=stderr)
    except (AT.CleanupUnconfirmed, AT.DurabilityUnconfirmed) as e:
        art = _bundle_path(out_dir_real, run_id)
        return _terminal("DEGRADED", "publish", capability=cap_sum, artifact=art,
                         artifact_sha256=_sha256_file(art), error_type=type(e).__name__)
    except Exception as e:
        return _terminal("FAILED", "run", capability=cap_sum, error_type=type(e).__name__)

    # M2: ยืนยัน out_dir ไม่ถูก retarget (symlink/junction swap) ระหว่าง run — artifact ต้องอยู่ใต้ canonical ที่ bind ใน STARTED
    if _canon(out_dir) != out_dir_real:
        return _terminal("FAILED", "out_dir_retargeted", capability=cap_sum)
    # M3.2-B: validate exact runner-result shape/status/durability + **exact path ใต้ out_dir (frozen canonical)** (sanitized เดียว)
    expected_path = os.path.join(out_dir_real, str(run_id) + ".bundle.json")
    if not (isinstance(result, dict) and result.get("status") == "PUBLISHED"
            and result.get("durability") in ("durable", "atomic-visibility-only")
            and isinstance(result.get("path"), str) and os.path.realpath(result["path"]) == expected_path):
        return _terminal("FAILED", "run_result_malformed", capability=cap_sum)

    # M3.2: verify final bundle จากดิสก์ (fail-closed) → คืน disk-loaded evidence/receipt (ไม่ใช้ในหน่วยความจำ)
    try:
        v = _verify_published(plan, frozen, expected_path)
    except Exception as e:
        return _terminal("FAILED", "verify_publish", capability=cap_sum, path=expected_path, error_type=type(e).__name__)

    term = _terminal("PUBLISHED", "complete", receipt=v["receipt"], capability=cap_sum,
                     durability_mode=result["durability"], path=expected_path,
                     artifact_sha256=v["artifact_sha256"], evidence_body_sha256=v["evidence_body_sha256"],
                     run_receipt_sha256=v["run_receipt_sha256"])
    if term.get("status") != "PUBLISHED":
        return term
    return {**term, "evidence": v["evidence"], "receipt": v["receipt"]}   # disk-loaded เท่านั้น
