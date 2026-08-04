"""
P5b default-deny acceptance (Codex G1) — ปิด acceptance C ตามข้อความเดิมแบบตรงตัว ผ่าน API /search:
  1) UNCLASSIFIED: seed ผ่าน **resolver จริง** (unknown source → get_rbac → ACTIVE admin-only,
     ไม่เขียน payload admin-only ด้วยมือ) แล้ว probe ทุก known role: admin พบ exact point, อีก 10 ไม่พบ
  2) missing-ACL / stale-schema / quarantine: ทุก role **รวม admin** ต้องไม่พบ
suite fail non-zero เมื่อ probe ใดขาด, transport ไม่ SUCCESS (DENIED/ERROR), หรือผลผิด expected

    P5B_COLLECTION=... P5B_RUN_ID=... KB_EVAL_KEYS=... python p5b_default_deny.py --api http://localhost:8402
"""
import argparse
import json
import os
import sys
import tempfile

from qdrant_client import QdrantClient

import policy as P
import eval_contract as ec
import p5b_fixtures as FX
from rbac_config import get_rbac
from ingest import store_in_qdrant
from ask_eval import http_call
from p5b_seed import read_marker

UNKNOWN_SOURCE = "p5b-unknown-source-xyz.pdf"   # get_rbac → UNCLASSIFIED (default-deny) admin-only
UNCL_ID = FX.uid("unclassified-defdeny")
UNCL_TOKEN = "UNCLZ-DEFDENY-TOKEN"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant-url", default=os.getenv("P5B_QDRANT_URL", "http://localhost:6401"))
    ap.add_argument("--api", default=os.getenv("P5B_API", "http://localhost:8402"))
    ap.add_argument("--collection", default=os.getenv("P5B_COLLECTION", ""))
    ap.add_argument("--run-id", default=os.getenv("P5B_RUN_ID", ""))
    args = ap.parse_args()
    keys = json.loads(os.getenv("KB_EVAL_KEYS", "{}"))
    if not args.collection or not args.run_id or not keys:
        print("ERROR: ต้องตั้ง P5B_COLLECTION, P5B_RUN_ID, KB_EVAL_KEYS", file=sys.stderr)
        sys.exit(2)

    client = QdrantClient(url=args.qdrant_url)
    count = client.count(args.collection).count
    P.assert_test_collection(args.collection, count, read_marker(client, args.collection), args.run_id)

    # 1) seed UNCLASSIFIED ผ่าน resolver จริง (พิสูจน์ default-deny mapping end-to-end)
    pol = P.resolve_document_policy({"source": UNKNOWN_SOURCE}, get_rbac)
    assert P.is_active(pol) and pol.collection_group == "UNCLASSIFIED" \
        and tuple(pol.allowed_roles) == ("admin",), f"resolver ไม่ได้ให้ ACTIVE admin-only: {pol}"
    chunk = [{"source": UNKNOWN_SOURCE, "id": UNCL_ID, "heading": "defdeny",
              "text": f"unclassified default deny probe {UNCL_TOKEN}", "vector": FX.det_vector(UNCL_ID)}]
    store_in_qdrant(chunk, client=client, collection_name=args.collection,
                    rbac_lookup=get_rbac, manifest_path=tempfile.mktemp(suffix=".jsonl"))

    conf = {f["name"]: f["id"] for f in FX.conformance_fixtures()}
    deny_targets = {"missing-ACL": conf["missing-roles"], "stale-schema": conf["stale-schema"],
                    "quarantine": conf["quarantined"]}
    roles = sorted(keys)
    fails = []

    def found(role, target_id, query):
        rec = http_call(args.api, "/search", {"query": query, "role": role, "top_k": 10}, keys[role])
        if rec["transport"] != ec.SUCCESS:          # DENIED/ERROR แทน CLEAN = fail (ไม่ใช่ 'ไม่พบ')
            return None
        return target_id in [str(p.get("point_id", "")) for p in rec["points"]]

    # UNCLASSIFIED → admin พบ, อีก 10 ไม่พบ
    print("== UNCLASSIFIED (admin-only, resolver-driven) ==")
    for role in roles:
        f = found(role, UNCL_ID, f"unclassified default deny {UNCL_TOKEN}")
        want = (role == "admin")
        mark = "ok" if f is want else "FAIL"
        if f is None:
            fails.append(f"UNCLASSIFIED role={role}: transport ไม่ SUCCESS")
            mark = "FAIL(transport)"
        elif f != want:
            fails.append(f"UNCLASSIFIED role={role}: found={f} want={want}")
        print(f"  [{mark}] role={role:10s} found={f} want={want}")

    # missing/stale/quarantine → ไม่มีใคร (รวม admin) พบ
    print("== missing-ACL / stale-schema / quarantine (ไม่มีใครพบแม้ admin) ==")
    for name, tid in deny_targets.items():
        leaked = []
        for role in roles:
            f = found(role, tid, f"default deny {name}")
            if f is None:
                fails.append(f"{name} role={role}: transport ไม่ SUCCESS")
            elif f:
                leaked.append(role)
        if leaked:
            fails.append(f"{name}: พบโดย {leaked} (LEAK)")
        print(f"  [{'FAIL' if leaked else 'ok'}] {name:14s} leaked={leaked}")

    print(f"\n[DEFAULT-DENY] fails={len(fails)} ({'PASS' if not fails else 'FAIL'})")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
