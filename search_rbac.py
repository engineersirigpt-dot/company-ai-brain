"""
Search with RBAC — ค้นหาแบบกำหนด role
แสดงให้เห็นว่า role ต่างกัน เห็นเอกสารต่างกัน

Usage:
    python search_rbac.py "คำถาม" --role production
    python search_rbac.py "วิธีเสนอราคา" --role sales
    python search_rbac.py "วิธีเสนอราคา" --role production
    python search_rbac.py "วิธีเสนอราคา" --role admin
"""
import sys
import argparse
sys.stdout.reconfigure(encoding="utf-8")

import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient

import policy  # ใช้ compiler/role set เดียวกับ API (กัน filter drift)
from qdrant_filter import to_qdrant_filter  # canonical adapter (fail-closed guard เดียวกับ API)

QDRANT_PATH = "./qdrant_storage"
COLLECTION_NAME = "company_docs"
TOP_K = 3

VALID_ROLES = sorted(policy.KNOWN_ROLES)  # จาก policy จุดเดียว (ไม่ hardcode/drift)


def _rbac_filter(role: str):
    """filter เดียวกับ API เป๊ะ — compile_retrieval_filter (4 เงื่อนไข: role + policy_status/version/schema)
    ไม่ใช่แค่ allowed_roles (กัน CLI โชว์ point ที่ API กรองทิ้ง = ภาพสิทธิ์ผิด)"""
    access = policy.EffectiveAccess(
        policy.ServicePrincipal("search-rbac-cli", (role,), True, "enforce"), role)
    return to_qdrant_filter(policy.compile_retrieval_filter(access))


def embed(tokenizer, model, text: str) -> list[float]:
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs)
    vec = out.last_hidden_state[:, 0, :]
    vec = F.normalize(vec, p=2, dim=1)
    return vec[0].tolist()


def search(query: str, role: str, top_k: int = TOP_K):
    print(f"\nQuery : {query}")
    print(f"Role  : {role}")
    print("=" * 60)

    # Load model
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", local_files_only=True)
    model = AutoModel.from_pretrained("BAAI/bge-m3", local_files_only=True)
    model.eval()

    vec = embed(tokenizer, model, query)
    client = QdrantClient(path=QDRANT_PATH)

    # Filter: canonical (เดียวกับ API — role + policy_status/version/schema)
    rbac_filter = _rbac_filter(role)

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vec,
        query_filter=rbac_filter,
        limit=top_k,
        with_payload=True,
    ).points

    if not results:
        print("ไม่พบเอกสารที่ role นี้มีสิทธิ์เข้าถึง")
        return

    for i, r in enumerate(results, 1):
        p = r.payload
        coll = p.get("collection_group", "?")
        level = p.get("confidentiality_level", "?")
        source = p.get("source", "?")
        score = r.score
        preview = p.get("text", "")[:120].replace("\n", " ")

        print(f"\n#{i}  Score: {score:.4f}  [{coll} / L{level}]")
        print(f"    Source: {source}")
        print(f"    {preview}...")

    # แสดงเพิ่มเติม: ถ้าค้นแบบไม่มี filter จะได้อะไร
    all_results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vec,
        limit=top_k,
        with_payload=True,
    ).points

    blocked = [
        r for r in all_results
        if role not in r.payload.get("allowed_roles", [])
    ]
    if blocked:
        print(f"\n[RBAC] ซ่อน {len(blocked)} ผลลัพธ์ที่ role '{role}' ไม่มีสิทธิ์:")
        for r in blocked:
            coll = r.payload.get("collection_group", "?")
            src = r.payload.get("source", "?")[:60]
            print(f"  ✗ [{coll}] {src}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="วิธีเสนอราคาขาย")
    parser.add_argument("--role", default="production", choices=VALID_ROLES)
    parser.add_argument("--top", type=int, default=TOP_K)
    args = parser.parse_args()

    search(args.query, args.role, args.top)


if __name__ == "__main__":
    main()
