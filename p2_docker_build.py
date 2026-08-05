"""
P2 Docker build wrapper — fail-closed base pin + platform lock (B2)
บังคับ `PY_BASE` เป็น **immutable digest** (`python@sha256:<64hex>`, ไม่ใช่ sentinel zeros / tag ลอย)
+ `--platform linux/amd64` แล้วบันทึก base digest + platform + model SHA ลง build manifest (source-controllable)
ไม่ให้ enforcement อยู่แค่ comment/CLI arg ลอย ๆ

    python p2_docker_build.py --py-base python@sha256:<digest> [--dry-run]
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys

import p2_pin as PIN

PLATFORM = "linux/amd64"
BUILD_MANIFEST = "p2_build_manifest.json"
_PY_DIGEST = re.compile(r"^python@sha256:[0-9a-f]{64}$")
_ZERO_DIGEST = "python@sha256:" + "0" * 64


def validate_py_base(value) -> list:
    """คืน list ของ error (ว่าง = ผ่าน). ต้องเป็น python@sha256:<64hex> จริง (ไม่ใช่ sentinel zeros / tag ลอย)"""
    if not isinstance(value, str) or not _PY_DIGEST.match(value):
        return [f"PY_BASE ต้องเป็น immutable digest 'python@sha256:<64hex>' (ห้าม tag ลอย): {value!r}"]
    if value == _ZERO_DIGEST:
        return ["PY_BASE เป็น sentinel zeros — ต้อง resolve digest จริง (linux/amd64) ก่อน build"]
    return []


def build_command(py_base: str) -> list:
    """docker build command ที่ล็อก platform + capture local image id (M1) + ส่ง pin ทั้งสอง source เดียว"""
    return ["docker", "build", "--platform", PLATFORM,
            "--build-arg", f"PY_BASE={py_base}",
            "--build-arg", f"MODEL_COMMIT={PIN.MODEL_COMMIT}",
            "--iidfile", "p2_image.id", "-f", "Dockerfile.p2", "."]


def build_manifest(py_base: str) -> dict:
    return {"py_base_digest": py_base, "platform": PLATFORM,
            "model_name": PIN.RERANKER_MODEL, "model_commit": PIN.MODEL_COMMIT}


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 pinned Docker build wrapper (fail-closed base/platform)")
    ap.add_argument("--py-base", required=True, help="python@sha256:<64hex> (linux/amd64 digest จริง)")
    ap.add_argument("--dry-run", action="store_true", help="พิมพ์ command + manifest โดยไม่รัน docker")
    args = ap.parse_args()

    errs = validate_py_base(args.py_base)
    if errs:
        print("BUILD REFUSED: " + "; ".join(errs), file=sys.stderr)
        return 2

    with open(BUILD_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(build_manifest(args.py_base), f, ensure_ascii=False, indent=2, sort_keys=True)
    cmd = build_command(args.py_base)
    print(f"build manifest -> {BUILD_MANIFEST}")
    print("$ " + " ".join(cmd))
    if args.dry_run:
        return 0
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
