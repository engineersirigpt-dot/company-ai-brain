"""
OCR เอกสารกลุ่ม AFII (text layer พังจาก ToUnicode CMap) ด้วย Claude vision แล้ว re-ingest

ขั้นตอนต่อไฟล์: PDF → render ทีละหน้า (150dpi PNG) → Claude ถอดเป็น Markdown ไทย
             → chunk + parent_text + RBAC (reuse ingest.py) → embed BGE-M3
             → ลบ points เก่าของ source นั้น → upsert ชุดใหม่

รันในคอนเทนเนอร์ brain_api (มี torch/transformers/qdrant_client/anthropic + ANTHROPIC_API_KEY):
    docker exec brain_api sh -c 'python /tmp/ocr_reingest.py > /tmp/ocr.log 2>&1'
ต้อง docker cp มาก่อน: ไฟล์นี้, ingest.py, rbac_config.py (ล่าสุด), และ PDF ใน /tmp/ocr_pdfs/
"""
import base64
import os
import sys

sys.path.insert(0, "/app")    # rbac_config.py (ฉบับ default-deny)
sys.path.insert(0, "/tmp")    # ingest.py ที่ cp เข้ามา

import anthropic
import fitz
from qdrant_client import QdrantClient
from qdrant_client.models import (FieldCondition, Filter, FilterSelector,
                                  MatchValue, PointStruct)
from transformers import AutoModel, AutoTokenizer

from ingest import attach_parent_text, chunk_by_headings, embed_chunks
from rbac_config import get_rbac
import policy  # M2: legacy-writer guard

QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
COLLECTION = os.getenv("COLLECTION_NAME", "company_docs")
OCR_MODEL = os.getenv("LLM_MODEL", "claude-opus-4-8")
PDF_DIR = "/tmp/ocr_pdfs"
OUT_DIR = "/tmp/ocr_out"

# ไฟล์เรียบง่าย (s1..s6) → source name เดิมใน Qdrant (ต้องตรงเป๊ะเพื่อลบของเก่า)
FILES = {
    "s1.pdf": "QP-710-01 Rev04 การวางแผนการผลิต (FSC)",
    "s2.pdf": "QP-721-03 Rev00 การขายและการเสนอราคาขายงาน Packaging",
    "s3.pdf": "QP-752-02 Rev00 การเปลี่ยนแปลงกระบวนการผลิต",
    "s4.pdf": "QP-821-03 Rev00 การวัดความพึงพอใจของลูกค้า ตลาดPackaging",
    "s5.pdf": "WI-423-01-02 Rev01 การใช้ตราสัญลักษณ์ FSC บนผลิตภัณฑ์",
    "s6.pdf": "WI-755-05-02 Rev01 FREIGHT และ INSURANCE",
}

OCR_PROMPT = """ถอดความเนื้อหาในภาพเอกสารนี้เป็น Markdown ภาษาไทยให้ครบถ้วนตรงตามต้นฉบับ
- ใช้ ## สำหรับหัวข้อ ตามโครงสร้างเอกสารจริง
- ตารางให้ใช้ markdown table โดยแต่ละแถวครบทุกคอลัมน์
- สะกดภาษาไทยให้ถูกต้อง รวมวรรณยุกต์
- ห้ามอธิบายเพิ่ม ห้ามใส่ code fence ห้ามแปล ตอบเป็นเนื้อหาเอกสารล้วนๆ
- ถ้าหน้านี้เป็นหน้าว่างหรือมีแต่โลโก้ ให้ตอบว่า (หน้าว่าง)"""


def ocr_pdf(llm, path: str) -> str:
    doc = fitz.open(path)
    parts = []
    for i, page in enumerate(doc):
        png = page.get_pixmap(dpi=150).tobytes("png")
        b64 = base64.standard_b64encode(png).decode()
        resp = llm.messages.create(
            model=OCR_MODEL,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png",
                                "data": b64}},
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if text and "(หน้าว่าง)" not in text:
            parts.append(text)
        print(f"  page {i + 1}/{len(doc)} -> {len(text)} chars", flush=True)
    return "\n\n".join(parts)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    llm = anthropic.Anthropic()
    qc = QdrantClient(host=QDRANT_HOST, port=6333, timeout=60)

    # M2: writer นี้เขียน payload ผ่าน get_rbac() ตรง ๆ (ไม่มี schema/version/status) — ห้ามใช้กับ
    # P1 collection มิฉะนั้น point จะหายใต้ filter v1. fail-fast ถ้าเจอ policy-v1 payload
    try:
        _sample, _ = qc.scroll(collection_name=COLLECTION, limit=50,
                               with_payload=True, with_vectors=False)
        policy.assert_legacy_writer_allowed([p.payload for p in _sample], "ocr_reingest.py")
    except RuntimeError:
        raise
    except Exception:
        pass  # collection ยังไม่มี/scroll ไม่ได้ = ไม่มี policy-v1 ให้ปกป้อง

    print("Loading BGE-M3...", flush=True)
    tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3")
    model.eval()

    total_new = 0
    for fname, source in FILES.items():
        path = os.path.join(PDF_DIR, fname)
        if not os.path.exists(path):
            print(f"[SKIP] no file: {fname}", flush=True)
            continue
        print(f"=== {source} ===", flush=True)

        text = ocr_pdf(llm, path)
        if len(text) < 500:
            print(f"[ABORT] OCR ได้เนื้อน้อยผิดปกติ ({len(text)} chars) — ไม่แตะของเดิม",
                  flush=True)
            continue
        out_md = os.path.join(OUT_DIR, f"{source}.md")
        with open(out_md, "w", encoding="utf-8") as f:
            f.write(text)

        chunks = chunk_by_headings(text, source_name=source)
        attach_parent_text(chunks, text)
        chunks = embed_chunks(tok, model, chunks)

        # ลบของเก่า (ขยะ AFII) เฉพาะ source นี้ — มี snapshot 2026-07-27 กันไว้แล้ว
        qc.delete(COLLECTION, points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source))])))

        points = [PointStruct(
            id=str(c["id"]), vector=c["vector"],
            payload={"text": c["text"],
                     "parent_text": c.get("parent_text", c["text"]),
                     "heading": c["heading"], "source": c["source"],
                     **get_rbac(c["source"])},
        ) for c in chunks]
        qc.upsert(COLLECTION, points=points)
        total_new += len(points)
        print(f"  -> replaced with {len(points)} chunks", flush=True)

    info = qc.get_collection(COLLECTION)
    print(f"DONE total_new={total_new} collection_points={info.points_count}",
          flush=True)


if __name__ == "__main__":
    main()
