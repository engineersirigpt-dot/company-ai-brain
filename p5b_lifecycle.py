"""
P5b writer lifecycle (Codex acceptance B) — เรียก **store_in_qdrant จริง** บน test collection
(ไม่ใช่จำลอง list) พิสูจน์ 2 regression ของ B1:
  1) ACTIVE → QUARANTINED : role เดิมเห็นศูนย์ point
  2) broad ACL → narrow ACL: revoked role เห็นศูนย์, retained role ยังเห็น generation ใหม่
single-writer serial (concurrency/atomicity = deploy gate)

    P5B_COLLECTION=... python p5b_lifecycle.py
"""
import argparse
import os
import sys
import tempfile

from qdrant_client import QdrantClient

import policy as P
import p5b_fixtures as FX
from qdrant_filter import to_qdrant_filter
from ingest import store_in_qdrant   # torch-lazy → import ได้ด้วย qdrant_client อย่างเดียว
from p5b_seed import read_marker


def _visible(client, coll, role) -> set:
    acc = P.EffectiveAccess(P.ServicePrincipal("t", (role,), True, "enforce"), role)
    pts, _ = client.scroll(coll, scroll_filter=to_qdrant_filter(P.compile_retrieval_filter(acc)),
                           limit=1000, with_payload=False, with_vectors=False)
    return {str(p.id) for p in pts}


def _lookup(coll, roles):
    return lambda s: {"collection_group": coll, "confidentiality_level": 3, "allowed_roles": roles}


def _chunk(source, cid):
    return [{"source": source, "id": cid, "text": f"lifecycle {source}",
             "heading": source, "vector": FX.det_vector(cid)}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("P5B_QDRANT_URL", "http://localhost:6401"))
    ap.add_argument("--collection", default=os.getenv("P5B_COLLECTION", ""))
    ap.add_argument("--run-id", default=os.getenv("P5B_RUN_ID", ""))
    args = ap.parse_args()

    client = QdrantClient(url=args.url)
    count = client.count(args.collection).count
    # M1: อ่าน marker จริง เทียบ run_id (lifecycle ห้ามส่ง True เอง)
    P.assert_test_collection(args.collection, count, read_marker(client, args.collection), args.run_id)
    mani = tempfile.mktemp(suffix=".jsonl")
    fails = []

    def store(chunks, lookup):
        store_in_qdrant(chunks, client=client, collection_name=args.collection,
                        rbac_lookup=lookup, manifest_path=mani)

    # regression 1: ACTIVE -> QUARANTINED
    id1 = FX.uid("lc1")
    ch1 = _chunk("P5B-LC-1.pdf", id1)
    store(ch1, _lookup("SALES", ["sales", "admin"]))
    if id1 not in _visible(client, args.collection, "sales"):
        fails.append("gen1: sales ควรเห็น point แต่ไม่เห็น")
    store(ch1, _lookup("SALES", "qc"))   # scalar allowed_roles -> resolver quarantine -> ไม่ upsert
    if id1 in _visible(client, args.collection, "sales"):
        fails.append("ACTIVE->QUARANTINED: point เก่ายัง visible (revoke ล้มเหลว)")

    # regression 2: broad -> narrow
    id2 = FX.uid("lc2")
    ch2 = _chunk("P5B-LC-2.pdf", id2)
    store(ch2, _lookup("PRODUCTION", ["production", "qc", "admin"]))
    if id2 not in _visible(client, args.collection, "qc"):
        fails.append("gen1: qc ควรเห็น (broad ACL)")
    store(ch2, _lookup("PRODUCTION", ["production", "admin"]))   # revoke qc
    if id2 in _visible(client, args.collection, "qc"):
        fails.append("broad->narrow: qc ที่ถูกถอนยัง visible")
    if id2 not in _visible(client, args.collection, "production"):
        fails.append("broad->narrow: production ที่ยังมีสิทธิ์ หายไป (availability)")

    for f in fails:
        print(f"  FAIL {f}")
    print(f"\n[LIFECYCLE] regressions={2} fails={len(fails)} "
          f"({'PASS' if not fails else 'FAIL'})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
