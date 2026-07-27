"""
Unit tests สำหรับ service auth — ไม่ต้อง start model/Qdrant/LLM (รันเร็ว, $0)
    python test_auth.py
ครอบ regression ของบั๊ก fail-open ที่ GPT ชี้ (enforce + registry ว่าง)
"""
import hashlib
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))


def _req(keys, header_key=None):
    r = MagicMock()
    r.app.state.api_keys = keys
    r.headers = {"X-API-Key": header_key} if header_key else {}
    return r


def run():
    import app.main as m

    KEY = "s3cret-voicebot"
    KH = hashlib.sha256(KEY.encode()).hexdigest()
    REG = {KH: {"service": "voicebot", "allowed_roles": ["production", "qc"]}}
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        print(f"  {'PASS' if cond else 'FAIL'} — {name}")
        passed += cond
        failed += not cond

    def call(keys, mode, role, header):
        m.AUTH_MODE = mode
        from fastapi import HTTPException
        try:
            m.check_service_auth(_req(keys, header), role, "/search")
            return None
        except HTTPException as e:
            return e.status_code

    print("== enforce ==")
    check("no key -> 401",              call(REG, "enforce", "qc", None) == 401)
    check("valid key+role -> pass",     call(REG, "enforce", "qc", KEY) is None)
    check("role out of scope -> 403",   call(REG, "enforce", "admin", KEY) == 403)
    # regression: บั๊กเดิม registry ว่าง + enforce เคยปล่อยผ่าน — ต้อง 401 แล้ว
    check("REGRESSION empty registry+enforce -> 401",
          call({}, "enforce", "qc", None) == 401)

    print("== warn ==")
    check("no key -> pass (log)",       call(REG, "warn", "qc", None) is None)
    check("bad role -> pass (log)",     call(REG, "warn", "admin", KEY) is None)

    print("== off ==")
    check("no key -> pass",             call(REG, "off", "qc", None) is None)

    print("== load_api_keys validation ==")
    import json, tempfile
    def load_tmp(obj):
        p = tempfile.mktemp(suffix=".json")
        json.dump(obj, open(p, "w"))
        try:
            m.load_api_keys(p); return True
        except RuntimeError:
            return False
        finally:
            os.remove(p)
    check("valid registry loads",       load_tmp(REG) is True)
    check("short key rejected",         load_tmp({"abc": {"service": "x"}}) is False)
    check("unknown role rejected",
          load_tmp({KH: {"service": "x", "allowed_roles": ["wizard"]}}) is False)
    check("missing service rejected",   load_tmp({KH: {"allowed_roles": []}}) is False)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
