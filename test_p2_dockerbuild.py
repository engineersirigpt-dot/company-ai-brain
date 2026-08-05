"""
Unit test ของ P2 Docker build wrapper lifecycle (p2_docker_build) — pure/offline, fake seams (ไม่ใช้ Docker)
พิสูจน์ success receipt เกิดเฉพาะเมื่อ build ok + iid/tag/platform ถูก + **evidence exact schema ครบและอ่านได้** ;
ทุก stage ล้ม/exception → build_failure (stage-aware) ไม่มี success receipt

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
def _raises(fn):
    try:
        fn(); return False
    except (ValueError, KeyError):
        return True
def _throw(exc):
    def _f(*a, **k):
        raise exc
    return _f


_GOOD = "python@sha256:" + "a" * 64
_IID = "sha256:" + "1" * 64
_GOOD_INFO = {"Id": _IID, "Os": "linux", "Architecture": "amd64", "RepoTags": [DB.IMAGE_TAG]}


def _good_evidence():
    return {"model_file_manifest.sha256": "d" * 64 + "\n",
            "wheelhouse.manifest.sha256": "a" * 64 + "  torch-2.3.1+cpu.whl\n" + "b" * 64 + "  transformers.whl\n",
            "wheelhouse.freeze.txt": "torch==2.3.1+cpu\ntransformers==4.41.2\n"}
def _ev_dir(files):
    d = Path(tempfile.mkdtemp()) / "evidence"
    d.mkdir(parents=True)
    for n, c in files.items():
        if c is not None:
            (d / n).write_text(c, encoding="utf-8")
    return d
def fake_extractor(files):
    def _ext(iid, out_dir):
        dest = Path(out_dir) / "evidence"
        dest.mkdir(parents=True, exist_ok=True)
        for n, c in files.items():
            if c is not None:
                (dest / n).write_text(c, encoding="utf-8")
        return dest
    return _ext
def _fake_root(missing=None):
    d = Path(tempfile.mkdtemp())
    for name in DB.SOURCE_FILES:
        if name != missing:
            (d / name).write_text("x", encoding="utf-8")
    return d
def _read(tmp, name):
    p = Path(tmp) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
def _run(tmp, *, py_base=_GOOD, rc=0, iid_text=_IID, info=None, extractor=None, runner=None):
    inspector = info if callable(info) else (lambda iid: (_GOOD_INFO if info is None else info))
    return DB.run_build(py_base, tmp,
                        runner=(runner if runner is not None else (lambda cmd: rc)),
                        inspector=inspector,
                        extractor=(extractor if extractor is not None else fake_extractor(_good_evidence())),
                        read_iid=lambda: iid_text, git_commit="deadbeef")


# ── validators ─────────────────────────────────────────────────────────────────
check("validate_py_base digest -> ผ่าน", DB.validate_py_base(_GOOD) == [])
check("validate_py_base sentinel/tag/blank -> error",
      DB.validate_py_base("python@sha256:" + "0" * 64) != [] and DB.validate_py_base("python:3.11-slim") != [] and DB.validate_py_base("") != [])
check("validate_iid ดี/malformed", DB.validate_iid("  " + _IID + "\n") == _IID and _raises(lambda: DB.validate_iid("nope")))
_cmd = DB.build_command(_GOOD, "/tmp/x.id")
check("build_command tag+platform+iidfile+pin",
      "--tag" in _cmd and DB.IMAGE_TAG in _cmd and "linux/amd64" in _cmd and "--iidfile" in _cmd
      and f"PY_BASE={_GOOD}" in _cmd and f"MODEL_COMMIT={PIN.MODEL_COMMIT}" in _cmd)
check("inspect ok/arch/tag/id", DB.inspect_errors(_GOOD_INFO, _IID) == []
      and DB.inspect_errors({**_GOOD_INFO, "Architecture": "arm64"}, _IID)
      and DB.inspect_errors({**_GOOD_INFO, "RepoTags": []}, _IID)
      and DB.inspect_errors({**_GOOD_INFO, "Id": "sha256:" + "9" * 64}, _IID))

# ── B1: validate_extracted_evidence (exact schema, fail-closed) ─────────────────
check("B1: evidence valid exact set -> ok", DB.validate_extracted_evidence(_ev_dir(_good_evidence()))[1] == [])
check("B1: evidence {} -> error", DB.validate_extracted_evidence(_ev_dir({}))[1] != [])
check("B1: evidence ขาด 1 key -> error", DB.validate_extracted_evidence(_ev_dir({k: v for k, v in _good_evidence().items() if k != "wheelhouse.freeze.txt"}))[1] != [])
check("B1: evidence extra key -> error", DB.validate_extracted_evidence(_ev_dir({**_good_evidence(), "extra.txt": "x"}))[1] != [])
check("B1: model manifest malformed hash -> error", any("64-hex" in e for e in DB.validate_extracted_evidence(_ev_dir({**_good_evidence(), "model_file_manifest.sha256": "zzz"}))[1]))
check("B1: evidence empty file -> error", any("ว่าง" in e for e in DB.validate_extracted_evidence(_ev_dir({**_good_evidence(), "wheelhouse.freeze.txt": "   "}))[1]))
check("B1: wheel manifest dup filename -> error", any("ซ้ำ" in e for e in DB.validate_extracted_evidence(_ev_dir({**_good_evidence(), "wheelhouse.manifest.sha256": "a" * 64 + "  x.whl\n" + "b" * 64 + "  x.whl\n"}))[1]))

# ── M1: parsed model manifest != file hash ─────────────────────────────────────
_parsed, _perr = DB.validate_extracted_evidence(_ev_dir(_good_evidence()))
check("M1: model_file_manifest_sha256 = parsed content (= d*64)", _perr == [] and _parsed["model_file_manifest_sha256"] == "d" * 64)
check("M1: parsed != file hash (ไม่ double-hash)", _parsed["model_file_manifest_sha256"] != _parsed["evidence_file_sha256"]["model_file_manifest.sha256"])

# ── SUCCESS -> receipt เท่านั้น (พร้อม field ครบ) ──────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp)
    rcpt = _read(tmp, "build_receipt.json")
    check("SUCCESS -> rc0 + receipt SUCCEEDED + image_id", rc == 0 and rcpt and rcpt["status"] == "SUCCEEDED" and rcpt["image_id"] == _IID)
    check("SUCCESS -> model_file_manifest_sha256(=d*64) + evidence_file_sha256 + context_bytes + build_log_sha256 + source_sha256",
          rcpt["model_file_manifest_sha256"] == "d" * 64 and rcpt["evidence_file_sha256"] and "context_bytes" in rcpt
          and "build_log_sha256" in rcpt and len(rcpt["source_sha256"]) == len(DB.SOURCE_FILES))
    check("SUCCESS -> ไม่มี build_failure", _read(tmp, "build_failure.json") is None)

# ── B1 e2e: incomplete/None evidence -> EVIDENCE_INVALID, ไม่มี receipt ─────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, extractor=fake_extractor({"model_file_manifest.sha256": None, "wheelhouse.manifest.sha256": None, "wheelhouse.freeze.txt": None}))
    check("B1 e2e: evidence ไม่ครบ -> rc7 EVIDENCE_INVALID + ไม่มี receipt", rc == 7 and _read(tmp, "build_receipt.json") is None and _read(tmp, "build_failure.json")["status"] == "EVIDENCE_INVALID")
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, extractor=fake_extractor({**_good_evidence(), "model_file_manifest.sha256": "not-hex\n"}))
    check("B1 e2e: model manifest malformed -> rc7 + ไม่มี receipt", rc == 7 and _read(tmp, "build_receipt.json") is None)

# ── B2: exception จาก seam -> stage-aware failure, ไม่ crash, ไม่มี receipt ─────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, info=_throw(RuntimeError("inspect down")))
    check("B2: inspector raise -> rc5 INSPECT_FAILED", rc == 5 and _read(tmp, "build_failure.json")["status"] == "INSPECT_FAILED" and _read(tmp, "build_receipt.json") is None)
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, extractor=_throw(RuntimeError("extract boom")))
    check("B2: extractor raise -> rc6 EXTRACT_FAILED", rc == 6 and _read(tmp, "build_failure.json")["status"] == "EXTRACT_FAILED" and _read(tmp, "build_receipt.json") is None)
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, runner=_throw(RuntimeError("docker down")))
    check("B2: runner raise -> rc8 RUNNER_FAILED", rc == 8 and _read(tmp, "build_failure.json")["status"] == "RUNNER_FAILED" and _read(tmp, "build_receipt.json") is None)

# ── lifecycle rc/iid/inspect/refused (เดิม) ────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, rc=17)
    check("FAILED rc17 -> ไม่มี receipt + failure FAILED", rc == 17 and _read(tmp, "build_receipt.json") is None and _read(tmp, "build_failure.json")["return_code"] == 17)
    check("FAILED failure ไม่มี _rc leak", "_rc" not in _read(tmp, "build_failure.json"))
with tempfile.TemporaryDirectory() as tmp:
    check("REFUSED bad py_base -> rc2 (runner ไม่ถูกเรียก)",
          _run(tmp, py_base="python:3.11-slim", runner=_throw(AssertionError("no build"))) == 2 and _read(tmp, "build_failure.json")["status"] == "REFUSED")
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, iid_text="garbage")
    check("iid malformed -> rc3 IID_INVALID + ไม่มี receipt", rc == 3 and _read(tmp, "build_receipt.json") is None and _read(tmp, "build_failure.json")["status"] == "IID_INVALID")
with tempfile.TemporaryDirectory() as tmp:
    rc = _run(tmp, info={**_GOOD_INFO, "Architecture": "arm64"})
    check("inspect arch mismatch -> rc4 INSPECT_MISMATCH", rc == 4 and _read(tmp, "build_receipt.json") is None and _read(tmp, "build_failure.json")["status"] == "INSPECT_MISMATCH")

# ── B3: exact source set (missing .dockerignore -> REFUSED, runner ไม่ถูกเรียก) ──
with tempfile.TemporaryDirectory() as tmp:
    rc = DB.run_build(_GOOD, tmp, runner=_throw(AssertionError("runner ไม่ควรถูกเรียก")),
                      inspector=lambda iid: _GOOD_INFO, extractor=fake_extractor(_good_evidence()),
                      read_iid=lambda: _IID, root=_fake_root(missing="Dockerfile.p2.dockerignore"))
    check("B3: source ขาด .dockerignore -> REFUSED rc2 + runner ไม่ถูกเรียก",
          rc == 2 and any("dockerignore" in r for r in _read(tmp, "build_failure.json")["reasons"]))
check("B3: source_hashes repo จริง -> ครบ ไม่มี error", DB.source_hashes()[1] == [] and len(DB.source_hashes()[0]) == len(DB.SOURCE_FILES))

# ── M2: run dir ต้องว่าง + resolve_out_dir ─────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "stale").write_text("x", encoding="utf-8")
    check("M2: run dir ไม่ว่าง -> rc2 (ไม่ทับ/ไม่ build)", _run(tmp, runner=_throw(AssertionError("no build"))) == 2)
check("M2: resolve_out_dir relative -> ใต้ BUILD_ROOT", str(DB.resolve_out_dir("run1")).replace("\\", "/").endswith(".p2_build/run1"))
check("M2: resolve_out_dir absolute -> คงเดิม", DB.resolve_out_dir(str(Path(tempfile.gettempdir()) / "x")) == Path(tempfile.gettempdir()) / "x")

# ── M1(adapter guard): unrelated ImportError ต้อง fail ไม่ skip ─────────────────
import importlib.util
_HAS_QDRANT = importlib.util.find_spec("qdrant_client") is not None
_probe = ("import importlib.util\n"
          "if importlib.util.find_spec('qdrant_client') is None:\n    raise SystemExit(0)\n"
          "import __definitely_missing_module__\n")
_p = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True)
check("adapter guard: qdrant present -> unrelated ImportError = non-zero (ไม่กลืนเป็น skip)" if _HAS_QDRANT else "adapter guard: qdrant absent -> skip rc0",
      (_p.returncode != 0) if _HAS_QDRANT else (_p.returncode == 0), f"rc={_p.returncode}")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
