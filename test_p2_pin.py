"""
Unit test ของ P2 pinned model identity (p2_pin) — pure, offline
พิสูจน์ว่า commit ที่ pin เป็น full immutable + ผ่าน validate_pin ก่อนนำไป bake image

    python test_p2_pin.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_pin as PIN
import p2_reranker as RK
import p2_runplan as RP

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))

check("MODEL_COMMIT เป็น full 40-hex", RP._is_full_commit(PIN.MODEL_COMMIT) and len(PIN.MODEL_COMMIT) == 40, PIN.MODEL_COMMIT)
check("tokenizer_commit == model_commit (snapshot เดียวกัน)", PIN.TOKENIZER_COMMIT == PIN.MODEL_COMMIT)
check("model_name อยู่ allowlist", PIN.RERANKER_MODEL in RK.ALLOWED_MODELS)
check("validate_pin ผ่าน (allowlist + full commit + positive params)",
      RK.validate_pin(PIN.RERANKER_MODEL, PIN.MODEL_COMMIT, 512, 16) == [],
      RK.validate_pin(PIN.RERANKER_MODEL, PIN.MODEL_COMMIT, 512, 16))
check("REQUIRED_FILES ครบ 6 (weights+tokenizer+config)",
      set(PIN.REQUIRED_FILES) == {"config.json", "model.safetensors", "tokenizer.json",
                                  "tokenizer_config.json", "sentencepiece.bpe.model", "special_tokens_map.json"})
check("MIN_SAFETENSORS_BYTES เป็น floor กัน LFS pointer (>0)", PIN.MIN_SAFETENSORS_BYTES > 0)
check("abbreviated 7-hex ของ commit -> validate_pin reject (กัน pin ย่อ)",
      any("revision" in e for e in RK.validate_pin(PIN.RERANKER_MODEL, PIN.MODEL_COMMIT[:7], 512, 16)))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
