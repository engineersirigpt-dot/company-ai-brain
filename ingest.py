"""
Ingestion Pipeline: Markdown → Chunk → Embed (BGE-M3) → Qdrant (local)

Usage:
    python ingest.py <markdown_file>
    python ingest.py parsed_output/Manual_ManagerAE.md
"""
import sys
import re
import hashlib
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from rbac_config import get_rbac

QDRANT_PATH = "./qdrant_storage"
COLLECTION_NAME = "company_docs"
VECTOR_DIM = 1024       # BGE-M3 dense vector size
MAX_CHUNK_CHARS = 2000  # ~500-700 tokens


def make_id(source: str, text: str) -> str:
    """ID จาก hash ของ source+text — ingest ซ้ำไม่ duplicate"""
    h = hashlib.md5(f"{source}:{text}".encode()).hexdigest()
    return h


MAX_PARENT_CHARS = 2000


def _parent_window(full: str, pos: int, length: int, target: int = MAX_PARENT_CHARS) -> str:
    """ตัด context ~target ตัวรอบตำแหน่ง chunk snap ขอบย่อหน้า (\\n\\n)"""
    if len(full) <= target:
        return full.strip()
    end = pos + length
    if length >= target:
        return full[pos:pos + target].strip()
    extra = target - length
    left = max(0, pos - extra // 2)
    right = min(len(full), end + (extra - (pos - left)))
    if right - left < target:
        left = max(0, right - target)
    if right - left < target:
        right = min(len(full), left + target)
    lb = full.rfind("\n\n", 0, left + 1)
    if lb != -1 and pos - lb <= target:
        left = lb + 2
    rb = full.find("\n\n", right)
    if rb != -1 and rb - left <= target:
        right = rb
    return full[left:right].strip()


def attach_parent_text(chunks: list[dict], full_text: str) -> None:
    """เติม parent_text (section context ~2000 ตัว) ให้แต่ละ chunk
    ช่วย chunk สั้นๆ ให้มีบริบทพอตอบ — API build_content จะอ่าน field นี้"""
    for c in chunks:
        t = (c["text"] or "").strip()
        pos = full_text.find(t)
        if pos < 0 and len(t) >= 60:  # probe ภายใน กัน heading/whitespace เพี้ยน
            pos = full_text.find(t[len(t) // 2:len(t) // 2 + 60].strip())
        c["parent_text"] = (
            c["text"][:MAX_PARENT_CHARS] if pos < 0
            else _parent_window(full_text, pos, len(t))
        )


def chunk_by_headings(text: str, source_name: str) -> list[dict]:
    """
    แบ่ง chunk ตามโครงสร้าง heading ของ markdown
    แต่ละ chunk = heading + content ใต้มัน
    ถ้า content ยาวเกิน MAX_CHUNK_CHARS → แบ่งตาม paragraph อีกที
    """
    chunks = []
    # split ที่ heading level 1-3
    sections = re.split(r'(?=^#{1,3} )', text, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # ดึง heading line แรก
        lines = section.splitlines()
        heading = lines[0] if lines[0].startswith('#') else ""
        content = section

        if len(content) <= MAX_CHUNK_CHARS:
            chunks.append({
                "id": make_id(source_name, content),
                "text": content,
                "heading": heading,
                "source": source_name,
            })
        else:
            # section ยาวเกิน → แบ่งตาม paragraph (2 newlines) หรือ table row
            sub_chunks = split_long_section(content, heading, source_name)
            chunks.extend(sub_chunks)

    # ถ้าไม่มี heading เลย (เช่น table-only doc) → chunk ทั้งหมดเป็นก้อนเดียว
    if not chunks:
        chunks.append({
            "id": make_id(source_name, text[:MAX_CHUNK_CHARS]),
            "text": text[:MAX_CHUNK_CHARS],
            "heading": "",
            "source": source_name,
        })

    return chunks


def split_long_section(content: str, heading: str, source_name: str) -> list[dict]:
    """แบ่ง section ยาวๆ ตาม paragraph หรือ table block"""
    chunks = []
    # แบ่งตาม blank line
    paragraphs = re.split(r'\n{2,}', content)
    buffer = heading + "\n" if heading else ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(buffer) + len(para) + 2 <= MAX_CHUNK_CHARS:
            buffer += para + "\n\n"
        else:
            if buffer.strip():
                chunks.append({
                    "id": make_id(source_name, buffer.strip()),
                    "text": buffer.strip(),
                    "heading": heading,
                    "source": source_name,
                })
            # paragraph เดี่ยวยาวมาก → ตัดตรงๆ
            if len(para) > MAX_CHUNK_CHARS:
                for i in range(0, len(para), MAX_CHUNK_CHARS):
                    t = (heading + "\n" if heading else "") + para[i:i+MAX_CHUNK_CHARS]
                    chunks.append({
                        "id": make_id(source_name, t),
                        "text": t,
                        "heading": heading,
                        "source": source_name,
                    })
                buffer = ""
            else:
                buffer = (heading + "\n" if heading else "") + para + "\n\n"

    if buffer.strip():
        chunks.append({
            "id": make_id(source_name, buffer.strip()),
            "text": buffer.strip(),
            "heading": heading,
            "source": source_name,
        })

    return chunks


def embed_chunks(tokenizer, model, chunks: list[dict], batch_size: int = 8) -> list[dict]:
    """Embed ด้วย BGE-M3 dense vector (CLS token + L2 normalize)"""
    texts = [c["text"] for c in chunks]
    print(f"Embedding {len(texts)} chunks...")
    all_vecs = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        inputs = tokenizer(
            batch,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        )
        with torch.no_grad():
            out = model(**inputs)
        # BGE-M3 dense: CLS token → normalize
        vecs = out.last_hidden_state[:, 0, :]
        vecs = F.normalize(vecs, p=2, dim=1)
        all_vecs.extend(vecs.tolist())
        print(f"  batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1}")

    for chunk, vec in zip(chunks, all_vecs):
        chunk["vector"] = vec
    return chunks


def store_in_qdrant(chunks: list[dict]):
    """เก็บ chunks + vectors ลง Qdrant local storage"""
    client = QdrantClient(path=QDRANT_PATH)

    # สร้าง collection ถ้ายังไม่มี
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"Created collection: {COLLECTION_NAME}")

    points = [
        PointStruct(
            id=str(chunk["id"]),
            vector=chunk["vector"],
            payload={
                "text": chunk["text"],
                "parent_text": chunk.get("parent_text", chunk["text"]),
                "heading": chunk["heading"],
                "source": chunk["source"],
                **get_rbac(chunk["source"]),
            },
        )
        for chunk in chunks
    ]

    client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"Stored {len(points)} points in Qdrant (collection: {COLLECTION_NAME})")

    info = client.get_collection(COLLECTION_NAME)
    print(f"Total vectors in collection: {info.points_count}")


def ingest(md_path: str):
    path = Path(md_path)
    if not path.exists():
        print(f"[ERROR] ไม่เจอไฟล์: {md_path}")
        sys.exit(1)

    print(f"Reading: {path.name}")
    text = path.read_text(encoding="utf-8")

    # 1. Chunk (+ parent context สำหรับ retrieval)
    chunks = chunk_by_headings(text, source_name=path.stem)
    attach_parent_text(chunks, text)
    print(f"Chunks: {len(chunks)}")
    for i, c in enumerate(chunks[:3]):
        preview = c["text"][:80].replace("\n", " ")
        print(f"  [{i}] {preview!r}...")

    # 2. Embed
    print("\nLoading BGE-M3 model (first run will download ~570MB)...")
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3")
    model.eval()
    chunks = embed_chunks(tokenizer, model, chunks)

    # 3. Store
    print("\nStoring in Qdrant...")
    store_in_qdrant(chunks)
    print("\n[OK] Done!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest.py <markdown_file>")
        sys.exit(1)
    ingest(sys.argv[1])
