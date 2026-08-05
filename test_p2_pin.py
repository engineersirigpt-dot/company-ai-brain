"""
Unit test ของ P2 pinned model identity (p2_pin) — pure, offline
พิสูจน์ว่า commit ที่ pin เป็น full immutable + ผ่าน validate_pin ก่อนนำไป bake image

    python test_p2_pin.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os

import p2_pin as PIN
import p2_reranker as RK
import p2_runplan as RP
import p2_fetch_model as FM
import p2_verify_snapshot as VS

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises_sysexit(fn):
    try:
        fn(); return False
    except SystemExit:
        return True
def no_sysexit(fn):
    try:
        fn(); return True
    except SystemExit:
        return False

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

# ── B2: MODEL_COMMIT build arg เป็น control จริง (assert == p2_pin ก่อน fetch) ──
os.environ.pop("P2_EXPECT_COMMIT", None)
check("B2: ไม่มี P2_EXPECT_COMMIT -> ไม่ fail (p2_pin single source)", no_sysexit(FM._assert_expected_commit))
os.environ["P2_EXPECT_COMMIT"] = PIN.MODEL_COMMIT
check("B2: P2_EXPECT_COMMIT == pin -> ไม่ fail", no_sysexit(FM._assert_expected_commit))
os.environ["P2_EXPECT_COMMIT"] = "d" * 40
check("B2: P2_EXPECT_COMMIT != pin -> SystemExit (build arg ไม่ใช่ control ปลอม)", raises_sysexit(FM._assert_expected_commit))
# M1: blank/whitespace ต้อง fail-closed (แยก None ออกจาก empty)
os.environ["P2_EXPECT_COMMIT"] = ""
check("M1: P2_EXPECT_COMMIT blank -> SystemExit (ไม่มองเป็น 'ไม่ตั้ง')", raises_sysexit(FM._assert_expected_commit))
os.environ["P2_EXPECT_COMMIT"] = "   "
check("M1: P2_EXPECT_COMMIT whitespace -> SystemExit", raises_sysexit(FM._assert_expected_commit))
os.environ.pop("P2_EXPECT_COMMIT", None)

# ── M2: verify snapshot fail-closed ด้วย SystemExit (ไม่ใช่ assert) ─────────────
os.environ["HF_HOME"] = os.path.join(os.environ.get("TEMP", "."), "p2_no_such_cache")
check("M2: verify snapshot dir หาย -> SystemExit (fail-closed)", raises_sysexit(VS.main))
os.environ.pop("HF_HOME", None)
check("M2: snapshot_path ชี้ใต้ snapshots/<SHA>",
      VS.snapshot_path("/opt/hf").replace("\\", "/").endswith("snapshots/" + PIN.MODEL_COMMIT))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
