"""
Unit test ของ P2 benchmark harness (Slice 2 scaffolding) — offline, fake Qdrant + MockScorer
พิสูจน์ dense/rerank/fused บน candidate universe เดียว + aggregate + output unapproved

    python test_p2_harness.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import policy as P
import p2_provider as PROV
import p2_reranker as RK
import p2_harness as H

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))


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
def access(role):
    return P.EffectiveAccess(P.ServicePrincipal("t", (role,), True, "enforce"), role)
IDENTITY = lambda spec: spec

# dense order (score): A(0.9) > B(0.8) > C(0.7). mock rerank: C > A > B → arms ต่างกัน
POINTS = [pt("A", ["qc", "admin"], 0.9, "HA", "alpha"),
          pt("B", ["qc", "admin"], 0.8, "HB", "beta"),
          pt("C", ["qc", "admin"], 0.7, "HC", "gamma")]
fake = FakeQdrant(POINTS)
scorer = RK.MockScorer({"HA alpha": 2.0, "HB beta": 1.0, "HC gamma": 3.0})
cands = PROV.build_candidates(fake, "c", access("qc"), [0.0] * 4, 10, filter_adapter=IDENTITY)

# ── rank_arms: candidate universe เดียว ────────────────────────────────────────
arms = H.rank_arms("q", cands, scorer)
check("dense order = ตาม dense_rank (score desc)", arms["dense"] == ["A", "B", "C"])
check("rerank order = ตาม mock score (C>A>B)", arms["rerank"] == ["C", "A", "B"], arms["rerank"])
check("ทุก arm เป็น permutation ของ universe เดียว",
      all(sorted(arms[a]) == ["A", "B", "C"] for a in H.ARMS))
check("fused_rrf เป็น permutation", sorted(arms["fused"]) == ["A", "B", "C"])

# ── eval_query: metrics ต่อ arm ────────────────────────────────────────────────
r = H.eval_query("q", cands, {"C": 3, "A": 1}, scorer)
check("candidate_recall@n = 1.0 (C,A อยู่ใน pool)", r["candidate_recall@n"] == 1.0)
check("rerank hit@1 = 1.0 (C relevant อันดับ1)", r["arms"]["rerank"]["hit@1"] == 1.0)
check("dense hit@1 = 1.0 (A relevant อันดับ1)", r["arms"]["dense"]["hit@1"] == 1.0)
check("nDCG@5 rerank > dense (C grade 3 ขึ้นนำ)", r["arms"]["rerank"]["ndcg@5"] > r["arms"]["dense"]["ndcg@5"])
check("metric ครบ arm (hit@1/3/5, mrr@5, ndcg@5, recall@5)",
      set(r["arms"]["dense"]) == {"hit@1", "hit@3", "hit@5", "mrr@5", "ndcg@5", "recall@5"})

# ── aggregate ──────────────────────────────────────────────────────────────────
agg = H.aggregate([r, r])
check("aggregate mean ต่อ arm", agg["rerank"]["ndcg@5"] == r["arms"]["rerank"]["ndcg@5"])
check("aggregate มี candidate_recall@n", "candidate_recall@n" in agg)

# ── B3: run_ranking zero-skip (COMPLETE) vs run_diagnostic (INCOMPLETE) ─────────
def raises2(fn):
    try:
        fn(); return False
    except Exception:
        return True
def qc_key(role):
    return P.ServicePrincipal("s", ("qc",), True, "enforce")   # qc-only key (จำลอง registry)
_qc = [{"query_id": "q1", "intent_id": "i1", "role": "qc", "query": "HC gamma", "relevance": {"C": 3, "A": 1}}]
_sales = [{"query_id": "q2", "intent_id": "i2", "role": "sales", "query": "x", "relevance": {"A": 1}}]
out = H.run_ranking(fake, "c", _qc, qc_key, lambda q: [0.0] * 4, scorer, top_n=10, filter_adapter=IDENTITY)
check("run_ranking COMPLETE + approved=False + n_completed==n_expected",
      out["status"] == "COMPLETE" and out["approved"] is False and out["n_completed"] == out["n_expected"] == 1)
check("run_ranking scorer metadata = mock (non-evidence)", out["scorer"]["model_revision"] == "mock")
check("B3: run_ranking role นอก scope -> RuntimeError (zero-skip, ไม่แปลงเป็น skipped)",
      raises2(lambda: H.run_ranking(fake, "c", _sales, qc_key, lambda q: [0.0] * 4, scorer, 10, IDENTITY)))
check("B3: run_ranking cases ว่าง -> error", raises2(lambda: H.run_ranking(fake, "c", [], qc_key, lambda q: [0.0] * 4, scorer, 10, IDENTITY)))
diag = H.run_diagnostic(fake, "c", _sales, qc_key, lambda q: [0.0] * 4, scorer, 10, IDENTITY)
check("B3: run_diagnostic error -> status INCOMPLETE + kind non-evidence + n_errors=1",
      diag["status"] == "INCOMPLETE" and diag["kind"] == "diagnostic-non-evidence" and diag["n_errors"] == 1)

# ── B1: validate_pin (immutable commit, allowlist) ─────────────────────────────
check("B1: validate_pin main -> error", any("revision" in e for e in RK.validate_pin(RK.RERANKER_MODEL, "main", 512, 16)))
check("B1: validate_pin commit SHA -> ผ่าน", RK.validate_pin(RK.RERANKER_MODEL, "a" * 40, 512, 16) == [])
check("B1: validate_pin model นอก allowlist -> error", any("allowlist" in e for e in RK.validate_pin("evil/model", "a" * 40, 512, 16)))
check("B1: validate_pin max_length 0 -> error", any("max_length" in e for e in RK.validate_pin(RK.RERANKER_MODEL, "a" * 40, 0, 16)))

# ── MockScorer contract ────────────────────────────────────────────────────────
check("MockScorer.score len == texts + finite", RK.MockScorer({"x": 1.5}).score("q", ["x", "y"]) == [1.5, 0.0])

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
