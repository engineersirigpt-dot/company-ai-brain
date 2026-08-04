"""
Unit tests สำหรับ service auth contract — P1 (ผ่าน policy.py, ไม่ต้อง start model/Qdrant/LLM)
    python test_auth.py
ครอบ enforce/warn/off table + regression บั๊ก fail-open (enforce + registry ว่าง -> 401)
auth logic ย้ายจาก check_service_auth() มาเป็น authenticate_service()+resolve_effective_access() ใน policy.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import policy as P

REG_ENTRY = {"service": "voicebot", "allowed_roles": ["production", "qc"]}
res = []
def check(name, cond):
    res.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'} — {name}")


def call(mode, role, has_key):
    """จำลอง endpoint auth: authenticate -> resolve ; คืน None ถ้าผ่าน, หรือ HTTP code ถ้า deny"""
    principal = P.authenticate_service(REG_ENTRY if has_key else None, "hint", mode)
    try:
        P.resolve_effective_access(principal, role)
        return None
    except P.AuthError as e:
        return e.code


print("== enforce ==")
check("no key -> 401", call("enforce", "qc", False) == 401)
check("valid key+role -> pass", call("enforce", "qc", True) is None)
check("role out of scope -> 403", call("enforce", "admin", True) == 403)
check("unknown role -> 403", call("enforce", "wizard", True) == 403)
# regression: บั๊กเดิม registry ว่าง + enforce เคยปล่อยผ่าน — ตอนนี้ resolve คืน 401
# (และ startup lifespan ยัง refuse-to-start เมื่อ enforce + ไม่มี key เลย — fail-closed สองชั้น)
check("REGRESSION empty registry+enforce -> 401", call("enforce", "qc", False) == 401)

print("== warn ==")
check("no key -> pass (unverified)", call("warn", "qc", False) is None)
check("bad role -> pass (unverified)", call("warn", "admin", True) is None)
check("warn principal.verified == False",
      P.authenticate_service(REG_ENTRY, "h", "warn").verified is False)

print("== off ==")
check("no key -> pass", call("off", "qc", False) is None)

# unknown/empty role ถูก deny ทุก mode (malformed input, fail-closed)
print("== role validation (ทุก mode) ==")
check("empty role -> 403 แม้ off", call("off", "", True) == 403)
check("unknown role -> 403 แม้ warn", call("warn", "wizard", True) == 403)

# ── load_api_keys validation — อยู่ใน app.main (ต้อง import heavy deps) ──────────
print("== load_api_keys validation ==")
try:
    import hashlib, json, tempfile
    import app.main as m
    KH = hashlib.sha256(b"k").hexdigest()

    def load_tmp(obj):
        p = tempfile.mktemp(suffix=".json")
        json.dump(obj, open(p, "w"))
        try:
            m.load_api_keys(p); return True
        except RuntimeError:
            return False
        finally:
            os.remove(p)
    check("valid registry loads", load_tmp({KH: {"service": "voicebot", "allowed_roles": ["qc"]}}) is True)
    check("short key rejected", load_tmp({"abc": {"service": "x"}}) is False)
    check("unknown role rejected", load_tmp({KH: {"service": "x", "allowed_roles": ["wizard"]}}) is False)
    check("missing service rejected", load_tmp({KH: {"allowed_roles": []}}) is False)
except ImportError as e:
    print(f"  SKIP — app.main import ไม่ได้ในสภาพแวดล้อมนี้ (heavy deps): {type(e).__name__}: {e}")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
