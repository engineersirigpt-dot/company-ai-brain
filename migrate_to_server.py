"""
ย้ายข้อมูลจาก Qdrant local (Windows) → Qdrant server (Ubuntu)
รันบน Windows เครื่องนี้ก่อน deploy

Usage:
    python migrate_to_server.py --server http://192.168.x.x:6333
    python migrate_to_server.py --server http://192.168.x.x:6333 --dry-run
"""
import sys
import argparse
sys.stdout.reconfigure(encoding="utf-8")

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import policy  # M2: กัน malformed policy-v1 หลุดเข้าปลายทาง

LOCAL_PATH = "./qdrant_storage"
COLLECTION_NAME = "company_docs"
VECTOR_DIM = 1024
BATCH_SIZE = 100


def migrate(server_url: str, dry_run: bool):
    print(f"Source : local  ({LOCAL_PATH})")
    print(f"Target : {server_url}")
    print(f"Mode   : {'DRY RUN' if dry_run else 'APPLY'}")
    print()

    local = QdrantClient(path=LOCAL_PATH)

    # ตรวจสอบ source
    try:
        info = local.get_collection(COLLECTION_NAME)
        total = info.points_count
        print(f"Source collection: {COLLECTION_NAME} — {total} vectors")
    except Exception as e:
        print(f"[ERROR] ไม่สามารถเข้า local Qdrant: {e}")
        sys.exit(1)

    if dry_run:
        print("\n[DRY RUN] ไม่ได้ย้ายจริง")
        return

    # เชื่อมต่อ server
    server = QdrantClient(url=server_url, timeout=30)
    print(f"เชื่อมต่อ server สำเร็จ")

    # สร้าง collection บน server ถ้ายังไม่มี
    existing = [c.name for c in server.get_collections().collections]
    if COLLECTION_NAME not in existing:
        server.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"สร้าง collection '{COLLECTION_NAME}' บน server แล้ว")
    else:
        server_info = server.get_collection(COLLECTION_NAME)
        print(f"Server มี collection แล้ว — {server_info.points_count} vectors อยู่แล้ว")

    # Scroll + upload ทีละ batch
    print(f"\nกำลังย้ายข้อมูล (batch size = {BATCH_SIZE})...")
    offset = None
    uploaded = 0

    while True:
        results, next_offset = local.scroll(
            collection_name=COLLECTION_NAME,
            limit=BATCH_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=True,  # ต้องดึง vector ด้วยเพื่อย้าย
        )

        if not results:
            break

        # M2: กัน malformed policy-v1 payload หลุดเข้า collection ปลายทาง (legacy payload ผ่านปกติ)
        bad = [(p.id, policy.validate_stored_payload(p.payload)[1])
               for p in results if not policy.validate_stored_payload(p.payload)[0]]
        if bad:
            print(f"[POLICY-GUARD] migrate_to_server: policy-v1 payload malformed {len(bad)} จุด — abort")
            for pid, reason in bad[:10]:
                print(f"  - {pid}: {reason}")
            sys.exit(1)

        points = [
            PointStruct(
                id=p.id,
                vector=p.vector,
                payload=p.payload,
            )
            for p in results
        ]

        server.upsert(collection_name=COLLECTION_NAME, points=points)
        uploaded += len(points)
        pct = uploaded / total * 100
        print(f"  {uploaded}/{total} ({pct:.0f}%)")

        if next_offset is None:
            break
        offset = next_offset

    print(f"\n[OK] ย้ายเสร็จ — {uploaded} vectors อยู่บน server แล้ว")
    print(f"ทดสอบ: curl {server_url}/collections/{COLLECTION_NAME}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True,
                        help="URL ของ Qdrant server เช่น http://192.168.1.10:6333")
    parser.add_argument("--dry-run", action="store_true",
                        help="ดูว่าจะทำอะไรโดยไม่ย้ายจริง")
    args = parser.parse_args()
    migrate(args.server, args.dry_run)
