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

# ── N1: unknown auth_mode -> fail-closed (บังคับเหมือน enforce) ────────────────
p_typo = P.authenticate_service(None, "h", "enfore")
check("auth_mode typo + key ผิด -> 401 (fail-closed)",
      auth_raises(lambda: P.resolve_effective_access(p_typo, "qc"), 401))
p_typo2 = P.authenticate_service({"service": "s", "allowed_roles": ["qc"]}, "h", "enfore")
check("auth_mode typo + role นอก scope -> 403",
      auth_raises(lambda: P.resolve_effective_access(p_typo2, "hr"), 403))

# ── M1: type-aware matches_policy (bool != int, float != int, null, scalar) ────
_mv = lambda k, v: [{"key": k, "value": v}]
_active_pl = lambda roles: {"acl_schema_version": 1, "policy_version": "poc-v1",
                            "policy_status": "ACTIVE", "allowed_roles": roles}
check("stored true vs value 1 -> ไม่ match (bool != int)", P.matches_policy({"k": True}, _mv("k", 1)) is False)
check("stored 1.0 vs value 1 -> ไม่ match (float != int)", P.matches_policy({"k": 1.0}, _mv("k", 1)) is False)
check("stored 1 vs value 1 -> match", P.matches_policy({"k": 1}, _mv("k", 1)) is True)
check("null allowed_roles -> ไม่ match (ต้อง IsNull)", P.matches_policy(_active_pl(None), _filter_for("admin")) is False)
check("scalar allowed_roles='qc' -> match (เหมือน Qdrant; กันที่ write boundary ไม่ใช่ filter)",
      P.matches_policy(_active_pl("qc"), _filter_for("qc")) is True)

# ── D1: scalar/malformed allowed_roles -> QUARANTINED ที่ write boundary ───────
_lookup = lambda coll, level, roles: (lambda s: {"collection_group": coll,
                                                 "confidentiality_level": level, "allowed_roles": roles})
def _q(coll, level, roles):
    return P.resolve_document_policy({"source": "x"}, _lookup(coll, level, roles)).policy_status
check("allowed_roles scalar 'qc' -> QUARANTINED", _q("SALES", 3, "qc") == "QUARANTINED")
check("allowed_roles [] -> QUARANTINED", _q("SALES", 3, []) == "QUARANTINED")
check("allowed_roles มี non-str -> QUARANTINED", _q("SALES", 3, ["qc", 1]) == "QUARANTINED")

# ── M4: strict level/collection type & range (ไม่ coerce) ─────────────────────
check("level=true -> QUARANTINED", _q("SALES", True, ["qc"]) == "QUARANTINED")
check("level='3' (str) -> QUARANTINED", _q("SALES", "3", ["qc"]) == "QUARANTINED")
check("level=2.9 (float) -> QUARANTINED (ไม่ปัดเป็น 2)", _q("SALES", 2.9, ["qc"]) == "QUARANTINED")
check("level=9 (นอก range) -> QUARANTINED", _q("SALES", 9, ["qc"]) == "QUARANTINED")
check("collection_group=123 (ไม่ใช่ str) -> QUARANTINED", _q(123, 3, ["qc"]) == "QUARANTINED")
check("valid mapping ยัง ACTIVE", _q("SALES", 3, ["sales", "admin"]) == "ACTIVE")

# ── B1: replace-by-source lifecycle (revoke gen เก่า) ─────────────────────────
class LifecycleQdrant:
    def __init__(self): self.corpus = []
    def apply(self, chunks, rbac_lookup):
        plan = P.plan_source_replacement(chunks, rbac_lookup)
        dele = set(plan["delete_sources"])
        self.corpus = [p for p in self.corpus if p["payload"].get("source") not in dele]  # revoke ก่อน (B1)
        for r in plan["active"]:
            pl = dict(r["policy"].payload()); pl["source"] = r["source"]
            self.corpus.append({"id": r["id"], "payload": pl})
        return plan
    def visible(self, role):
        return {p["id"] for p in self.corpus if P.matches_policy(p["payload"], _filter_for(role))}

lc = LifecycleQdrant()
lc.apply([{"source": "QP-721 sales.pdf", "id": "c1"}], get_rbac)                 # gen1 SALES active
check("B1 gen1: sales เห็น point", lc.visible("sales") == {"c1"})
lc.apply([{"source": "QP-721 sales.pdf", "id": "c1"}], _lookup("SALES", 3, "qc"))  # gen2 mapping พัง -> quarantine
check("B1 ACTIVE->QUARANTINED: sales ค้น point เก่าไม่เจอ (revoke gen เก่า)", lc.visible("sales") == set())

lc2 = LifecycleQdrant()
lc2.apply([{"source": "docX.pdf", "id": "d1"}], _lookup("PRODUCTION", 2, ["production", "qc", "admin"]))
check("B1 gen1: qc เห็น (broad ACL)", lc2.visible("qc") == {"d1"})
lc2.apply([{"source": "docX.pdf", "id": "d1"}], _lookup("PRODUCTION", 2, ["production", "admin"]))
check("B1 broad->narrow: qc ที่ถูกถอน ค้นไม่เจอ", lc2.visible("qc") == set())
check("B1 broad->narrow: production ที่ยังมีสิทธิ์ ยังเห็น", lc2.visible("production") == {"d1"})

# ── M3: durable manifest (terminal outcome ชัด ไม่ success กำกวม) ──────────────
plan = P.plan_source_replacement([{"source": "QP-721 sales.pdf", "id": "c1"}, {"source": "", "id": "c2"}], get_rbac)
entries = P.ingest_manifest_entries(plan, run_id="run-1", ts="2026-08-04T00:00:00")
outcomes = {e["source"]: e["outcome"] for e in entries}
check("M3 manifest: active=ACTIVE, empty-source=QUARANTINED, มี run_id/ts/hash/version",
      outcomes.get("QP-721 sales.pdf") == "ACTIVE" and outcomes.get("") == "QUARANTINED"
      and all({"source_sha256", "run_id", "ts", "policy_version"} <= set(e) for e in entries), entries)

# ── M2: legacy-writer guard + stored-payload validation ───────────────────────
def _guard_raises(payloads):
    try:
        P.assert_legacy_writer_allowed(payloads, "tool"); return False
    except RuntimeError:
        return True
check("legacy-writer guard: เจอ policy-v1 payload -> raise",
      _guard_raises([{"text": "x"}, {"policy_version": "poc-v1", "allowed_roles": ["qc"]}]))
check("legacy-writer guard: legacy payload ล้วน -> ผ่าน",
      P.assert_legacy_writer_allowed([{"source": "x", "collection_group": "SALES"}], "tool") is None)
_good_v1 = {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": "SALES", "confidentiality_level": 3, "allowed_roles": ["sales", "admin"]}
check("validate_stored_payload: v1 ถูก -> ok", P.validate_stored_payload(_good_v1)[0] is True)
check("validate_stored_payload: v1 allowed_roles scalar -> invalid",
      P.validate_stored_payload(dict(_good_v1, allowed_roles="sales"))[0] is False)
check("validate_stored_payload: legacy (ไม่มี marker) -> ผ่าน (migrate ปกติได้)",
      P.validate_stored_payload({"source": "x", "collection_group": "SALES"})[0] is True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
