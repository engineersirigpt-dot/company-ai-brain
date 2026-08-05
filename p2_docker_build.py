"""
P2 Docker build wrapper — fail-closed **build lifecycle** (B1/B2/M2/N1)

แยก 2 artifact ชัดเจน:
- `build_request.json`  = inputs ที่เขียน **ก่อน** build (status=PENDING: base/platform/model/source hashes)
- `build_receipt.json`  = เขียน **atomic หลัง** docker exit 0 + validate iid + inspect(os/arch/tag/id) เท่านั้น
                          (status=SUCCEEDED + image id + evidence hashes) ; build ล้ม → `build_failure.json` (คนละ schema)

บังคับ: PY_BASE = immutable digest จริง (ไม่ใช่ sentinel/tag) + `--platform linux/amd64` + **tag image**
        (path anchored กับ repo root, run directory แยกต่อ run กัน stale/concurrent overwrite)

    python p2_docker_build.py --py-base python@sha256:<amd64 digest> [--dry-run]
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

# ไฟล์ source ที่ผูก hash ลง receipt (พิสูจน์ว่า build มาจาก artifact ชุดนี้)
SOURCE_FILES = ("Dockerfile.p2", "Dockerfile.p2.dockerignore", "requirements.p2.txt", "p2_pin.py",
                "p2_reranker.py", "p2_fetch_model.py", "p2_verify_snapshot.py", "p2_model_smoke.py",
                "p2_docker_build.py")
# evidence ใน image ที่ต้อง extract + bind กับ image id (M2)
EVIDENCE_FILES = ("/opt/model_file_manifest.sha256", "/opt/wheelhouse.manifest.sha256", "/opt/wheelhouse.freeze.txt")


def validate_py_base(value) -> list:
    """คืน list ของ error (ว่าง = ผ่าน). ต้องเป็น python@sha256:<64hex> จริง (ไม่ใช่ sentinel zeros / tag ลอย)"""
    if not isinstance(value, str) or not _PY_DIGEST.match(value):
        return [f"PY_BASE ต้องเป็น immutable digest 'python@sha256:<64hex>' (ห้าม tag ลอย): {value!r}"]
    if value == _ZERO_DIGEST:
        return ["PY_BASE เป็น sentinel zeros — ต้อง resolve digest จริง (linux/amd64) ก่อน build"]
    return []


def validate_iid(text) -> str:
    t = (text or "").strip() if isinstance(text, str) else ""
    if not _IID.match(t):
        raise ValueError(f"iid ต้องเป็น sha256:<64hex>: {text!r}")
    return t


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_hashes(root: Path = REPO_ROOT) -> dict:
    return {name: _sha256_file(root / name) for name in SOURCE_FILES if (root / name).is_file()}


def build_command(py_base: str, iid_path) -> list:
    """docker build — ล็อก platform + **tag image** (B2) + capture iid (M1) + ส่ง pin ทั้งสอง source เดียว"""
    return ["docker", "build", "--platform", PLATFORM, "--tag", IMAGE_TAG,
            "--build-arg", f"PY_BASE={py_base}",
            "--build-arg", f"MODEL_COMMIT={PIN.MODEL_COMMIT}",
            "--iidfile", str(iid_path), "-f", "Dockerfile.p2", "."]


def inspect_errors(info: dict, iid: str) -> list:
    """post-build inspect ต้องยืนยัน os/arch = linux/amd64, tag ชี้ image เดียวกัน, Id == iidfile"""
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


def _atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)   # atomic บน fs เดียวกัน


def run_build(py_base, out_dir, *, runner, inspector, extractor=None, read_iid=None,
              git_commit="", root: Path = REPO_ROOT) -> int:
    """
    lifecycle เดียว fail-closed. return 0 = success (มี build_receipt SUCCEEDED เท่านั้น) ;
    rc อื่น = fail (มี build_failure, ไม่มี success receipt). seams (runner/inspector/extractor/read_iid) inject ได้เพื่อ test โดยไม่ใช้ Docker
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    req_path, receipt_path = out_dir / "build_request.json", out_dir / "build_receipt.json"
    fail_path, iid_path = out_dir / "build_failure.json", out_dir / "p2_image.id"
    # N1/B1: ล้าง artifact ของ run นี้ก่อน (กัน stale iid/receipt ปนรอบก่อน)
    for p in (req_path, receipt_path, fail_path, iid_path):
        if p.exists():
            p.unlink()

    errs = validate_py_base(py_base)
    if errs:
        _atomic_write_json(fail_path, {"status": "REFUSED", "reasons": errs})
        return 2

    request = {"status": "PENDING", "py_base_digest": py_base, "platform": PLATFORM,
               "image_tag": IMAGE_TAG, "model_name": PIN.RERANKER_MODEL, "model_commit": PIN.MODEL_COMMIT,
               "git_commit": git_commit, "source_sha256": source_hashes(root)}
    _atomic_write_json(req_path, request)

    rc = runner(build_command(py_base, iid_path))
    if rc != 0:
        _atomic_write_json(fail_path, {**request, "status": "FAILED", "return_code": rc})
        return rc

    # success path: อ่าน iid + validate + inspect ก่อนถือว่าสำเร็จ
    try:
        raw = read_iid() if read_iid is not None else iid_path.read_text(encoding="utf-8")
        iid = validate_iid(raw)
    except (ValueError, OSError) as e:
        _atomic_write_json(fail_path, {**request, "status": "FAILED", "return_code": 0, "reason": f"iid invalid: {e}"})
        return 3

    ins_errs = inspect_errors(inspector(iid), iid)
    if ins_errs:
        _atomic_write_json(fail_path, {**request, "status": "FAILED", "return_code": 0, "inspect_errors": ins_errs})
        return 4

    evidence = extractor(iid, out_dir) if extractor is not None else {}
    _atomic_write_json(receipt_path, {**request, "status": "SUCCEEDED", "return_code": 0,
                                      "image_id": iid, "evidence_sha256": evidence})
    return 0


# ── real seams (ใช้ตอนรันจริง — ไม่ถูกเรียกใน unit test) ────────────────────────
def _docker_inspect(iid: str) -> dict:
    out = subprocess.check_output(["docker", "image", "inspect", "--format", "{{json .}}", iid],
                                  cwd=str(REPO_ROOT))
    d = json.loads(out)
    return {"Id": d.get("Id"), "Os": d.get("Os"), "Architecture": d.get("Architecture"),
            "RepoTags": d.get("RepoTags")}


def _docker_extract_evidence(iid: str, out_dir: Path) -> dict:
    """M2: create container (ไม่ start) จาก validated iid → cp evidence ออกมา → hash → rm ; bind กับ image id"""
    cid = subprocess.check_output(["docker", "create", "--network", "none", iid],
                                  cwd=str(REPO_ROOT)).decode().strip()
    got = {}
    try:
        dest = Path(out_dir) / "evidence"
        dest.mkdir(parents=True, exist_ok=True)
        for src in EVIDENCE_FILES:
            local = dest / Path(src).name
            try:
                subprocess.check_call(["docker", "cp", f"{cid}:{src}", str(local)], cwd=str(REPO_ROOT))
                got[Path(src).name] = _sha256_file(local)
            except subprocess.CalledProcessError:
                got[Path(src).name] = None
    finally:
        subprocess.call(["docker", "rm", cid], cwd=str(REPO_ROOT))
    return got


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT)).decode().strip()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 pinned Docker build wrapper (fail-closed lifecycle)")
    ap.add_argument("--py-base", required=True, help="python@sha256:<64hex> (linux/amd64 digest จริง)")
    ap.add_argument("--out-dir", default=None, help="run directory (default .p2_build/<run-id>)")
    ap.add_argument("--dry-run", action="store_true", help="validate + print command โดยไม่รัน docker")
    args = ap.parse_args()

    errs = validate_py_base(args.py_base)
    if errs:
        print("BUILD REFUSED: " + "; ".join(errs), file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else (BUILD_ROOT / uuid.uuid4().hex)
    if args.dry_run:
        print("$ " + " ".join(build_command(args.py_base, out_dir / "p2_image.id")))
        return 0
    rc = run_build(args.py_base, out_dir,
                   runner=lambda cmd: subprocess.call(cmd, cwd=str(REPO_ROOT)),
                   inspector=_docker_inspect, extractor=_docker_extract_evidence, git_commit=_git_commit())
    print(("SUCCEEDED -> " if rc == 0 else f"FAILED (rc={rc}) -> ") + str(out_dir))
    return rc


if __name__ == "__main__":
    sys.exit(main())
