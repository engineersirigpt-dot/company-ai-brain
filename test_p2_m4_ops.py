"""
Unit test ของ p2_m4_ops — operational wrapper (pure/offline, fake ports)
PUBLISHED/DEGRADED/FAILED/CapabilityError · treat Cleanup/Durability exception เป็น authority ·
persist provenance ข้าม process

    python test_p2_m4_ops.py
"""
import io
import os
import shutil
import sys
import tempfile
import types

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_atomic as AT
import p2_eval as E
import p2_fs_probe as FS
import p2_m4_harness as HN
import p2_m4_ops as OPS
import p2_provenance as PV
import p2_reranker as RK
import p2_runplan as RP

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))

_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
VEC1, VEC2 = [0.1, 0.2, 0.3], [0.4, 0.5, 0.6]
QT1, QT2 = "คำถาม negation", "คำถาม table-row"
def _pairs(items): return [HN.component(p, t)["pair_sha256"] for p, t in items]
def _pl(r): return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
                    "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": r}
CORPUS = {"pa": {"source": "D1", "rerank_text": "alpha", "payload": _pl(["qc", "admin"])},
          "pb": {"source": "D2", "rerank_text": "beta", "payload": _pl(["sales", "admin"])}}


class PinnedScorer:
    def __init__(s, m): s.smap = m; s.queries = []
    def metadata(s):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": "a" * 40,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H, "inference_config": dict(IC)}
    def score(s, q, t): s.queries.append(q); return [s.smap.get(x, 0.0) for x in t]
class FakeIso:
    def __init__(s, initial_count=0): s._ic = initial_count; s.calls = []; s._m = None; s.torn = False
    def provision(s):
        s.calls.append("provision")
        return {"project_id": "p", "network_id": "n", "volume_id": "v", "collection_id": "coll-u", "endpoint": "http://iso:6333"}
    def observe_initial_count(s): s.calls.append("count"); return s._ic
    def observe_published_ports(s): return 0
    def observe_endpoint_is_production(s): return False
    def write_marker(s, m): s._m = m
    def read_marker(s): return s._m
    def seed(s, c): s.calls.append("seed")
    def teardown(s): s.torn = True
class FakeProvider:
    def __init__(s, by): s.by = by; s._b = None
    def bind(s, h): s._b = h
    def observed_target_identity(s): return {"collection_id": s._b["collection_id"], "endpoint": s._b["endpoint"]}
    def filtered_candidates(s, role, qv, limit): return list(s.by.get(role, []))
class FakeOracle:
    def __init__(s, u, v): s.u = u; s.v = v; s._b = None
    def bind(s, h): s._b = h
    def observed_target_identity(s): return {"collection_id": s._b["collection_id"], "endpoint": s._b["endpoint"]}
    def unfiltered_topn(s, qv, limit): return list(s.u.get(tuple(qv), []))
    def observe_visibility(s, role): return s.v[role]
class FakeClock:
    def __init__(s): s.n = 0
    def now_iso(s): s.n += 1; return "2026-08-05T05:0%d:00+07:00" % s.n


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
        "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC)}
PROV = {"qc": [("A", "ta")], "sales": [("B", "tb")]}
UNFIL = {tuple(VEC1): [("S", "ts"), ("A", "ta")], tuple(VEC2): [("S", "ts"), ("B", "tb")]}
VIS = {"qc": {"authorized_pairs": _pairs([("A", "ta")]), "sentinel_pairs": _pairs([("S", "ts")])},
       "sales": {"authorized_pairs": _pairs([("B", "tb")]), "sentinel_pairs": _pairs([("S", "ts")])}}
CASES = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1},
         {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": VEC2}]


class SecretScorer:                                   # metadata() raise exception ที่มี secret
    def metadata(s): raise RuntimeError("Authorization: Bearer TOP-SECRET-TOKEN")
    def score(s, q, t): return [0.0 for _ in t]
def _ports(iso=None, scorer=None):
    return types.SimpleNamespace(scorer=scorer or PinnedScorer({"ta": 2.0, "tb": 2.0}), isolation=iso or FakeIso(),
                                 provider=FakeProvider(PROV), oracle=FakeOracle(UNFIL, VIS), clock=FakeClock())
def _run(out_dir, log, ports, attempt_id="att-1"):
    return OPS.run_m4a_operational(provenance_log=log, attempt_id=attempt_id, now="2026-08-06T09:00:00+07:00", out_dir=out_dir,
                                   plan=PLAN, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
                                   ports=ports, argv=["python", "p2_m4_runner.py"], stdout=b"ok", stderr=b"")


# ── PUBLISHED + STARTED→terminal ledger ───────────────────────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
r = _run(d, log, _ports())
check("PUBLISHED status + durability + path + evidence/receipt", r["status"] == "PUBLISHED" and r["durability_mode"] in ("durable", "atomic-visibility-only") and os.path.isfile(r["path"]) and "evidence" in r)
_ev = PV.read_provenance(log)
check("ledger: STARTED ก่อน terminal PUBLISHED (attempt เดียว)", [x["event"] for x in _ev] == ["STARTED", "PUBLISHED"])
check("reconcile -> PUBLISHED", PV.reconcile(_ev)["att-1"] == "PUBLISHED")
shutil.rmtree(d, ignore_errors=True)

# ── FAILED (interlock ผิด → RunnerError) ──────────────────────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
r = _run(d, log, _ports(iso=FakeIso(initial_count=5)))
check("FAILED status + phase run + ไม่มี artifact", r["status"] == "FAILED" and r["phase"] == "run" and not os.path.exists(os.path.join(d, "run-1.bundle.json")))
check("FAILED ledger: STARTED + FAILED(error_type)", [x["event"] for x in PV.read_provenance(log)] == ["STARTED", "FAILED"] and PV.read_provenance(log)[1]["error_type"] == "RunnerError")
shutil.rmtree(d, ignore_errors=True)

# ── DEGRADED (durability fail หลัง publish — treat exception เป็น authority) ───
# probe เรียก AT._fsync_dir ครั้งแรก (ต้องผ่าน) แล้ว publish เรียกครั้งสอง (ให้ล้ม → DurabilityUnconfirmed)
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
_orig = AT._fsync_dir; _fc = {"n": 0}
def _fsync_2nd(p):
    _fc["n"] += 1
    if _fc["n"] >= 2:
        raise OSError("fsync boom")
    return _orig(p)
AT._fsync_dir = _fsync_2nd
try:
    r = _run(d, log, _ports())
finally:
    AT._fsync_dir = _orig
check("DEGRADED (จาก DurabilityUnconfirmed) + artifact ปรากฏแต่ไม่ report PUBLISHED", r["status"] == "DEGRADED" and r["error_type"] == "DurabilityUnconfirmed" and os.path.isfile(os.path.join(d, "run-1.bundle.json")))
check("DEGRADED ledger terminal", PV.reconcile(PV.read_provenance(log))["att-1"] == "DEGRADED")
shutil.rmtree(d, ignore_errors=True)

# ── CapabilityError (fs_probe fail → ไม่ provision/model) ──────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
_op = FS.probe_output_fs
FS.probe_output_fs = lambda out: (_ for _ in ()).throw(FS.CapabilityError("fs ไม่รองรับ"))
pt = _ports()
try:
    r = _run(d, log, pt)
finally:
    FS.probe_output_fs = _op
check("CapabilityError -> FAILED phase fs_probe + ไม่ provision/model", r["status"] == "FAILED" and r["phase"] == "fs_probe" and pt.isolation.calls == [] and pt.scorer.queries == [])

# ── B3: STARTED-append fail -> abort ก่อน run ; terminal-append fail -> PROVENANCE_UNCONFIRMED ──
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
_ap = PV.append_provenance
PV.append_provenance = lambda l, rec: (_ for _ in ()).throw(OSError("log write fail"))
pt = _ports()
try:
    r = _run(d, log, pt)
finally:
    PV.append_provenance = _ap
check("B3: STARTED append fail -> FAILED/provenance_started + ไม่ provision/model", r["status"] == "FAILED" and r["phase"] == "provenance_started" and pt.isolation.calls == [])
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
_cnt = {"n": 0}
def _fail_terminal(l, rec):
    _cnt["n"] += 1
    if _cnt["n"] == 2:
        raise OSError("log disk full")
    return _ap(l, rec)
PV.append_provenance = _fail_terminal
try:
    r = _run(d, log, _ports())
finally:
    PV.append_provenance = _ap
check("B3: terminal append fail หลัง publish -> PROVENANCE_UNCONFIRMED (ไม่ clean PUBLISHED)", r["status"] == "PROVENANCE_UNCONFIRMED" and "evidence" not in r)
shutil.rmtree(d, ignore_errors=True)

# ── M2: provenance ไม่เก็บ raw exception text (กัน credential/query leak) ──────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.jsonl")
r = _run(d, log, _ports(scorer=SecretScorer()))
_body = open(log, encoding="utf-8").read()
check("M2: exception ที่มี secret -> FAILED (error_type=RuntimeError) + log ไม่มี TOP-SECRET", r["status"] == "FAILED" and r["error_type"] == "RuntimeError" and "TOP-SECRET" not in _body and "Bearer" not in _body)
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
