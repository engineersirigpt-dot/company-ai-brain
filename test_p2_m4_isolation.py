"""
Unit test ของ p2_m4_isolation — isolation adapter (contract enforcer เหนือ fake driver, offline)
+ **capstone: M4a synthetic run เต็มด้วย adapter จริงทั้ง 4 ตัว** (isolation+provider+oracle+scorer) -> PUBLISHED/PASS

    python test_p2_m4_isolation.py
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
import p2_m4_isolation as ISO
import p2_m4_qdrant as QA
import p2_m4_runner as RUN
import p2_m4_scorer as SC
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

ISO_EP = "http://m4qd-iso:6333"
HANDLE = {"project_id": "proj-u", "network_id": "net-u", "volume_id": "vol-u",
          "collection_id": "coll-u", "endpoint": ISO_EP}


class FakeDriver:
    def __init__(s, *, handle=None, count=0, ports=0, is_prod=False):
        s._handle = handle if handle is not None else dict(HANDLE)
        s._count = count; s._ports = ports; s._prod = is_prod
        s.calls = []; s._marker = None; s.torn = 0
    def provision(s): s.calls.append("provision"); return dict(s._handle)
    def count(s): return s._count
    def published_ports(s): return s._ports
    def endpoint_is_production(s): return s._prod
    def write_marker(s, m): s.calls.append("write"); s._marker = m
    def read_marker(s): s.calls.append("read"); return s._marker
    def seed(s, c): s.calls.append("seed")
    def teardown(s): s.calls.append("teardown"); s.torn += 1


# ── isolation adapter: lifecycle + contract enforcement ───────────────────────
iso = ISO.QdrantDockerIsolation(driver=FakeDriver())
check("isolation: observe ก่อน provision -> IsolationError", raises(lambda: iso.observe_initial_count(), ISO.IsolationError))
h = iso.provision()
check("isolation: provision -> handle 5 คีย์ครบ", set(h) == {"project_id", "network_id", "volume_id", "collection_id", "endpoint"})
check("isolation: observe_initial_count = int 0 แท้", h and iso.observe_initial_count() == 0 and type(iso.observe_initial_count()) is int)
check("isolation: observe_published_ports = int 0 + endpoint_is_production = False แท้",
      iso.observe_published_ports() == 0 and iso.observe_endpoint_is_production() is False)
iso.write_marker("m4-run-uuid")
check("isolation: marker write->read round-trip", iso.read_marker() == "m4-run-uuid")
check("isolation: provision ซ้ำ -> IsolationError", raises(lambda: iso.provision(), ISO.IsolationError))
iso.teardown()
check("isolation: ops หลัง teardown -> IsolationError", raises(lambda: iso.observe_initial_count(), ISO.IsolationError))
iso.teardown()  # idempotent
check("isolation: teardown idempotent (driver.teardown ครั้งเดียว)", iso._driver.torn == 1)

# IsolationProof จาก observations ของ adapter -> ต้องผ่าน E.validate_m4_isolation_proof
iso2 = ISO.QdrantDockerIsolation(driver=FakeDriver()); h2 = iso2.provision()
iso2.write_marker("m4-run-uuid"); _rb = iso2.read_marker()
_proof = HN.build_isolation_proof(project_id=h2["project_id"], network_id=h2["network_id"], volume_id=h2["volume_id"],
                                  collection_id=h2["collection_id"], marker="m4-run-uuid", marker_readback=_rb,
                                  initial_point_count=iso2.observe_initial_count(),
                                  network_published_ports=iso2.observe_published_ports(),
                                  endpoint_is_production=iso2.observe_endpoint_is_production())
check("isolation: IsolationProof จาก adapter -> validate ผ่าน ([] errors)", E.validate_m4_isolation_proof(_proof) == [])

# contract violations (fail-closed)
_dup = {**HANDLE, "network_id": "proj-u"}                  # ids ไม่ distinct
check("isolation: handle ids ไม่ distinct -> IsolationError", raises(lambda: ISO.QdrantDockerIsolation(driver=FakeDriver(handle=_dup)).provision(), ISO.IsolationError))
_blank = {**HANDLE, "endpoint": "  "}
check("isolation: handle endpoint ว่าง -> IsolationError", raises(lambda: ISO.QdrantDockerIsolation(driver=FakeDriver(handle=_blank)).provision(), ISO.IsolationError))
_bi = ISO.QdrantDockerIsolation(driver=FakeDriver(count=True)); _bi.provision()
check("isolation: count = bool -> IsolationError (int แท้เท่านั้น)", raises(lambda: _bi.observe_initial_count(), ISO.IsolationError))
_bf = ISO.QdrantDockerIsolation(driver=FakeDriver(count=0.0)); _bf.provision()
check("isolation: count = float -> IsolationError", raises(lambda: _bf.observe_initial_count(), ISO.IsolationError))
_pd = FakeDriver(is_prod=True)
check("isolation: provision บน production endpoint -> IsolationError + teardown ถูกเรียก",
      raises(lambda: ISO.QdrantDockerIsolation(driver=_pd).provision(), ISO.IsolationError) and _pd.torn >= 1)
_nb = FakeDriver(is_prod=1)                                # non-bool
check("isolation: endpoint_is_production ไม่ใช่ bool -> IsolationError (provision abort)",
      raises(lambda: ISO.QdrantDockerIsolation(driver=_nb).provision(), ISO.IsolationError))
check("isolation: ไม่ inject driver -> IsolationError", raises(lambda: ISO.QdrantDockerIsolation(driver=None), ISO.IsolationError))

# adapter propagates driver cleanup failure (B2) — ไม่กลืน (runner ต้องเห็น -> ไม่ publish)
class _RaiseTeardown(FakeDriver):
    def teardown(s): s.calls.append("teardown"); raise ISO.CleanupUnconfirmed("cleanup ยืนยันไม่ได้")
_rt = ISO.QdrantDockerIsolation(driver=_RaiseTeardown()); _rt.provision()
check("isolation: driver teardown ล้ม -> adapter propagate CleanupUnconfirmed (ไม่กลืน)", raises(lambda: _rt.teardown(), ISO.CleanupUnconfirmed))

# ── DockerQdrantDriver (real seam) — offline-testable: image pin / **Docker-inspect identity** / cleanup accumulate ──
import json as _json
_IMG = "qdrant/qdrant@sha256:" + "d" * 64
_sess = {"s": None}
class FakeSess:
    def __init__(s, endpoint, collection, size): s._ep = endpoint; s._coll = collection; s.recreated = False; _sess["s"] = s
    def recreate_collection(s): s.recreated = True
    def count(s): return 0
    def write_marker(s, m): s._m = m
    def read_marker(s): return getattr(s, "_m", None)
    def seed(s, c): pass
def make_run(*, teardown_exc=None, networks=None):
    _nets = _json.dumps({"net-1": {"IPAddress": "10.0.0.5"}} if networks is None else networks)
    def run(cmd):
        if "create" in cmd: return "net-1" if "network" in cmd else "vol-1"
        if cmd[1] == "run": return "cont-1"
        if cmd[1] == "inspect": return _nets if "Networks" in " ".join(cmd) else "0"
        if "rm" in cmd:
            if teardown_exc is not None: raise teardown_exc
            return ""
        return ""
    return run
def _drv(**kw): return ISO.DockerQdrantDriver(session_factory=FakeSess, project_id="proj-1", qdrant_image=_IMG, **kw)

check("B3: DockerQdrantDriver qdrant_image ไม่ pin digest (:latest) -> IsolationError",
      raises(lambda: ISO.DockerQdrantDriver(run=make_run(), session_factory=FakeSess, project_id="proj-1", qdrant_image="qdrant/qdrant:latest"), ISO.IsolationError))
# B1: identity จาก Docker inspect — container ไม่อยู่บน internal network ที่สร้าง -> abort ก่อน mutate (session ไม่ถูกสร้าง)
_sess["s"] = None
_bad = _drv(run=make_run(networks={"other-net": {"IPAddress": "1.2.3.4"}}))
check("B1: container ไม่อยู่บน internal network (Docker inspect) -> abort ก่อน mutate (recreate ไม่ถูกเรียก)",
      raises(lambda: _bad.provision(), ISO.IsolationError) and _sess["s"] is None)
# B1 happy: อยู่บน network -> endpoint derive จาก Docker-inspect IP + recreate หลัง verify
_sess["s"] = None
_h = _drv(run=make_run()).provision()
check("B1: identity จาก Docker inspect -> endpoint = container IP (ไม่ใช่ค่า supplied) + recreate หลัง verify + 4 distinct ids",
      _h["endpoint"] == "http://10.0.0.5:6333" and _sess["s"].recreated is True and len({_h["project_id"], _h["network_id"], _h["volume_id"], _h["collection_id"]}) == 4)
# B2 cleanup
_d2 = _drv(run=make_run(teardown_exc=RuntimeError("docker daemon error"))); _d2.provision()
check("B2: teardown docker error -> CleanupUnconfirmed (สะสม, ไม่กลืน)", raises(lambda: _d2.teardown(), ISO.CleanupUnconfirmed))
_d3 = _drv(run=make_run(teardown_exc=RuntimeError("Error: No such container: cont-1"))); _d3.provision()
check("B2: teardown 'no such' -> idempotent OK (ไม่ raise)", _d3.teardown() is None)

# ── capstone: M4a synthetic run เต็ม (adapter จริง 4 ตัว) -> PUBLISHED/PASS ─────
_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
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
        nxt = start + limit if start + limit < len(s.points) else None
        return (s.points[start:start + limit], nxt)
def factory(points): return lambda endpoint: FakeSession(points, endpoint)
POINTS = [_Pt("A", _payload(["qc", "admin"], "ta"), 0.90), _Pt("B", _payload(["sales", "admin"], "tb"), 0.80),
          _Pt("S", _payload(["management"], "ts"), 0.99)]

class PinnedScorer:                                        # metadata ตรง PLAN pin
    def __init__(s): s.queries = []
    def metadata(s):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": "a" * 40,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H, "inference_config": dict(IC)}
    def score(s, q, texts): s.queries.append(q); return [{"ta": 2.0, "tb": 2.0}.get(t, 0.0) for t in texts]
class FakeClock:
    def __init__(s): s.n = 0
    def now_iso(s): s.n += 1; return "2026-08-05T05:0%d:00+07:00" % s.n

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

d = tempfile.mkdtemp(prefix="p2m4iso-")
_probe = QA.approved_probe_principal_factory(frozenset(PLAN["evaluated_roles"]))
_oplan = {"case-qc": {"effective_role": "qc", "point_ids": ["A", "S"]},
          "case-sales": {"effective_role": "sales", "point_ids": ["B", "S"]}}
_scorer = SC.build_m4_scorer(PLAN, loader=lambda plan: PinnedScorer())     # scorer factory + verify ก่อนใช้
_isodrv = FakeDriver()
_pt = types.SimpleNamespace(
    scorer=_scorer,
    isolation=ISO.QdrantDockerIsolation(driver=_isodrv),
    provider=QA.QdrantM4Provider(factory(POINTS), principal_factory=_probe, filter_adapter=_IDENTITY),
    oracle=QA.QdrantM4Oracle(factory(POINTS), observation_plan=_oplan, principal_factory=_probe),
    clock=FakeClock())
r = RUN.run_m4a(plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
                ports=_pt, out_dir=d, argv=["python", "p2_m4_runner.py"], stdout=b"ok", stderr=b"")
check("capstone: adapter จริง 4 ตัว -> RUN.run_m4a PUBLISHED", r["status"] == "PUBLISHED" and os.path.isfile(r["path"]))
check("capstone: evidence PASS (M4a synthetic mechanics ผ่านด้วย adapter จริง)", r["evidence"]["status"] == "PASS")
check("capstone: bundle re-validate ผ่าน public gate",
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, r["evidence"], r["receipt"]) == [],
      RP.validate_m4_preflight_bundle(PLAN, FROZEN, r["evidence"], r["receipt"]))
check("capstone: isolation lifecycle เต็ม (provision→observe→write→read→seed→teardown)",
      _isodrv.calls[0] == "provision" and "seed" in _isodrv.calls and _isodrv.torn == 1 and _isodrv.calls.index("write") < _isodrv.calls.index("read") < _isodrv.calls.index("seed"))
check("capstone: IsolationProof + independent_oracle PASS ใน evidence", E.validate_m4_isolation_proof(r["evidence"]["isolation_proof"]) == [] and r["evidence"]["independent_oracle"] == "PASS" and r["evidence"]["isolated_interlock"] == "PASS")
check("capstone: scorer เห็นเฉพาะ authorized query (ไม่มี sentinel ts ถึง model)", _scorer.queries == [QT1, QT2])
check("B4: M4a synthetic evidence = decision_eligible False (mechanics เท่านั้น — ใช้เป็น decision ไม่ได้)",
      r["evidence"]["decision_eligible"] is False)
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
