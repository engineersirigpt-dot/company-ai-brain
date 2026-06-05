"""
Universal Document Parser — Thai + English
รองรับ: PDF (text-based / scanned), DOCX, XLSX, PPTX, HTML, Images

Usage:
    python parse_doc.py <path_to_file>
    python parse_doc.py docs/manual.pdf
    python parse_doc.py docs/data.xlsx
"""
import sys
from pathlib import Path

import fitz  # PyMuPDF — ใช้ detect PDF type เท่านั้น
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
)
from docling.document_converter import PdfFormatOption

OUTPUT_DIR = Path("parsed_output")


def get_output_path(file_path: Path) -> Path:
    """คืน output path สำหรับไฟล์ที่ parse แล้ว
    ถ้าชื่อไฟล์ซ้ำกันจาก subfolder ต่างกัน → prefix ด้วยชื่อ folder แม่
    เพื่อป้องกัน overwrite กัน
    """
    stem = file_path.stem
    parent = file_path.parent.name
    candidate = OUTPUT_DIR / f"{stem}.md"
    # ถ้า parent มีชื่อ (ไม่ใช่ root "." หรือชื่อเดียวกับ OUTPUT_DIR) → ตรวจหา collision
    if parent and parent not in (".", "parsed_output"):
        # ถ้าไฟล์นั้นมีอยู่แล้วแต่เนื้อหาที่จะ conflict (ไม่รู้ว่ามาจาก path เดิมหรือเปล่า)
        # ใช้ parent prefix เพื่อความปลอดภัย ถ้าชื่อ stem ดูทั่วไปเกินไป
        GENERIC_NAMES = {"readme", "index", "default", "main", "document", "doc"}
        if stem.lower() in GENERIC_NAMES:
            candidate = OUTPUT_DIR / f"{parent}__{stem}.md"
    return candidate

SUPPORTED_FORMATS = {
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".html", ".htm", ".md",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
}

# ถ้า avg text ต่อหน้าต่ำกว่านี้ → ถือว่า scanned
SCANNED_THRESHOLD = 100


def detect_pdf_type(path: Path) -> str:
    doc = fitz.open(str(path))
    total_chars = sum(len(page.get_text().strip()) for page in doc)
    avg = total_chars / len(doc) if len(doc) > 0 else 0
    doc.close()
    return "scanned" if avg < SCANNED_THRESHOLD else "text"


def build_converter(file_type: str) -> DocumentConverter:
    """สร้าง converter ตามประเภทไฟล์"""
    if file_type == "scanned":
        # EasyOCR รองรับภาษาไทย + อังกฤษ
        # images_scale=0.7 ลด RAM ~50% — ป้องกัน std::bad_alloc บน 16GB RAM
        ocr_options = EasyOcrOptions(lang=["th", "en"])
        pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            ocr_options=ocr_options,
            images_scale=0.7,
        )
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    else:
        # text-based PDF — ปิด OCR ทั้งหมด ไม่ download model ไม่จำเป็น
        pipeline_options = PdfPipelineOptions(do_ocr=False)
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )


def parse_file(file_path: str):
    path = Path(file_path)

    if not path.exists():
        print(f"[ERROR] ไม่เจอไฟล์: {file_path}")
        sys.exit(1)

    ext = path.suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        print(f"[ERROR] ไม่รองรับ format: {ext}")
        print(f"        รองรับ: {', '.join(sorted(SUPPORTED_FORMATS))}")
        sys.exit(1)

    # --- ตรวจสอบประเภท PDF ---
    file_type = "other"
    if ext == ".pdf":
        file_type = detect_pdf_type(path)

    print(f"ไฟล์   : {path.name}")
    print(f"Format : {ext}")
    if ext == ".pdf":
        print(f"PDF type: {file_type.upper()} ({'ใช้ EasyOCR Thai+EN' if file_type == 'scanned' else 'อ่าน text layer ตรงๆ'})")
    print("-" * 60)

    # --- แปลงเอกสาร ---
    converter = build_converter(file_type)
    result = converter.convert(str(path))
    markdown = result.document.export_to_markdown()

    # --- บันทึกผล ---
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = get_output_path(path)
    out_path.write_text(markdown, encoding="utf-8")

    # --- preview ---
    preview = markdown[:2000]
    print(preview)
    if len(markdown) > 2000:
        print(f"\n... (ตัดทอน — ทั้งหมด {len(markdown):,} ตัวอักษร)")

    print(f"\n[OK] บันทึกที่: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_doc.py <file>")
        print(f"รองรับ: {', '.join(sorted(SUPPORTED_FORMATS))}")
        sys.exit(1)
    parse_file(sys.argv[1])
