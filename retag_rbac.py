"""
Retag RBAC — เพิ่ม metadata RBAC ให้ chunks ที่อยู่ใน Qdrant แล้ว
ไม่ต้อง re-embed (แก้แค่ payload)

Usage:
    python retag_rbac.py           # preview ว่าจะ tag อะไร
    python retag_rbac.py --apply   # apply จริง
"""
import sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

from qdrant_client import QdrantClient
from rbac_config import get_collection, get_rbac, COLLECTIONS
import policy  # M2: legacy-writer guard

QDRANT_PATH = "./qdrant_storage"
COLLECTION_NAME = "company_docs"


def scroll_all(client: QdrantClient) -> list:
    """ดึง points ทั้งหมดจาก Qdrant (ไม่ดึง vector — เร็วกว่า)"""
    all_points = []
    offset = None
    while True:
        results, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(results)
        if next_offset is None:
            break
        offset = next_offset
    return all_points


def main(apply: bool):
    client = QdrantClient(path=QDRANT_PATH)
    print("กำลังดึง points จาก Qdrant...")
    points = scroll_all(client)
    print(f"พบ {len(points)} points\n")

    # M2: retag เขียน payload (collection/level/roles) โดยไม่ผ่าน policy resolver/validator —
    # ห้ามใช้กับ P1 collection (จะได้ payload ที่ไม่ผ่าน strict contract). fail-fast ถ้าเจอ policy-v1
    policy.assert_legacy_writer_allowed([p.payload for p in points], "retag_rbac.py")

    # จัดกลุ่ม point ID ตาม collection
    by_collection: dict[str, list] = defaultdict(list)
    for p in points:
        source = p.payload.get("source", "")
        coll = get_collection(source)
        by_collection[coll].append(p.id)

    # สรุปให้เห็นภาพ
    print(f"{'Collection':<15} {'Level':<8} {'Points':>7}  Roles")
    print("-" * 70)
    for coll, ids in sorted(by_collection.items()):
        meta = COLLECTIONS[coll]
        roles = ", ".join(meta["allowed_roles"])
        print(f"{coll:<15} L{meta['confidentiality_level']:<7} {len(ids):>7}  {roles}")
    print(f"\nรวม: {len(points)} points ใน {len(by_collection)} collections")

    if not apply:
        print("\n[DRY RUN] ไม่ได้แก้ Qdrant — รัน `python retag_rbac.py --apply` เพื่อ apply จริง")
        return

    # Apply: set_payload ทีละ collection (efficient)
    print("\nกำลัง tag...")
    for coll, ids in by_collection.items():
        meta = COLLECTIONS[coll]
        payload = {
            "collection_group": coll,
            "confidentiality_level": meta["confidentiality_level"],
            "allowed_roles": meta["allowed_roles"],
        }
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload=payload,
            points=ids,
        )
        print(f"  [{coll}] tagged {len(ids)} points")

    print(f"\n[OK] Tagged {len(points)} points เรียบร้อย")
    print("ลอง python search_rbac.py --role production เพื่อดูผล")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    main(apply)
