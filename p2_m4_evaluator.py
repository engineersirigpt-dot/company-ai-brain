"""
M4a evaluator — รัน **ภายใน pinned reranker container** บน internal network เดียวกับ Qdrant (synthetic corpus)
real scorer (bge-reranker) + real Qdrant (provider/oracle) + container-side isolation -> run_m4a -> bundle
เรียกโดย controller (host) ผ่าน docker run ; อ่าน env ; เขียน bundle ไป /out ; print RESULT บรรทัดเดียว (controller parse)
"""
import json
import os
import sys
import uuid

sys.path.insert(0, "/host")                                 # current source (mounted)

import p2_eval as E
import p2_m4_harness as HN
import p2_m4_isolation as ISO
import p2_m4_qdrant as QA
import p2_m4_runner as RUN
import p2_pin as PIN
import p2_reranker as RK
import p2_runplan as RP

QD_EP = os.environ["M4_QDRANT_ENDPOINT"]                    # http://m4qd-<tok>:6333 (internal DNS)
IMG = os.environ["M4_EVAL_IMAGE_DIGEST"]                    # sha256:<64hex> (evaluator container image)
NET, VOL, PROJ = os.environ["M4_NETWORK_ID"], os.environ["M4_VOLUME_ID"], os.environ["M4_PROJECT_ID"]
OUT = os.environ.get("M4_OUT", "/out")
VS = 4
COLL = "m4eval-" + uuid.uuid4().hex[:8]


class RealQdrantClient:
    """qdrant_client + observed_target_identity + query/scroll (query_filter = Filter จาก build_candidates อยู่แล้ว)"""
    def __init__(self, endpoint):
        from qdrant_client import QdrantClient
        self._c = QdrantClient(url=endpoint); self._ep = endpoint
    def observed_target_identity(self, collection_id):
        self._c.get_collections(); return {"collection_id": collection_id, "endpoint": self._ep}
    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        return self._c.query_points(collection_name=collection_name, query=query, query_filter=query_filter,
                                    limit=limit, with_payload=with_payload)
    def scroll(self, collection_name, with_payload, limit, offset):
        return self._c.scroll(collection_name=collection_name, with_payload=with_payload, limit=limit, offset=offset)


def main() -> int:
    scorer = RK.load_pinned_cross_encoder(revision=PIN.MODEL_COMMIT)
    md = scorer.metadata()

    A, B, S = (str(uuid.UUID(int=i)) for i in (1, 2, 3))    # UUID point ids (valid Qdrant)
    def _pl(roles): return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
                            "collection_group": "RECALL", "confidentiality_level": 3, "allowed_roles": roles}
    tA = "quarterly qc inspection checklist for feline products"
    tB = "sales pricing sheet for the north region"
    tS = "confidential management-only merger secret"
    corpus = {A: {"source": "DA", "rerank_text": tA, "payload": _pl(["qc", "admin"])},
              B: {"source": "DB", "rerank_text": tB, "payload": _pl(["sales", "admin"])},
              S: {"source": "DS", "rerank_text": tS, "payload": _pl(["management"])}}
    QT1, QT2 = "qc inspection checklist", "sales pricing"
    frozen = HN.build_frozen_manifest(
        cases={"case-qc": HN.frozen_case(effective_role="qc", category="negation", query_text=QT1,
                                         query_vector=[0.9, 0.1, 0, 0], authorized_items=[(A, tA)], sentinel_items=[(S, tS)]),
               "case-sales": HN.frozen_case(effective_role="sales", category="table-row", query_text=QT2,
                                            query_vector=[0.1, 0.9, 0, 0], authorized_items=[(B, tB)], sentinel_items=[(S, tS)])},
        required_categories=["negation", "table-row"], evaluated_roles=["qc", "sales"])
    _H = "a" * 64
    plan = {"run_id": "run-1", "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION, "n_set": [10, 20, 30, 50],
            "seed": 1, "resamples": 10000, "primary_metric": "ndcg@5", "intent_grouping": "intent_id",
            "thresholds": dict(RP.DEFAULT_THRESHOLDS), "gate_tags": ["negation", "table-row"], "evaluated_roles": ["qc", "sales"],
            "m4_case_manifest_sha256": E.m4_case_manifest_sha256(frozen), "required_categories": ["negation", "table-row"],
            "expected_counts": {"dev_intents": 1, "dev_queries": 1, "test_intents": 1, "test_queries": 1},
            "artifact_digests": {"eval_set_sha256": _H, "corpus_manifest_sha256": E.corpus_manifest_sha256(corpus),
                                 "retrieval_index_manifest_sha256": _H},
            "model_commit": md["model_revision"], "tokenizer_commit": md["tokenizer_revision"],
            "model_file_manifest_sha256": md["model_file_manifest_sha256"], "image_digest": IMG,
            "inference_config": dict(md["inference_config"])}
    cases = [{"case_id": "case-qc", "effective_role": "qc", "query_text": QT1, "query_vector": [0.9, 0.1, 0, 0]},
             {"case_id": "case-sales", "effective_role": "sales", "query_text": QT2, "query_vector": [0.1, 0.9, 0, 0]}]

    probe = QA.approved_probe_principal_factory(frozenset(plan["evaluated_roles"]))
    session = ISO.QdrantSession.connect(QD_EP, COLL, VS)
    isolation = ISO.QdrantDockerIsolation(driver=ISO.QdrantSessionDriver(
        session=session, project_id=PROJ, network_id=NET, volume_id=VOL, collection_id=COLL, endpoint=QD_EP,
        published_ports=0, endpoint_is_production=False))
    oplan = {"case-qc": {"effective_role": "qc", "point_ids": [A, S]},
             "case-sales": {"effective_role": "sales", "point_ids": [B, S]}}
    import types
    ports = types.SimpleNamespace(
        scorer=scorer, isolation=isolation,
        provider=QA.QdrantM4Provider(lambda ep: RealQdrantClient(ep), principal_factory=probe),
        oracle=QA.QdrantM4Oracle(lambda ep: RealQdrantClient(ep), observation_plan=oplan, principal_factory=probe),
        clock=_Clock())
    r = RUN.run_m4a(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker="m4-run-uuid-REAL",
                    ports=ports, out_dir=OUT, argv=["python", "p2_m4_evaluator.py"], stdout=b"real", stderr=b"")
    ev = r["evidence"]
    result = {"status": r["status"], "evidence_status": ev["status"],
              "isolated_interlock": ev["isolated_interlock"], "independent_oracle": ev["independent_oracle"],
              "sentinel_reached_model": ev["sentinel_reached_model"], "unauthorized_in_model_inputs": ev["unauthorized_in_model_inputs"],
              "decision_eligible": ev["decision_eligible"], "bundle_path": r["path"]}
    print("M4A_RESULT " + json.dumps(result))
    return 0 if (r["status"] == "PUBLISHED" and ev["status"] == "PASS") else 1


class _Clock:
    def __init__(s): s.n = 0
    def now_iso(s):
        s.n += 1
        return "2026-08-07T0%d:00:00+07:00" % min(s.n, 9)


if __name__ == "__main__":
    sys.exit(main())
