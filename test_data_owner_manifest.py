"""
Unit test ของ data_owner_manifest — builder ผลิต skeleton ที่ AI-safe (human field ว่าง) + hash-bound + verify-only

    python test_data_owner_manifest.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import data_owner_manifest as DM

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True


d = tempfile.mkdtemp(prefix="dowman-")
os.makedirs(os.path.join(d, "sub"))
with open(os.path.join(d, "a.md"), "w", encoding="utf-8") as f:
    f.write("HR policy alpha")
with open(os.path.join(d, "sub", "b.md"), "w", encoding="utf-8") as f:
    f.write("SOP beta")

entries = DM.scan_files(d)
check("scan: เจอไฟล์ครบ + เรียงตาม source", [e["source"] for e in entries] == ["a.md", "sub/b.md"])
check("scan: มี file_sha256 (64-hex) ต่อไฟล์", all(len(e["file_sha256"]) == 64 for e in entries))

m = DM.build_manifest_skeleton(entries, manifest_version="v1")
check("build: row_count ตรง", m["row_count"] == 2)
check("build: manifest_sha256 = recompute", m["manifest_sha256"] == DM.manifest_sha256(m))

# AI-safety: ทุก row human field ว่าง + human_reviewed=false + label PENDING
r0 = m["rows"][0]
check("AI-safe: human_reviewed=false", r0["human_reviewed"] is False)
check("AI-safe: label_status=PENDING", r0["label_status"] == "PENDING")
check("AI-safe: classification/allowed_roles ว่าง", r0["classification"] is None and r0["allowed_roles"] == [])
check("AI-safe: assert_ai_safe ผ่านบน skeleton", DM.assert_ai_safe(m) is None)

# guardrail: ถ้ามี row ถูกตั้ง human_reviewed/label แทนมนุษย์ -> ManifestError
m_bad = json.loads(json.dumps(m)); m_bad["rows"][0]["human_reviewed"] = True
check("guardrail: human_reviewed=true -> ManifestError", raises(lambda: DM.assert_ai_safe(m_bad), DM.ManifestError))
m_bad2 = json.loads(json.dumps(m)); m_bad2["rows"][0]["classification"] = "Confidential"
check("guardrail: classification ถูกกรอก -> ManifestError", raises(lambda: DM.assert_ai_safe(m_bad2), DM.ManifestError))

# hash-bound: เปลี่ยนเนื้อไฟล์ -> file_sha256 + manifest_sha256 เปลี่ยน
with open(os.path.join(d, "a.md"), "w", encoding="utf-8") as f:
    f.write("HR policy alpha EDITED")
m2 = DM.build_manifest_skeleton(DM.scan_files(d), manifest_version="v1")
check("hash-bound: แก้ไฟล์ -> manifest_sha256 เปลี่ยน", m2["manifest_sha256"] != m["manifest_sha256"])

# verify-only: ตรงทุกอย่าง -> ok ; sha ไม่ตรง -> not ok ; version ไม่ตรง -> not ok
ok, reasons = DM.verify_signoff(m, approved_manifest_sha256=m["manifest_sha256"], approved_manifest_version="v1")
check("verify: manifest ตรงที่ลงนาม -> ok", ok, reasons)
ok2, r2 = DM.verify_signoff(m2, approved_manifest_sha256=m["manifest_sha256"], approved_manifest_version="v1")
check("verify: manifest เปลี่ยน (hash ไม่ตรงที่ลงนาม) -> not ok", not ok2 and r2)
ok3, r3 = DM.verify_signoff(m, approved_manifest_sha256=m["manifest_sha256"], approved_manifest_version="v2")
check("verify: version ไม่ตรง -> not ok", not ok3)
# receipt-in-file tamper: แก้ row แต่ไม่ re-hash -> manifest_sha256 ในไฟล์ != recompute
m_tam = json.loads(json.dumps(m)); m_tam["rows"][0]["size_bytes"] = 999999
ok4, r4 = DM.verify_signoff(m_tam, approved_manifest_sha256=m["manifest_sha256"], approved_manifest_version="v1")
check("verify: manifest ถูกแก้ไม่ re-hash -> not ok (ในไฟล์ ≠ recompute)", not ok4)

shutil.rmtree(d, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
