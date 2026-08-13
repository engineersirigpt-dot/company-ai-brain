"""
Unit test ของ qdrant_filter.to_qdrant_filter — RBAC filter adapter (ปลายทางสุดของการกันสิทธิ์)
เน้น fail-closed: spec ว่าง/malformed ต้อง raise (ไม่กลายเป็น Filter(must=[]) = match-all = leak)

    python test_qdrant_filter.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import qdrant_filter as QF

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True

# valid: value -> MatchValue, any -> MatchAny ; ครบทุกเงื่อนไข (AND)
spec = [
    {"key": "acl_schema_version", "value": 1},
    {"key": "policy_version", "value": "poc-v1"},
    {"key": "policy_status", "value": "ACTIVE"},
    {"key": "allowed_roles", "any": ["sales"]},
]
f = QF.to_qdrant_filter(spec)
check("valid spec -> Filter มี must ครบ 4 เงื่อนไข (ไม่ drop)", len(f.must) == 4, len(f.must))
check("allowed_roles ใช้ MatchAny", any(getattr(c.match, "any", None) == ["sales"] for c in f.must))
check("value ใช้ MatchValue", any(getattr(c.match, "value", None) == "poc-v1" for c in f.must))

# 🔴 fail-closed: spec ว่าง -> ValueError (ไม่ยอมให้เป็น match-all = leak ทั้งคลัง)
check("empty spec [] -> ValueError (fail-closed, กัน match-all leak)", raises(lambda: QF.to_qdrant_filter([]), ValueError))

# malformed: condition ไม่มี value/any -> ValueError (ไม่ KeyError/ไม่เงียบ)
check("condition ไม่มี value/any -> ValueError", raises(lambda: QF.to_qdrant_filter([{"key": "x"}]), ValueError))

# single valid condition -> ok (ไม่ raise เพราะ must ไม่ว่าง)
check("single condition -> ok (must ไม่ว่าง)", len(QF.to_qdrant_filter([{"key": "allowed_roles", "any": ["hr"]}]).must) == 1)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
