"""
Unit test ของ p2_m4_scorer — pinned cross-encoder factory + fail-closed plan-pin verifier (offline, fake loader)
metadata ตรง RunPlan pin -> ผ่าน ; mismatch/mock -> M4ScorerError ; build_m4_scorer verify ก่อนคืน

    python test_p2_m4_scorer.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_eval as E
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

_H = "a" * 64
IC = {"model_name": RK.RERANKER_MODEL, "max_length": 512, "batch_size": 16, "device": "cpu", "dtype": "float32"}
PLAN = {"run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION,
        "n_set": [10, 20, 30, 50], "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5",
        "intent_grouping": "intent_id", "thresholds": dict(RP.DEFAULT_THRESHOLDS),
        "gate_tags": ["negation"], "evaluated_roles": ["qc"], "m4_case_manifest_sha256": _H,
        "required_categories": ["negation"], "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 1, "test_queries": 1},
        "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": _H, "retrieval_index_manifest_sha256": _H},
        "model_commit": "a" * 40, "tokenizer_commit": "a" * 40, "model_file_manifest_sha256": _H,
        "image_digest": "sha256:" + "e" * 64, "inference_config": dict(IC)}


class FakePinned:                                          # metadata ตรง PLAN pin (เหมือน PinnedCrossEncoder.metadata)
    def __init__(s, *, revision="a" * 40, manifest=_H): s._rev = revision; s._man = manifest; s.queries = []
    def metadata(s):
        return {"kind": "pinned-cross-encoder", "model_name": RK.RERANKER_MODEL, "model_revision": s._rev,
                "tokenizer_revision": "a" * 40, "model_file_manifest_sha256": s._man, "inference_config": dict(IC)}
    def score(s, q, texts): s.queries.append(q); return [1.0 for _ in texts]
class MockLike:                                            # kind != pinned-cross-encoder
    def metadata(s): return {"kind": "mock-non-evidence"}
    def score(s, q, texts): return [0.0 for _ in texts]


check("assert_scorer_matches_plan: metadata ตรง plan -> ScorerProof (kind=pinned-cross-encoder)",
      SC.assert_scorer_matches_plan(FakePinned(), PLAN)["scorer_kind"] == "pinned-cross-encoder")
check("assert_scorer_matches_plan: model_revision ผิด -> M4ScorerError (fail-closed)",
      raises(lambda: SC.assert_scorer_matches_plan(FakePinned(revision="b" * 40), PLAN), SC.M4ScorerError))
check("assert_scorer_matches_plan: model_file_manifest ผิด -> M4ScorerError",
      raises(lambda: SC.assert_scorer_matches_plan(FakePinned(manifest="c" * 64), PLAN), SC.M4ScorerError))
check("assert_scorer_matches_plan: mock scorer (kind ผิด) -> M4ScorerError",
      raises(lambda: SC.assert_scorer_matches_plan(MockLike(), PLAN), SC.M4ScorerError))

# build_m4_scorer: inject loader (offline) -> verify ก่อนคืน
_built = SC.build_m4_scorer(PLAN, loader=lambda plan: FakePinned())
check("build_m4_scorer(loader ok) -> คืน scorer ที่ผ่าน verify + score ได้", _built.score("q", ["a", "b"]) == [1.0, 1.0])
check("build_m4_scorer(loader คืน scorer ผิด pin) -> M4ScorerError (verify ก่อนคืน)",
      raises(lambda: SC.build_m4_scorer(PLAN, loader=lambda plan: FakePinned(revision="d" * 40)), SC.M4ScorerError))
check("build_m4_scorer: plan ไม่มี run_id -> M4ScorerError", raises(lambda: SC.build_m4_scorer({}, loader=lambda plan: FakePinned()), SC.M4ScorerError))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
