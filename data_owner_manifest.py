"""
Data Owner **manifest builder** (tooling สำหรับ DATA_OWNER_SIGNOFF_PACK.md §3) — คำนวณ SHA-256 ต่อไฟล์จริง +
วาง row skeleton ให้มนุษย์กรอก classification/allowed_roles/label ; คำนวณ `manifest_sha256` ที่ผูกกับ sign-off

**Guardrail (บังคับในโค้ด):** tooling **ไม่กรอก human decision** — ทุก row ออกมาด้วย classification=null, allowed_roles=[],
human_reviewed=**false**, label_status=**PENDING** ; `assert_ai_safe()` ปฏิเสธ manifest ที่ human field ถูกตั้งค่า (กัน AI/
สคริปต์ตั้ง approved/human-reviewed แทนมนุษย์) ; `verify_signoff()` เป็น **verify เท่านั้น ไม่ approve**

ใช้: `python data_owner_manifest.py build <dir> [--out m.json] [--version v1]`
     `python data_owner_manifest.py verify <m.json> --sha <64hex> --version v1`
"""
from __future__ import annotations

import hashlib
import json
import os

MANIFEST_SCHEMA_VERSION = 1

# ฟิลด์ที่ **มนุษย์เท่านั้น** กรอก — tooling ต้องปล่อยว่าง (AI-safety)
_HUMAN_FIELDS = ("doc_owner", "classification", "confidentiality_level", "has_pii", "is_trade_secret",
                 "egress_policy", "purpose", "redaction", "deletion_trigger", "retention")


class ManifestError(Exception):
    """manifest ผิด contract / tooling ถูกใช้ตั้ง human field (guardrail)"""


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_files(root: str) -> list:
    """สแกนไฟล์ทั้งหมดใต้ root (recursive) → [{source(relpath, posix), file_sha256, size_bytes}] เรียงตาม source"""
    if not os.path.isdir(root):
        raise ManifestError(f"ไม่ใช่ไดเรกทอรี: {root!r}")
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append({"source": rel, "file_sha256": _file_sha256(full), "size_bytes": os.path.getsize(full)})
    return sorted(out, key=lambda r: r["source"])


def _blank_row(entry: dict) -> dict:
    row = {"source": entry["source"], "file_sha256": entry["file_sha256"], "size_bytes": entry["size_bytes"],
           "allowed_roles": [], "human_reviewed": False, "label_status": "PENDING"}
    for k in _HUMAN_FIELDS:
        row[k] = None                                       # มนุษย์กรอก — tooling เว้น null
    return row


def build_manifest_skeleton(entries: list, *, manifest_version: str = "v1") -> dict:
    """สร้าง manifest skeleton (human field ว่างทั้งหมด) + manifest_sha256 ; ไม่ตั้ง approved/human_reviewed"""
    rows = [_blank_row(e) for e in sorted(entries, key=lambda r: r["source"])]
    manifest = {"manifest_schema_version": MANIFEST_SCHEMA_VERSION, "manifest_version": manifest_version,
                "row_count": len(rows), "rows": rows}
    manifest["manifest_sha256"] = manifest_sha256(manifest)
    assert_ai_safe(manifest)                                # กันพลาด: skeleton ต้อง AI-safe เสมอ
    return manifest


def _canonical_body(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if k != "manifest_sha256"}


def canonical_manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(_canonical_body(manifest), sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def assert_ai_safe(manifest: dict) -> None:
    """
    guardrail: manifest ที่ tooling/AI ผลิต **ห้ามมี human decision** — ทุก row ต้อง human_reviewed=false,
    label_status=PENDING และ human field เป็น null/ว่าง ; ถ้าไม่ → ManifestError (แปลว่ามีการกรอกแทนมนุษย์)
    """
    for i, row in enumerate(manifest.get("rows", [])):
        if row.get("human_reviewed") is not False:
            raise ManifestError(f"row {i}: human_reviewed ต้องเป็น false ใน tooling output (มนุษย์เท่านั้นตั้ง true)")
        if row.get("label_status") != "PENDING":
            raise ManifestError(f"row {i}: label_status ต้องเป็น PENDING")
        if row.get("allowed_roles"):
            raise ManifestError(f"row {i}: allowed_roles ต้องว่างใน skeleton (มนุษย์กรอก)")
        for k in _HUMAN_FIELDS:
            if row.get(k) is not None:
                raise ManifestError(f"row {i}: {k} ต้องเป็น null ใน tooling output (มนุษย์กรอก)")


def verify_signoff(manifest: dict, *, approved_manifest_sha256: str, approved_manifest_version: str) -> tuple:
    """
    **verify เท่านั้น (ไม่ approve):** ตรวจว่า manifest ที่จะใช้จริงตรงกับที่มนุษย์ลงนาม —
    recompute manifest_sha256 + เทียบกับที่ approve + version ตรง ; คืน (ok: bool, reasons: list)
    """
    reasons = []
    recomputed = manifest_sha256(manifest)
    if manifest.get("manifest_sha256") != recomputed:
        reasons.append(f"manifest_sha256 ในไฟล์ ≠ recompute ({manifest.get('manifest_sha256')} != {recomputed})")
    if recomputed != approved_manifest_sha256:
        reasons.append("manifest ที่จะใช้ ≠ ที่มนุษย์ลงนาม (hash ไม่ตรง) — sign-off เดิมเป็นโมฆะ ต้องลงนามใหม่")
    if manifest.get("manifest_version") != approved_manifest_version:
        reasons.append(f"manifest_version ≠ ที่อนุมัติ ({manifest.get('manifest_version')} != {approved_manifest_version})")
    return (not reasons, reasons)


def _main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "build":
        root = argv[1]
        version = "v1"
        out = None
        i = 2
        while i < len(argv):
            if argv[i] == "--version" and i + 1 < len(argv):
                version = argv[i + 1]; i += 2
            elif argv[i] == "--out" and i + 1 < len(argv):
                out = argv[i + 1]; i += 2
            else:
                i += 1
        manifest = build_manifest_skeleton(scan_files(root), manifest_version=version)
        text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"เขียน manifest -> {out}  (rows={manifest['row_count']}  sha256={manifest['manifest_sha256']})")
            print("หมายเหตุ: human field ว่างทั้งหมด (PENDING) — Data Owner กรอก classification/allowed_roles/label เอง")
        else:
            print(text)
        return 0
    if len(argv) >= 2 and argv[0] == "verify":
        with open(argv[1], encoding="utf-8") as f:
            manifest = json.load(f)
        sha = ver = None
        i = 2
        while i < len(argv):
            if argv[i] == "--sha" and i + 1 < len(argv):
                sha = argv[i + 1]; i += 2
            elif argv[i] == "--version" and i + 1 < len(argv):
                ver = argv[i + 1]; i += 2
            else:
                i += 1
        ok, reasons = verify_signoff(manifest, approved_manifest_sha256=sha, approved_manifest_version=ver)
        print("VERIFY:", "OK" if ok else "MISMATCH")
        for r in reasons:
            print("  -", r)
        return 0 if ok else 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
