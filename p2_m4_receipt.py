"""
P2 M4a **outer receipt** — host-authoritative evidence layer (ปิด Codex real-run B1/B2/B3/M1)

ปัญหาที่ปิด: bundle ชั้นใน (evidence/receipt) สร้าง **ภายใน evaluator container** → ค่า image/isolation/cleanup เป็นสิ่งที่
evaluator "ประกาศเอง" จาก env ไม่ใช่ observation จาก Docker controller ฝั่ง host → ปลอมได้ (false-PASS) และ PASS ถูก
publish ก่อน cleanup (cleanup-failure ไม่ถูกจับ)

outer receipt = **host controller** สังเกตจาก Docker inspect จริง (evaluator image, network/volume/collection identity,
published ports, endpoint) + วัด process จริง (command/exit/timestamps/stdout+stderr digest) + ยืนยัน cleanup **หลัง**
teardown แล้วจึง resolve terminal verdict — bind กับ **inner bundle SHA-256** ทั้งก้อน

**fail-closed:** validator recompute terminal_status เอง (ไม่เชื่อค่าที่ receipt เขียนมา) ; observed ≠ declared → `FAILED`
(false-PASS ปิด) ; cleanup ไม่ยืนยัน → `DEGRADED` (ไม่ใช่ PASS) ; PASS ต่อเมื่อ observed==declared ครบ + inner PASS +
cleanup confirmed

cross-check identity: controller สังเกต **raw** network/volume/project/collection id จาก Docker → `typed_id_sha256` →
เทียบกับ `*_sha256` ที่ evaluator baked ใน isolation_proof (leaf helper เดียวกับ evaluator/harness — ไม่ drift)

pure module (ไม่แตะ docker) — offline-testable เต็ม ; controller (p2_m4_controller) เป็นผู้ป้อน observation จริง
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import p2_eval as E     # typed_id_sha256 (leaf hash ร่วม), _canonical_json (canonical bytes ร่วม)

OUTER_RECEIPT_VERSION = 1

# terminal verdicts
PASS, DEGRADED, FAILED = "PASS", "DEGRADED", "FAILED"


class OuterReceiptError(Exception):
    """สร้าง/validate outer receipt ไม่ผ่าน contract (โครงสร้าง/ชนิด/ปลอม)"""


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def bundle_sha256(inner_bundle_bytes: bytes) -> str:
    """SHA-256 ของ **ไฟล์ bundle ชั้นในทั้งก้อน** (bytes ตรงจากดิสก์ — ไม่ re-serialize เพื่อไม่ให้ normalization บิด)"""
    if not isinstance(inner_bundle_bytes, (bytes, bytearray)):
        raise OuterReceiptError("inner_bundle_bytes ต้องเป็น bytes")
    return _sha256_hex(bytes(inner_bundle_bytes))


def _is_sha256(x) -> bool:
    return isinstance(x, str) and len(x) == 64 and all(c in "0123456789abcdef" for c in x)


def _is_image_id(x) -> bool:
    # docker image id / digest = sha256:<64hex>
    return isinstance(x, str) and x.startswith("sha256:") and _is_sha256(x[7:])


def _bool(x) -> bool:
    return x is True or x is False


# ── body ที่เข้า hash (ทุกฟิลด์ยกเว้น outer_receipt_sha256) ─────────────────────
def _receipt_body(receipt: dict) -> dict:
    return {k: v for k, v in receipt.items() if k != "outer_receipt_sha256"}


def outer_receipt_sha256(receipt: dict) -> str:
    return _sha256_hex(E._canonical_json(_receipt_body(receipt)))


def build_outer_receipt(*, attempt_id, run_id, inner_bundle_bytes, inner_evidence,
                        observed, process, cleanup) -> dict:
    """
    ประกอบ outer receipt จาก observation ที่ **controller เก็บจาก Docker/subprocess จริง** :
      observed = {expected_evaluator_image, evaluator_image, qdrant_image_ref, qdrant_image,
                  network_id, volume_id, project_id, collection_id, published_ports,
                  endpoint, endpoint_is_production}   ← ทั้งหมดจาก docker inspect / controller config
      process  = {command:[..], exit_code, started_utc, finished_utc, stdout_sha256, stderr_sha256,
                  git_commit, git_tree_dirty, dependency_digest}
      cleanup  = {confirmed:bool, residual:[..]}
    inner_* = ค่าที่ evaluator ประกาศ (จาก bundle) — เก็บไว้ให้ validator เทียบกับ observed
    terminal_status = resolve จาก observed-vs-declared (คำนวณ ไม่ใช่รับมา)
    """
    if not (isinstance(attempt_id, str) and attempt_id.strip()):
        raise OuterReceiptError("attempt_id ต้องเป็น non-blank str")
    if not isinstance(inner_evidence, dict):
        raise OuterReceiptError("inner_evidence ต้องเป็น dict")
    ip = inner_evidence.get("isolation_proof")
    if not isinstance(ip, dict):
        raise OuterReceiptError("inner_evidence.isolation_proof ต้องเป็น dict")

    inner = {
        "bundle_sha256": bundle_sha256(inner_bundle_bytes),
        "evidence_status": inner_evidence.get("status"),
        "run_id": inner_evidence.get("run_id"),
        "image_digest": inner_evidence.get("image_digest"),
        "isolation": {
            "project_id_sha256": ip.get("project_id_sha256"),
            "network_id_sha256": ip.get("network_id_sha256"),
            "volume_id_sha256": ip.get("volume_id_sha256"),
            "collection_id_sha256": ip.get("collection_id_sha256"),
            "network_published_ports": ip.get("network_published_ports"),
            "endpoint_is_production": ip.get("endpoint_is_production"),
            "initial_point_count": ip.get("initial_point_count"),
        },
    }
    receipt = {
        "outer_receipt_version": OUTER_RECEIPT_VERSION,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "inner": inner,
        "observed": dict(observed),
        "process": dict(process),
        "cleanup": dict(cleanup),
    }
    receipt["terminal_status"] = _resolve_terminal(receipt)
    receipt["outer_receipt_sha256"] = outer_receipt_sha256(receipt)
    return receipt


# ── terminal resolution (หัวใจ fail-closed) ────────────────────────────────────
def false_pass_reasons(receipt: dict) -> list:
    """
    เงื่อนไข **false-PASS** (observed ≠ declared / ผิด invariant) → บล็อกเป็น FAILED ทันที
    (แยกเป็นฟังก์ชันเพื่อ validator ใช้ซ้ำ + test อ่านชัด)
    """
    r = []
    obs = receipt.get("observed") or {}
    inner = receipt.get("inner") or {}
    iso = inner.get("isolation") or {}
    proc = receipt.get("process") or {}

    exp = obs.get("expected_evaluator_image")
    got = obs.get("evaluator_image")
    if not _is_image_id(got):
        r.append("observed.evaluator_image ไม่ใช่ image id (sha256:<64hex>) — ไม่มี Docker observation")
    if not _is_image_id(exp):
        r.append("observed.expected_evaluator_image (controller pin) ไม่ใช่ image id")
    # B1: image ที่รันจริง (docker inspect) ต้อง == pin ของ controller == ที่ evaluator ประกาศใน bundle
    if _is_image_id(got) and _is_image_id(exp) and got != exp:
        r.append(f"executed evaluator image ≠ controller pin: {got} != {exp}")
    if _is_image_id(got) and got != inner.get("image_digest"):
        r.append(f"executed evaluator image ≠ inner bundle image_digest (evaluator ประกาศไม่ตรงที่รันจริง): "
                 f"{got} != {inner.get('image_digest')}")

    # B2: identity ที่ Docker เห็นจริง ต้อง hash ตรงกับ *_sha256 ที่ evaluator baked
    for raw_key, sha_key in (("network_id", "network_id_sha256"), ("volume_id", "volume_id_sha256"),
                             ("project_id", "project_id_sha256"), ("collection_id", "collection_id_sha256")):
        raw = obs.get(raw_key)
        if not (isinstance(raw, str) and raw.strip()):
            r.append(f"observed.{raw_key} ว่าง — ไม่มี Docker observation")
            continue
        want = iso.get(sha_key)
        try:
            got_h = E.typed_id_sha256(raw)
        except Exception as e:
            r.append(f"hash observed.{raw_key} ไม่ได้: {e}")
            continue
        if got_h != want:
            r.append(f"Docker {raw_key} hash ≠ isolation_proof.{sha_key} (identity ที่รันจริง≠ที่ประกาศ)")

    # B2: published ports / production — observed (docker inspect) ต้อง 0 / non-production และตรงกับ declared
    pp_obs = obs.get("published_ports")
    if not (isinstance(pp_obs, int) and not isinstance(pp_obs, bool)):
        r.append("observed.published_ports ต้องเป็น int (docker inspect)")
    else:
        if pp_obs != 0:
            r.append(f"observed.published_ports = {pp_obs} ≠ 0 (Qdrant เปิด port ออก host — ไม่ isolated)")
        if pp_obs != iso.get("network_published_ports"):
            r.append(f"observed.published_ports {pp_obs} ≠ declared {iso.get('network_published_ports')}")
    prod_obs = obs.get("endpoint_is_production")
    if not _bool(prod_obs):
        r.append("observed.endpoint_is_production ต้องเป็น bool (docker/controller)")
    else:
        if prod_obs is not False:
            r.append("observed.endpoint_is_production = True (รันชน production)")
        if prod_obs != iso.get("endpoint_is_production"):
            r.append("observed.endpoint_is_production ≠ declared")

    # inner verdict + process ต้อง healthy (false-PASS ถ้า inner ไม่ PASS แต่จะ mark PASS)
    if inner.get("evidence_status") != "PASS":
        r.append(f"inner evidence status = {inner.get('evidence_status')} (ไม่ PASS)")
    ec = proc.get("exit_code")
    if not (isinstance(ec, int) and not isinstance(ec, bool)) or ec != 0:
        r.append(f"process.exit_code = {ec!r} (evaluator ไม่ exit 0)")
    return r


def _resolve_terminal(receipt: dict) -> str:
    if false_pass_reasons(receipt):
        return FAILED
    cl = receipt.get("cleanup") or {}
    if cl.get("confirmed") is not True or (cl.get("residual") or []):
        return DEGRADED     # capability รันได้ แต่ยืนยัน teardown ไม่ได้ → ห้าม PASS (กระทบ run ถัดไป)
    return PASS


def resolve_terminal_status(receipt: dict) -> str:
    """terminal verdict ที่ **คำนวณใหม่จาก observation** (ใช้ตรวจว่า receipt ไม่ได้เขียน status ปลอม)"""
    return _resolve_terminal(receipt)


# ── validator (fail-closed) — errs ว่าง = receipt เชื่อถือได้และ terminal ตรง ──
def validate_m4_outer_receipt(receipt, *, inner_bundle_bytes) -> list:
    """
    ตรวจ outer receipt กับ **ไฟล์ bundle ชั้นในจริง** (bytes) : โครงสร้าง/ชนิด, bundle sha256 ตรงไฟล์,
    outer_receipt_sha256 ตรง body, และ terminal_status == ค่าที่ recompute (กัน status ปลอม)
    คืน list ของ error (ว่าง = ผ่าน)
    """
    errs = []
    if not isinstance(receipt, dict):
        return ["receipt ไม่ใช่ dict"]
    if receipt.get("outer_receipt_version") != OUTER_RECEIPT_VERSION:
        errs.append("outer_receipt_version ผิด")
    for k in ("attempt_id", "run_id", "inner", "observed", "process", "cleanup",
              "terminal_status", "outer_receipt_sha256"):
        if k not in receipt:
            errs.append(f"ขาดฟิลด์ {k}")
    if errs:
        return errs
    inner = receipt["inner"]
    if not isinstance(inner, dict) or not isinstance(inner.get("isolation"), dict):
        return errs + ["inner/inner.isolation ผิดรูป"]

    # bundle sha256 ต้องตรงกับไฟล์ชั้นในจริง (bind หลักฐานทั้งก้อน)
    try:
        actual = bundle_sha256(inner_bundle_bytes)
    except OuterReceiptError as e:
        return errs + [str(e)]
    if inner.get("bundle_sha256") != actual:
        errs.append(f"inner.bundle_sha256 ≠ sha256(ไฟล์ bundle จริง): {inner.get('bundle_sha256')} != {actual}")

    # outer receipt integrity (body hash)
    if not _is_sha256(receipt.get("outer_receipt_sha256") or ""):
        errs.append("outer_receipt_sha256 ไม่ใช่ sha256")
    elif receipt["outer_receipt_sha256"] != outer_receipt_sha256(receipt):
        errs.append("outer_receipt_sha256 ไม่ตรง body (receipt ถูกแก้)")

    # terminal ต้อง = recompute (fail-closed ต่อ status ปลอม)
    recomputed = _resolve_terminal(receipt)
    if receipt.get("terminal_status") != recomputed:
        errs.append(f"terminal_status = {receipt.get('terminal_status')} แต่ recompute ได้ {recomputed} (status ปลอม)")

    if receipt.get("terminal_status") not in (PASS, DEGRADED, FAILED):
        errs.append("terminal_status ไม่ใช่ค่าใน {PASS,DEGRADED,FAILED}")
    return errs


# ── durable atomic publish (no-clobber) ────────────────────────────────────────
def publish_outer_receipt(*, out_dir: str, attempt_id: str, receipt: dict) -> str:
    """
    เขียน out_dir/<attempt_id>.outer-receipt.json แบบ **atomic no-clobber** (immutable ต่อ attempt) ; คืน path
    - เขียน temp บน filesystem เดียวกับ final → fsync → hard-link (FileExistsError ถ้ามีแล้ว)
    - attempt_id ต้องเป็น safe basename (กันหลุด out_dir)
    """
    if not (isinstance(attempt_id, str) and attempt_id.strip()):
        raise OuterReceiptError("attempt_id ว่าง")
    if not all(c.isalnum() or c in "-_." for c in attempt_id) or attempt_id in (".", ".."):
        raise OuterReceiptError(f"attempt_id ต้องเป็น safe basename (alnum/-/_/.): {attempt_id!r}")
    body = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, attempt_id + ".outer-receipt.json")
    if os.path.dirname(os.path.realpath(final)) != os.path.realpath(out_dir):
        raise OuterReceiptError(f"attempt_id หลุดออกนอก out_dir: {attempt_id!r}")
    fd, tmp = tempfile.mkstemp(prefix=f".{attempt_id}.", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, final)
        except FileExistsError:
            raise OuterReceiptError(f"attempt {attempt_id!r} มี outer receipt อยู่แล้ว (immutable)")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return final
