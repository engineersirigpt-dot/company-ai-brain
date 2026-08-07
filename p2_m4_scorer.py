"""
P2 M4 scorer adapter (ports.scorer) — pinned cross-encoder factory + fail-closed plan-pin verifier

scorer port (runner): `metadata()` (pinned-cross-encoder) + `score(query_text, texts)->[float]*len(texts)`.
งานจริงของ scorer มีแค่ score()+metadata() — RBAC/rerank อยู่ที่ p2_provider/harness แล้ว จึง **reuse
`p2_reranker.PinnedCrossEncoder`** (injectable: รับ model/tokenizer objects) ตรง ๆ เป็น ports.scorer.

โมดูลนี้เพิ่ม 2 อย่าง:
  - `assert_scorer_matches_plan(scorer, plan)` — fail-closed ยืนยัน metadata ตรง RunPlan pin **ก่อนใช้**
    (model_revision/tokenizer_revision/model_file_manifest_sha256/inference_config/model_name = plan) ; mismatch/mock → raise
  - `build_m4_scorer(plan, loader=...)` — โหลด pinned cross-encoder ตาม pin ใน plan แล้ว verify (real path = torch/model ;
    **รันจริง = NO-GO** จน slice review) ; offline inject `loader(plan)->scorer` (fake)
"""
from __future__ import annotations

import p2_m4_harness as HN
import p2_runplan as RP


class M4ScorerError(Exception):
    """scorer metadata ไม่ตรง RunPlan pin / โหลด model ไม่สำเร็จ"""


def _expected(plan) -> dict:
    if not isinstance(plan, dict) or "run_id" not in plan:
        raise M4ScorerError("plan ต้องเป็น dict ที่มี run_id")
    return {**RP.m4_run_request(plan), "run_id": plan["run_id"]}


def assert_scorer_matches_plan(scorer, plan) -> dict:
    """
    fail-closed: `HN.validate_scorer_metadata` เทียบ metadata() กับ RunPlan-derived pin —
    kind=='pinned-cross-encoder' + model_revision/tokenizer_revision/model_file_manifest_sha256/inference_config/model_name ตรง plan
    (เดียวกับที่ runner ตรวจก่อน provision) ; คืน ScorerProof ; ผิด → error (แปลงเป็น M4ScorerError)
    """
    try:
        return HN.validate_scorer_metadata(scorer, _expected(plan))
    except M4ScorerError:
        raise
    except Exception as e:
        raise M4ScorerError(f"scorer metadata ไม่ตรง RunPlan pin: {e}") from e


def _default_loader(plan):
    """real path: โหลด pinned bge-reranker ตาม pin ใน plan (lazy import — torch/transformers/hub) ; รันจริง = NO-GO"""
    import p2_reranker
    ic = plan["inference_config"]
    return p2_reranker.load_pinned_cross_encoder(ic["model_name"], revision=plan["model_commit"],
                                                 device=ic.get("device", "cpu"),
                                                 max_length=ic["max_length"], batch_size=ic["batch_size"])


def build_m4_scorer(plan, *, loader=_default_loader):
    """โหลด scorer ตาม plan pin แล้ว **verify metadata ตรง plan ก่อนคืน** (loaded model ต้องตรง pin ที่ประกาศ)"""
    scorer = loader(plan)
    assert_scorer_matches_plan(scorer, plan)
    return scorer
