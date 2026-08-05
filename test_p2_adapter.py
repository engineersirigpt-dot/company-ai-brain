"""
Unit test ของ P2 evidence adapter — **pure section รันด้วย dependency ขั้นต่ำของ adapter** (ไม่ลาก qdrant_client)
+ integration section (real p2_harness) แบบ guarded (skip ถ้าไม่มี qdrant_client)

pure: build_quality_rows join frozen cases (identity authoritative) + reject + M3 N-key + output ผ่าน RP validators
integration: real run_ranking → adapter (พิสูจน์ raw shape ตรงกับ harness จริง)

    python test_p2_adapter.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import copy

import p2_runplan as RP
import p2_evidence_adapter as A
import p2_pin as PIN

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn):
    try:
        fn(); return False
    except (ValueError, KeyError):
        return True


# ── synthetic raw (เลียนแบบ output ของ p2_harness.run_ranking; harness ใส่ intent/role/tags ที่ adapter ไม่เชื่อ) ──
def raw_row(qid, iid, tags, dense, rr, fused, role="qc"):
    return {"query_id": qid, "role": role, "intent_id": iid,
            "candidate_recall@n": 1.0, "n_candidates": 3,
            "arms": {"dense": {"ndcg@5": dense}, "rerank": {"ndcg@5": rr}, "fused": {"ndcg@5": fused}}}
def raw_ranking(rows):
    return {"kind": "mechanics-ranking-unapproved", "approved": False, "decision_eligible": False,
            "status": "COMPLETE", "top_n": 10, "n_expected": len(rows), "n_completed": len(rows),
            "scorer": {"model": "mock"}, "aggregate": {}, "per_query": rows}
def fcase(i, tag):
    return {"query_id": f"tq{i}", "intent_id": f"ti{i}", "role": "qc", "challenge_tags": [tag],
            "split": "test", "case_type": "ranking"}

TAGS = ["negation", "table-row", "sibling-hard-negative"]
CASES = [fcase(i, TAGS[i]) for i in range(3)]
# raw โกหก intent/role/tags → adapter ต้องยึด frozen
RAW = raw_ranking([raw_row("tq0", "LIE0", ["evil"], 0.5, 0.9, 0.9),
                   raw_row("tq1", "LIE1", ["evil"], 0.6, 0.8, 0.85),
                   raw_row("tq2", "LIE2", ["evil"], 0.7, 0.75, 0.8)])

# ── build_quality_rows: join + identity authoritative ──────────────────────────
rows = A.build_quality_rows(RAW, CASES)
check("quality rows ครบ 3 + arms.ndcg@5 finite", len(rows) == 3 and all(RP._is_unit_float(r["arms"][a]["ndcg@5"]) for r in rows for a in ("dense", "rerank", "fused")))
check("identity/tags จาก frozen cases (ไม่เชื่อ raw ที่โกหก)",
      all(rows[i]["intent_id"] == f"ti{i}" and rows[i]["role"] == "qc" and rows[i]["challenge_tags"] == [TAGS[i]] for i in range(3)))

# ── reject fake/missing/extra/dup/arm/ndcg/kind ────────────────────────────────
def _mut(fn):
    r = copy.deepcopy(RAW); fn(r["per_query"]); return r
check("reject: query_id ไม่อยู่ใน frozen", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq[0].__setitem__("query_id", "nope")), CASES)))
check("reject: missing query (2 rows)", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq.pop()), CASES)))
check("reject: duplicate query_id", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq.append(dict(pq[0]))), CASES)))
check("reject: arm ขาด (fused)", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq[0]["arms"].pop("fused")), CASES)))
check("reject: ndcg@5 ไม่ finite", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq[0]["arms"]["dense"].__setitem__("ndcg@5", float("nan"))), CASES)))
check("reject: kind ไม่ใช่ run_ranking", raises(lambda: A.build_quality_rows({"per_query": []}, CASES)))
check("reject: approved != False", raises(lambda: A.build_quality_rows({**RAW, "approved": True}, CASES)))

# ── M3: N-key normalization (ไม่ truncate/overwrite เงียบ) ──────────────────────
_v = {"point_recall": 0.9, "doc_recall": 0.9, "candidate_hit": 1.0, "completed_queries": 1, "completed_intents": 1}
_ROOTX = "a" * 64   # dummy root สำหรับทดสอบ key coercion (validate ตัวจริงอยู่ decide_p2)
check("M3: dev key 10.5 (float) -> reject", raises(lambda: A.build_dev_evidence({10.5: dict(_v), "10": dict(_v)}, _ROOTX)))
check("M3: dev key '10.0' -> reject", raises(lambda: A.build_dev_evidence({"10.0": dict(_v)}, _ROOTX)))
check("M3: dev key ' 10' (whitespace) -> reject", raises(lambda: A.build_dev_evidence({" 10": dict(_v)}, _ROOTX)))
check("M3: dev key True (bool) -> reject", raises(lambda: A.build_dev_evidence({True: dict(_v)}, _ROOTX)))
check("M3: dev key '010' (leading zero) -> reject", raises(lambda: A.build_dev_evidence({"010": dict(_v)}, _ROOTX)))
check("M3: dev key collision {10,'10'} -> reject", raises(lambda: A.build_dev_evidence({10: dict(_v), "10": dict(_v)}, _ROOTX)))
check("M3: dev key '10'/int 10 (canonical) -> ok", set(A.build_dev_evidence({"10": dict(_v), 20: dict(_v)}, _ROOTX)["by_n"]) == {10, 20})

# ── output ผ่าน validators ของ p2_runplan (consumer เดียวกับ decide_p2) ─────────
_H = "a" * 64
PLAN = {"run_id": "r", "benchmark_contract_version": RP.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5",
        "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "gate_tags": list(TAGS), "evaluated_roles": ["qc"],
        "m4_case_manifest_sha256": "a" * 64, "required_categories": ["negation"],
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 3, "test_queries": 3},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H},
        "model_commit": "b" * 40, "tokenizer_commit": "b" * 40, "model_file_manifest_sha256": _H,
        "image_digest": "sha256:" + "c" * 64,
        "inference_config": {"model_name": PIN.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}}
ROOT = RP.run_manifest_sha256(PLAN)
EC = PLAN["expected_counts"]

dev = A.build_dev_evidence({n: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0,
                                "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}, ROOT)
check("build_dev_evidence -> validate_dev_evidence ผ่าน", RP.validate_dev_evidence(dev, ROOT, EC) == [], RP.validate_dev_evidence(dev, ROOT, EC))
sel = RP.select_n(dev, ROOT, EC)
sd = RP.selection_digest(ROOT, dev["raw_result_digest"], sel["selected_n"])

quality = A.build_quality_evidence(RAW, CASES, ROOT, sd, sel["selected_n"])
check("build_quality_evidence -> validate_quality_evidence ผ่าน (ผูก selection)",
      RP.validate_quality_evidence(quality, ROOT, EC, sd, sel["selected_n"]) == [], RP.validate_quality_evidence(quality, ROOT, EC, sd, sel["selected_n"]))
_resolved, _jerr = RP._resolve_quality_rows(quality["per_query"], CASES)
check("build_quality_evidence -> _resolve_quality_rows (join frozen) ไม่มี error", _jerr == [] and len(_resolved) == 3, _jerr)

stages = {"candidate_retrieval": [10] * 5, "rerank": [100] * 5, "rrf": [1] * 5, "total": [150] * 5}
latency = A.build_latency_evidence(stages, ROOT, sd, sel["selected_n"], warmup=2)
check("build_latency_evidence -> validate_latency_evidence ผ่าน",
      RP.validate_latency_evidence(latency, ROOT, EC["test_queries"], RP.DEFAULT_THRESHOLDS, sd, sel["selected_n"]) == [])

check("evidence ไม่มี approved/decision field (approval = decide_p2 เท่านั้น)",
      not any(k in quality or k in dev or k in latency for k in ("approved", "decision_eligible", "arm")))

# ── integration (real p2_harness) — guard เจาะจง optional dep เท่านั้น (M1) ──────
# skip เฉพาะเมื่อไม่มี qdrant_client ; ถ้ามีแล้ว p2_harness import ล้ม = ให้ error จริง (ไม่กลืนเป็น skip)
import importlib.util
_INTEG = importlib.util.find_spec("qdrant_client") is not None
if not _INTEG:
    print("SKIP integration: ไม่มี qdrant_client (optional dep) — pure section ครอบ contract แล้ว")
else:
    import policy as P
    import p2_reranker as RK
    import p2_harness as H

if _INTEG:
    class _Res:
        def __init__(self, pts): self.points = pts
    class _Pt:
        def __init__(self, pid, payload, score): self.id = pid; self.payload = payload; self.score = score
    class FakeQdrant:
        def __init__(self, points): self.points = points
        def query_points(self, collection_name, query, query_filter, limit, with_payload):
            hit = [p for p in self.points if P.matches_policy(p.payload, query_filter)]
            return _Res(sorted(hit, key=lambda p: -p.score)[:limit])
    def _pl(roles):
        return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
                "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
    def _pt(pid, score, heading, text):
        p = _pl(["qc", "admin"]); p.update({"heading": heading, "text": text, "source": f"D-{pid}"})
        return _Pt(pid, p, score)
    _fake = FakeQdrant([_pt("A", 0.9, "HA", "alpha"), _pt("B", 0.8, "HB", "beta"), _pt("C", 0.7, "HC", "gamma")])
    _scorer = RK.MockScorer({"HA alpha": 2.0, "HB beta": 1.0, "HC gamma": 3.0})
    _icases = [{**fcase(0, "negation"), "query": "HC gamma", "relevance": {"C": 3, "A": 1}},
               {**fcase(1, "table-row"), "query": "HA alpha", "relevance": {"A": 3}},
               {**fcase(2, "sibling-hard-negative"), "query": "HB beta", "relevance": {"B": 2}}]
    _raw = H.run_ranking(_fake, "c", _icases, lambda role: P.ServicePrincipal("s", ("qc",), True, "enforce"),
                         lambda q: [0.0] * 4, _scorer, top_n=10, filter_adapter=lambda spec: spec)
    _irows = A.build_quality_rows(_raw, _icases)
    check("integration: real run_ranking → adapter join ได้ (shape ตรง harness จริง)",
          len(_irows) == 3 and all(_irows[i]["intent_id"] == f"ti{i}" and _irows[i]["challenge_tags"] == [TAGS[i]] for i in range(3)))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
