"""
P5b fixtures (pure, ไม่พึ่ง qdrant) — นิยาม synthetic points สำหรับ conformance + canary
ใช้ร่วมกันโดย p5b_seed.py / p5b_conformance.py และ pre-verify ได้ offline (test_p5b_fixtures.py)

- conformance_fixtures(): จุดที่รู้ผลคาดหวังต่อ role (list/scalar/null/missing/unknown/stale/
  type-mismatch/quarantine) — พิสูจน์ Qdrant filter semantics ด้วย scroll (Codex acceptance A)
- canary_fixtures(manifest): synthetic canary ตาม permission_manifest — ACTIVE + allowed_roles =
  authorized_roles (สำหรับ /search permission gate, acceptance C)
"""
from __future__ import annotations
import hashlib
import json
import uuid

import policy as P

VECTOR_DIM = 1024
_BASE = {"acl_schema_version": 1, "policy_version": P.POLICY_VERSION, "policy_status": P.ACTIVE}


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "kb-p5b." + name))


# run-marker point (Codex M1) — เก็บ run_id จริงใน collection เพื่ออ่านกลับมาเทียบก่อน mutate
# payload ไม่มี policy v1 field ที่ match filter (policy_status=MARKER ≠ ACTIVE) → inert ต่อทุก probe
MARKER_ID = uid("run-marker")
MARKER_KEY = "_p5b_run_marker"


def marker_point(run_id: str) -> dict:
    return {"id": MARKER_ID, "vector": det_vector("run-marker"),
            "payload": {MARKER_KEY: run_id, "policy_status": "MARKER"}}


def det_vector(seed: str, dim: int = VECTOR_DIM) -> list:
    """vector deterministic (ไม่ใช้ random) — เนื้อหาไม่สำคัญต่อ filter/scroll; ต้อง non-zero สำหรับ cosine"""
    h = hashlib.sha256(seed.encode()).digest()
    vals = [((h[i % len(h)] / 255.0) - 0.5) or 0.01 for i in range(dim)]
    norm = sum(v * v for v in vals) ** 0.5
    return [v / norm for v in vals]


def conformance_fixtures() -> list:
    """
    แต่ละจุด: {id, name, payload, expect_roles} — expect_roles = role ที่ **Qdrant filter v1 ต้อง match**
    (อ้าง Qdrant Match Any/type matching): scalar keyword match ได้, null/missing/type-mismatch ไม่ match
    """
    def pt(name, payload, expect_roles, note=""):
        return {"id": uid(name), "name": name, "payload": payload,
                "expect_roles": set(expect_roles), "note": note}
    return [
        pt("list-qc", {**_BASE, "collection_group": "RECALL", "confidentiality_level": 3,
                       "allowed_roles": ["qc", "admin"]}, {"qc", "admin"}, "list ACTIVE ปกติ"),
        pt("scalar-qc", {**_BASE, "collection_group": "RECALL", "confidentiality_level": 3,
                         "allowed_roles": "qc"}, {"qc"}, "scalar keyword — Qdrant match แต่ store-integrity violation"),
        pt("null-roles", {**_BASE, "collection_group": "RECALL", "confidentiality_level": 3,
                          "allowed_roles": None}, set(), "null → ต้อง IsNull ไม่ match"),
        pt("missing-roles", {**_BASE, "collection_group": "RECALL", "confidentiality_level": 3},
           set(), "ไม่มี allowed_roles → ไม่มีใคร match"),
        pt("unknown-role", {**_BASE, "collection_group": "RECALL", "confidentiality_level": 3,
                            "allowed_roles": ["wizard"]}, set(), "role ไม่รู้จัก → ไม่มี known role match"),
        pt("stale-schema", {**_BASE, "acl_schema_version": 0, "collection_group": "RECALL",
                            "confidentiality_level": 3, "allowed_roles": ["qc", "admin"]}, set(), "stale schema → ไม่ match"),
        pt("schema-true", {**_BASE, "acl_schema_version": True, "collection_group": "RECALL",
                           "confidentiality_level": 3, "allowed_roles": ["qc", "admin"]}, set(), "bool ≠ int → ไม่ match"),
        pt("schema-float", {**_BASE, "acl_schema_version": 1.0, "collection_group": "RECALL",
                            "confidentiality_level": 3, "allowed_roles": ["qc", "admin"]}, set(), "float ≠ int → ไม่ match"),
        pt("quarantined", {**_BASE, "policy_status": "QUARANTINED", "collection_group": "RECALL",
                           "confidentiality_level": 3, "allowed_roles": ["qc", "admin", "management"]},
           set(), "QUARANTINED → ไม่ match แม้ admin"),
    ]


# role set ที่ conformance จะยิงทดสอบ (ครอบ authorized/denied ของ fixtures)
CONFORMANCE_ROLES = ["qc", "admin", "sales"]


def canary_fixtures(manifest: dict) -> list:
    """synthetic canary ตาม permission_manifest — ACTIVE, allowed_roles = authorized_roles"""
    out = []
    for c in manifest["canaries"]:
        pol = P.DocumentPolicy(P.ACL_SCHEMA_VERSION, P.POLICY_VERSION, P.ACTIVE,
                               c["collection"], 3, tuple(c["authorized_roles"]))
        ok, reason = P.validate_document_policy(pol)
        if not ok:
            raise ValueError(f"canary {c['canary_name']} policy invalid: {reason}")
        payload = pol.payload()
        payload["source"] = c["canary_name"]
        payload["text"] = f"{c['probe_query']} {c.get('canary_token', '')}"
        payload["heading"] = c["canary_name"]
        out.append({"id": c["point_id"], "name": c["canary_name"], "payload": payload})
    return out


def load_manifest(path: str = "permission_manifest.json") -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
