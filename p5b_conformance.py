"""
P5b conformance (Codex acceptance A) — ยิง **compiled filter เดียวกับ API** ผ่าน Qdrant `scroll`
(ไม่ใช้ vector similarity/top-k) เทียบผลกับ expect_roles ของ fixture → พิสูจน์ Qdrant filter semantics

    P5B_COLLECTION=... python p5b_conformance.py
exit 0 = model (matches_policy) สอดคล้อง Qdrant จริงทุกจุด; ≠0 = mismatch (report extra/missing)
"""
import argparse
import os
import sys

from qdrant_client import QdrantClient

import policy as P
import p5b_fixtures as FX
from qdrant_filter import to_qdrant_filter


def _filter(role):
    acc = P.EffectiveAccess(P.ServicePrincipal("t", (role,), True, "enforce"), role)
    return to_qdrant_filter(P.compile_retrieval_filter(acc))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("P5B_QDRANT_URL", "http://localhost:6401"))
    ap.add_argument("--collection", default=os.getenv("P5B_COLLECTION", ""))
    args = ap.parse_args()

    client = QdrantClient(url=args.url)
    fixtures = FX.conformance_fixtures()
    fx_ids = {f["id"] for f in fixtures}

    fails = []
    for role in FX.CONFORMANCE_ROLES:
        pts, _ = client.scroll(args.collection, scroll_filter=_filter(role),
                               limit=1000, with_payload=False, with_vectors=False)
        seen = {str(p.id) for p in pts} & fx_ids
        expect = {f["id"] for f in fixtures if role in f["expect_roles"]}
        extra, missing = sorted(seen - expect), sorted(expect - seen)
        status = "ok" if not extra and not missing else "FAIL"
        print(f"  [{status}] role={role:8s} match={len(seen)} expect={len(expect)}"
              + (f" extra(LEAK)={extra} missing={missing}" if extra or missing else ""))
        if extra or missing:
            fails.append(role)

    # แสดง fixture-by-fixture ที่ Qdrant match แต่ละ role (diagnostic)
    print(f"\n[CONFORMANCE] roles={FX.CONFORMANCE_ROLES} fails={len(fails)} "
          f"({'PASS' if not fails else 'FAIL — model ไม่ตรง Qdrant'})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
