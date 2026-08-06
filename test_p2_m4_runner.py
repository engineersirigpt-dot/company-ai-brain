"""
Unit test ของ p2_m4_runner — pure/injectable M4a runner (offline, fake ports)
happy publish ผ่าน public gate · **fail-before-mutate** (interlock/corpus/target ผิด → abort ก่อน write/seed/model) ·
corpus↔RunPlan bind · provider/oracle target bind · lifecycle (provision-fail teardown, teardown-before-publish) · run_id safe

    python test_p2_m4_runner.py
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
import p2_m4_harness as HN
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
VEC1, VEC2 = [0.1, 0.2, 0.3], [0.4, 0.5, 0.6]
QT1, QT2 = "คำถาม negation", "คำถาม table-row"
def _pairs(items): return [HN.component(p, t)["pair_sha256"] for p, t in items]
def _pl(roles): return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
                        "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
CORPUS = {"pa": {"source": "D1", "rerank_text": "alpha", "payload": _pl(["qc", "admin"])},
          "pb": {"source": "D2", "rerank_text": "beta", "payload": _pl(["sales", "admin"])}}
CORPUS_SHA = E.corpus_manifest_sha256(CORPUS)
OTHER_CORPUS = {"pz": {"source": "DX", "rerank_text": "zeta", "payload": _pl(["qc"])}}


class PinnedScorer:
    def __init__(self, smap): self.smap = smap; self.queries = []
    def metadata(self):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": "a" * 40,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": _H, "inference_config": dict(IC)}
    def score(self, q, texts): self.queries.append(q); return [self.smap.get(t, 0.0) for t in texts]
class MockScorer:
    def __init__(self, smap): self.smap = smap
    def score(self, q, texts): return [self.smap.get(t, 0.0) for t in texts]


class FakeIso:
    def __init__(self, *, initial_count=0, published_ports=0, is_prod=False, readback=None):
        self._ic, self._pp, self._prod, self._rb = initial_count, published_ports, is_prod, readback
        self.calls = []; self._marker = None; self.torn = False
    def provision(self):
        self.calls.append("provision")
        return {"project_id": "proj-u", "network_id": "net-u", "volume_id": "vol-u",
                "collection_id": "coll-u", "endpoint": "http://isolated-m4:6333"}
    def observe_initial_count(self): self.calls.append("count"); return self._ic
    def observe_published_ports(self): self.calls.append("ports"); return self._pp
    def observe_endpoint_is_production(self): self.calls.append("prod"); return self._prod
    def write_marker(self, m): self.calls.append("write"); self._marker = m
    def read_marker(self): self.calls.append("read"); return self._rb if self._rb is not None else self._marker
    def seed(self, corpus): self.calls.append("seed")
    def teardown(self): self.calls.append("teardown"); self.torn = True
class ProvisionRaises(FakeIso):
    def provision(self): self.calls.append("provision"); raise RuntimeError("provision boom")
class TeardownRaises(FakeIso):
    def teardown(self): self.calls.append("teardown"); self.torn = True; raise RuntimeError("teardown boom")
class CountBadTeardownRaises(FakeIso):
    def __init__(self): super().__init__(initial_count=5)
    def teardown(self): self.calls.append("teardown"); self.torn = True; raise RuntimeError("teardown boom")
class ProvBadTeardownRaises(ProvisionRaises):
    def teardown(self): self.calls.append("teardown"); self.torn = True; raise RuntimeError("teardown boom")

class FakeProvider:
    def __init__(self, by_role, target=None): self.by_role = by_role; self.seen = []; self._bound = None; self._t = target
    def bind(self, handle): self._bound = handle
    def observed_target_identity(self):
        return self._t if self._t is not None else {"collection_id": self._bound["collection_id"], "endpoint": self._bound["endpoint"]}
    def filtered_candidates(self, role, qv, limit): self.seen.append((role, tuple(qv))); return list(self.by_role.get(role, []))

class FakeOracle:
    def __init__(self, unfiltered_by_qv, vis_by_role, target=None): self.u = unfiltered_by_qv; self.v = vis_by_role; self._bound = None; self._t = target
    def bind(self, handle): self._bound = handle
    def observed_target_identity(self):
        return self._t if self._t is not None else {"collection_id": self._bound["collection_id"], "endpoint": self._bound["endpoint"]}
    def unfiltered_topn(self, qv, limit): return list(self.u.get(tuple(qv), []))
    def observe_visibility(self, role): return self.v[role]

class FakeClock:
    def __init__(self): self.n = 0
    def now_iso(self): self.n += 1; return "2026-08-05T05:0%d:00+07:00" % self.n


FROZEN = HN.build_frozen_manifest(
    cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_text=QT1, query_vector=VEC1,
                                     authorized_items=[("A", "ta")], sentinel_items=[("S", "ts")]),
           "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_text=QT2, query_vector=VEC2,
                                        authorized_items=[("B", "tb")], sentinel_items=[("S", "ts")])},
    required_categories=["negation", "table-row"], evaluated_roles=["qc", "sales"])
MAN = E.m4_case_manifest_sha256(FROZEN)
PLAN = {"run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5",
        "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "gate_tags": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"],
        "m4_case_manifest_sha256": MAN, "required_categories": ["negation", "table-row"],
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 1, "test_queries": 1},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": CORPUS_SHA, "retrieval_index_manifest_sha256": _H},
        "model_commit": "a" * 40, "tokenizer_commit": "a" * 40, "model_file_manifest_sha256": _H,
        "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC)}

SMAP = {"ta": 2.0, "tb": 2.0}
PROV = {"qc": [("A", "ta")], "sales": [("B", "tb")]}
UNFIL = {tuple(VEC1): [("S", "ts"), ("A", "ta")], tuple(VEC2): [("S", "ts"), ("B", "tb")]}
VIS = {"qc": {"authorized_pairs": _pairs([("A", "ta")]), "sentinel_pairs": _pairs([("S", "ts")])},
       "sales": {"authorized_pairs": _pairs([("B", "tb")]), "sentinel_pairs": _pairs([("S", "ts")])}}
CASES = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1},
         {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": VEC2}]


def ports(*, scorer=None, iso=None, provider=None, oracle=None):
    return types.SimpleNamespace(scorer=scorer or PinnedScorer(SMAP), isolation=iso or FakeIso(),
                                 provider=provider or FakeProvider(PROV), oracle=oracle or FakeOracle(UNFIL, VIS),
                                 clock=FakeClock())
def fresh(): return tempfile.mkdtemp(prefix="p2runner-")
def call(pt, out_dir, corpus=CORPUS, plan=PLAN):
    return RUN.run_m4a(plan=plan, frozen=FROZEN, cases=CASES, corpus=corpus, marker="m4-run-uuid",
                       ports=pt, out_dir=out_dir, argv=["python", "p2_m4_runner.py", "--preflight"], stdout=b"ok", stderr=b"")
def attempt(exc, *, iso=None, provider=None, oracle=None, scorer=None, corpus=CORPUS, plan=PLAN):
    d = fresh(); pt = ports(scorer=scorer, iso=iso, provider=provider, oracle=oracle)
    raised = raises(lambda: call(pt, d, corpus=corpus, plan=plan), exc)
    no_art = not any("run-1" in n for n in (os.listdir(d) if os.path.isdir(d) else []))
    shutil.rmtree(d, ignore_errors=True)
    return raised, no_art, pt


# ── happy path ────────────────────────────────────────────────────────────────
BASE = fresh(); ISO = FakeIso(); PT = ports(iso=ISO)
RESULT = call(PT, BASE)
check("run_m4a -> PUBLISHED (bundle file เดียว)", RESULT["status"] == "PUBLISHED" and os.path.isfile(RESULT["path"]) and RESULT["path"].endswith("run-1.bundle.json"))
check("bundle re-validate ผ่าน public gate", RP.validate_m4_preflight_bundle(PLAN, FROZEN, RESULT["evidence"], RESULT["receipt"]) == [], RP.validate_m4_preflight_bundle(PLAN, FROZEN, RESULT["evidence"], RESULT["receipt"]))
check("evidence status = PASS", RESULT["evidence"]["status"] == "PASS")
check("scorer ได้ query จริงของ case", PT.scorer.queries == [QT1, QT2])
check("provider bind + เห็นเฉพาะ role ที่ authorize", PT.provider._bound is not None and PT.provider.seen == [("qc", tuple(VEC1)), ("sales", tuple(VEC2))])
check("provenance: write ก่อน read", ISO.calls.index("write") < ISO.calls.index("read"))
check("provenance: observe interlock ก่อน write/seed", max(ISO.calls.index("count"), ISO.calls.index("ports"), ISO.calls.index("prod")) < ISO.calls.index("write") < ISO.calls.index("seed"))
check("provenance: readback มาจาก read_marker", RESULT["evidence"]["isolation_proof"]["marker_readback_sha256"] == HN._id_hash("m4-run-uuid"))
check("teardown ถูกเรียก (happy)", ISO.torn)

# ── B1: interlock ผิด -> abort ก่อน write/seed/model (fail-before-mutate) ──────
for label, iso in [("count!=0", FakeIso(initial_count=5)), ("ports!=0", FakeIso(published_ports=1)), ("production", FakeIso(is_prod=True))]:
    ok, na, pt = attempt(RUN.RunnerError, iso=iso)
    check(f"B1: {label} -> RunnerError + ไม่มี artifact", ok and na)
    check(f"B1: {label} -> ไม่ write/seed, provider/model ไม่ถูกเรียก", "write" not in iso.calls and "seed" not in iso.calls and pt.provider.seen == [] and pt.scorer.queries == [])
_mm = FakeIso(readback="TAMPERED")
ok, na, pt = attempt(RUN.RunnerError, iso=_mm)
check("B1: marker readback != written -> RunnerError + ไม่มี artifact", ok and na)
check("B1: marker mismatch -> write/read เกิด แต่ seed/model ไม่เกิด", "write" in _mm.calls and "read" in _mm.calls and "seed" not in _mm.calls and pt.scorer.queries == [])

# ── B2: corpus ต้องผูก RunPlan (ก่อน provision) ───────────────────────────────
ok, na, pt = attempt(RUN.RunnerError, corpus=OTHER_CORPUS)
check("B2: corpus_manifest_sha256 ไม่ตรง RunPlan -> RunnerError ก่อน provision", ok and na and pt.isolation.calls == [])
ok, _, pt = attempt(RUN.RunnerError, corpus=["not", "a", "dict"])
check("B2: corpus ไม่ใช่ dict -> RunnerError ก่อน provision", ok and pt.isolation.calls == [])

# ── B3: provider/oracle target ต้อง bind isolated handle ──────────────────────
ok, na, pt = attempt(RUN.RunnerError, provider=FakeProvider(PROV, target={"collection_id": "WRONG", "endpoint": "http://x"}))
check("B3: provider target != isolated handle -> abort ก่อน seed", ok and na and "seed" not in pt.isolation.calls and pt.scorer.queries == [])
ok, na, pt = attempt(RUN.RunnerError, oracle=FakeOracle(UNFIL, VIS, target={"collection_id": "WRONG", "endpoint": "http://x"}))
check("B3: oracle target != isolated handle -> abort ก่อน seed", ok and na and "seed" not in pt.isolation.calls)

# ── B4: lifecycle — provision fail / teardown fail ────────────────────────────
ok, na, pt = attempt(RuntimeError, iso=ProvisionRaises())
check("B4: provision raise -> teardown ยังถูกเรียก + ไม่มี artifact", ok and na and pt.isolation.torn)
ok, na, pt = attempt(RUN.RunnerError, iso=TeardownRaises())
check("B4: teardown raise หลัง work -> RunnerError + ไม่มี PASS artifact", ok and na)
check("B4: teardown-fail -> work รันจริง (teardown เกิดก่อน publish)", pt.scorer.queries == [QT1, QT2])

# ── B1(model boundary): provider ปล่อย sentinel -> PermissionError ────────────
ok, na, pt = attempt(PermissionError, provider=FakeProvider({"qc": [("S", "ts")], "sales": [("B", "tb")]}))
check("provider leak sentinel -> PermissionError + ไม่มี artifact + teardown", ok and na and pt.isolation.torn)

# ── oracle observed ไม่ตรง frozen -> publish refused ──────────────────────────
_bad = {"qc": {"authorized_pairs": _pairs([("B", "tb")]), "sentinel_pairs": _pairs([("S", "ts")])},
        "sales": VIS["sales"]}
ok, na, _ = attempt(AT.PublishRefused, oracle=FakeOracle(UNFIL, _bad))
check("oracle observed_authorized != frozen -> PublishRefused + ไม่มี artifact", ok and na)

# ── B2: frozen/case preflight — fail-before-mutate (ก่อน provision/scorer/seed/model) ────────
_case3 = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1},
          {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": VEC2},
          {"case_id": "case-extra", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1}]
def attempt_cases(exc, cases):
    d = fresh(); pt = ports()
    ok = raises(lambda: RUN.run_m4a(plan=PLAN, frozen=FROZEN, cases=cases, corpus=CORPUS, marker="m4-run-uuid",
                                    ports=pt, out_dir=d, argv=["x"], stdout=b"", stderr=b""), exc)
    shutil.rmtree(d, ignore_errors=True)
    return ok, pt
_bad_first = [{"case_id": "case-qc", "effective_role": "WRONG", "query_text": QT1, "query_vector": VEC1},
              {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": VEC2}]
_bad_last = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": VEC1},
             {"case_id": "case-sales", "effective_role": "sales", "query_text": "ผิด query", "query_vector": VEC2}]
ok, pt = attempt_cases(RUN.RunnerError, _bad_first)
check("B2: case แรก role ผิด -> RunnerError ก่อน provision (ไม่ seed/model)", ok and pt.isolation.calls == [] and pt.scorer.queries == [])
ok, pt = attempt_cases(RUN.RunnerError, _bad_last)
check("B2: case ท้าย query_text ผิด -> RunnerError ก่อน provision", ok and pt.isolation.calls == [] and pt.scorer.queries == [])
ok, pt = attempt_cases(RUN.RunnerError, CASES + [_case3[2]])
check("B2: case set เกิน frozen (extra) -> RunnerError ก่อน provision", ok and pt.isolation.calls == [])
ok, pt = attempt_cases(RUN.RunnerError, [CASES[0]])
check("B2: case set ขาด frozen (missing) -> RunnerError ก่อน provision", ok and pt.isolation.calls == [])
ok, pt = attempt_cases(RUN.RunnerError, [CASES[0], CASES[0]])
check("B2: case ซ้ำ -> RunnerError ก่อน provision", ok and pt.isolation.calls == [])
ok, na, pt = attempt(RUN.RunnerError, plan={**PLAN, "m4_case_manifest_sha256": "0" * 64})
check("B2: frozen digest != RunPlan -> RunnerError ก่อน provision", ok and pt.isolation.calls == [])

# ── M2: cleanup observability (work/provision fail + teardown fail) ───────────
def _capture(iso):
    d = fresh(); pt = ports(iso=iso); exc = None
    try:
        call(pt, d)
    except BaseException as e:
        exc = e
    na = not any("run-1" in n for n in (os.listdir(d) if os.path.isdir(d) else []))
    shutil.rmtree(d, ignore_errors=True)
    return exc, na
_e, _na = _capture(CountBadTeardownRaises())
check("M2: work fail + teardown fail -> primary=RunnerError + note cleanup + ไม่มี artifact",
      isinstance(_e, RUN.RunnerError) and any("cleanup" in n for n in getattr(_e, "__notes__", [])) and _na)
_e, _na = _capture(ProvBadTeardownRaises())
check("M2: provision fail + teardown fail -> primary=RuntimeError(provision) + note cleanup",
      isinstance(_e, RuntimeError) and not isinstance(_e, RUN.RunnerError) and any("cleanup" in n for n in getattr(_e, "__notes__", [])) and _na)

# ── mock scorer / M1 run_id / immutability / bad case ─────────────────────────
ok, na, pt = attempt(TypeError, scorer=MockScorer(SMAP))
check("mock scorer -> raise + ไม่มี artifact + ไม่ provision", ok and na and pt.isolation.calls == [])
ok, na, pt = attempt(RUN.RunnerError, plan={**PLAN, "run_id": "a/b"})
check("M1: run_id ไม่ปลอดภัยใน plan -> RunnerError ก่อน provision", ok and na and pt.isolation.calls == [])
ok, na, pt = attempt(RUN.RunnerError, plan={**PLAN, "run_id": "CON"})
check("M1: reserved run_id ใน plan -> RunnerError ก่อน provision (ไม่ seed/model)", ok and pt.isolation.calls == [] and pt.scorer.queries == [])
ok, na, pt = attempt(RUN.RunnerError, plan={**PLAN, "run_id": "run-1\n"})
check("M1: run_id trailing newline ใน plan -> RunnerError ก่อน provision", ok and pt.isolation.calls == [])
check("รัน run_id เดิมซ้ำ -> PublishRefused (immutable)", raises(lambda: call(ports(), BASE), AT.PublishRefused))
check("cases ว่าง -> RunnerError", raises(lambda: RUN.run_m4a(plan=PLAN, frozen=FROZEN, cases=[], corpus=CORPUS, marker="m", ports=ports(), out_dir=fresh(), argv=["x"], stdout=b"", stderr=b""), RUN.RunnerError))
_badrole = [{"case_id": "case-qc", "effective_role": "sales", "query_text": QT1, "query_vector": VEC1}]
check("effective_role ไม่ตรง frozen -> RunnerError", raises(lambda: RUN.run_m4a(plan=PLAN, frozen=FROZEN, cases=_badrole, corpus=CORPUS, marker="m", ports=ports(), out_dir=fresh(), argv=["x"], stdout=b"", stderr=b""), RUN.RunnerError))

shutil.rmtree(BASE, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
