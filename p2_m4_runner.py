"""
P2 M4a real-path runner (pure/injectable orchestrator) — Codex GO (FIX5 re-review `0A033C4`).
**เขียน/ทดสอบแบบ injectable เท่านั้น ; รัน M4a บน Qdrant/model จริง = NO-GO** จน runner-review + Data Owner sign-off.

runner **ไม่ import qdrant_client/torch** — infra ทุกอย่างฉีดผ่าน `ports` (duck-typed) เพื่อให้ทั้ง flow
offline-testable และ provenance ตรวจย้อนได้ (Codex load-bearing checks: ทุก observation field มาจาก call จริง):

  ports.scorer   : .metadata() , .score(query_text, texts)                       (pinned cross-encoder จริง)
  ports.isolation: .provision() -> {project_id,network_id,volume_id,collection_id,endpoint}
                   .observe_initial_count() -> int         (#1 count collection ก่อน seed จริง)
                   .observe_published_ports() -> int        (#2 inspect isolated network จริง)
                   .observe_endpoint_is_production() -> bool (#3 derive จาก endpoint/policy ไม่ hardcode)
                   .write_marker(marker) ; .read_marker() -> readback   (#4 read-after-write ผ่าน target เดียว)
                   .seed(corpus) ; .teardown()
  ports.provider : .filtered_candidates(effective_role, query_vector, limit) -> [(point_id, rerank_text)]
                   (compiled RBAC filter ก่อน retrieval — เฉพาะ authorized ถึง model)
  ports.oracle   : .unfiltered_topn(query_vector, limit) -> [(point_id, rerank_text)]  (raw ไม่ผ่าน filter)
                   .observe_visibility(effective_role) -> {authorized_pairs, sentinel_pairs}
                   (#5 independent direct scroll แยก client จาก provider)
  ports.clock    : .now_iso() -> str (ISO-8601 + tz)

flow (fail-closed): validate plan → M4RunRequest → validate scorer pin → provision + observe interlock →
  seed → ต่อ case (filtered provider + unfiltered oracle + run_case ; sentinel ถึง model = raise) →
  IsolationProof/OracleProof (จาก observation) → build_run_verdicts (derive) → evidence + receipt →
  **validate public bundle → atomic publish** (#6 ไม่ทิ้ง PASS artifact ถ้า fail) → teardown เสมอ
"""
from __future__ import annotations

import p2_atomic as AT
import p2_eval as E
import p2_m4_harness as HN
import p2_runplan as RP

M4A_STAGE = E.M4_STAGE_PREFLIGHT     # runner นี้ = M4a preflight (decision_eligible=False, N=50)
M4A_SELECTED_N = 50


class RunnerError(Exception):
    """orchestration ล้มเหลวก่อน publish (plan/case/coverage ผิด) — ไม่มี PASS artifact"""


def _sentinel_items(unfiltered, sentinel_pairs):
    """sentinel ที่ observe จริงใน unfiltered top-N (load-bearing: sentinel ต้องโผล่ unfiltered)"""
    want = set(sentinel_pairs or [])
    return [(pid, txt) for (pid, txt) in unfiltered if HN.component(pid, txt)["pair_sha256"] in want]


def run_m4a(*, plan, frozen, cases, corpus, marker, ports, out_dir, argv, stdout, stderr) -> dict:
    """
    รัน M4a preflight ครบ flow → publish evidence+receipt แบบ atomic. คืน {status, path, evidence, receipt}.
    ล้มเหลว/leak/interlock ผิด → raise (RunnerError / PermissionError / PublishRefused) โดยไม่ทิ้ง PASS artifact
    """
    perrs = RP.validate_run_plan(plan)
    if perrs:
        raise RunnerError("run_plan invalid: " + "; ".join(map(str, perrs[:3])))
    if not isinstance(cases, list) or not cases:
        raise RunnerError("cases ว่าง")
    root = RP.run_manifest_sha256(plan)
    expected = {**RP.m4_run_request(plan), "run_id": plan["run_id"]}

    sc, iso, prov, orac, clock = ports.scorer, ports.isolation, ports.provider, ports.oracle, ports.clock

    scorer_proof = HN.validate_scorer_metadata(sc, expected)   # fail-closed: mock/wrong pin → raise (ก่อน provision)
    started = clock.now_iso()
    handle = iso.provision()
    try:
        # observe interlock ก่อน seed — แต่ละ field มาจาก observation call แยกกัน (provenance ชัด, ไม่ hardcode)
        init_count = iso.observe_initial_count()
        published_ports = iso.observe_published_ports()
        is_production = iso.observe_endpoint_is_production()
        iso.write_marker(marker)
        readback = iso.read_marker()                           # อ่านกลับจาก target เดียวกัน (ไม่ reuse marker written)
        iso_proof = HN.build_isolation_proof(
            project_id=handle["project_id"], network_id=handle["network_id"],
            volume_id=handle["volume_id"], collection_id=handle["collection_id"],
            marker=marker, marker_readback=readback,
            initial_point_count=init_count, network_published_ports=published_ports,
            endpoint_is_production=is_production)

        iso.seed(corpus)                                        # seed หลัง observe empty-count

        records, observed = [], []
        for cspec in cases:
            cid, role = cspec["case_id"], cspec["effective_role"]
            fc = (frozen.get("cases") or {}).get(HN._id_hash(cid))
            if fc is None:
                raise RunnerError(f"case {cid!r} ไม่อยู่ frozen manifest")
            if role != fc.get("effective_role"):
                raise RunnerError(f"case {cid!r} effective_role ไม่ตรง frozen")
            qt, qv = cspec["query_text"], cspec["query_vector"]
            filtered = prov.filtered_candidates(role, qv, M4A_SELECTED_N)      # authorized เท่านั้น (filter ก่อน retrieval)
            unfiltered = orac.unfiltered_topn(qv, M4A_SELECTED_N)             # raw (independent reader)
            rec = HN.run_case(expected=expected, scorer=sc, case_id=cid, frozen_case=fc,
                              query_text=qt, query_vector=qv, candidates=filtered,
                              unfiltered_items=unfiltered, sentinel_items=_sentinel_items(unfiltered, fc.get("sentinel_pairs")),
                              selected_n=M4A_SELECTED_N)          # sentinel ถึง model → PermissionError (ไม่ PASS)
            records.append(rec)
            vis = orac.observe_visibility(role)                  # independent direct-scroll visibility ต่อ role
            observed.append({"case_id_sha256": HN._id_hash(cid),
                             "observed_authorized_pairs": vis["authorized_pairs"],
                             "observed_sentinel_pairs": vis["sentinel_pairs"]})

        oracle_proof = HN.build_oracle_proof(frozen=frozen, index_sha256=expected["retrieval_index_manifest_sha256"],
                                             collection_id=handle["collection_id"], observed_visibility=observed)
        verdicts = HN.build_run_verdicts(expected=expected, isolation_proof=iso_proof,
                                         oracle_proof=oracle_proof, case_records=records, frozen=frozen)
        run_meta = {"m4_case_manifest_sha256": plan["m4_case_manifest_sha256"], "run_id": plan["run_id"],
                    "run_manifest_sha256": root, "image_digest": expected["image_digest"],
                    "retrieval_index_manifest_sha256": expected["retrieval_index_manifest_sha256"],
                    "eval_set_sha256": plan["artifact_digests"]["eval_set_sha256"],
                    "corpus_manifest_sha256": plan["artifact_digests"]["corpus_manifest_sha256"],
                    "selected_n": M4A_SELECTED_N, "decision_eligible": False}
        evidence = HN.assemble_evidence(records, stage=M4A_STAGE, run_meta=run_meta, scorer_proof=scorer_proof,
                                        isolation_proof=iso_proof, oracle_proof=oracle_proof, verdicts=verdicts)
        finished = clock.now_iso()
        receipt = HN.assemble_receipt(evidence, run_manifest=root, m4_case_manifest=plan["m4_case_manifest_sha256"],
                                      expected=expected, argv=argv, stdout=stdout, stderr=stderr,
                                      isolation_proof=iso_proof, started_utc=started, finished_utc=finished, exit_code=0)
        evidence["run_receipt_sha256"] = E.m4_run_receipt_sha256(receipt)

        # #6: validate public bundle **ก่อน** publish ; atomic (temp→rename) ; fail → ไม่ทิ้ง PASS artifact
        path = AT.publish_m4_bundle(out_dir=out_dir, run_id=plan["run_id"], evidence=evidence, receipt=receipt,
                                    validate=lambda: RP.validate_m4_preflight_bundle(plan, frozen, evidence, receipt))
    finally:
        iso.teardown()                                          # teardown เสมอ (แม้ leak/refuse)
    return {"status": "PUBLISHED", "path": path, "evidence": evidence, "receipt": receipt}
