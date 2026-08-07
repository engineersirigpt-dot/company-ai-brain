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
import p2_m4_runner as RUN
import p2_provenance as PV
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
class FakeClock:                                       # ISO+tz เพิ่มขึ้น (wrapper + runner ใช้ authority เดียวกัน)
    def __init__(s): s.n = 0
    def now_iso(s): s.n += 1; return "2026-08-05T05:0%d:00+07:00" % s.n
class BadClock:
    def now_iso(s): return "not-a-timestamp"
class AnomalyClock:                                   # valid 3 ครั้งแรก (STARTED+runner) แล้ว bad ที่ terminal
    def __init__(s): s.n = 0
    def now_iso(s):
        s.n += 1
        return "2026-08-05T05:0%d:00+07:00" % s.n if s.n <= 3 else "BAD-TERMINAL-CLOCK"


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
AID = "att-00000001"
def _ports2(clock=None, iso=None, scorer=None):        # ports + clock override (M3.1: wrapper ใช้ ports.clock)
    p = _ports(iso=iso, scorer=scorer)
    if clock is not None:
        p.clock = clock
    return p
def _run(out_dir, log, ports, plan=PLAN, attempt_id=AID):
    return OPS.run_m4a_operational(provenance_db=log, attempt_id=attempt_id, out_dir=out_dir,
                                   plan=plan, frozen=FROZEN, cases=CASES, corpus=CORPUS, marker="m4-run-uuid",
                                   ports=ports, argv=["python", "p2_m4_runner.py"], stdout=b"ok", stderr=b"")


# ── PUBLISHED + ledger + M3 metadata binding ──────────────────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r = _run(d, log, _ports())
check("PUBLISHED + durability + path + evidence/receipt", r["status"] == "PUBLISHED" and r["durability_mode"] in ("durable", "atomic-visibility-only") and os.path.isfile(r["path"]) and "evidence" in r)
_evs = PV.read_provenance(log)
check("ledger STARTED→PUBLISHED + reconcile", [x["event"] for x in _evs] == ["STARTED", "PUBLISHED"] and PV.reconcile(_evs)[AID] == "PUBLISHED")
_st, _tm = _evs[0], _evs[1]
check("M3: STARTED bind run_manifest/m4_manifest/model/image/out_dir + started_at", _st["run_manifest_sha256"] == RP.run_manifest_sha256(PLAN) and _st["m4_case_manifest_sha256"] == PLAN["m4_case_manifest_sha256"] and _st["model_revision"] == PLAN["model_commit"] and _st["image_digest"] == PLAN["image_digest"] and _st["out_dir_realpath"] == os.path.realpath(d) and _st["started_at"] == "2026-08-05T05:01:00+07:00")
check("M3: terminal bind capability + artifact/evidence/receipt digest + finished_at (แยก)", _tm["capability"]["hardlink_no_clobber"] and _tm["artifact_sha256"] == __import__("hashlib").sha256(open(r["path"], "rb").read()).hexdigest() and _tm["evidence_body_sha256"] == r["evidence"]["evidence_body_sha256"] and _tm["run_receipt_sha256"] == r["evidence"]["run_receipt_sha256"] and _tm["finished_at"] == "2026-08-05T05:04:00+07:00")
check("M3.1: wrapper ใช้ clock authority เดียวกับ runner + monotonic + ไม่มี clock_anomaly", _st["started_at"] == "2026-08-05T05:01:00+07:00" and _tm["finished_at"] > _st["started_at"] and "clock_anomaly" not in _tm)
# B3: export diagnostic JSONL snapshot ผูกกับ db + receipt (path injective: run_id.<safe>.<hash>)
_exp = r.get("provenance_export"); _expath = OPS._provenance_export_path(os.path.realpath(d), "run-1", AID)
check("B3: terminal export provenance JSONL (atomic, injective path) + receipt (row_count/max_seq/jsonl_sha256) ผูก db",
      isinstance(_exp, dict) and _exp["path"] == _expath and os.path.isfile(_expath) and _exp["row_count"] == 2 and _exp["max_seq"] == 2 and E._is_sha256(_exp["jsonl_sha256"]))
import json as _json
_exlines = [l for l in open(_expath, encoding="utf-8").read().split("\n") if l]
check("B3: export JSONL = ledger snapshot (2 events, reconcile ตรง) + digest ตรง receipt",
      len(_exlines) == 2 and PV.reconcile([_json.loads(l) for l in _exlines])[AID] == "PUBLISHED" and __import__("hashlib").sha256(open(_expath, "rb").read()).hexdigest() == _exp["jsonl_sha256"])
shutil.rmtree(d, ignore_errors=True)

# ── B3: retry same run_id, ต่าง attempt_id -> export คนละ path (retry-safe, ไม่ชน no-clobber) ──
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r1 = _run(d, log, _ports(iso=FakeIso(initial_count=5)), attempt_id="att-first0001")     # attempt แรก FAILED (interlock)
r2 = _run(d, log, _ports(), attempt_id="att-second002")                                 # attempt สอง PUBLISHED (run_id เดิม)
_e1 = r1.get("provenance_export"); _e2 = r2.get("provenance_export")
check("B3: retry run_id เดิม ต่าง attempt -> export คนละ path (ไม่ชน) + attempt สองไม่ถูกกลบ + ไม่มี export_error",
      r1["status"] == "FAILED" and r2["status"] == "PUBLISHED" and isinstance(_e1, dict) and isinstance(_e2, dict)
      and _e1["path"] != _e2["path"] and os.path.isfile(_e1["path"]) and os.path.isfile(_e2["path"])
      and "provenance_export_error" not in r2 and "provenance_export_error" not in r1)
shutil.rmtree(d, ignore_errors=True)

# ── M1: attempt filename injective — 'att:...' กับ 'att_...' (valid ทั้งคู่) ต้องไม่ชน path เดียวกัน ──
check("M1: export path injective — att:0000001 vs att_0000001 -> path ต่างกัน (colon/underscore ไม่ชน)",
      OPS._provenance_export_path("/o", "run-1", "att:0000001") != OPS._provenance_export_path("/o", "run-1", "att_0000001"))
check("M1: export path deterministic (input เดิม -> path เดิม)",
      OPS._provenance_export_path("/o", "run-1", "att:0000001") == OPS._provenance_export_path("/o", "run-1", "att:0000001"))

# ── M3.1: clock ไม่น่าเชื่อ (STARTED) -> FAILED/clock ก่อน provision ───────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
pt = _ports2(clock=BadClock())
r = _run(d, log, pt)
check("M3.1: STARTED clock ไม่ใช่ ISO+tz -> FAILED/clock + ไม่ provision + ไม่มี STARTED", r["status"] == "FAILED" and r["phase"] == "clock" and pt.isolation.calls == [] and PV.read_provenance(log) == [])
shutil.rmtree(d, ignore_errors=True)

# ── M3.1: terminal clock anomaly -> DEGRADED (load-bearing: status/reconcile ไม่ใช่ clean PUBLISHED) ─
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r = _run(d, log, _ports2(clock=AnomalyClock()))
_ae1 = PV.read_provenance(log)[1]
check("M3.1: terminal clock invalid -> DEGRADED/clock_anomaly (ไม่ clean PUBLISHED + ไม่แนบ evidence)", r["status"] == "DEGRADED" and r["phase"] == "clock_anomaly" and r.get("clock_anomaly") is True and "evidence" not in r)
check("M3.1: anomaly load-bearing ที่ ledger -> event/status=DEGRADED + reconcile=DEGRADED (gate ด้วย status พอ)", _ae1["event"] == "DEGRADED" and _ae1["status"] == "DEGRADED" and _ae1.get("clock_anomaly") is True and PV.reconcile(PV.read_provenance(log))[AID] == "DEGRADED")
shutil.rmtree(d, ignore_errors=True)

# ── M3.2: PUBLISHED fail-closed — verify final bundle จากดิสก์ ─────────────────
# _verify_published: valid bundle -> 64-hex digests ; tampered/invalid -> ValueError
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r = _run(d, log, _ports())
_vb = OPS._verify_published(PLAN, FROZEN, r["path"])
check("M3.2: _verify_published(valid) -> artifact 64-hex + digests", len(_vb["artifact_sha256"]) == 64 and E._is_sha256(_vb["evidence_body_sha256"]) and E._is_sha256(_vb["run_receipt_sha256"]))
with open(r["path"], "w", encoding="utf-8") as f:
    f.write('{"evidence":{"x":1},"receipt":{"y":2}}')     # tamper บนดิสก์
check("M3.2: _verify_published(tampered) -> ValueError (re-run public gate จับ)", raises(lambda: OPS._verify_published(PLAN, FROZEN, r["path"]), ValueError))
shutil.rmtree(d, ignore_errors=True)
# wrapper: run สำเร็จแต่ final bundle invalid/หาย -> FAILED/verify_publish (ไม่ clean PUBLISHED)
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
_bad = os.path.join(d, "run-1.bundle.json"); open(_bad, "w", encoding="utf-8").write("{not a bundle}")
_orig_run = RUN.run_m4a
RUN.run_m4a = lambda **kw: {"status": "PUBLISHED", "path": _bad, "durability": "atomic-visibility-only",
                            "evidence": {"evidence_body_sha256": "a" * 64, "run_receipt_sha256": "a" * 64}, "receipt": {}}
try:
    r = _run(d, log, _ports())
finally:
    RUN.run_m4a = _orig_run
check("M3.2: final bundle invalid บนดิสก์ -> FAILED/verify_publish (ไม่ clean PUBLISHED + ไม่แนบ evidence)", r["status"] == "FAILED" and r["phase"] == "verify_publish" and "evidence" not in r and PV.reconcile(PV.read_provenance(log))[AID] == "FAILED")
shutil.rmtree(d, ignore_errors=True)
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
# path ตรง run_id ใต้ out_dir (ผ่าน exact-path guard) แต่ไฟล์ไม่มี -> read fail ใน _verify_published
RUN.run_m4a = lambda **kw: {"status": "PUBLISHED", "path": os.path.join(d, "run-1.bundle.json"), "durability": "durable",
                            "evidence": {"evidence_body_sha256": "a" * 64, "run_receipt_sha256": "a" * 64}, "receipt": {}}
try:
    r = _run(d, log, _ports())
finally:
    RUN.run_m4a = _orig_run
check("M3.2: final bundle หาย (read fail) -> FAILED/verify_publish", r["status"] == "FAILED" and r["phase"] == "verify_publish")
shutil.rmtree(d, ignore_errors=True)

# ── M3.2-B: malformed runner result (ไม่มี path) -> FAILED/run_result_malformed (ไม่ crash) ──
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
RUN.run_m4a = lambda **kw: {"status": "PUBLISHED", "durability": "durable", "evidence": {}, "receipt": {}}   # ไม่มี path
try:
    r = _run(d, log, _ports())
finally:
    RUN.run_m4a = _orig_run
check("M3.2-B: result ไม่มี path -> FAILED/run_result_malformed (normalize, ไม่ crash + ปิด attempt)", r["status"] == "FAILED" and r["phase"] == "run_result_malformed" and PV.reconcile(PV.read_provenance(log))[AID] == "FAILED")
shutil.rmtree(d, ignore_errors=True)

# ── M3.2-B: bundle valid แต่ path นอก out_dir -> FAILED/run_result_malformed (isolation contract, ไม่แนบ payload) ──
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
outside = tempfile.mkdtemp(prefix="p2ops-out-")
_ob = os.path.join(outside, "run-1.bundle.json")
open(_ob, "w", encoding="utf-8").write('{"evidence":{"e":1},"receipt":{"r":1}}')   # valid bundle แต่ผิด directory
RUN.run_m4a = lambda **kw: {"status": "PUBLISHED", "path": _ob, "durability": "durable",
                            "evidence": {"evidence_body_sha256": "a" * 64, "run_receipt_sha256": "a" * 64}, "receipt": {"k": 1}}
try:
    r = _run(d, log, _ports())
finally:
    RUN.run_m4a = _orig_run
check("M3.2-B: path นอก out_dir -> FAILED/run_result_malformed + ไม่ verify + ไม่แนบ evidence/receipt", r["status"] == "FAILED" and r["phase"] == "run_result_malformed" and "evidence" not in r and "receipt" not in r and PV.reconcile(PV.read_provenance(log))[AID] == "FAILED")
shutil.rmtree(d, ignore_errors=True); shutil.rmtree(outside, ignore_errors=True)

# ── M2: out_dir ถูก retarget (symlink/junction swap) ระหว่าง run -> FAILED/out_dir_retargeted ──
# _canon เรียกครั้งเดียวตอน STARTED (bind) + อีกครั้งตอน re-verify ; ให้ค่าต่างกัน = จำลอง swap
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
_rc = OPS._canon; _cc = {"n": 0}
def _canon_swap(p):
    _cc["n"] += 1
    return _rc(p) if _cc["n"] == 1 else _rc(p) + "-SWAPPED"
OPS._canon = _canon_swap
try:
    r = _run(d, log, _ports())
finally:
    OPS._canon = _rc
check("M2: out_dir retarget ระหว่าง run -> FAILED/out_dir_retargeted (ไม่ bind artifact target ใหม่ + ไม่แนบ evidence)", r["status"] == "FAILED" and r["phase"] == "out_dir_retargeted" and "evidence" not in r and PV.reconcile(PV.read_provenance(log))[AID] == "FAILED")
check("M2: STARTED bind out_dir_realpath = canonical ตอนเริ่ม (ค่าเดียวใช้ทั้ง probe/runner/verify)", PV.read_provenance(log)[0]["out_dir_realpath"] == _rc(d))
shutil.rmtree(d, ignore_errors=True)

# ── B3.1: attempt_id generate/validate ────────────────────────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r = _run(d, log, _ports(), attempt_id=None)
check("B3.1: attempt_id=None -> generate (crypto-random) + PUBLISHED", r["status"] == "PUBLISHED" and r["attempt_id"].startswith("att-") and len(r["attempt_id"]) >= 12)
check("B3.1: generated attempt_id ใน ledger ตรงกัน", PV.read_provenance(log)[0]["attempt_id"] == r["attempt_id"])
check("B3.1: attempt_id ไม่ปลอดภัย -> ValueError", raises(lambda: _run(tempfile.mkdtemp(prefix="p2ops-"), os.path.join(d, "x.db"), _ports(), attempt_id="bad id!"), ValueError))
shutil.rmtree(d, ignore_errors=True)

# ── FAILED (interlock ผิด → RunnerError) ──────────────────────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r = _run(d, log, _ports(iso=FakeIso(initial_count=5)))
check("FAILED phase run + ไม่มี artifact + ledger", r["status"] == "FAILED" and r["phase"] == "run" and not os.path.exists(os.path.join(d, "run-1.bundle.json")) and PV.reconcile(PV.read_provenance(log))[AID] == "FAILED")
shutil.rmtree(d, ignore_errors=True)

# ── plan invalid -> FAILED/plan_invalid (STARTED plan_valid=False, ไม่ provision) ──
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
pt = _ports()
r = _run(d, log, pt, plan={**PLAN, "run_id": "bad id!"})
check("plan invalid -> FAILED/plan_invalid + ไม่ provision + STARTED plan_valid=False", r["status"] == "FAILED" and r["phase"] == "plan_invalid" and pt.isolation.calls == [] and PV.read_provenance(log)[0]["plan_valid"] is False)
shutil.rmtree(d, ignore_errors=True)

# ── DEGRADED (durability fail หลัง publish) ───────────────────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
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
check("DEGRADED (DurabilityUnconfirmed) + artifact ปรากฏแต่ไม่ report PUBLISHED", r["status"] == "DEGRADED" and r["error_type"] == "DurabilityUnconfirmed" and os.path.isfile(os.path.join(d, "run-1.bundle.json")) and PV.reconcile(PV.read_provenance(log))[AID] == "DEGRADED")
shutil.rmtree(d, ignore_errors=True)

# ── CapabilityError (fs_probe fail → ไม่ provision/model) ──────────────────────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
_op = FS.probe_output_fs
FS.probe_output_fs = lambda out: (_ for _ in ()).throw(FS.CapabilityError("fs ไม่รองรับ"))
pt = _ports()
try:
    r = _run(d, log, pt)
finally:
    FS.probe_output_fs = _op
check("CapabilityError -> FAILED/fs_probe + ไม่ provision/model", r["status"] == "FAILED" and r["phase"] == "fs_probe" and pt.isolation.calls == [] and pt.scorer.queries == [])

# ── B3: STARTED-append fail -> abort ; terminal-append fail -> PROVENANCE_UNCONFIRMED ──
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
_ae = PV.append_event
PV.append_event = lambda l, rec: (_ for _ in ()).throw(OSError("log write fail"))
pt = _ports()
try:
    r = _run(d, log, pt)
finally:
    PV.append_event = _ae
check("B3: STARTED append fail -> FAILED/provenance_started + ไม่ provision", r["status"] == "FAILED" and r["phase"] == "provenance_started" and pt.isolation.calls == [])
# B1.3: STARTED append indeterminate (COMMIT ambiguous) -> PROVENANCE_INDETERMINATE (ไม่ใช่ FAILED retryable) + ไม่ provision
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
PV.append_event = lambda l, rec: (_ for _ in ()).throw(PV.ProvenanceIndeterminate("commit ambiguous"))
pt = _ports()
try:
    r = _run(d, log, pt)
finally:
    PV.append_event = _ae
check("B1.3: STARTED indeterminate -> PROVENANCE_INDETERMINATE + ไม่ provision (ไม่ใช่ FAILED retryable)",
      r["status"] == "PROVENANCE_INDETERMINATE" and r["phase"] == "provenance_started" and pt.isolation.calls == [])
shutil.rmtree(d, ignore_errors=True)
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
_cnt = {"n": 0}
def _fail_terminal(l, rec):
    _cnt["n"] += 1
    if _cnt["n"] == 2:
        raise OSError("log disk full")
    return _ae(l, rec)
PV.append_event = _fail_terminal
try:
    r = _run(d, log, _ports())
finally:
    PV.append_event = _ae
check("B3: terminal append fail -> PROVENANCE_UNCONFIRMED (ไม่ clean PUBLISHED)", r["status"] == "PROVENANCE_UNCONFIRMED" and "evidence" not in r)
shutil.rmtree(d, ignore_errors=True)

# ── M2: provenance ไม่เก็บ raw exception text (อ่าน db ดิบแบบ binary-safe) ─────
d = tempfile.mkdtemp(prefix="p2ops-"); log = os.path.join(d, "prov.db")
r = _run(d, log, _ports(scorer=SecretScorer()))
_raw = open(log, "rb").read()                              # SQLite db เป็น binary — เช็ค secret ไม่โผล่ทั้งไฟล์
check("M2: exception มี secret -> FAILED(error_type=RuntimeError) + db ไม่มี TOP-SECRET/Bearer", r["status"] == "FAILED" and r["error_type"] == "RuntimeError" and b"TOP-SECRET" not in _raw and b"Bearer" not in _raw)
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
