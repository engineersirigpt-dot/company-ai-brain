"""
Unit test ของ p2_m4_launch — run_m4a_locked (single flow, attestation load-bearing) offline
- controller รัน run_m4a (จำลองรันใน pinned evaluator container) คืน {executed_image_digest, bundle}
- launcher: executed == plan.image_digest -> ok ; ไม่ตรง -> LaunchError ; qdrant_image_ref แยก schema ; bundle ผ่าน gate

    python test_p2_m4_launch.py
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
import p2_m4_launch as LA
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

_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
SCORER_IMG = "sha256:" + "e" * 64                          # plan.image_digest = scorer/evaluator image (sha256:<64hex>)
QDRANT_REF = "qdrant/qdrant@sha256:" + "f" * 64            # แยก: Qdrant repo ref (immutable)
VEC1, VEC2 = [0.1, 0.2, 0.3], [0.4, 0.5, 0.6]
QT1, QT2 = "คำถาม negation", "คำถาม table-row"
_IDENTITY = lambda spec: spec
def _pl(roles):
    return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
def _payload(roles, txt): p = _pl(roles); p.update({"source": "D1", "rerank_text": txt}); return p

class _Pt:
    def __init__(s, pid, payload, score): s.id = pid; s.payload = payload; s.score = score
class _Res:
    def __init__(s, pts): s.points = pts
class FakeSession:
    def __init__(s, points, endpoint): s.points = points; s._endpoint = endpoint
    def observed_target_identity(s, collection_id): return {"collection_id": collection_id, "endpoint": s._endpoint}
    def query_points(s, collection_name, query, query_filter, limit, with_payload):
        hit = list(s.points) if query_filter is None else [p for p in s.points if P.matches_policy(p.payload, query_filter)]
        return _Res(sorted(hit, key=lambda p: -p.score)[:limit])
    def scroll(s, collection_name, with_payload, limit, offset):
        start = offset or 0
        return (s.points[start:start + limit], start + limit if start + limit < len(s.points) else None)
def factory(points): return lambda endpoint: FakeSession(points, endpoint)
POINTS = [_Pt("A", _payload(["qc", "admin"], "ta"), 0.90), _Pt("B", _payload(["sales", "admin"], "tb"), 0.80),
          _Pt("S", _payload(["management"], "ts"), 0.99)]
class PinnedScorer:
    def __init__(s): s.queries = []
    def metadata(s):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": "a" * 40,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H, "inference_config": dict(IC)}
    def score(s, q, texts): s.queries.append(q); return [{"ta": 2.0, "tb": 2.0}.get(t, 0.0) for t in texts]
class FakeIso:
    def __init__(s): s.torn = False
    def provision(s): return {"project_id": "proj-u", "network_id": "net-u", "volume_id": "vol-u", "collection_id": "coll-u", "endpoint": "http://iso:6333"}
    def observe_initial_count(s): return 0
    def observe_published_ports(s): return 0
    def observe_endpoint_is_production(s): return False
    def write_marker(s, m): s._m = m
    def read_marker(s): return s._m
    def seed(s, c): pass
    def teardown(s): s.torn = True
class FakeClock:
    def __init__(s): s.n = 0
    def now_iso(s): s.n += 1; return "2026-08-05T05:0%d:00+07:00" % s.n

CORPUS = {"pa": {"source": "D1", "rerank_text": "alpha", "payload": _pl(["qc", "admin"])},
          "pb": {"source": "D2", "rerank_text": "beta", "payload": _pl(["sales", "admin"])}}
FROZEN = HN.build_frozen_manifest(
    cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_text=QT1, query_vector=VEC1, authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
           "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_text=QT2, query_vector=VEC2, authorized_items=[("B", "tb")], sentinel_items=[("S", "ts")])},
    required_categories=["negation", "table-row"], evaluated_roles=["qc", "sales"])
PLAN = {"run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION, "n_set": [10, 20, 30, 50],
        "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5", "intent_grouping": "intent_id",
        "thresholds": dict(RP.DEFAULT_THRESHOLDS), "gate_tags": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"],
        "m4_case_manifest_sha256": E.m4_case_manifest_sha256(FROZEN), "required_categories": ["negation", "table-row"],
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 1, "test_queries": 1},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": E.corpus_manifest_sha256(CORPUS), "retrieval_index_manifest_sha256": _H},
        "model_commit": "a" * 40, "tokenizer_commit": "a" * 40, "model_file_manifest_sha256": _H,
        "image_digest": SCORER_IMG, "inference_config": dict(IC)}
CASES = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1},
         {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": VEC2}]


class FakeController:
    """จำลอง real controller: รัน scorer+runner (adapter จริง) 'ภายใน pinned container' แล้วคืน executed image + bundle"""
    def __init__(s, *, executed_image=None, tamper=False): s._exec = executed_image; s._tamper = tamper
    def execute(s, *, plan, frozen, cases, corpus, marker, out_dir, qdrant_image_ref):
        probe = QA.approved_probe_principal_factory(frozenset(plan["evaluated_roles"]))
        oplan = {"case-qc": {"effective_role": "qc", "point_ids": ["A", "S"]}, "case-sales": {"effective_role": "sales", "point_ids": ["B", "S"]}}
        pt = types.SimpleNamespace(scorer=PinnedScorer(), isolation=FakeIso(),
                                   provider=QA.QdrantM4Provider(factory(POINTS), principal_factory=probe, filter_adapter=_IDENTITY),
                                   oracle=QA.QdrantM4Oracle(factory(POINTS), observation_plan=oplan, principal_factory=probe),
                                   clock=FakeClock())
        r = RUN.run_m4a(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker=marker, ports=pt,
                        out_dir=out_dir, argv=["python", "p2_m4_runner.py"], stdout=b"ok", stderr=b"")
        ev = dict(r["evidence"])
        if s._tamper: ev["status"] = "PASS-TAMPERED"        # ทำ bundle ไม่ผ่าน gate
        return {"executed_image_digest": s._exec if s._exec is not None else plan["image_digest"],
                "bundle": {"evidence": ev, "receipt": r["receipt"]}}


d = tempfile.mkdtemp(prefix="p2m4la-")
_ok = LA.run_m4a_locked(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
                        out_dir=d, qdrant_image_ref=QDRANT_REF, controller=FakeController())
check("run_m4a_locked: controller executed==plan.image_digest + bundle ผ่าน gate -> คืน evidence/receipt",
      _ok["executed_image_digest"] == SCORER_IMG and _ok["evidence"]["status"] == "PASS")
check("B4: executed image != plan.image_digest (scorer ไม่ได้รันใน pinned container) -> LaunchError",
      raises(lambda: LA.run_m4a_locked(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
             out_dir=tempfile.mkdtemp(prefix="p2m4la-"), qdrant_image_ref=QDRANT_REF,
             controller=FakeController(executed_image="sha256:" + "9" * 64)), LA.LaunchError))
check("B1/B4: qdrant_image_ref = scorer digest (sha256:...) ผิด schema -> LaunchError (สองค่าแยกกัน)",
      raises(lambda: LA.run_m4a_locked(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
             out_dir=tempfile.mkdtemp(prefix="p2m4la-"), qdrant_image_ref=SCORER_IMG, controller=FakeController()), LA.LaunchError))
check("run_m4a_locked: RunPlan invalid -> LaunchError",
      raises(lambda: LA.run_m4a_locked(plan={**PLAN, "run_id": "bad id!"}, frozen=FROZEN, cases=CASES, corpus=CORPUS,
             marker="m4-run-uuid", out_dir=tempfile.mkdtemp(prefix="p2m4la-"), qdrant_image_ref=QDRANT_REF, controller=FakeController()), LA.LaunchError))
check("run_m4a_locked: bundle ไม่ผ่าน public gate -> LaunchError",
      raises(lambda: LA.run_m4a_locked(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
             out_dir=tempfile.mkdtemp(prefix="p2m4la-"), qdrant_image_ref=QDRANT_REF, controller=FakeController(tamper=True)), LA.LaunchError))
check("run_m4a_locked: ไม่ inject controller -> LaunchError",
      raises(lambda: LA.run_m4a_locked(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
             out_dir=tempfile.mkdtemp(prefix="p2m4la-"), qdrant_image_ref=QDRANT_REF, controller=object()), LA.LaunchError))
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
