"""
Search Pipeline: Query → Embed → Qdrant search → แสดงผล

Usage:
    python search.py "คำถาม" --role production   ← แนะนำ (ใช้ RBAC filter)
    python search.py "คำถาม" --role admin
    python search.py "คำถาม"                     ← admin mode (เห็นทุก doc)

Roles: admin, management, production, prepress, qc, engineering,
       sales, purchasing, logistics, hr, it
"""
import sys
import argparse
sys.stdout.reconfigure(encoding="utf-8")

import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

QDRANT_PATH = "./qdrant_storage"
COLLECTION_NAME = "company_docs"
TOP_K = 3

VALID_ROLES = [
    "admin", "management", "production", "prepress", "qc",
    "engineering", "sales", "purchasing", "logistics", "hr", "it",
]


def embed_query(tokenizer, model, text: str) -> list[float]:
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs)
    vec = out.last_hidden_state[:, 0, :]
    vec = F.normalize(vec, p=2, dim=1)
    return vec[0].tolist()


def search(query: str, role: str | None = None):
    print(f"Query: {query!r}")
    if role:
        print(f"Role : {role}")
    else:
        print("Role : [ไม่ระบุ — เห็นทุก document, ใช้สำหรับ admin/debug เท่านั้น]")
    print()

    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", local_files_only=True)
    model = AutoModel.from_pretrained("BAAI/bge-m3", local_files_only=True)
    model.eval()

    vec = embed_query(tokenizer, model, query)
    client = QdrantClient(path=QDRANT_PATH)

    rbac_filter = None
    if role and role != "admin":
        rbac_filter = Filter(
            must=[FieldCondition(key="allowed_roles", match=MatchAny(any=[role]))]
        )

    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vec,
        query_filter=rbac_filter,
        limit=TOP_K,
        with_payload=True,
    )
    results = response.points

    print(f"Top {TOP_K} results:")
    print("=" * 60)
    for i, r in enumerate(results, 1):
        coll = r.payload.get("collection_group", "?")
        level = r.payload.get("confidentiality_level", "?")
        source = r.payload.get("source", "")
        text_preview = r.payload.get("text", "")[:300].replace("\n", " ")
        print(f"[{i}] score={r.score:.4f} | [{coll}/L{level}] {source}")
        print(f"    {text_preview}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+")
    parser.add_argument("--role", choices=VALID_ROLES, default=None,
                        help="role ของผู้ใช้ (ถ้าไม่ระบุ = admin mode เห็นทุก doc)")
    args = parser.parse_args()
    search(" ".join(args.query), args.role)
