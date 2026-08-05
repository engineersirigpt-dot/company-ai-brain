"""
Unit test ของ P2 evidence adapter — pure/offline (fake Qdrant + MockScorer)
พิสูจน์ producer(p2_harness) → adapter → **ผ่าน validators ของ p2_runplan** (consumer เดียวกับ decide_p2)
+ identity/tags authoritative จาก frozen cases + reject fake/missing/extra/dup/arm/ndcg

    python test_p2_adapter.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import copy

import policy as P
import p2_provider as PROV
import p2_reranker as RK
import p2_harness as H
import p2_runplan as RP
import p2_evidence_adapter as A

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn):
    try:
        fn(); return False
    except (ValueError, KeyError):
        return True


# ── fake Qdrant + MockScorer (เหมือน test_p2_harness) ──────────────────────────
class _Res:
    def __init__(self, pts): self.points = pts
class _Pt:
    def __init__(self, pid, payload, score): self.id = pid; self.payload = payload; self.score = score
class FakeQdrant:
    def __init__(self, points): self.points = points
    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        hit = [p for p in self.points if P.matches_policy(p.payload, query_filter)]
        return _Res(sorted(hit, key=lambda p: -p.score)[:limit])

def pl(roles):
    return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
def pt(pid, roles, score, heading, text):
    p = pl(roles); p.update({"heading": heading, "text": text, "source": f"D-{pid}"})
    return _Pt(pid, p, score)
def qc_key(role):
    return P.ServicePrincipal("s", ("qc",), True, "enforce")
IDENTITY = lambda spec: spec

POINTS = [pt("A", ["qc", "admin"], 0.9, "HA", "alpha"),
          pt("B", ["qc", "admin"], 0.8, "HB", "beta"),
          pt("C", ["qc", "admin"], 0.7, "HC", "gamma")]
fake = FakeQdrant(POINTS)
scorer = RK.MockScorer({"HA alpha": 2.0, "HB beta": 1.0, "HC gamma": 3.0})

TAGS = ["negation", "table-row", "sibling-hard-negative"]
def fcase(i, tag, query, rel):
    return {"query_id": f"tq{i}", "intent_id": f"ti{i}", "role": "qc", "lang": "th", "category": tag,
            "challenge_tags": [tag], "split": "test", "case_type": "ranking", "relevance": rel,
            "query": query, "hard_negative_ids": [], "relevant_sources": [f"D-{list(rel)[0]}"],
            "label_status": "human-reviewed", "reviewed_by": "t", "review_revision": "r1"}
CASES = [fcase(0, "negation", "HC gamma", {"C": 3, "A": 1}),
         fcase(1, "table-row", "HA alpha", {"A": 3}),
         fcase(2, "sibling-hard-negative", "HB beta", {"B": 2})]

RAW = H.run_ranking(fake, "c", CASES, qc_key, lambda q: [0.0] * 4, scorer, top_n=10, filter_adapter=IDENTITY)
check("harness raw = unapproved COMPLETE (producer)", RAW["approved"] is False and RAW["status"] == "COMPLETE" and RAW["kind"] == "mechanics-ranking-unapproved")

# ── build_quality_rows: join + identity authoritative ──────────────────────────
rows = A.build_quality_rows(RAW, CASES)
check("quality rows ครบ 3 + arms.ndcg@5 finite ทุก arm",
      len(rows) == 3 and all(set(r["arms"]) == {"dense", "rerank", "fused"} and all(RP._is_unit_float(r["arms"][a]["ndcg@5"]) for a in r["arms"]) for r in rows))
check("identity/tags มาจาก frozen cases (by query_id)",
      all(r["intent_id"] == f"ti{i}" and r["role"] == "qc" and r["challenge_tags"] == [TAGS[i]] for i, r in enumerate(rows)))
# harness โกหก identity → adapter ยังยึด frozen (authoritative)
RAW_LIE = copy.deepcopy(RAW)
RAW_LIE["per_query"][0]["intent_id"] = "LIE"; RAW_LIE["per_query"][0]["role"] = "admin"
rows_lie = A.build_quality_rows(RAW_LIE, CASES)
check("harness โกหก intent/role → adapter ใช้ frozen (ti0/qc) ไม่เชื่อ evidence",
      rows_lie[0]["intent_id"] == "ti0" and rows_lie[0]["role"] == "qc")

# ── reject fake/missing/extra/dup/arm/ndcg ─────────────────────────────────────
def _mut(fn):
    r = copy.deepcopy(RAW); fn(r["per_query"]); return r
check("reject: query_id ไม่อยู่ใน frozen", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq[0].__setitem__("query_id", "nope")), CASES)))
check("reject: missing query (2 rows)", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq.pop()), CASES)))
check("reject: duplicate query_id", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq.append(dict(pq[0]))), CASES)))
check("reject: arm ขาด (fused)", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq[0]["arms"].pop("fused")), CASES)))
check("reject: ndcg@5 ไม่ finite", raises(lambda: A.build_quality_rows(_mut(lambda pq: pq[0]["arms"]["dense"].__setitem__("ndcg@5", float("nan"))), CASES)))
check("reject: raw ไม่ใช่ output ของ run_ranking", raises(lambda: A.build_quality_rows({"per_query": []}, CASES)))

# ── output ผ่าน validators ของ p2_runplan (consumer เดียวกับ decide_p2) ─────────
_H = "a" * 64
PLAN = {"run_id": "r", "benchmark_contract_version": RP.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5",
        "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "gate_tags": list(TAGS), "evaluated_roles": ["qc"],
        "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 3, "test_queries": 3},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H},
        "model_commit": "b" * 40, "tokenizer_commit": "b" * 40, "model_file_manifest_sha256": _H,
        "image_digest": "sha256:" + "c" * 64,
        "inference_config": {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}}
ROOT = RP.run_manifest_sha256(PLAN)
EC = PLAN["expected_counts"]

raw_by_n = {n: {"point_recall": 0.98, "doc_recall": 0.99, "candidate_hit": 1.0,
                "completed_queries": 1, "completed_intents": 1} for n in (10, 20, 30, 50)}
dev = A.build_dev_evidence(raw_by_n, ROOT)
check("build_dev_evidence -> validate_dev_evidence ผ่าน", RP.validate_dev_evidence(dev, ROOT, EC) == [], RP.validate_dev_evidence(dev, ROOT, EC))
sel = RP.select_n(dev, ROOT, EC)
sd = RP.selection_digest(ROOT, dev["raw_result_digest"], sel["selected_n"])

quality = A.build_quality_evidence(RAW, CASES, ROOT, sd, sel["selected_n"])
check("build_quality_evidence -> validate_quality_evidence ผ่าน (ผูก selection)",
      RP.validate_quality_evidence(quality, ROOT, EC, sd, sel["selected_n"]) == [], RP.validate_quality_evidence(quality, ROOT, EC, sd, sel["selected_n"]))
resolved, jerr = RP._resolve_quality_rows(quality["per_query"], CASES)
check("build_quality_evidence -> _resolve_quality_rows (join frozen) ไม่มี error", jerr == [] and len(resolved) == 3, jerr)

stages = {"candidate_retrieval": [10] * 5, "rerank": [100] * 5, "rrf": [1] * 5, "total": [150] * 5}
latency = A.build_latency_evidence(stages, ROOT, sd, sel["selected_n"], warmup=2)
check("build_latency_evidence -> validate_latency_evidence ผ่าน",
      RP.validate_latency_evidence(latency, ROOT, EC["test_queries"], RP.DEFAULT_THRESHOLDS, sd, sel["selected_n"]) == [],
      RP.validate_latency_evidence(latency, ROOT, EC["test_queries"], RP.DEFAULT_THRESHOLDS, sd, sel["selected_n"]))

# ── adapter output ไม่มี approval/decision (surface เดียว = decide_p2) ──────────
check("evidence ไม่มี approved/decision_eligible field",
      not any(k in quality or k in dev or k in latency for k in ("approved", "decision_eligible", "arm")))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
