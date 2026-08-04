"""
Unit + fake-Qdrant matrix test ของ policy compiler (P1) — ไม่ต้องรัน Qdrant/model/API
พิสูจน์ contract §1-6: fail-closed auth, explicit filter (admin ไม่ bypass), effective ACL,
stale/quarantine invisible, admin-spoof ถูกปฏิเสธ

    python test_policy.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import policy as P
from rbac_config import get_rbac

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def auth_raises(fn, code):
    try:
        fn(); return False
    except P.AuthError as e:
        return e.code == code


# ── ServicePrincipal / authenticate_service ───────────────────────────────────
p_ok = P.authenticate_service({"service": "voicebot", "allowed_roles": ["sales", "qc"]}, "", "enforce")
check("key valid -> authenticated + roles", p_ok.authenticated and set(p_ok.allowed_roles) == {"sales", "qc"})
check("enforce + authenticated -> verified", p_ok.verified is True)
p_bad = P.authenticate_service(None, "hint", "enforce")
check("key ผิด -> unauthenticated", p_bad.authenticated is False and p_bad.allowed_roles == ())
p_warn = P.authenticate_service({"service": "s", "allowed_roles": ["qc"]}, "", "warn")
check("warn mode -> verified=False (ห้ามใช้เป็นหลักฐาน hardened)", p_warn.verified is False)

# ── resolve_effective_access: fail-closed (§2/§6) ─────────────────────────────
acc = P.resolve_effective_access(p_ok, "qc")
check("resolve ปกติ -> effective_role เดียว (ไม่ union)", acc.effective_role == "qc")
check("enforce + key ผิด -> 401", auth_raises(lambda: P.resolve_effective_access(p_bad, "qc"), 401))
check("enforce + role นอก scope -> 403",
      auth_raises(lambda: P.resolve_effective_access(p_ok, "hr"), 403))
check("role ว่าง -> 403", auth_raises(lambda: P.resolve_effective_access(p_ok, ""), 403))
check("role ไม่รู้จัก -> 403", auth_raises(lambda: P.resolve_effective_access(p_ok, "ceo"), 403))
check("admin-spoof: principal ไม่มี admin ขอ admin (enforce) -> 403",
      auth_raises(lambda: P.resolve_effective_access(p_ok, "admin"), 403))
# warn/off: ผ่านได้แม้นอก scope แต่ verified=False
acc_warn = P.resolve_effective_access(p_warn, "admin")
check("warn: role นอก scope ผ่านได้ แต่ principal ยัง unverified",
      acc_warn.effective_role == "admin" and acc_warn.principal.verified is False)

# ── compile_retrieval_filter: explicit เสมอ แม้ admin (§5) ─────────────────────
def _filter_for(role):
    pr = P.ServicePrincipal("t", (role,), True, "enforce")
    return P.compile_retrieval_filter(P.resolve_effective_access(pr, role))
f_qc = _filter_for("qc")
check("filter มี 4 เงื่อนไข AND", len(f_qc) == 4)
keys = [c["key"] for c in f_qc]
check("filter บังคับ schema+policy+status+roles",
      keys == ["acl_schema_version", "policy_version", "policy_status", "allowed_roles"], keys)
check("status ต้อง ACTIVE", {"key": "policy_status", "value": P.ACTIVE} in f_qc)
check("allowed_roles any=[role]", {"key": "allowed_roles", "any": ["qc"]} in f_qc)
f_admin = _filter_for("admin")
check("admin **ไม่** None + ใช้ allowed_roles contains admin (ไม่ bypass)",
      f_admin is not None and {"key": "allowed_roles", "any": ["admin"]} in f_admin)

# ── matches_policy semantics (fail-closed) ────────────────────────────────────
active = {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
          "allowed_roles": ["qc", "admin"]}
check("ACTIVE + role ตรง -> match", P.matches_policy(active, _filter_for("qc")) is True)
check("ACTIVE + role ไม่ตรง -> ไม่ match", P.matches_policy(active, _filter_for("sales")) is False)
check("missing field ทั้งหมด -> ไม่ match (fail-closed)", P.matches_policy({}, _filter_for("admin")) is False)
stale = dict(active, acl_schema_version=0)
check("stale acl_schema_version -> ไม่ match แม้ role ตรง", P.matches_policy(stale, _filter_for("qc")) is False)
oldver = dict(active, policy_version="poc-v0")
check("stale policy_version -> ไม่ match", P.matches_policy(oldver, _filter_for("qc")) is False)
quar = dict(active, policy_status="QUARANTINED")
check("QUARANTINED -> ไม่ match แม้ admin", P.matches_policy(dict(quar, allowed_roles=["admin"]), _filter_for("admin")) is False)

# ── document policy resolver / validator (§4) ─────────────────────────────────
pol_recall = P.resolve_document_policy({"source": "QP-852 recall procedure.pdf"}, get_rbac)
check("source รู้จัก (recall) -> ACTIVE + RECALL roles",
      P.is_active(pol_recall) and "qc" in pol_recall.allowed_roles and "sales" not in pol_recall.allowed_roles, pol_recall)
pol_unknown = P.resolve_document_policy({"source": "random_unknown_doc.pdf"}, get_rbac)
check("source ไม่รู้จักแต่โครงถูก -> ACTIVE UNCLASSIFIED admin-only (ไม่ quarantine)",
      P.is_active(pol_unknown) and pol_unknown.collection_group == "UNCLASSIFIED"
      and tuple(pol_unknown.allowed_roles) == ("admin",), pol_unknown)
pol_nosrc = P.resolve_document_policy({"source": ""}, get_rbac)
check("source ว่าง -> QUARANTINED", pol_nosrc.policy_status == "QUARANTINED" and pol_nosrc.quarantine_reason)
pol_bad_lookup = P.resolve_document_policy({"source": "x.pdf"}, lambda s: {"boom": 1})
check("rbac_lookup คืนผิดชนิด -> QUARANTINED", pol_bad_lookup.policy_status == "QUARANTINED")
# validate_document_policy โดยตรง
check("ACL ว่าง -> invalid",
      P.validate_document_policy(P.DocumentPolicy(1, "poc-v1", "ACTIVE", "X", 2, ()))[0] is False)
check("unknown role ใน ACL -> invalid",
      P.validate_document_policy(P.DocumentPolicy(1, "poc-v1", "ACTIVE", "X", 2, ("ghost",)))[0] is False)
check("payload() มี field ครบ v1",
      set(pol_recall.payload()) == {"acl_schema_version", "policy_version", "policy_status",
                                    "collection_group", "confidentiality_level", "allowed_roles"})


# ── fake-Qdrant matrix: ทุก role vs synthetic corpus (§5/§7) ───────────────────
class _Pts:
    def __init__(self, pts): self.points = pts
class FakeQdrant:
    """บังคับ filter เหมือน Qdrant จริง (ผ่าน matches_policy) — filter อยู่ใน query ก่อน retrieval"""
    def __init__(self, corpus): self.corpus = corpus
    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        hit = [p for p in self.corpus if P.matches_policy(p["payload"], query_filter)]
        return _Pts(hit[:limit])

def _pt(pid, source, mutate=None):
    pol = P.resolve_document_policy({"source": source}, get_rbac)
    payload = pol.payload()
    if mutate:
        mutate(payload)
    return {"id": pid, "payload": payload}

CORPUS = [
    _pt("recall", "QP-852 recall.pdf"),
    _pt("sales", "QP-721 sales quote.pdf"),
    _pt("hr", "iso_jd training.pdf"),
    _pt("purchasing", "QP-741 purchasing.pdf"),
    _pt("production", "QP-710 production.pdf"),
    _pt("unclassified", "weird_unmapped.pdf"),
    _pt("stale", "QP-852 recall.pdf", mutate=lambda p: p.__setitem__("acl_schema_version", 0)),
    _pt("quar", "QP-852 recall.pdf", mutate=lambda p: p.__setitem__("policy_status", "QUARANTINED")),
]
fake = FakeQdrant(CORPUS)

def _independent_visible(payload, role):
    """oracle อิสระ (ไม่ผ่าน compiler) — visible iff ACTIVE + version ปัจจุบัน + role ใน ACL"""
    return (payload.get("policy_status") == "ACTIVE"
            and payload.get("acl_schema_version") == 1
            and payload.get("policy_version") == "poc-v1"
            and role in payload.get("allowed_roles", []))

matrix_ok = True
for role in sorted(P.KNOWN_ROLES):
    spec = _filter_for(role)
    seen = {x["id"] for x in CORPUS if P.matches_policy(x["payload"], spec)}
    expected = {x["id"] for x in CORPUS if _independent_visible(x["payload"], role)}
    if seen != expected:
        matrix_ok = False
        print(f"    role={role}: seen={seen} expected={expected}")
check("matrix: ทุก role เห็นตรง independent oracle (compiler == semantics)", matrix_ok)

# named spot-checks ผ่าน FakeQdrant.query_points (เหมือน endpoint จริง)
def _visible_ids(role):
    r = fake.query_points("c", [0.0], _filter_for(role), 100, True)
    return {p["id"] for p in r.points}
check("qc เห็น RECALL+PRODUCTION ไม่เห็น SALES/HR/UNCLASSIFIED",
      _visible_ids("qc") == {"recall", "production"}, _visible_ids("qc"))
check("sales เห็นแค่ SALES", _visible_ids("sales") == {"sales"}, _visible_ids("sales"))
check("hr เห็นแค่ HR", _visible_ids("hr") == {"hr"}, _visible_ids("hr"))
adm = _visible_ids("admin")
check("admin เห็นทุก ACTIVE แต่ **ไม่เห็น** stale/quarantine",
      adm == {"recall", "sales", "hr", "purchasing", "production", "unclassified"}, adm)
check("it (ไม่มีสิทธิ์ collection ไหนใน corpus) เห็น 0 (fail-closed)", _visible_ids("it") == set())

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
