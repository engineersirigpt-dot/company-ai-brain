"""
P5b seeder — seed conformance + canary fixtures เข้า **isolated test collection** (Codex P5B-B1/A/C/M1)
interlock: ปฏิเสธ production name / ชื่อไม่ใช่ p5b / target ไม่ว่างที่ **run-marker จริงไม่ตรง**
(ไม่มี --recreate ที่ลบ collection อื่นได้ — ต้องใช้ run_id/collection ใหม่ต่อ run)

    P5B_RUN_ID=<unique>  P5B_COLLECTION=company_docs_p5b_<run_id>  python p5b_seed.py
"""
import argparse
import os
import sys

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

import policy as P
import p5b_fixtures as FX


def read_marker(client, collection: str):
    """อ่าน run-marker จริงจาก collection (None ถ้าไม่มี/อ่านไม่ได้)"""
    try:
        pts = client.retrieve(collection, ids=[FX.MARKER_ID], with_payload=True)
        return pts[0].payload.get(FX.MARKER_KEY) if pts else None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("P5B_QDRANT_URL", "http://localhost:6401"))
    ap.add_argument("--collection", default=os.getenv("P5B_COLLECTION", ""))
    ap.add_argument("--run-id", default=os.getenv("P5B_RUN_ID", ""))
    args = ap.parse_args()
    if not args.collection or not args.run_id:
        print("ERROR: ต้องตั้ง P5B_COLLECTION และ P5B_RUN_ID", file=sys.stderr)
        sys.exit(2)

    client = QdrantClient(url=args.url)
    existing = [c.name for c in client.get_collections().collections]
    count = client.count(args.collection).count if args.collection in existing else 0
    stored = read_marker(client, args.collection) if count else None

    # M1: guard ด้วย marker จริงที่อ่านกลับมาเทียบ (ไม่ใช่ boolean ที่ caller เปิดเอง)
    P.assert_test_collection(args.collection, count, stored, args.run_id)

    if args.collection not in existing:
        client.create_collection(
            args.collection,
            vectors_config=VectorParams(size=FX.VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"created collection {args.collection}")

    manifest = FX.load_manifest()
    conf = FX.conformance_fixtures()
    canary = FX.canary_fixtures(manifest)
    m = FX.marker_point(args.run_id)
    points = [PointStruct(id=m["id"], vector=m["vector"], payload=m["payload"])]
    points += [PointStruct(id=f["id"], vector=FX.det_vector(f["id"]), payload=f["payload"])
               for f in conf + canary]
    client.upsert(args.collection, points=points)
    print(f"[SEED] {len(points)} points -> {args.collection} (marker + conformance {len(conf)} "
          f"+ canary {len(canary)}) run_id={args.run_id}")


if __name__ == "__main__":
    main()
