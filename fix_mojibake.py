"""
แก้ mojibake ภาษาไทยในไฟล์ markdown ที่ parse จาก PDF ฟอนต์เพี้ยน (กลุ่ม tone-mark)

ที่มา: ฟอนต์ใน PDF map วรรณยุกต์ + เลข ไปตำแหน่ง Latin ผิด (base อักษรไทยถูก)
map นี้ derive จากการที่ codepoint เรียงต่อเนื่อง + validate ด้วยปีพุทธที่สมเหตุผล
  - วรรณยุกต์ U+00C9-CB -> ่ ้ ๊
  - เลข     U+0158-0161 -> 0-9  (Ř=0 ... š=9 ; ŚŝŞŜ -> 2564 ยืนยันแล้ว)

*ใช้กับกลุ่ม tone-mark เท่านั้น* — ไฟล์ AFII (text layer พังยับ) ต้อง OCR แยก

Usage:
    python fix_mojibake.py <dir> [--apply]   (ไม่มี --apply = ตรวจอย่างเดียว)
"""
import sys, glob, io, os, unicodedata

TONE = {"É": "่", "Ê": "้", "Ë": "๊"}   # É ้Ê Ë -> ่ ้ ๊
DIGITS = {c: str(i) for i, c in enumerate(
    ["Ř", "ř", "Ś", "ś", "Ŝ",
     "ŝ", "Ş", "ş", "Š", "š"])}             # Ř..š -> 0..9
FIXMAP = {**TONE, **DIGITS}

# codepoint ที่ถือว่า "ปกติ" หลังแก้: ASCII, ไทย, punctuation, ± (เครื่องหมายจริง)
def is_ok(ch):
    o = ord(ch)
    return o < 128 or 0x0E00 <= o <= 0x0E7F or ch in "–—‘’“”…± \t\n\r"

def fix_text(t):
    return "".join(FIXMAP.get(ch, ch) for ch in t)

def suspects(t):
    return [ch for ch in set(t) if not is_ok(ch)]

def main():
    d = sys.argv[1]
    apply = "--apply" in sys.argv
    files = sorted(glob.glob(os.path.join(d, "*.md")))
    targets = [f for f in files if any(c in io.open(f, encoding="utf-8").read() for c in TONE)]
    print(f"tone-mark files: {len(targets)}   apply={apply}\n")
    total_before = total_after = 0
    for f in targets:
        raw = io.open(f, encoding="utf-8").read()
        fixed = fix_text(raw)
        nb = sum(1 for ch in raw if ch in FIXMAP)
        left = suspects(fixed)
        total_before += nb
        total_after += sum(1 for ch in fixed if not is_ok(ch))
        flag = "" if not left else f"  ⚠ leftover: {left}"
        print(f"  {os.path.basename(f)[:45]:45} fixed={nb:5}{flag}")
        if apply:
            io.open(f, "w", encoding="utf-8").write(fixed)
    print(f"\nรวมแก้ {total_before} ตัว | เหลือ suspect (นอก ±) {total_after} ตัว")
    print("[APPLIED]" if apply else "[DRY-RUN]")

if __name__ == "__main__":
    main()
