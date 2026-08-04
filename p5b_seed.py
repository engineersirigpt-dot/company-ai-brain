"""
P5b seeder — seed conformance + canary fixtures เข้า **isolated test collection** (Codex P5B-B1/A/C)
บังคับ interlock: ปฏิเสธ production name / ชื่อไม่ใช่ p5b / target ไม่ว่างโดยไม่ตั้งใจ

    P5B_COLLECTION=company_docs_p5b_<run_id> python p5b_seed.py --recreate
"""
import argparse
import os
import sys

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

import policy as P
import p5b_fixtures as FX


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("P5B_QDRANT_URL", "http://localhost:6401"))
    ap.add_argument("--collection", default=os.getenv("P5B_COLLECTION", ""))
    ap.add_argument("--recreate", action="store_true", help="ลบ+สร้างใหม่ (เฉพาะ collection p5b)")
    ap.add_argument("--allow-nonempty", action="store_true")
    args = ap.parse_args()
    if not args.collection:
        print("ERROR: ต้องตั้ง --collection หรือ env P5B_COLLECTION", file=sys.stderr)
        sys.exit(2)

    client = QdrantClient(url=args.url)
    existing = [c.name for c in client.get_collections().collections]
    count = client.count(args.collection).count if args.collection in existing else 0

    # P5B-B1 interlock: fail-closed ต่อ production/ชื่อผิด/target ไม่ว่าง
    P.assert_test_collection(args.collection, count, run_marker_ok=args.allow_nonempty or args.recreate)

    if args.recreate and args.collection in existing:
        client.delete_collection(args.collection)
        existing.remove(args.collection)
    if args.collection not in existing:
        client.create_collection(
            args.collection,
            vectors_config=VectorParams(size=FX.VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"created collection {args.collection}")

    manifest = FX.load_manifest()
    conf = FX.conformance_fixtures()
    canary = FX.canary_fixtures(manifest)
    points = [PointStruct(id=f["id"], vector=FX.det_vector(f["id"]), payload=f["payload"])
              for f in conf + canary]
    client.upsert(args.collection, points=points)
    print(f"[SEED] {len(points)} points -> {args.collection} "
          f"(conformance {len(conf)} + canary {len(canary)})")


if __name__ == "__main__":
    main()
