"""
Unit test ของ p2_m4_qdrant — Qdrant provider + oracle adapter (offline, fake session via client_factory)
B1: identity มาจาก session ที่สร้างจาก handle endpoint (production-pinned/mismatch → abort) ·
B2: oracle observe_visibility case-scoped (สอง case role เดียวกัน คนละ set ได้) ·
M1: approved-probe authorization (role นอก approved set รวม admin → PermissionError) ·
integration: เสียบเข้า RUN.run_m4a จริง → PUBLISHED/PASS

    python test_p2_m4_qdrant.py
"""
import io
import os
import shutil
import sys
import tempfile
import types

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import policy as P
import p2_eval as E
import p2_m4_harness as HN
import p2_m4_qdrant as QA
import p2_m4_runner as RUN
import p2_reranker as RK
import p2_runplan as RP

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True

_IDENTITY = lambda spec: spec                              # offline: ส่ง spec ตรงให้ fake (จริงใช้ to_qdrant_filter)
ISO_EP = "http://isolated-m4:6333"
HANDLE = {"project_id": "proj-u", "network_id": "net-u", "volume_id": "vol-u",
          "collection_id": "coll-u", "endpoint": ISO_EP}
PF = QA.approved_probe_principal_factory({"qc", "sales"})   # approved probe set ผูก evaluated_roles


def _pl(roles):
    return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
def _payload(roles, rerank_text, source="D1"):
    p = _pl(roles); p.update({"source": source, "rerank_text": rerank_text}); return p


class _Pt:
    def __init__(s, pid, payload, score): s.id = pid; s.payload = payload; s.score = score
class _Res:
    def __init__(s, pts): s.points = pts
class FakeSession:
    """session ที่ยืนยัน endpoint ของตัวเอง (observed_target_identity) + query_points/scroll"""
    def __init__(s, points, endpoint): s.points = points; s._endpoint = endpoint
    def observed_target_identity(s, collection_id): return {"collection_id": collection_id, "endpoint": s._endpoint}
    def query_points(s, collection_name, query, query_filter, limit, with_payload):
        hit = list(s.points) if query_filter is None else [p for p in s.points if P.matches_policy(p.payload, query_filter)]
        return _Res(sorted(hit, key=lambda p: -p.score)[:limit])
    def scroll(s, collection_name, with_payload, limit, offset):
        start = offset or 0
        nxt = start + limit if start + limit < len(s.points) else None
        return (s.points[start:start + limit], nxt)
class LeakySession(FakeSession):
    def query_points(s, collection_name, query, query_filter, limit, with_payload):
        return _Res(sorted(s.points, key=lambda p: -p.score)[:limit])   # เพิกเฉย filter (backend รั่ว)

def factory(points, endpoint_override=None):
    """client_factory(endpoint) → session ผูก endpoint นั้น (override = จำลอง client ชี้คนละที่)"""
    return lambda endpoint: FakeSession(points, endpoint_override or endpoint)
def leaky_factory(points):
    return lambda endpoint: LeakySession(points, endpoint)


PA = _Pt("A", _payload(["qc", "admin"], "ta"), 0.90)
PB = _Pt("B", _payload(["sales", "admin"], "tb"), 0.80)
PS = _Pt("S", _payload(["management"], "ts"), 0.99)         # SENTINEL score สูงสุด (unauthorized qc/sales)
POINTS = [PA, PB, PS]
def _pair(pid, txt): return HN.component(pid, txt)["pair_sha256"]


# ── provider: identity จาก session + authorized-only + fail-closed detector ──────
prov = QA.QdrantM4Provider(factory(POINTS), principal_factory=PF, filter_adapter=_IDENTITY)
prov.bind(HANDLE)
check("provider.observed_target_identity = session identity (จาก endpoint ใน handle)",
      prov.observed_target_identity() == {"collection_id": "coll-u", "endpoint": ISO_EP})
check("provider.filtered_candidates(qc) = [(A,ta)] (S/B ถูก filter, map เป็น tuple)",
      prov.filtered_candidates("qc", [0.1, 0.2, 0.3], 50) == [("A", "ta")])
check("provider.filtered_candidates(sales) = [(B,tb)]",
      prov.filtered_candidates("sales", [0.4, 0.5, 0.6], 50) == [("B", "tb")])
check("provider: unbound -> AdapterError",
      raises(lambda: QA.QdrantM4Provider(factory(POINTS), principal_factory=PF).filtered_candidates("qc", [0.1], 50), QA.AdapterError))
check("provider: role approved แต่ไม่มี authorized point -> AdapterError (candidates ว่าง)",
      raises(lambda: QA.approved_probe_principal_factory({"hr"}) and (lambda pv: (pv.bind(HANDLE), pv.filtered_candidates("hr", [0.1, 0.2, 0.3], 50)))(QA.QdrantM4Provider(factory(POINTS), principal_factory=QA.approved_probe_principal_factory({"hr"}), filter_adapter=_IDENTITY)), QA.AdapterError))
_leaky = QA.QdrantM4Provider(leaky_factory(POINTS), principal_factory=PF, filter_adapter=_IDENTITY); _leaky.bind(HANDLE)
check("provider: backend รั่ว sentinel (bypass filter) -> PermissionError (fail batch ผ่าน adapter)",
      raises(lambda: _leaky.filtered_candidates("qc", [0.1, 0.2, 0.3], 50), PermissionError))
check("provider: ไม่ inject principal_factory -> AdapterError",
      raises(lambda: QA.QdrantM4Provider(factory(POINTS), principal_factory=None), QA.AdapterError))

# ── B1: identity ต้องมาจาก session จริง — production-pinned/mismatch client -> abort ──
_prod = QA.QdrantM4Provider(factory(POINTS, endpoint_override="http://production:6333"), principal_factory=PF, filter_adapter=_IDENTITY)
check("B1: client ชี้ production แต่ bind handle isolated -> AdapterError (ไม่ผ่านโดยการ copy handle)",
      raises(lambda: _prod.bind(HANDLE), QA.AdapterError))
class _WrongCollSession(FakeSession):
    def observed_target_identity(s, collection_id): return {"collection_id": "other-coll", "endpoint": s._endpoint}
_wc = QA.QdrantM4Provider(lambda ep: _WrongCollSession(POINTS, ep), principal_factory=PF, filter_adapter=_IDENTITY)
check("B1: session ชี้คนละ collection -> AdapterError", raises(lambda: _wc.bind(HANDLE), QA.AdapterError))
_rb = QA.QdrantM4Provider(factory(POINTS), principal_factory=PF, filter_adapter=_IDENTITY); _rb.bind(HANDLE)
check("B1: rebind -> AdapterError (session ผูก endpoint เดิม, ต้อง instance ใหม่)", raises(lambda: _rb.bind(HANDLE), QA.AdapterError))

# ── M1: approved-probe authorization — role นอก approved set (รวม admin) -> PermissionError ──
check("M1: filtered_candidates(admin) — admin นอก approved probe set -> PermissionError (แม้เป็น KNOWN_ROLE)",
      raises(lambda: prov.filtered_candidates("admin", [0.1, 0.2, 0.3], 50), PermissionError))
check("M1: approved_probe_principal_factory ว่าง -> AdapterError", raises(lambda: QA.approved_probe_principal_factory(set()), QA.AdapterError))
check("M1: approved factory mint principal เฉพาะ role ใน set (qc ผ่าน, it ไม่ผ่าน)",
      isinstance(PF("qc"), P.ServicePrincipal) and PF("qc").verified and raises(lambda: PF("it"), PermissionError))

# ── oracle: unfiltered_topn + observe_visibility case-scoped ตรง frozen ──────────
OPLAN = {"case-qc": {"effective_role": "qc", "point_ids": ["A", "S"]},
         "case-sales": {"effective_role": "sales", "point_ids": ["B", "S"]}}
orac = QA.QdrantM4Oracle(factory(POINTS), observation_plan=OPLAN, principal_factory=PF)
orac.bind(HANDLE)
check("oracle.observed_target_identity = session identity", orac.observed_target_identity() == {"collection_id": "coll-u", "endpoint": ISO_EP})
check("oracle.unfiltered_topn = raw top-N (sentinel S score สูงสุดต้องอยู่, ไม่มี RBAC filter)",
      orac.unfiltered_topn([0.1, 0.2, 0.3], 50) == [("S", "ts"), ("A", "ta"), ("B", "tb")])
_vqc = orac.observe_visibility("case-qc", "qc")
check("oracle.observe_visibility(case-qc,qc) = {authorized:[A], sentinel:[S]} (ตรง frozen)",
      sorted(_vqc["authorized_pairs"]) == sorted([_pair("A", "ta")]) and sorted(_vqc["sentinel_pairs"]) == sorted([_pair("S", "ts")]))
check("oracle.observe_visibility(case-sales,sales) = {authorized:[B], sentinel:[S]}",
      orac.observe_visibility("case-sales", "sales") == {"authorized_pairs": [_pair("B", "tb")], "sentinel_pairs": [_pair("S", "ts")]})
# tamper: S authorize qc -> ย้ายไป authorized (detect)
PS_T = _Pt("S", _payload(["qc", "management"], "ts"), 0.99)
_ot = QA.QdrantM4Oracle(factory([PA, PB, PS_T]), observation_plan=OPLAN, principal_factory=PF); _ot.bind(HANDLE)
_vt = _ot.observe_visibility("case-qc", "qc")
check("oracle: tamper (sentinel S authorize qc) -> S ไป authorized, sentinel ว่าง -> ไม่ตรง frozen (detect)",
      _pair("S", "ts") in _vt["authorized_pairs"] and _pair("S", "ts") not in _vt["sentinel_pairs"])
check("oracle: unbound -> AdapterError", raises(lambda: QA.QdrantM4Oracle(factory(POINTS), observation_plan=OPLAN, principal_factory=PF).observe_visibility("case-qc", "qc"), QA.AdapterError))
check("oracle: case ไม่มีใน plan -> AdapterError", raises(lambda: orac.observe_visibility("case-x", "qc"), QA.AdapterError))
check("oracle: role ไม่ตรง plan entry -> AdapterError", raises(lambda: orac.observe_visibility("case-qc", "sales"), QA.AdapterError))
_omiss = QA.QdrantM4Oracle(factory([PA, PB]), observation_plan=OPLAN, principal_factory=PF); _omiss.bind(HANDLE)
check("oracle: point ใน plan หายจากคอลเลกชัน -> AdapterError", raises(lambda: _omiss.observe_visibility("case-qc", "qc"), QA.AdapterError))
check("oracle: plan ว่าง -> AdapterError", raises(lambda: QA.QdrantM4Oracle(factory(POINTS), observation_plan={}, principal_factory=PF), QA.AdapterError))
check("oracle: plan entry ขาด point_ids -> AdapterError", raises(lambda: QA.QdrantM4Oracle(factory(POINTS), observation_plan={"c": {"effective_role": "qc"}}, principal_factory=PF), QA.AdapterError))
check("oracle: plan point_ids ซ้ำ -> AdapterError", raises(lambda: QA.QdrantM4Oracle(factory(POINTS), observation_plan={"c": {"effective_role": "qc", "point_ids": ["A", "A"]}}, principal_factory=PF), QA.AdapterError))
_opg = QA.QdrantM4Oracle(factory(POINTS), observation_plan=OPLAN, principal_factory=PF, scroll_page=1); _opg.bind(HANDLE)
check("oracle: scroll paginate (page=1) ยังอ่านครบ -> observe ตรง", _opg.observe_visibility("case-qc", "qc") == _vqc)

# ── B2: สอง case role เดียวกัน (qc) คนละ authorized/sentinel set -> observation ต่างกัน ──
PC = _Pt("C", _payload(["qc", "admin"], "tc"), 0.70)
PT = _Pt("T", _payload(["management"], "tt"), 0.60)
OPLAN2 = {"case-qc1": {"effective_role": "qc", "point_ids": ["A", "S"]},
          "case-qc2": {"effective_role": "qc", "point_ids": ["C", "T"]}}
_o2 = QA.QdrantM4Oracle(factory([PA, PB, PS, PC, PT]), observation_plan=OPLAN2, principal_factory=PF); _o2.bind(HANDLE)
_v1 = _o2.observe_visibility("case-qc1", "qc"); _v2 = _o2.observe_visibility("case-qc2", "qc")
check("B2: สอง case role qc -> observation ต่างกันตาม case (case-scoped ไม่ merge)",
      _v1 == {"authorized_pairs": [_pair("A", "ta")], "sentinel_pairs": [_pair("S", "ts")]}
      and _v2 == {"authorized_pairs": [_pair("C", "tc")], "sentinel_pairs": [_pair("T", "tt")]} and _v1 != _v2)

# ── integration: เสียบ adapter จริงเข้า RUN.run_m4a (offline) -> PUBLISHED/PASS ──
_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
VEC1, VEC2 = [0.1, 0.2, 0.3], [0.4, 0.5, 0.6]
QT1, QT2 = "คำถาม negation", "คำถาม table-row"
CORPUS = {"pa": {"source": "D1", "rerank_text": "alpha", "payload": _pl(["qc", "admin"])},
          "pb": {"source": "D2", "rerank_text": "beta", "payload": _pl(["sales", "admin"])}}
FROZEN = HN.build_frozen_manifest(
    cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_text=QT1, query_vector=VEC1,
                                     authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
           "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_text=QT2, query_vector=VEC2,
                                        authorized_items=[("B", "tb")], sentinel_items=[("S", "ts")])},
    required_categories=["negation", "table-row"], evaluated_roles=["qc", "sales"])
PLAN = {"run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5",
        "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "gate_tags": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"],
        "m4_case_manifest_sha256": E.m4_case_manifest_sha256(FROZEN), "required_categories": ["negation", "table-row"],
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 1, "test_queries": 1},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": E.corpus_manifest_sha256(CORPUS),
                             "retrieval_index_manifest_sha256": _H},
        "model_commit": "a" * 40, "tokenizer_commit": "a" * 40, "model_file_manifest_sha256": _H,
        "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC)}
CASES = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1},
         {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": VEC2}]

class PinnedScorer:
    def __init__(s, smap): s.smap = smap; s.queries = []
    def metadata(s):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": "a" * 40,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H, "inference_config": dict(IC)}
    def score(s, q, texts): s.queries.append(q); return [s.smap.get(t, 0.0) for t in texts]
class FakeIso:
    def __init__(s): s.calls = []; s._m = None; s.torn = False
    def provision(s): s.calls.append("provision"); return dict(HANDLE)
    def observe_initial_count(s): return 0
    def observe_published_ports(s): return 0
    def observe_endpoint_is_production(s): return False
    def write_marker(s, m): s._m = m
    def read_marker(s): return s._m
    def seed(s, c): s.calls.append("seed")
    def teardown(s): s.torn = True
class FakeClock:
    def __init__(s): s.n = 0
    def now_iso(s): s.n += 1; return "2026-08-05T05:0%d:00+07:00" % s.n

d = tempfile.mkdtemp(prefix="p2m4qd-")
_probe = QA.approved_probe_principal_factory(frozenset(PLAN["evaluated_roles"]))   # ผูก approved set กับ RunPlan
_iplan = {"case-qc": {"effective_role": "qc", "point_ids": ["A", "S"]},
          "case-sales": {"effective_role": "sales", "point_ids": ["B", "S"]}}
_pt = types.SimpleNamespace(
    scorer=PinnedScorer({"ta": 2.0, "tb": 2.0}), isolation=FakeIso(),
    provider=QA.QdrantM4Provider(factory(POINTS), principal_factory=_probe, filter_adapter=_IDENTITY),
    oracle=QA.QdrantM4Oracle(factory(POINTS), observation_plan=_iplan, principal_factory=_probe),
    clock=FakeClock())
r = RUN.run_m4a(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
                ports=_pt, out_dir=d, argv=["python", "p2_m4_runner.py"], stdout=b"ok", stderr=b"")
check("integration: adapter จริง (client_factory + case-scoped) -> RUN.run_m4a PUBLISHED", r["status"] == "PUBLISHED" and os.path.isfile(r["path"]))
check("integration: evidence PASS (permission-leak proof ผ่านด้วย adapter จริง)", r["evidence"]["status"] == "PASS")
check("integration: bundle re-validate ผ่าน public gate",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, r["evidence"], r["receipt"]) == [],
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, r["evidence"], r["receipt"]))
check("integration: scorer เห็นเฉพาะ authorized query ของ case (ไม่มี sentinel ถึง model)", _pt.scorer.queries == [QT1, QT2])
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
