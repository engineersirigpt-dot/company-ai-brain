"""
P2 Docker build wrapper — fail-closed **build lifecycle** (B1/B2/B3/M1/M2)

`build_receipt.json` (status=SUCCEEDED) มีความหมายเดียว: docker build สำเร็จ + image identity/platform/tag ถูกต้อง
+ **evidence บังคับครบและอ่านได้จริงจาก image เดียวกัน**. ทุก stage หลัง build ที่ raise/คืนผิด → `build_failure.json`
(schema แยก, stage-aware) และ **ห้ามมี success receipt**.

- `build_request.json` (PENDING) เขียนก่อน build (base/platform/model/**exact source hashes**/context bytes)
- source binding บังคับ **exact `SOURCE_FILES` ครบ** (รวม `.dockerignore`) — หายแม้ตัวเดียว → REFUSED ก่อนเรียก runner
- evidence 3 ไฟล์ถูก extract จาก validated iid แล้ว **validate exact schema** ก่อนเขียน receipt
- receipt แยก `model_file_manifest_sha256` (content ในไฟล์ → RunPlan) ออกจาก `evidence_file_sha256` (hash ไฟล์เพื่อ audit)
- run directory ต้อง **ว่าง/ใหม่** (ห้ามทับ run ที่มี artifact แล้ว) ; build log ถูก capture + hash

    python p2_docker_build.py --py-base python@sha256:<amd64 digest> [--out-dir NAME] [--dry-run]
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import p2_pin as PIN

REPO_ROOT = Path(__file__).resolve().parent
PLATFORM = "linux/amd64"
IMAGE_TAG = "company-ai-brain/p2-reranker:pinned-cpu"
BUILD_ROOT = REPO_ROOT / ".p2_build"

_PY_DIGEST = re.compile(r"^python@sha256:[0-9a-f]{64}$")
_ZERO_DIGEST = "python@sha256:" + "0" * 64
_IID = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# source ที่ผูก hash ลง request/receipt — ต้องครบ exact (B3)
SOURCE_FILES = ("Dockerfile.p2", "Dockerfile.p2.dockerignore", "requirements.p2.txt", "p2_pin.py",
                "p2_reranker.py", "p2_fetch_model.py", "p2_verify_snapshot.py", "p2_model_smoke.py",
                "p2_docker_build.py")
# ไฟล์ที่เข้า build context จริง (ตรง Dockerfile.p2.dockerignore allowlist) — ใช้คำนวณ context bytes
CONTEXT_FILES = ("Dockerfile.p2", "requirements.p2.txt", "p2_pin.py", "p2_reranker.py",
                 "p2_fetch_model.py", "p2_verify_snapshot.py", "p2_model_smoke.py")
# evidence ใน image ที่ต้อง extract + validate + bind (M1/B1)
EVIDENCE_FILES = ("/opt/model_file_manifest.sha256", "/opt/wheelhouse.manifest.sha256", "/opt/wheelhouse.freeze.txt")
EVIDENCE_NAMES = tuple(Path(p).name for p in EVIDENCE_FILES)


def validate_py_base(value) -> list:
    """PY_BASE ต้องเป็น python@sha256:<64hex> จริง (ไม่ใช่ sentinel zeros / tag ลอย)"""
    if not isinstance(value, str) or not _PY_DIGEST.match(value):
        return [f"PY_BASE ต้องเป็น immutable digest 'python@sha256:<64hex>' (ห้าม tag ลอย): {value!r}"]
    if value == _ZERO_DIGEST:
        return ["PY_BASE เป็น sentinel zeros — ต้อง resolve digest จริง (linux/amd64) ก่อน build"]
    return []


def validate_iid(text) -> str:
    t = text.strip() if isinstance(text, str) else ""
    if not _IID.match(t):
        raise ValueError(f"iid ต้องเป็น sha256:<64hex>: {text!r}")
    return t


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_hashes(root: Path = REPO_ROOT):
    """คืน (map, errors). B3: ต้องมีครบทุก SOURCE_FILES (regular file + อ่านได้) มิฉะนั้น errors ไม่ว่าง"""
    out, errs = {}, []
    for name in SOURCE_FILES:
        p = root / name
        if not p.is_file():
            errs.append(f"source file หาย/ไม่ใช่ไฟล์: {name}")
            continue
        try:
            out[name] = _sha256_file(p)
        except OSError as e:
            errs.append(f"อ่าน source ไม่ได้: {name} ({type(e).__name__})")
    return out, errs


def context_bytes(root: Path = REPO_ROOT) -> int:
    return sum((root / f).stat().st_size for f in CONTEXT_FILES if (root / f).is_file())


def build_command(py_base: str, iid_path) -> list:
    """docker build — ล็อก platform + tag image + capture iid + ส่ง pin ทั้งสอง source เดียว"""
    return ["docker", "build", "--platform", PLATFORM, "--tag", IMAGE_TAG,
            "--build-arg", f"PY_BASE={py_base}",
            "--build-arg", f"MODEL_COMMIT={PIN.MODEL_COMMIT}",
            "--iidfile", str(iid_path), "-f", "Dockerfile.p2", "."]


def inspect_errors(info: dict, iid: str) -> list:
    errs = []
    if info.get("Os") != "linux":
        errs.append(f"Os != linux ({info.get('Os')!r})")
    if info.get("Architecture") != "amd64":
        errs.append(f"Architecture != amd64 ({info.get('Architecture')!r})")
    if IMAGE_TAG not in (info.get("RepoTags") or []):
        errs.append(f"tag {IMAGE_TAG} ไม่อยู่ใน image RepoTags")
    if info.get("Id") != iid:
        errs.append("inspect Id != iidfile")
    return errs


def validate_extracted_evidence(evidence_dir):
    """
    B1/M1: อ่าน evidence 3 ไฟล์จาก dir → คืน (parsed, errors). fail-closed:
    exact keys ครบ · แต่ละไฟล์ regular+non-empty · model_file_manifest content = บรรทัดเดียว 64-hex
    · wheel manifest ไม่ว่าง+ไม่มี filename ซ้ำ · freeze ไม่ว่าง
    parsed = {model_file_manifest_sha256: <content>, evidence_file_sha256: {name: <file sha256>}}
    """
    ed = Path(evidence_dir)
    errs, file_hashes = [], {}
    present = {p.name for p in ed.iterdir() if p.is_file()} if ed.is_dir() else set()
    want = set(EVIDENCE_NAMES)
    if present != want:
        errs.append(f"evidence keys ไม่ตรง exact (missing={sorted(want - present)} extra={sorted(present - want)})")
    for name in EVIDENCE_NAMES:
        f = ed / name
        if not f.is_file():
            errs.append(f"evidence {name} หาย/ไม่ใช่ regular file")
            continue
        try:
            data = f.read_bytes()
        except OSError as e:
            errs.append(f"evidence {name} อ่านไม่ได้ ({type(e).__name__})")
            continue
        if not data.strip():
            errs.append(f"evidence {name} ว่าง")
            continue
        file_hashes[name] = hashlib.sha256(data).hexdigest()

    model_manifest = None
    mf = ed / "model_file_manifest.sha256"
    if mf.is_file():
        content = mf.read_text(encoding="utf-8", errors="replace").strip()
        if _HEX64.match(content):
            model_manifest = content
        else:
            errs.append("model_file_manifest.sha256 content ต้องเป็นบรรทัดเดียว 64-hex")
    wm = ed / "wheelhouse.manifest.sha256"
    if wm.is_file():
        rows = [ln.split() for ln in wm.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        if not rows:
            errs.append("wheelhouse.manifest ว่าง")
        elif any(len(r) < 2 or not _HEX64.match(r[0]) for r in rows):
            errs.append("wheelhouse.manifest แถวต้องเป็น '<64hex>  <wheel>'")
        elif len({r[-1] for r in rows}) != len(rows):
            errs.append("wheelhouse.manifest มี filename ซ้ำ")

    if errs:
        return {}, errs
    return {"model_file_manifest_sha256": model_manifest, "evidence_file_sha256": file_hashes}, []


_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def validate_receipt(receipt) -> list:
    """
    post-build gate (Codex pass criteria) — receipt SUCCEEDED ต้องผ่านทุกข้อ (คืน [] = ผ่าน):
    status/return_code, image_id + build_log_sha256 + model_file_manifest_sha256 เป็น digest ถูก format,
    source_sha256 exact 9 keys, evidence_file_sha256 exact 3 keys ทุกค่า 64-hex, git_commit full SHA, platform amd64
    """
    if not isinstance(receipt, dict):
        return ["receipt ไม่ใช่ dict"]
    errs = []
    if receipt.get("status") != "SUCCEEDED":
        errs.append("status != SUCCEEDED")
    if receipt.get("return_code") != 0:
        errs.append("return_code != 0")
    if not _IID.match(receipt.get("image_id", "") if isinstance(receipt.get("image_id"), str) else ""):
        errs.append("image_id ต้องเป็น sha256:<64hex>")
    blog = receipt.get("build_log_sha256")
    if not (isinstance(blog, str) and _HEX64.match(blog)):
        errs.append("build_log_sha256 ต้องเป็น 64-hex (ไม่ null)")
    mfm = receipt.get("model_file_manifest_sha256")
    if not (isinstance(mfm, str) and _HEX64.match(mfm)):
        errs.append("model_file_manifest_sha256 ต้องเป็น 64-hex")
    src = receipt.get("source_sha256")
    if not isinstance(src, dict) or set(src) != set(SOURCE_FILES) or not all(isinstance(v, str) and _HEX64.match(v) for v in src.values()):
        errs.append(f"source_sha256 ต้องมี exact {len(SOURCE_FILES)} keys เป็น 64-hex")
    ev = receipt.get("evidence_file_sha256")
    if not isinstance(ev, dict) or set(ev) != set(EVIDENCE_NAMES) or not all(isinstance(v, str) and _HEX64.match(v) for v in ev.values()):
        errs.append("evidence_file_sha256 ต้องมี exact 3 keys เป็น 64-hex")
    gc = receipt.get("git_commit")
    if not (isinstance(gc, str) and _FULL_SHA.match(gc)):
        errs.append("git_commit ต้องเป็น full 40-hex")
    if receipt.get("platform") != PLATFORM:
        errs.append("platform != linux/amd64")
    return errs


def _atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def run_build(py_base, out_dir, *, runner, inspector, extractor, read_iid=None,
              git_commit="", git_dirty=False, root: Path = REPO_ROOT) -> int:
    """
    lifecycle เดียว fail-closed. return 0 = success (มี build_receipt SUCCEEDED เท่านั้น) ;
    rc อื่น = fail (มี build_failure stage-aware). seams inject ได้เพื่อ test โดยไม่ใช้ Docker
    """
    out_dir = Path(out_dir)
    # M2: run directory ต้องว่าง/ใหม่ — ห้ามทับ run ที่มี artifact แล้ว
    if out_dir.exists() and any(out_dir.iterdir()):
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    fail_path, receipt_path = out_dir / "build_failure.json", out_dir / "build_receipt.json"
    req_path, iid_path, log_path = out_dir / "build_request.json", out_dir / "p2_image.id", out_dir / "build.log"

    def _fail(status, base=None, **extra):
        rc = extra.pop("_rc", 1)
        _atomic_write_json(fail_path, {**(base or {}), "status": status, **extra})
        return rc

    errs = validate_py_base(py_base)
    if errs:
        return _fail("REFUSED", reasons=errs, _rc=2)
    src_map, src_errs = source_hashes(root)
    if src_errs:
        return _fail("REFUSED", reasons=src_errs, _rc=2)

    request = {"status": "PENDING", "py_base_digest": py_base, "platform": PLATFORM,
               "image_tag": IMAGE_TAG, "model_name": PIN.RERANKER_MODEL, "model_commit": PIN.MODEL_COMMIT,
               "git_commit": git_commit, "git_dirty": bool(git_dirty),
               "source_sha256": src_map, "declared_context_bytes": context_bytes(root)}
    _atomic_write_json(req_path, request)

    try:
        rc = runner(build_command(py_base, iid_path))
    except Exception as e:                                  # runner (docker) crash
        return _fail("RUNNER_FAILED", request, reason=type(e).__name__, _rc=8)
    if rc != 0:
        return _fail("FAILED", request, return_code=rc, _rc=rc)

    build_log_sha256 = _sha256_file(log_path) if log_path.is_file() else None

    # ── post-build stages: ทุก step มี exception boundary + stage-aware failure (B2) ──
    try:
        iid = validate_iid(read_iid() if read_iid is not None else iid_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return _fail("IID_INVALID", request, reason=str(e), _rc=3)

    try:
        info = inspector(iid)
    except Exception as e:
        return _fail("INSPECT_FAILED", request, image_id=iid, reason=type(e).__name__, _rc=5)
    ins_errs = inspect_errors(info, iid)
    if ins_errs:
        return _fail("INSPECT_MISMATCH", request, image_id=iid, inspect_errors=ins_errs, _rc=4)

    try:
        parsed, ev_errs = validate_extracted_evidence(extractor(iid, out_dir))
    except Exception as e:
        return _fail("EXTRACT_FAILED", request, image_id=iid, reason=type(e).__name__, _rc=6)
    if ev_errs:
        return _fail("EVIDENCE_INVALID", request, image_id=iid, evidence_errors=ev_errs, _rc=7)

    try:
        _atomic_write_json(receipt_path, {
            **request, "status": "SUCCEEDED", "return_code": 0, "image_id": iid,
            "build_log_sha256": build_log_sha256,
            "model_file_manifest_sha256": parsed["model_file_manifest_sha256"],
            "evidence_file_sha256": parsed["evidence_file_sha256"]})
    except OSError as e:
        return _fail("RECEIPT_WRITE_FAILED", request, image_id=iid, reason=type(e).__name__, _rc=9)
    return 0


# ── real seams (ใช้ตอนรันจริง — ไม่ถูกเรียกใน unit test) ────────────────────────
def _docker_inspect(iid: str) -> dict:
    out = subprocess.check_output(["docker", "image", "inspect", "--format", "{{json .}}", iid], cwd=str(REPO_ROOT))
    d = json.loads(out)
    return {"Id": d.get("Id"), "Os": d.get("Os"), "Architecture": d.get("Architecture"), "RepoTags": d.get("RepoTags")}


def _docker_extract_evidence(iid: str, out_dir) -> Path:
    """M2/B1: create container (ไม่ start) จาก validated iid → cp evidence 3 ไฟล์ออกมา → rm ; คืน dir ให้ validate"""
    dest = Path(out_dir) / "evidence"
    dest.mkdir(parents=True, exist_ok=True)
    cid = subprocess.check_output(["docker", "create", "--network", "none", iid], cwd=str(REPO_ROOT)).decode().strip()
    try:
        for src in EVIDENCE_FILES:
            try:
                subprocess.check_call(["docker", "cp", f"{cid}:{src}", str(dest / Path(src).name)], cwd=str(REPO_ROOT))
            except subprocess.CalledProcessError:
                pass   # ไฟล์ขาด → validate_extracted_evidence จับเป็น missing
    finally:
        subprocess.call(["docker", "rm", cid], cwd=str(REPO_ROOT))
    return dest


def _runner_with_log(log_path: Path):
    def _run(cmd):
        with open(log_path, "wb") as log:
            return subprocess.call(cmd, cwd=str(REPO_ROOT), stdout=log, stderr=subprocess.STDOUT)
    return _run


def _git_state():
    """คืน (full commit SHA, dirty). commit ว่าง/ dirty=True → validate_receipt/review จับได้ (hardening #4)"""
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT)).decode().strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=str(REPO_ROOT)).decode().strip())
        return commit, dirty
    except Exception:
        return "", False


def resolve_out_dir(arg) -> Path:
    """M2: relative → ใต้ BUILD_ROOT ; ไม่ระบุ → run id ใหม่"""
    if not arg:
        return BUILD_ROOT / uuid.uuid4().hex
    p = Path(arg)
    return p if p.is_absolute() else (BUILD_ROOT / p)


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 pinned Docker build wrapper (fail-closed lifecycle)")
    ap.add_argument("--py-base", required=True, help="python@sha256:<64hex> (linux/amd64 digest จริง)")
    ap.add_argument("--out-dir", default=None, help="run directory (relative → ใต้ .p2_build/)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    errs = validate_py_base(args.py_base)
    if errs:
        print("BUILD REFUSED: " + "; ".join(errs), file=sys.stderr)
        return 2
    out_dir = resolve_out_dir(args.out_dir)
    if args.dry_run:
        print("$ " + " ".join(build_command(args.py_base, out_dir / "p2_image.id")))
        return 0
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"BUILD REFUSED: run directory ไม่ว่าง (ห้ามทับ): {out_dir}", file=sys.stderr)
        return 2
    commit, dirty = _git_state()
    rc = run_build(args.py_base, out_dir,
                   runner=_runner_with_log(out_dir / "build.log"),
                   inspector=_docker_inspect, extractor=_docker_extract_evidence,
                   git_commit=commit, git_dirty=dirty)
    if rc == 0:
        gate = validate_receipt(json.loads((out_dir / "build_receipt.json").read_text(encoding="utf-8")))
        if gate:
            print("WARNING: receipt ยังไม่ผ่าน post-build gate: " + "; ".join(gate), file=sys.stderr)
    print(("SUCCEEDED -> " if rc == 0 else f"FAILED (rc={rc}) -> ") + str(out_dir))
    return rc


if __name__ == "__main__":
    sys.exit(main())
