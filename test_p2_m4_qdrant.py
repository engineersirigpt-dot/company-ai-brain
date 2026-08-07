"""
Unit test ของ p2_m4_qdrant — real Qdrant provider + oracle adapter (offline, fake qdrant client)
provider: filtered_candidates = authorized เท่านั้น (map dict→tuple) + fail-closed detector ยิงผ่าน adapter ·
oracle: unfiltered_topn รวม sentinel · observe_visibility classify ตรง frozen + tamper detect ·
integration: เสียบเข้า RUN.run_m4a จริง → PUBLISHED/PASS (offline)

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
import p2_atomic as AT
import p2_eval as E
import p2_m4_harness as HN
import p2_m4_qdrant as QA
import p2_m4_runner as RUN
import p2_provider as PROV
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
HANDLE = {"project_id": "proj-u", "network_id": "net-u", "volume_id": "vol-u",
          "collection_id": "coll-u", "endpoint": "http://isolated-m4:6333"}


def _pl(roles):
    return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
def _payload(roles, rerank_text, source="D1"):
    p = _pl(roles); p.update({"source": source, "rerank_text": rerank_text}); return p


class _Pt:
    def __init__(s, pid, payload, score): s.id = pid; s.payload = payload; s.score = score
class _Res:
    def __init__(s, pts): s.points = pts
class FakeQdrant:
    """query_points: filter=None → ทั้งหมด (unfiltered) ; filter=spec → matches_policy (เหมือน Qdrant ก่อน retrieval) ; scroll: paginate"""
    def __init__(s, points): s.points = points
    def query_points(s, collection_name, query, query_filter, limit, with_payload):
        hit = list(s.points) if query_filter is None else [p for p in s.points if P.matches_policy(p.payload, query_filter)]
        return _Res(sorted(hit, key=lambda p: -p.score)[:limit])
    def scroll(s, collection_name, with_payload, limit, offset):
        start = offset or 0
        nxt = start + limit if start + limit < len(s.points) else None
        return (s.points[start:start + limit], nxt)
class LeakyQdrant(FakeQdrant):
    def query_points(s, collection_name, query, query_filter, limit, with_payload):
        return _Res(sorted(s.points, key=lambda p: -p.score)[:limit])   # เพิกเฉย filter (จำลอง backend รั่ว)


# corpus: A→qc, B→sales, S(SENTINEL score สูงสุด)→management (ไม่ authorize ทั้ง qc/sales)
PA = _Pt("A", _payload(["qc", "admin"], "ta"), 0.90)
PB = _Pt("B", _payload(["sales", "admin"], "tb"), 0.80)
PS = _Pt("S", _payload(["management"], "ts"), 0.99)
POINTS = [PA, PB, PS]
PLAN_OBS = {"qc": ["A", "S"], "sales": ["B", "S"]}         # universe ต่อ case (authorized ∪ sentinel)
def _pair(pid, txt): return HN.component(pid, txt)["pair_sha256"]


# ── provider: filtered_candidates = authorized เท่านั้น (map dict→tuple) ──────────
prov = QA.QdrantM4Provider(FakeQdrant(POINTS), filter_adapter=_IDENTITY)
prov.bind(HANDLE)
check("provider.observed_target_identity = handle collection/endpoint",
      prov.observed_target_identity() == {"collection_id": "coll-u", "endpoint": "http://isolated-m4:6333"})
check("provider.filtered_candidates(qc) = [(A,ta)] เท่านั้น (S/B ถูก filter, map เป็น tuple)",
      prov.filtered_candidates("qc", [0.1, 0.2, 0.3], 50) == [("A", "ta")])
check("provider.filtered_candidates(sales) = [(B,tb)]",
      prov.filtered_candidates("sales", [0.4, 0.5, 0.6], 50) == [("B", "tb")])
check("provider: unbound -> AdapterError", raises(lambda: QA.QdrantM4Provider(FakeQdrant(POINTS)).filtered_candidates("qc", [0.1], 50), QA.AdapterError))
check("provider: role ไม่มี authorized point -> AdapterError (candidates ว่าง)",
      raises(lambda: prov.filtered_candidates("hr", [0.1, 0.2, 0.3], 50), QA.AdapterError))
# fail-closed detector (build_candidates B2) ยิงผ่าน adapter เมื่อ backend รั่ว sentinel
leaky = QA.QdrantM4Provider(LeakyQdrant(POINTS), filter_adapter=_IDENTITY); leaky.bind(HANDLE)
check("provider: backend รั่ว sentinel (bypass filter) -> PermissionError (fail batch ผ่าน adapter)",
      raises(lambda: leaky.filtered_candidates("qc", [0.1, 0.2, 0.3], 50), PermissionError))

# ── oracle: unfiltered_topn รวม sentinel + observe_visibility ตรง frozen ──────────
orac = QA.QdrantM4Oracle(FakeQdrant(POINTS), observation_plan=PLAN_OBS)
orac.bind(HANDLE)
check("oracle.observed_target_identity = handle collection/endpoint",
      orac.observed_target_identity() == {"collection_id": "coll-u", "endpoint": "http://isolated-m4:6333"})
_unf = orac.unfiltered_topn([0.1, 0.2, 0.3], 50)
check("oracle.unfiltered_topn = raw top-N (sentinel S score สูงสุดต้องอยู่, ไม่มี RBAC filter)",
      _unf == [("S", "ts"), ("A", "ta"), ("B", "tb")])
_vqc = orac.observe_visibility("qc")
check("oracle.observe_visibility(qc) = {authorized:[A], sentinel:[S]} (classify จาก payload จริง ตรง frozen)",
      sorted(_vqc["authorized_pairs"]) == sorted([_pair("A", "ta")]) and sorted(_vqc["sentinel_pairs"]) == sorted([_pair("S", "ts")]))
_vsa = orac.observe_visibility("sales")
check("oracle.observe_visibility(sales) = {authorized:[B], sentinel:[S]}",
      sorted(_vsa["authorized_pairs"]) == sorted([_pair("B", "tb")]) and sorted(_vsa["sentinel_pairs"]) == sorted([_pair("S", "ts")]))
# tamper: S ถูกแก้ payload ให้ authorize qc -> oracle เห็น S เป็น authorized (ไม่ใช่ sentinel) -> mismatch frozen (detect)
PS_TAMP = _Pt("S", _payload(["qc", "management"], "ts"), 0.99)
orac_t = QA.QdrantM4Oracle(FakeQdrant([PA, PB, PS_TAMP]), observation_plan=PLAN_OBS); orac_t.bind(HANDLE)
_vt = orac_t.observe_visibility("qc")
check("oracle: tamper (sentinel S authorize qc) -> S ย้ายไป authorized, sentinel ว่าง -> ไม่ตรง frozen (detect)",
      _pair("S", "ts") in _vt["authorized_pairs"] and _pair("S", "ts") not in _vt["sentinel_pairs"])
check("oracle: unbound -> AdapterError", raises(lambda: QA.QdrantM4Oracle(FakeQdrant(POINTS), observation_plan=PLAN_OBS).observe_visibility("qc"), QA.AdapterError))
check("oracle: role ไม่มีใน observation_plan -> AdapterError", raises(lambda: orac.observe_visibility("it"), QA.AdapterError))
# point ใน plan หายจากคอลเลกชัน -> AdapterError (fail-closed)
orac_miss = QA.QdrantM4Oracle(FakeQdrant([PA, PB]), observation_plan=PLAN_OBS); orac_miss.bind(HANDLE)
check("oracle: point ใน plan ไม่พบในคอลเลกชัน -> AdapterError", raises(lambda: orac_miss.observe_visibility("qc"), QA.AdapterError))
check("oracle: observation_plan ว่าง -> AdapterError (constructor)", raises(lambda: QA.QdrantM4Oracle(FakeQdrant(POINTS), observation_plan={}), QA.AdapterError))
# scroll pagination (scroll_page เล็ก) ยังอ่านครบ
orac_pg = QA.QdrantM4Oracle(FakeQdrant(POINTS), observation_plan=PLAN_OBS, scroll_page=1); orac_pg.bind(HANDLE)
check("oracle: scroll paginate (page=1) ยังอ่าน point ครบ -> observe ตรง", orac_pg.observe_visibility("qc") == _vqc)

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
_prov = QA.QdrantM4Provider(FakeQdrant(POINTS), filter_adapter=_IDENTITY)
_orac = QA.QdrantM4Oracle(FakeQdrant(POINTS), observation_plan=PLAN_OBS)
_pt = types.SimpleNamespace(scorer=PinnedScorer({"ta": 2.0, "tb": 2.0}), isolation=FakeIso(),
                            provider=_prov, oracle=_orac, clock=FakeClock())
r = RUN.run_m4a(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
                ports=_pt, out_dir=d, argv=["python", "p2_m4_runner.py"], stdout=b"ok", stderr=b"")
check("integration: adapter จริง -> RUN.run_m4a PUBLISHED", r["status"] == "PUBLISHED" and os.path.isfile(r["path"]))
check("integration: evidence PASS (permission-leak proof ผ่านด้วย adapter จริง)", r["evidence"]["status"] == "PASS")
check("integration: bundle re-validate ผ่าน public gate",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, r["evidence"], r["receipt"]) == [],
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, r["evidence"], r["receipt"]))
check("integration: scorer เห็นเฉพาะ authorized text (ta,tb) ไม่เห็น sentinel (ts)",
      _pt.scorer.queries == [QT1, QT2])
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
