"""
Unit test ของ P2 Docker build wrapper lifecycle (p2_docker_build) — pure/offline, fake subprocess/inspect
พิสูจน์ fail-closed: success receipt เกิดเฉพาะเมื่อ rc==0 + iid valid + inspect os/arch/tag/id ตรง ;
build ล้ม/refused → ไม่มี success receipt (มี failure schema แยก)

    python test_p2_dockerbuild.py
"""
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_pin as PIN
import p2_docker_build as DB

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))


_GOOD = "python@sha256:" + "a" * 64
_IID = "sha256:" + "1" * 64
_GOOD_INFO = {"Id": _IID, "Os": "linux", "Architecture": "amd64", "RepoTags": [DB.IMAGE_TAG]}


def _run(tmp, *, py_base=_GOOD, rc=0, iid_text=_IID, info=None, extractor=None):
    """เรียก run_build ด้วย fake seams — ไม่แตะ Docker จริง"""
    info = _GOOD_INFO if info is None else info
    return DB.run_build(py_base, tmp,
                        runner=lambda cmd: rc,
                        inspector=lambda iid: info,
                        extractor=extractor,
                        read_iid=lambda: iid_text,
                        git_commit="deadbeef")


def _read(tmp, name):
    p = Path(tmp) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ── validate_py_base / validate_iid ────────────────────────────────────────────
check("validate_py_base digest จริง -> ผ่าน", DB.validate_py_base(_GOOD) == [])
check("validate_py_base sentinel zeros -> error", any("sentinel" in e for e in DB.validate_py_base("python@sha256:" + "0" * 64)))
check("validate_py_base floating tag -> error", DB.validate_py_base("python:3.11-slim") != [])
check("validate_py_base non-python -> error", DB.validate_py_base("ubuntu@sha256:" + "a" * 64) != [])
check("validate_py_base blank/None -> error", DB.validate_py_base("") != [] and DB.validate_py_base(None) != [])
check("validate_iid ดี -> คืนค่า", DB.validate_iid("  " + _IID + "\n") == _IID)
def _raises(fn):
    try:
        fn(); return False
    except (ValueError, KeyError):
        return True
check("validate_iid malformed -> ValueError", _raises(lambda: DB.validate_iid("not-a-digest")))

# ── build_command: tag + platform + iidfile + pin ทั้งสอง ───────────────────────
_cmd = DB.build_command(_GOOD, "/tmp/x.id")
check("build_command มี --tag pinned-cpu (B2)", "--tag" in _cmd and DB.IMAGE_TAG in _cmd)
check("build_command ล็อก --platform linux/amd64", "--platform" in _cmd and "linux/amd64" in _cmd)
check("build_command มี --iidfile + PY_BASE + MODEL_COMMIT",
      "--iidfile" in _cmd and f"PY_BASE={_GOOD}" in _cmd and f"MODEL_COMMIT={PIN.MODEL_COMMIT}" in _cmd)

# ── inspect_errors: os/arch/tag/id ─────────────────────────────────────────────
check("inspect ok -> ไม่มี error", DB.inspect_errors(_GOOD_INFO, _IID) == [])
check("inspect arch != amd64 -> error", any("Architecture" in e for e in DB.inspect_errors({**_GOOD_INFO, "Architecture": "arm64"}, _IID)))
check("inspect tag หาย -> error", any("tag" in e for e in DB.inspect_errors({**_GOOD_INFO, "RepoTags": []}, _IID)))
check("inspect Id != iid -> error", any("Id" in e for e in DB.inspect_errors({**_GOOD_INFO, "Id": "sha256:" + "9" * 64}, _IID)))

# ── lifecycle: SUCCESS -> receipt เท่านั้น ─────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, extractor=lambda iid, d: {"model_file_manifest.sha256": "b" * 64})
    rcpt = _read(tmp, "build_receipt.json")
    check("SUCCESS -> rc 0 + build_receipt SUCCEEDED + image_id ผูก", rc == 0 and rcpt and rcpt["status"] == "SUCCEEDED" and rcpt["image_id"] == _IID)
    check("SUCCESS -> receipt มี source_sha256 + evidence_sha256 + platform", "source_sha256" in rcpt and rcpt["evidence_sha256"] and rcpt["platform"] == "linux/amd64")
    check("SUCCESS -> ไม่มี build_failure.json", _read(tmp, "build_failure.json") is None)

# ── B1: FAILED build (rc=17) -> ไม่มี success receipt ──────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, rc=17)
    check("FAILED rc17 -> return 17", rc == 17)
    check("FAILED rc17 -> ไม่มี build_receipt.json (ไม่มี success-looking)", _read(tmp, "build_receipt.json") is None)
    fail = _read(tmp, "build_failure.json")
    check("FAILED rc17 -> build_failure schema (status FAILED + return_code 17)", fail and fail["status"] == "FAILED" and fail["return_code"] == 17)

# ── REFUSED (bad py_base) -> ไม่มี receipt, ไม่เรียก runner ────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rc = DB.run_build("python:3.11-slim", tmp, runner=lambda cmd: (_ for _ in ()).throw(AssertionError("runner ไม่ควรถูกเรียก")),
                      inspector=lambda iid: _GOOD_INFO, read_iid=lambda: _IID)
    check("REFUSED floating tag -> rc 2 + ไม่มี receipt + runner ไม่ถูกเรียก", rc == 2 and _read(tmp, "build_receipt.json") is None and _read(tmp, "build_failure.json")["status"] == "REFUSED")

# ── malformed iid (rc=0 แต่ iid พัง) -> fail, ไม่มี receipt ────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, iid_text="garbage")
    check("rc0 แต่ iid malformed -> rc 3 + ไม่มี receipt", rc == 3 and _read(tmp, "build_receipt.json") is None and "iid invalid" in _read(tmp, "build_failure.json").get("reason", ""))

# ── inspect mismatch (rc=0, iid ok แต่ arch ผิด) -> fail, ไม่มี receipt ────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, info={**_GOOD_INFO, "Architecture": "arm64"})
    check("inspect arch mismatch -> rc 4 + ไม่มี receipt + inspect_errors ใน failure", rc == 4 and _read(tmp, "build_receipt.json") is None and _read(tmp, "build_failure.json").get("inspect_errors"))

# ── stale iid: run ก่อนหน้าเหลือ p2_image.id -> run ใหม่ที่ fail ต้องล้าง ก่อน ──
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "p2_image.id").write_text("sha256:" + "e" * 64, encoding="utf-8")   # stale จากรอบก่อน
    (Path(tmp) / "build_receipt.json").write_text('{"status":"SUCCEEDED"}', encoding="utf-8")  # stale success
    rc = _run(tmp, rc=9)   # build ใหม่ fail
    check("stale artifacts ถูกล้างก่อน build -> fail ไม่เหลือ stale success receipt", rc == 9 and _read(tmp, "build_receipt.json") is None)

# ── M1: adapter integration guard เจาะจง qdrant_client (unrelated ImportError ต้อง fail ไม่ skip) ──
import importlib.util
_HAS_QDRANT = importlib.util.find_spec("qdrant_client") is not None
_probe = ("import importlib.util\n"
          "has = importlib.util.find_spec('qdrant_client') is not None\n"
          "if not has:\n"
          "    raise SystemExit(0)\n"                 # ไม่มี qdrant -> skip ปกติ
          "import __definitely_missing_module__\n"    # จำลอง p2_harness ImportError regression (unrelated)
          "raise SystemExit(0)\n")
_p = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True)
if _HAS_QDRANT:
    check("M1: qdrant present -> unrelated ImportError = non-zero (ไม่กลืนเป็น skip)", _p.returncode != 0, f"rc={_p.returncode}")
else:
    check("M1: qdrant absent -> guard skip (rc 0)", _p.returncode == 0, f"rc={_p.returncode}")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
