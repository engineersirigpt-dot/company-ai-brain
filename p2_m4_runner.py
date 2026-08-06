"""
P2 M4a real-path runner (pure/injectable orchestrator) — Codex GO (FIX5) → review bfa69a0 (FIX B1-B4/M1-M2).
**เขียน/ทดสอบแบบ injectable เท่านั้น ; รัน M4a บน Qdrant/model จริง = NO-GO** จน adapter-review + Data Owner sign-off.

runner **ไม่ import qdrant_client/torch** — infra ฉีดผ่าน `ports` (duck-typed). fail-closed แบบ **fail-before-mutate**:
interlock/corpus/target ผิด → abort **ก่อน** write_marker/seed/model ; teardown ครอบ provision + เกิด **ก่อน** publish

  ports.scorer   : .metadata() , .score(query_text, texts)
  ports.isolation: .provision() -> {project_id,network_id,volume_id,collection_id,endpoint}
                   .observe_initial_count() / .observe_published_ports() / .observe_endpoint_is_production()
                   .write_marker(marker) ; .read_marker() -> readback
                   .seed(corpus) ; .teardown()   (teardown ต้อง partial-provision-safe/idempotent — B4)
  ports.provider : .bind(handle) ; .observed_target_identity() -> {collection_id,endpoint}       (B3)
                   .filtered_candidates(effective_role, query_vector, limit) -> [(point_id, rerank_text)]
  ports.oracle   : .bind(handle) ; .observed_target_identity() -> {collection_id,endpoint}        (B3, client แยก)
                   .unfiltered_topn(query_vector, limit) ; .observe_visibility(effective_role)
  ports.clock    : .now_iso() -> str

flow: validate plan → M4RunRequest → **bind corpus↔RunPlan (B2)** → validate scorer pin → provision →
  **exact interlock check ก่อน write_marker (B1)** → write/read marker → validate IsolationProof **ก่อน seed (B1)** →
  **bind+verify provider/oracle target == isolated handle ก่อน seed (B3)** → seed → run cases (sentinel→raise) →
  proofs → verdicts → assemble evidence → **teardown → build receipt → validate bundle → atomic publish (B4/#6)**
"""
from __future__ import annotations

import p2_atomic as AT
import p2_eval as E
import p2_m4_harness as HN
import p2_runplan as RP

M4A_STAGE = E.M4_STAGE_PREFLIGHT
M4A_SELECTED_N = 50


class RunnerError(Exception):
    """orchestration/provenance ล้มเหลว — abort ก่อน publish, ไม่มี PASS artifact"""


def _sentinel_items(unfiltered, sentinel_pairs):
    want = set(sentinel_pairs or [])
    return [(pid, txt) for (pid, txt) in unfiltered if HN.component(pid, txt)["pair_sha256"] in want]


def _safe_teardown(iso):
    """cleanup best-effort — คืน teardown exception (ไม่ raise) เพื่อไม่กลบ original แต่ยังสังเกตได้ (B4/M2)"""
    try:
        iso.teardown()
        return None
    except Exception as te:
        return te


def _note(exc, msg) -> None:
    """M2: preserve original cause + แนบ cleanup failure ให้ operator เห็น (ไม่เปลี่ยน primary cause)"""
    try:
        exc.add_note(msg)
    except Exception:
        pass


def _preflight_frozen_cases(plan, frozen, cases) -> None:
    """
    B2: fail-before-mutate สำหรับ frozen/case input — ตรวจ **ทั้งหมดก่อน provision/scorer/seed/model**
    (frozen valid + digest/roles/categories == RunPlan ; case set exact + ทุก id/role/query hash ตรง frozen)
    """
    ferrs = E.validate_m4_frozen_manifest(frozen)
    if ferrs:
        raise RunnerError("frozen manifest invalid: " + "; ".join(map(str, ferrs[:3])))
    if E.m4_case_manifest_sha256(frozen) != plan["m4_case_manifest_sha256"]:
        raise RunnerError("frozen digest != RunPlan.m4_case_manifest_sha256")
    if set(frozen.get("evaluated_roles") or []) != set(plan["evaluated_roles"]):
        raise RunnerError("frozen evaluated_roles != RunPlan")
    if set(frozen.get("required_categories") or []) != set(plan["required_categories"]):
        raise RunnerError("frozen required_categories != RunPlan")
    fcases, seen = frozen["cases"], []
    for c in cases:
        if not isinstance(c, dict) or not isinstance(c.get("case_id"), str) or not isinstance(c.get("effective_role"), str):
            raise RunnerError("case spec ต้องมี case_id/effective_role เป็น str")
        cidh = HN._id_hash(c["case_id"])
        seen.append(cidh)
        fc = fcases.get(cidh)
        if fc is None:
            raise RunnerError(f"case {c['case_id']!r} ไม่อยู่ frozen manifest")
        if c["effective_role"] != fc.get("effective_role"):
            raise RunnerError(f"case {c['case_id']!r} effective_role ไม่ตรง frozen")
        try:
            qt_ok = HN._text_hash(c.get("query_text")) == fc.get("query_text_sha256")
            qv_ok = HN._vec_hash(c.get("query_vector")) == fc.get("query_vector_sha256")
        except (ValueError, TypeError):
            raise RunnerError(f"case {c['case_id']!r} query text/vector malformed")
        if not qt_ok:
            raise RunnerError(f"case {c['case_id']!r} query_text ไม่ตรง frozen QueryProbe")
        if not qv_ok:
            raise RunnerError(f"case {c['case_id']!r} query_vector ไม่ตรง frozen QueryProbe")
    if len(seen) != len(set(seen)):
        raise RunnerError("case spec ซ้ำ (duplicate case_id)")
    if set(seen) != set(fcases):
        raise RunnerError("case set != frozen manifest (missing/extra)")


def _target_identity(handle) -> dict:
    return {"collection_id": handle["collection_id"], "endpoint": handle["endpoint"]}


def run_m4a(*, plan, frozen, cases, corpus, marker, ports, out_dir, argv, stdout, stderr) -> dict:
    """รัน M4a preflight → publish bundle แบบ atomic. คืน {status, path, evidence, receipt}. fail-closed ทุกเส้น"""
    perrs = RP.validate_run_plan(plan)
    if perrs:
        raise RunnerError("run_plan invalid: " + "; ".join(map(str, perrs[:3])))
    if not isinstance(cases, list) or not cases:
        raise RunnerError("cases ว่าง")
    _preflight_frozen_cases(plan, frozen, cases)   # B2: frozen/case fail-before-mutate (ก่อน provision/scorer/seed/model)
    root = RP.run_manifest_sha256(plan)
    expected = {**RP.m4_run_request(plan), "run_id": plan["run_id"]}

    # B2: corpus ต้องผูก RunPlan.corpus_manifest_sha256 (ก่อน provision/seed) — กัน seed corpus อื่นแต่ evidence อ้าง corpus นี้
    if not isinstance(corpus, dict):
        raise RunnerError("corpus ต้องเป็น canonical frozen corpus dict")
    cerrs = E.validate_corpus(corpus)
    if cerrs:
        raise RunnerError("corpus invalid: " + "; ".join(map(str, cerrs[:3])))
    if E.corpus_manifest_sha256(corpus) != plan["artifact_digests"]["corpus_manifest_sha256"]:
        raise RunnerError("corpus_manifest_sha256 ไม่ตรง RunPlan (seed corpus ต้องเป็นชุดที่ evidence อ้าง)")

    sc, iso, prov, orac, clock = ports.scorer, ports.isolation, ports.provider, ports.oracle, ports.clock
    scorer_proof = HN.validate_scorer_metadata(sc, expected)   # fail-closed pin (ก่อน provision — ยังไม่มี resource)
    started = clock.now_iso()

    handle = None
    try:
        handle = iso.provision()
        # B1: exact interlock ทันทีหลัง observe และ **ก่อน write_marker** — ผิด → abort ก่อน mutate/model
        init_count = iso.observe_initial_count()
        if not E._exact_zero_int(init_count):
            raise RunnerError("initial_point_count != 0 (collection ไม่ว่างก่อน seed) — abort ก่อน write/seed")
        published_ports = iso.observe_published_ports()
        if not E._exact_zero_int(published_ports):
            raise RunnerError("network_published_ports != 0 (network ไม่ internal) — abort ก่อน write/seed")
        is_production = iso.observe_endpoint_is_production()
        if is_production is not False:
            raise RunnerError("endpoint_is_production ไม่ False (target อาจเป็น production) — abort ก่อน write/seed")

        iso.write_marker(marker)
        readback = iso.read_marker()                           # read-after-write ผ่าน target เดียว (ไม่ reuse marker)
        iso_proof = HN.build_isolation_proof(
            project_id=handle["project_id"], network_id=handle["network_id"],
            volume_id=handle["volume_id"], collection_id=handle["collection_id"],
            marker=marker, marker_readback=readback,
            initial_point_count=init_count, network_published_ports=published_ports,
            endpoint_is_production=is_production)
        ierrs = E.validate_m4_isolation_proof(iso_proof)       # B1: validate **ก่อน seed** (marker mismatch → abort ก่อน seed/model)
        if ierrs:
            raise RunnerError("IsolationProof invalid: " + "; ".join(map(str, ierrs[:3])))

        # B3: provider/oracle ต้อง bind isolated target เดียวกับ handle ก่อน seed/query (พิสูจน์ไม่ได้ชี้ collection อื่น)
        prov.bind(handle)
        orac.bind(handle)
        tgt = _target_identity(handle)
        if prov.observed_target_identity() != tgt:
            raise RunnerError("provider target != isolated handle (อาจ query collection อื่น) — abort ก่อน seed")
        if orac.observed_target_identity() != tgt:
            raise RunnerError("oracle target != isolated handle — abort ก่อน seed")

        iso.seed(corpus)                                       # seed หลังผ่าน interlock/target validation ทั้งหมด

        records, observed = [], []
        for cspec in cases:
            cid, role = cspec["case_id"], cspec["effective_role"]
            fc = (frozen.get("cases") or {}).get(HN._id_hash(cid))
            if fc is None:
                raise RunnerError(f"case {cid!r} ไม่อยู่ frozen manifest")
            if role != fc.get("effective_role"):
                raise RunnerError(f"case {cid!r} effective_role ไม่ตรง frozen")
            qt, qv = cspec["query_text"], cspec["query_vector"]
            filtered = prov.filtered_candidates(role, qv, M4A_SELECTED_N)
            unfiltered = orac.unfiltered_topn(qv, M4A_SELECTED_N)
            rec = HN.run_case(expected=expected, scorer=sc, case_id=cid, frozen_case=fc,
                              query_text=qt, query_vector=qv, candidates=filtered,
                              unfiltered_items=unfiltered, sentinel_items=_sentinel_items(unfiltered, fc.get("sentinel_pairs")),
                              selected_n=M4A_SELECTED_N)         # sentinel ถึง model → PermissionError
            records.append(rec)
            vis = orac.observe_visibility(role)
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
    except BaseException as e:
        terr = _safe_teardown(iso)   # cleanup ทุก failure รวม **partial provision** (teardown idempotent) ; ไม่กลบ original
        if terr is not None:         # M2: teardown ก็ล้ม → แนบ note (resource อาจค้าง) โดยคง primary cause เดิม
            _note(e, f"cleanup ล้มเหลวด้วย: {terr!r} — isolated resource อาจค้าง ต้องตรวจ manual")
        raise

    # B4: work สำเร็จ → teardown ให้เรียบร้อย **ก่อน** publish ; teardown fail → ไม่มี PASS artifact
    try:
        iso.teardown()
    except Exception as e:
        raise RunnerError("teardown failed — ไม่ publish PASS artifact") from e

    finished = clock.now_iso()
    receipt = HN.assemble_receipt(evidence, run_manifest=root, m4_case_manifest=plan["m4_case_manifest_sha256"],
                                  expected=expected, argv=argv, stdout=stdout, stderr=stderr,
                                  isolation_proof=iso_proof, started_utc=started, finished_utc=finished, exit_code=0)
    evidence["run_receipt_sha256"] = E.m4_run_receipt_sha256(receipt)
    path = AT.publish_m4_bundle(out_dir=out_dir, run_id=plan["run_id"], evidence=evidence, receipt=receipt,
                                validate=lambda: RP.validate_m4_preflight_bundle(plan, frozen, evidence, receipt))
    # M3.1: persist durability mode ใน operational result (Windows = atomic-visibility-only, POSIX = durable)
    return {"status": "PUBLISHED", "path": path, "durability": AT.durability_mode(),
            "evidence": evidence, "receipt": receipt}
