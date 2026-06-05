# Company AI Brain — PoC Phase 1

Enterprise Knowledge System — ระบบ AI ตัวกลางสำหรับองค์กร

## วิธีติดตั้ง

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## วิธีใช้งาน

### 1. แปลงเอกสาร → Markdown
```bash
python parse_doc.py <file.pdf>
python parse_doc.py <file.xlsx>
```
ผลลัพธ์เก็บที่ `parsed_output/`

### 2. Ingest ทีละไฟล์
```bash
python ingest.py parsed_output/<file>.md
```

### 3. Ingest ทั้ง folder
```bash
python batch_ingest.py <folder>
```

### 4. ค้นหา
```bash
python search.py "คำถามของคุณ"
```

### 5. วัดคุณภาพ
```bash
python eval.py
python eval.py eval_set.json
```

## รองรับไฟล์
PDF (text-based, scanned), XLSX, DOCX, PPTX, HTML, Images

> **หมายเหตุ:** PDF ที่ export จาก PowerPoint (slide PDF) ไม่รองรับ เนื่องจาก memory ไม่เพียงพอขณะ OCR — ใช้ไฟล์ .pptx แทน
