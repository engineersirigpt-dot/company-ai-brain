"""
สร้าง synthetic API-key registry สำหรับ P5b — one key per role, scope = role นั้นตัวเดียว
(ทำให้ spoof: key ของ role r ขอ role อื่น → out-of-scope → 403 ตาม acceptance C)

    python p5b_gen_keys.py
เขียน api_keys.p5b.json (gitignored) + พิมพ์ KB_EVAL_KEYS ให้ ask_eval.py
** synthetic ล้วน ไม่ลับ ห้ามใช้กับ registry จริง **
"""
import hashlib
import json
import sys

import policy as P


def main() -> None:
    roles = sorted(P.KNOWN_ROLES)
    registry, eval_keys = {}, {}
    for r in roles:
        raw = f"p5b-{r}-synthetic-key"          # deterministic (reproducible), ไม่ลับ
        registry[hashlib.sha256(raw.encode()).hexdigest()] = {"service": f"p5b-{r}", "allowed_roles": [r]}
        eval_keys[r] = raw
    with open("api_keys.p5b.json", "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=1)
    print(f"wrote api_keys.p5b.json ({len(registry)} keys, one per role scoped to itself)", file=sys.stderr)
    print("KB_EVAL_KEYS=" + json.dumps(eval_keys, ensure_ascii=False))


if __name__ == "__main__":
    main()
