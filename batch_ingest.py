"""
Batch Ingestion Pipeline: folder → parse → embed → Qdrant

Usage:
    python batch_ingest.py <folder>
    python batch_ingest.py docs/
    python batch_ingest.py .          # โฟลเดอร์ปัจจุบัน

ไฟล์ที่รองรับ: pdf, docx, xlsx, pptx, html, png, jpg, jpeg, tiff, bmp
ข้าม: .doc (binary Word), .mp3, .zip ฯลฯ, ไฟล์ที่ parse แล้ว
"""
import sys
import time
import subprocess
from pathlib import Path

from parse_doc import SUPPORTED_FORMATS, OUTPUT_DIR, get_output_path
from ingest import chunk_by_headings, embed_chunks, store_in_qdrant

import torch
from transformers import AutoTokenizer, AutoModel


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _eta(elapsed: float, done: int, total: int) -> str:
    if done == 0 or elapsed < 1:
        return ""
    avg = elapsed / done
    remaining = avg * (total - done)
    pct = done / total * 100
    return f"| ผ่านไป {_fmt_time(elapsed)} | เหลือ ~{_fmt_time(remaining)} ({pct:.0f}%)"


# นามสกุลที่ข้ามเสมอ
SKIP_EXTENSIONS = {
    ".md", ".txt", ".json", ".py", ".sqlite", ".lock",
    ".doc",                          # binary Word เก่า — docling ไม่รองรับ
    ".mp3", ".wav", ".m4a",         # audio
    ".zip", ".rar", ".7z",          # archive
    ".exe", ".dll", ".bat",         # binary
}


def parse_safe(f: Path) -> bool:
    """รัน parse_doc.py ใน subprocess แยก — ถ้า crash/segfault จะข้ามแทนที่จะพัง batch"""
    result = subprocess.run(
        [sys.executable, "parse_doc.py", str(f)],
        timeout=600,  # 10 นาที max ต่อไฟล์
    )
    if result.returncode != 0:
        print(f"  [SKIP] process crashed (exit {result.returncode})")
        return False
    return True


def already_parsed(path: Path) -> bool:
    return get_output_path(path).exists()


def batch(folder: str):
    folder_path = Path(folder)
    if not folder_path.is_dir():
        print(f"[ERROR] ไม่ใช่ folder: {folder}")
        sys.exit(1)

    # หาไฟล์ทั้งหมดที่รองรับ (recursive ลงไปทุก subfolder)
    candidates = [
        f for f in folder_path.rglob("*")
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_FORMATS
        and f.suffix.lower() not in SKIP_EXTENSIONS
    ]

    if not candidates:
        print(f"ไม่มีไฟล์ที่รองรับใน {folder}")
        sys.exit(0)

    # แยก: ต้อง parse ใหม่ vs ข้ามเพราะมี .md แล้ว
    to_parse = [f for f in candidates if not already_parsed(f)]
    skip = [f for f in candidates if already_parsed(f)]

    print(f"พบไฟล์ทั้งหมด : {len(candidates)}")
    print(f"มี .md แล้ว   : {len(skip)} ไฟล์ (ข้าม parse, ingest ใหม่)")
    print(f"ต้อง parse    : {len(to_parse)} ไฟล์")
    print()

    # --- Stage 1: Parse ---
    run_start = time.time()
    if to_parse:
        print("=" * 60)
        print("STAGE 1: Parsing")
        print("=" * 60)
        stage1_start = time.time()
        for i, f in enumerate(to_parse, 1):
            t0 = time.time()
            elapsed_total = t0 - stage1_start
            eta_str = _eta(elapsed_total, i - 1, len(to_parse))
            print(f"\n[{i}/{len(to_parse)}] {f.name}  {eta_str}")
            try:
                ok = parse_safe(f)
                if ok:
                    print(f"  ใช้เวลา {time.time() - t0:.1f}s")
            except subprocess.TimeoutExpired:
                print(f"  [SKIP] timeout (>10 นาที)")

    # รวมทุก .md ที่จะ ingest (ทั้งที่เพิ่ง parse และที่มีอยู่แล้ว)
    md_files = [OUTPUT_DIR / f.with_suffix(".md").name for f in candidates
                if (OUTPUT_DIR / f.with_suffix(".md").name).exists()]

    if not md_files:
        print("[ERROR] ไม่มีไฟล์ .md สำหรับ ingest")
        sys.exit(1)

    # --- Stage 2: Load model ครั้งเดียว ---
    print("\n" + "=" * 60)
    print("STAGE 2: Loading BGE-M3 (once)")
    print("=" * 60)
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3")
    model.eval()

    # --- Stage 3: Embed + Store ---
    print("\n" + "=" * 60)
    print("STAGE 3: Embed + Ingest to Qdrant")
    print("=" * 60)

    total_chunks = 0
    stage3_start = time.time()
    for i, md in enumerate(md_files, 1):
        t0 = time.time()
        elapsed_total = t0 - stage3_start
        eta_str = _eta(elapsed_total, i - 1, len(md_files))
        print(f"\n[{i}/{len(md_files)}] {md.name}  {eta_str}")
        text = md.read_text(encoding="utf-8")
        chunks = chunk_by_headings(text, source_name=md.stem)
        chunks = embed_chunks(tokenizer, model, chunks)
        store_in_qdrant(chunks)
        total_chunks += len(chunks)
        print(f"  {len(chunks)} chunks | ใช้เวลา {time.time() - t0:.1f}s")

    total_time = time.time() - run_start
    print(f"\n[OK] Done! ingest รวม {total_chunks} chunks จาก {len(md_files)} ไฟล์ | รวม {_fmt_time(total_time)}")


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    batch(folder)
