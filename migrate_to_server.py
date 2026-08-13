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


def migrate(server_url: str, dry_run: bool, replace: bool = False):
    print(f"Source : local  ({LOCAL_PATH})")
    print(f"Target : {server_url}")
    print(f"Mode   : {'DRY RUN' if dry_run else ('APPLY --replace' if replace else 'APPLY (additive)')}")
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
    # --replace: ลบ collection เดิมก่อน migrate = "clean full replace" (server == local เป๊ะ)
    if replace and COLLECTION_NAME in existing:
        server.delete_collection(COLLECTION_NAME)
        existing.remove(COLLECTION_NAME)
        print(f"[--replace] ลบ collection '{COLLECTION_NAME}' เดิมบน server (clean migration)")
    if COLLECTION_NAME not in existing:
        server.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"สร้าง collection '{COLLECTION_NAME}' บน server แล้ว")
    else:
        n = server.get_collection(COLLECTION_NAME).points_count
        print(f"Server มี collection แล้ว — {n} vectors อยู่แล้ว")
        if n > 0:
            # ⚠️ security: upsert เป็น additive — point ที่ถูกลบ/แคบ ACL ที่ local จะ "ไม่ถูกลบ" ออกจาก server
            # → เอกสารเก่า/ACL กว้างอาจค้างบน server ให้ค้นเจอ (stale = leak). clean migrate ต้องใช้ --replace
            print("[WARN] migrate แบบ additive (upsert) — จะไม่ลบ point เก่าที่หายไปจาก local\n"
                  "       เอกสารที่ลบ/แคบ ACL ที่ local อาจค้างบน server ด้วย ACL เก่า (เสี่ยง leak)\n"
                  "       → ใช้ `--replace` เพื่อ migrate สะอาด (ลบ collection เดิมก่อน)")

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
    parser.add_argument("--replace", action="store_true",
                        help="ลบ collection เดิมบน server ก่อน migrate (clean — กัน stale ACL เก่าค้าง)")
    args = parser.parse_args()
    migrate(args.server, args.dry_run, args.replace)
