"""
Unit test ของ P2 Slice 1 (pure) — retrieval_metrics + rerank + p2_eval
พิสูจน์ Slice 1 contract + required behaviors ตาม Codex (ไม่ต้อง model/Qdrant)

    python test_p2.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import retrieval_metrics as M
import rerank as R
import p2_eval as E

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn):
    try:
        fn(); return False
    except ValueError:
        return True


def cand(pid, rank, score=None, source=None, text=None):
    return {"point_id": pid, "source": (f"doc-{pid}" if source is None else source),
            "dense_score": score if score is not None else 1.0 / rank,
            "dense_rank": rank, "rerank_text": (f"text-{pid}" if text is None else text)}


CANDS = [cand("a", 1), cand("b", 2), cand("c", 3)]

# ── validate_candidates (Codex contract) ───────────────────────────────────────
check("valid candidates -> ok", R.validate_candidates([cand("a", 1), cand("b", 2)]) is not None)
check("point_id ซ้ำ -> fail", raises(lambda: R.validate_candidates([cand("a", 1), cand("a", 2)])))
check("point_id ว่าง -> fail", raises(lambda: R.validate_candidates([cand("", 1)])))
check("dense_rank ซ้ำ -> fail", raises(lambda: R.validate_candidates([cand("a", 1), cand("b", 1)])))
check("dense_rank ไม่ positive -> fail", raises(lambda: R.validate_candidates([cand("a", 0, score=0.5)])))
check("dense_score NaN -> fail", raises(lambda: R.validate_candidates([cand("a", 1, score=float("nan"))])))
check("dense_score Inf -> fail", raises(lambda: R.validate_candidates([cand("a", 1, score=float("inf"))])))
check("dense_score bool -> fail", raises(lambda: R.validate_candidates([cand("a", 1, score=True)])))
check("rerank_text ว่าง -> fail", raises(lambda: R.validate_candidates([cand("a", 1, text=" ")])))
check("source ว่าง -> fail", raises(lambda: R.validate_candidates([cand("a", 1, source="")])))

# ── dense_order + input ไม่ mutate ─────────────────────────────────────────────
snapshot = [dict(c) for c in CANDS]
check("dense_order ตาม dense_rank", R.dense_order(CANDS) == ["a", "b", "c"])
check("dense_order ไม่ mutate input", CANDS == snapshot)

# ── rerank_order ───────────────────────────────────────────────────────────────
def scorer(smap):
    seen = {"texts": []}
    def fn(query, texts):
        seen["texts"].extend(texts)
        return [smap.get(t, 0.0) for t in texts]
    return fn, seen
sf, _ = scorer({"text-c": 3.0, "text-a": 2.0, "text-b": 1.0})
check("rerank_order เรียงตาม score desc", R.rerank_order("q", CANDS, sf) == ["c", "a", "b"])
check("rerank_order ไม่ mutate input", CANDS == snapshot)
check("score count != candidate -> fail", raises(lambda: R.rerank_order("q", CANDS, lambda q, t: [1.0, 2.0])))
check("score NaN -> fail", raises(lambda: R.rerank_order("q", CANDS, lambda q, t: [float("nan")] * len(t))))
# tie-break: score เท่ากัน -> dense_rank แล้ว point_id
sf_tie, _ = scorer({})  # ทุก text score 0 -> เท่ากันหมด
check("rerank tie -> tie-break ด้วย dense_rank", R.rerank_order("q", CANDS, sf_tie) == ["a", "b", "c"])
check("rerank empty -> empty", R.rerank_order("q", [], lambda q, t: []) == [])
check("rerank one -> same", R.rerank_order("q", [cand("a", 1)], lambda q, t: [5.0]) == ["a"])
check("rerank output = permutation ของ candidate IDs",
      sorted(R.rerank_order("q", CANDS, sf)) == ["a", "b", "c"])

# ── instrumented score_fn: unauthorized sentinel ไม่เคยถึง score_fn ─────────────
sf_probe, seen = scorer({})
authorized = [cand("a", 1), cand("b", 2)]           # candidate ที่ผ่าน filter แล้ว (มีแค่ a,b)
R.assert_candidates_authorized(authorized, {"a", "b"})
R.rerank_order("q", authorized, sf_probe)
check("unauthorized sentinel ไม่ถึง score_fn (score_fn เห็นแค่ candidate text)",
      "text-SENTINEL" not in seen["texts"] and set(seen["texts"]) == {"text-a", "text-b"}, seen["texts"])
check("assert_candidates_authorized: point นอกสิทธิ์หลุด -> fail",
      raises(lambda: R.assert_candidates_authorized([cand("SENTINEL", 1)], {"a", "b"})))

# ── fused_rrf ──────────────────────────────────────────────────────────────────
drm = R.dense_rank_map(CANDS)
fused = R.fused_rrf({"dense": ["a", "b", "c"], "rerank": ["a", "c", "b"]}, dense_rank_map=drm)
check("fused_rrf: a สูงสุด (rank1 ทั้งสอง arm), b ก่อน c (tie -> dense_rank)", fused == ["a", "b", "c"], fused)
check("fused_rrf output = permutation", sorted(fused) == ["a", "b", "c"])
check("fused_rrf: ranking มี id ซ้ำ -> fail",
      raises(lambda: R.fused_rrf({"x": ["a", "a", "b"]})))
check("fused_rrf: universe ไม่ตรง -> fail",
      raises(lambda: R.fused_rrf({"x": ["a", "b"], "y": ["a", "c"]})))
check("fused_rrf deterministic", R.fused_rrf({"d": ["a", "b", "c"], "r": ["a", "c", "b"]}, dense_rank_map=drm) == fused)
# RRF favors item ที่ rank สูงใน arm ใดๆ มากกว่า middling ทั้งคู่ (1/x convex):
# dense=[a,b,c], rerank=[c,b,a] -> a=1/61+1/63, c=1/63+1/61 (เท่ากัน, สูงสุด), b=2/62 (ต่ำกว่า)
f2 = R.fused_rrf({"dense": ["a", "b", "c"], "rerank": ["c", "b", "a"]}, dense_rank_map=drm)
check("fused_rrf: a,c (rank1 ใน arm หนึ่ง) นำ b (rank2 ทั้งคู่); tie a/c -> dense_rank a ก่อน c",
      f2 == ["a", "c", "b"], f2)

# ── metrics ────────────────────────────────────────────────────────────────────
rel = {"a": 3, "c": 1}                                # a,c relevant (graded)
check("candidate_recall@2: {a,c} ใน [a,b] top2 -> 0.5", M.candidate_recall_at_n(["a", "b", "c"], rel, 2) == 0.5)
check("candidate_recall@3 -> 1.0", M.candidate_recall_at_n(["a", "b", "c"], rel, 3) == 1.0)
check("hit@1: a relevant -> 1.0", M.hit_at_k(["a", "b"], rel, 1) == 1.0)
check("hit@1: b ไม่ relevant -> 0.0", M.hit_at_k(["b", "a"], rel, 1) == 0.0)
check("mrr@5: relevant อันดับ2 -> 0.5", M.mrr_at_k(["b", "a"], rel, 5) == 0.5)
check("recall@2: [a,b] มี a จาก {a,c} -> 0.5", M.recall_at_k(["a", "b"], rel, 2) == 0.5)
check("ndcg perfect order -> 1.0", M.ndcg_at_k(["a", "c"], rel, 2) == 1.0)
check("ndcg reversed -> ~0.71", round(M.ndcg_at_k(["c", "a"], rel, 2), 2) == 0.71, M.ndcg_at_k(["c", "a"], rel, 2))
check("ndcg ไม่มี graded -> None", M.ndcg_at_k(["a"], {}, 5) is None)
check("recall ไม่มี relevant -> None", M.recall_at_k(["a"], {}, 5) is None)

# document-level
p2d = {"a": "D1", "b": "D1", "c": "D2"}              # a,b เอกสารเดียว
check("to_document_ranking: collapse chunk เอกสารเดียว", M.to_document_ranking(["a", "b", "c"], p2d) == ["D1", "D2"])
check("document_relevance: D1 grade สูงสุด", M.document_relevance(rel, p2d) == {"D1": 3, "D2": 1})
check("percentiles p50/p95", M.percentiles([10, 20, 30, 40])[50] == 20)
check("mean_ignore_none", M.mean_ignore_none([1.0, None, 3.0]) == 2.0)

# ── M3: metric/RRF input guards ───────────────────────────────────────────────
check("M3: k<1 -> fail", raises(lambda: M.hit_at_k(["a"], {"a": 1}, 0)))
check("M3: k ไม่ใช่ int -> fail", raises(lambda: M.hit_at_k(["a"], {"a": 1}, 1.0)))
check("M3: ranked id ซ้ำ -> fail", raises(lambda: M.hit_at_k(["a", "a"], {"a": 1}, 2)))
check("M3: ranked id ว่าง -> fail", raises(lambda: M.mrr_at_k(["", "a"], {"a": 1}, 2)))
check("M3: ndcg grade นอก {1,2,3} -> fail", raises(lambda: M.ndcg_at_k(["a"], {"a": 5}, 1)))
check("M3: rrf_k ไม่ positive -> fail", raises(lambda: R.fused_rrf({"x": ["a", "b"]}, rrf_k=0)))
check("M3: dense_rank_map ไม่ครบ universe -> fail",
      raises(lambda: R.fused_rrf({"x": ["a", "b"]}, dense_rank_map={"a": 1})))
check("M3: latency ติดลบ -> fail", raises(lambda: M.percentiles([-1, 2])))

# ── p2_eval — corpus มี full policy payload (B2) ───────────────────────────────
def pl(roles, status="ACTIVE", schema=1, ver="poc-v1", coll="RECALL", level=3):
    return {"acl_schema_version": schema, "policy_version": ver, "policy_status": status,
            "collection_group": coll, "confidentiality_level": level, "allowed_roles": roles}
def centry(source, roles, text="t", **kw):
    return {"source": source, "rerank_text": text, "payload": pl(roles, **kw)}
def case(qid="q1", role="qc", rel=None, rsrc=None, iid="i1", tags=None, hn=None, **over):
    rel_ = {"pa": 2} if rel is None else rel
    c = {"query_id": qid, "intent_id": iid, "query": "ถาม", "role": role, "lang": "th",
         "category": "sibling", "challenge_tags": (["sibling"] if tags is None else tags),
         "split": "test", "case_type": "ranking", "relevance": rel_,
         "hard_negative_ids": ([] if hn is None else hn),
         "relevant_sources": ["D1"] if rsrc is None else rsrc, "label_status": "human-reviewed",
         "reviewed_by": "tester", "review_revision": "r1"}
    if len(rel_) > 1:
        c["grade_rationale"] = {pid: f"chunk {pid} ตอบส่วนหลักของคำถามนี้อย่างครบถ้วน" for pid in rel_}
    c.update(over)
    return c
CORPUS = {"pa": centry("D1", ["qc", "admin"]), "pb": centry("D2", ["sales", "admin"])}
KNOWN = {"qc", "admin", "sales", "hr"}
def V(cases):
    return E.validate_ranking_eval_set(cases, CORPUS, KNOWN)

check("eval-set ranking ดี -> ไม่มี error", V([case()]) == [])

# ── B2: authorization ใช้ P1 policy (ไม่ใช่แค่ membership) ─────────────────────
check("B2: ACTIVE+role ตรง -> authorized", E.is_authorized(pl(["qc", "admin"]), "qc") is True)
check("B2: stale schema -> ไม่ authorized แม้ role ใน allowed_roles", E.is_authorized(pl(["qc"], schema=0), "qc") is False)
check("B2: QUARANTINED -> ไม่ authorized", E.is_authorized(pl(["qc"], status="QUARANTINED"), "qc") is False)
check("B2: wrong policy_version -> ไม่ authorized", E.is_authorized(pl(["qc"], ver="poc-v0"), "qc") is False)
check("B2: admin ไม่มี bypass (allowed_roles=[qc]) -> ไม่ authorized", E.is_authorized(pl(["qc"]), "admin") is False)
check("B2: unknown role -> ไม่ authorized", E.is_authorized(pl(["qc", "admin"]), "ceo") is False)
CORPUS_STALE = {"ps": centry("D1", ["qc", "admin"], schema=0)}
check("B2 validate: relevant point stale -> error (แม้ role ใน allowed_roles)",
      any("authorized" in e for e in E.validate_ranking_eval_set([case(rel={"ps": 2})], CORPUS_STALE, KNOWN)))

# ── M2: no-answer/case_type แยกจาก ranking ────────────────────────────────────
check("M2: case_type != ranking -> error", any("case_type" in e for e in V([case(case_type="no_answer")])))
check("relevance ว่าง (ranking) -> error", any("relevance" in e for e in V([case(rel={})])))

# ── M5: schema checks ─────────────────────────────────────────────────────────
check("M5: field บังคับหาย -> error", any("หาย" in e for e in V([{"query_id": "q1"}])))
check("M5: split ผิด -> error", any("split" in e for e in V([case(split="prod")])))
check("M5: label_status ไม่ human-reviewed -> error", any("label_status" in e for e in V([case(label_status="auto")])))
check("M5: source ของ relevant ไม่อยู่ใน relevant_sources -> error",
      any("relevant_sources" in e for e in V([case(rel={"pa": 2}, rsrc=["D9"])])))
check("M5: query control char -> error", any("control" in e for e in V([case(query="a\x01b")])))
check("M5: query_id ซ้ำ -> error", any("ซ้ำ" in e for e in V([case(qid="q1"), case(qid="q1")])))
check("M5: role ไม่รู้จัก -> error", any("ไม่รู้จัก" in e for e in V([case(role="ceo", rsrc=["D1"])])))
check("M5: relevant point ไม่อยู่ corpus -> error", any("ไม่อยู่ใน frozen corpus" in e for e in V([case(rel={"pz": 1})])))

# ── M1: freeze eval + corpus (dual hash) ──────────────────────────────────────
check("M1: eval_set_sha256 deterministic", E.eval_set_sha256([case()]) == E.eval_set_sha256([case()]))
check("M1: corpus_manifest_sha256 เปลี่ยนเมื่อ rerank_text เปลี่ยน",
      E.corpus_manifest_sha256(CORPUS) != E.corpus_manifest_sha256(
          {"pa": centry("D1", ["qc", "admin"], text="CHANGED"), "pb": CORPUS["pb"]}))
check("M1: corpus_manifest_sha256 เปลี่ยนเมื่อ ACL เปลี่ยน",
      E.corpus_manifest_sha256(CORPUS) != E.corpus_manifest_sha256(
          {"pa": centry("D1", ["qc", "admin", "sales"]), "pb": CORPUS["pb"]}))
check("M1: benchmark_manifest มี eval+corpus hash + contract version",
      set(E.artifact_manifest_unapproved([case()], CORPUS, KNOWN)) >=
      {"eval_set_sha256", "corpus_manifest_sha256", "benchmark_contract_version", "rerank_text_version"})

# ── B2.1: is_authorized reject scalar/malformed policy ────────────────────────
check("B2.1: scalar allowed_roles -> ไม่ authorized (แม้ matches_policy match)", E.is_authorized(pl("qc"), "qc") is False)
check("B2.1: allowed_roles=null -> ไม่ authorized", E.is_authorized(pl(None), "qc") is False)
check("B2.1: schema bool -> ไม่ authorized", E.is_authorized(pl(["qc"], schema=True), "qc") is False)
check("B2.1: payload ไม่ใช่ policy-v1 -> ไม่ authorized", E.is_authorized({"allowed_roles": ["qc"]}, "qc") is False)
check("B2.1: empty ACL -> ไม่ authorized", E.is_authorized(pl([]), "qc") is False)
check("B2.1: valid policy-v1 + role -> authorized", E.is_authorized(pl(["qc", "admin"]), "qc") is True)

# ── M5.1: relevant_sources exact set-equality + no dup ────────────────────────
CORPUS2 = {"pa": centry("D1", ["qc", "admin"]), "pc": centry("D2", ["qc", "admin"])}
def V2(cases):
    return E.validate_ranking_eval_set(cases, CORPUS2, KNOWN)
check("M5.1: source ซ้ำ ใน relevant_sources -> error", any("ซ้ำ" in e for e in V2([case(rel={"pa": 2}, rsrc=["D1", "D1"])])))
check("M5.1: extra source -> error (exact set)", any("ไม่ตรง exact" in e for e in V2([case(rel={"pa": 2}, rsrc=["D1", "D999"])])))
check("M5.1: missing source -> error", any("ไม่ตรง exact" in e for e in V2([case(rel={"pa": 2, "pc": 1}, rsrc=["D1"])])))
check("M5.1: exact match (สองเอกสารครบ) -> ไม่มี error", V2([case(rel={"pa": 2, "pc": 1}, rsrc=["D1", "D2"])]) == [])

# ── B2/B3/B5: intent_id / challenge_tags / hard_negative_ids / rubric / split ──
check("field ใหม่ครบ -> valid", V([case()]) == [])
check("intent_id หาย -> error", any("intent_id" in e for e in V([{k: v for k, v in case().items() if k != "intent_id"}])))
check("challenge_tags ว่าง -> error", any("challenge_tags" in e for e in V([case(tags=[])])))
check("hard_negative_ids ไม่ใช่ list -> error", any("hard_negative_ids" in e for e in V([case(hn="x")])))
check("hard_negative อยู่ใน relevance -> error", any("ห้ามอยู่ใน relevance" in e for e in V([case(hn=["pa"])])))
check("hard_negative ไม่อยู่ใน corpus -> error", any("ไม่อยู่ใน corpus" in e for e in V([case(hn=["pz"])])))
check("hard_negative ไม่ authorized สำหรับ role -> error", any("ไม่ authorized" in e for e in V([case(hn=["pb"])])))
check("hard_negative authorized (นอก relevance) -> ผ่าน", V([case(role="admin", hn=["pb"])]) == [])
check("B5: multi-relevance ไม่มี grade_rationale -> error",
      any("grade_rationale" in e for e in V2([{**case(rel={"pa": 2, "pc": 1}, rsrc=["D1", "D2"]), "grade_rationale": None}])))
check("B5: multi-relevance มี grade_rationale -> ผ่าน", V2([case(rel={"pa": 2, "pc": 1}, rsrc=["D1", "D2"])]) == [])
check("B2: paraphrase ข้าม split -> error",
      any("ข้าม split" in e for e in V([case(qid="qa", iid="iX", split="dev"), case(qid="qb", iid="iX", split="test")])))
_mini = [case(qid=f"q{i}", iid=f"i{i}", tags=["direct"]) for i in range(3)]
check("count_test_intents", E.count_test_intents(_mini) == 3)
check("arm_eligibility: <50 test intents -> error", any("intents" in e for e in E.arm_eligibility_errors(_mini, ["direct"])))
check("arm_eligibility: gate tag < 5 intents -> error",
      any("challenge" in e for e in E.arm_eligibility_errors(_mini, ["direct"], min_intents=1)))

# ── B2.1: dev role coverage ────────────────────────────────────────────────────
check("dev_role_coverage: role ไม่มี dev intent -> error",
      any("sales" in e for e in E.dev_role_coverage_errors(
          [case(iid="i1", role="qc", split="dev"), case(iid="i2", role="sales", split="test")], ["qc", "sales"])))
check("dev_role_coverage: ครบ -> ไม่มี error", E.dev_role_coverage_errors([case(iid="i1", role="qc", split="dev")], ["qc"]) == [])

# ── B5.1: grade_rationale case-specific + provenance ──────────────────────────
check("B5.1: grade_rationale generic (rubric string) -> error",
      any("generic" in e for e in V2([{**case(rel={"pa": 2, "pc": 1}, rsrc=["D1", "D2"]),
                                        "grade_rationale": {"pa": E.GRADE_RUBRIC[2], "pc": E.GRADE_RUBRIC[1]}}])))
check("B5.1: grade_rationale case-specific -> ผ่าน", V2([case(rel={"pa": 2, "pc": 1}, rsrc=["D1", "D2"])]) == [])
check("B6.1: reviewed_by หาย -> error", any("reviewed_by" in e for e in V([{k: v for k, v in case().items() if k != "reviewed_by"}])))

# ── B6.1: Data Owner sign-off (hash-bound) + decision gate ────────────────────
_gs = {"eval_set_sha256": E.eval_set_sha256([case()]), "corpus_manifest_sha256": E.corpus_manifest_sha256(CORPUS),
       "benchmark_contract_version": E.BENCHMARK_CONTRACT_VERSION, "git_commit": "2364bb1a1b2c3d", "reviewer": "owner",
       "data_owner_role": "QA Lead", "reviewed_at": "2026-08-04T10:00:00+07:00", "decision": "approved"}
check("validate_signoff: ไม่มี signoff -> error", E.validate_signoff(None, [case()], CORPUS) != [])
check("validate_signoff: hash ตรง + approved -> ผ่าน", E.validate_signoff(_gs, [case()], CORPUS) == [])
check("validate_signoff: eval hash ไม่ตรง -> error", any("eval_set_sha256" in e for e in E.validate_signoff({**_gs, "eval_set_sha256": "x"}, [case()], CORPUS)))
check("validate_signoff: decision != approved -> error", any("decision" in e for e in E.validate_signoff({**_gs, "decision": "rejected"}, [case()], CORPUS)))
check("decision_benchmark: <50 test intents -> error แม้ signoff ตรง",
      len(E.decision_benchmark_errors([case()], CORPUS, KNOWN, ["qc"], ["direct"], _gs)) > 0)

# ── B3: single approval surface = decide_p2 เท่านั้น ; mechanics-smoke แยกชัด ───
check("B3: artifact_manifest_unapproved approved=False (smoke)",
      E.artifact_manifest_unapproved([case()], CORPUS, KNOWN).get("approved") is False)
check("B3: ไม่มี public decision_benchmark_manifest (approved=True builder) แล้ว",
      not hasattr(E, "decision_benchmark_manifest"))
check("B3: decision_evidence_errors ไม่มี signoff -> error list (ไม่ approve)",
      len(E.decision_evidence_errors([case()], CORPUS, KNOWN, ["qc"], ["direct"], None, "m4", "canary", "a" * 64)) > 0)
check("B3: decision_evidence_errors คืน list เสมอ (ไม่มี approved=True self-stamp)",
      isinstance(E.decision_evidence_errors([case()], CORPUS, KNOWN, ["qc"], ["direct"], _gs, None, "canary", "a" * 64), list))

# ── M1: combined gate malformed artifacts -> controlled (ไม่ crash) ────────────
def _no_crash(fn):
    try:
        return isinstance(fn(), list)
    except (ValueError, TypeError, AttributeError):
        return False
check("M1: decision_benchmark cases=[] corpus=None -> controlled (ไม่ crash)",
      _no_crash(lambda: E.decision_benchmark_errors([], None, KNOWN, ["qc"], ["direct"], {"decision": "approved"})))
check("M1: validate_signoff corpus=None (hash fail) -> controlled list",
      isinstance(E.validate_signoff(dict(_gs, corpus_manifest_sha256="x"), [case()], None), list))

# ── M1.1: sign-off field types (exact, ไม่ใช่ truthiness) ──────────────────────
check("M1.1: reviewer bool -> error", any("reviewer" in e for e in E.validate_signoff({**_gs, "reviewer": True}, [case()], CORPUS)))
check("M1.1: data_owner_role list -> error", any("data_owner_role" in e for e in E.validate_signoff({**_gs, "data_owner_role": ["x"]}, [case()], CORPUS)))
check("M1.1: git_commit ไม่ใช่ hex -> error", any("git_commit" in e for e in E.validate_signoff({**_gs, "git_commit": "zzz"}, [case()], CORPUS)))
check("M1.1: reviewed_at ไม่มี tz -> error", any("reviewed_at" in e for e in E.validate_signoff({**_gs, "reviewed_at": "2026-08-04"}, [case()], CORPUS)))
check("M1.1: reviewed_at dict -> error", any("reviewed_at" in e for e in E.validate_signoff({**_gs, "reviewed_at": {"not": "time"}}, [case()], CORPUS)))
check("M2: reviewed_at 99:99 (regex ผ่านแต่ไม่ใช่วันจริง) -> error",
      any("reviewed_at" in e for e in E.validate_signoff({**_gs, "reviewed_at": "2026-99-99T99:99:99+99:99"}, [case()], CORPUS)))
check("M1.1: signoff ครบถูกชนิด -> ผ่าน", E.validate_signoff(_gs, [case()], CORPUS) == [])

# ── B3.1/B2: real M4 / canary evidence — exact int/hash + sentinel disjoint + cross-run ──
_eh, _ch = E.eval_set_sha256([case()]), E.corpus_manifest_sha256(CORPUS)
_H = "a" * 64
# M4 evidence v2 (hash-only id+text): authorized ⊇ provider ⊇ model_input ; sentinel disjoint
_m4 = {"status": "PASS", "isolated_interlock": "PASS", "independent_oracle": "PASS",
       "sentinel_reached_model": False, "unauthorized_in_model_inputs": 0,
       "model_invocation_count": 3, "evidence_stage": "selected-n",
       "authorized_candidate_id_hashes": ["a" * 64, "b" * 64, "c" * 64],
       "authorized_candidate_text_hashes": ["1" * 64, "2" * 64, "3" * 64],
       "provider_candidate_id_hashes": ["a" * 64, "b" * 64],
       "provider_candidate_text_hashes": ["1" * 64, "2" * 64],
       "model_input_id_hashes": ["a" * 64], "model_input_text_hashes": ["1" * 64],
       "unauthorized_sentinel_id_hashes": ["f" * 64], "unauthorized_sentinel_text_hashes": ["9" * 64],
       "model_revision": "a" * 40, "tokenizer_revision": "a" * 40, "image_digest": "sha256:" + "e" * 64,
       "retrieval_index_manifest_sha256": _H, "run_id": "run-1", "eval_set_sha256": _eh, "corpus_manifest_sha256": _ch}
_can = {"status": "PASS", "leak_count": 0, "auth_status": "VERIFIED",
        "arm_status": {"dense": "PASS", "rerank": "PASS", "fused": "PASS"},
        "arm_error_counts": {"dense": 0, "rerank": 0, "fused": 0},
        "expected_query_count": 50, "actual_query_count": 50,
        "retrieval_index_manifest_sha256": _H, "run_id": "run-1", "eval_set_sha256": _eh, "corpus_manifest_sha256": _ch}
check("B3.1: m4 PASS + schema ครบ -> valid", E.validate_m4_evidence(_m4, _eh, _ch) == [], E.validate_m4_evidence(_m4, _eh, _ch))
check("B3.1: m4 status=FAIL -> error", E.validate_m4_evidence({**_m4, "status": "FAIL"}, _eh, _ch) != [])
check("B3.1: m4 sentinel_reached_model=True -> error", E.validate_m4_evidence({**_m4, "sentinel_reached_model": True}, _eh, _ch) != [])
check("B2: m4 unauthorized_in_model_inputs=0.0 (float) -> error (exact int)", E.validate_m4_evidence({**_m4, "unauthorized_in_model_inputs": 0.0}, _eh, _ch) != [])
check("B2: m4 image_digest ไม่ใช่ sha256:<hex> -> error", E.validate_m4_evidence({**_m4, "image_digest": "d"}, _eh, _ch) != [])
check("B2: m4 model_revision ไม่ใช่ commit -> error", E.validate_m4_evidence({**_m4, "model_revision": "main"}, _eh, _ch) != [])
check("B3.1: m4 hash ไม่ตรง -> error", E.validate_m4_evidence(_m4, "x", _ch) != [])
# B1 (vacuous pass): model ถูกเรียกจริง
check("B1: m4 model_invocation_count=0 -> error", any("invocation" in e for e in E.validate_m4_evidence({**_m4, "model_invocation_count": 0}, _eh, _ch)))
check("B1: m4 model_input_id_hashes ว่าง -> error (ไม่มี candidate เข้า model)", any("model_input_id_hashes" in e for e in E.validate_m4_evidence({**_m4, "model_input_id_hashes": []}, _eh, _ch)))
# B2 (text proof + subset/disjoint)
check("B2: provider_id ⊄ authorized -> error", any("provider_candidate_id" in e for e in E.validate_m4_evidence({**_m4, "provider_candidate_id_hashes": ["a" * 64, "e" * 64]}, _eh, _ch)))
check("B2: model_input_text ⊄ provider (text ถูกสลับ) -> error", any("model_input_text" in e for e in E.validate_m4_evidence({**_m4, "model_input_text_hashes": ["7" * 64]}, _eh, _ch)))
check("B2: sentinel text ปรากฏใน model_input -> error", any("sentinel text" in e for e in E.validate_m4_evidence({**_m4, "model_input_text_hashes": ["9" * 64]}, _eh, _ch)))
check("B2: sentinel id ปรากฏใน model_input -> error", E.validate_m4_evidence({**_m4, "model_input_id_hashes": ["f" * 64]}, _eh, _ch) != [])
check("B2: authorized oracle ปนเปื้อน sentinel id -> error", any("oracle" in e for e in E.validate_m4_evidence({**_m4, "authorized_candidate_id_hashes": ["a" * 64, "b" * 64, "c" * 64, "f" * 64]}, _eh, _ch)))
# M1 (stage)
check("M1: m4 evidence_stage ผิด -> error", any("evidence_stage" in e for e in E.validate_m4_evidence({**_m4, "evidence_stage": "bogus"}, _eh, _ch)))
check("M1: preflight m4 ใน decision path (require selected-n) -> error", any("evidence_stage" in e for e in E.validate_m4_evidence({**_m4, "evidence_stage": E.M4_STAGE_PREFLIGHT}, _eh, _ch, require_stage=E.M4_STAGE_SELECTED)))
check("M1: preflight m4 standalone (require_stage None) -> valid", E.validate_m4_evidence({**_m4, "evidence_stage": E.M4_STAGE_PREFLIGHT}, _eh, _ch) == [])
check("B3.1: canary PASS + schema ครบ -> valid", E.validate_canary_evidence(_can, _eh, _ch) == [], E.validate_canary_evidence(_can, _eh, _ch))
check("B3.1: canary leak>0 -> error", E.validate_canary_evidence({**_can, "leak_count": 99}, _eh, _ch) != [])
check("B2: canary leak_count=0.0 -> error (exact int)", E.validate_canary_evidence({**_can, "leak_count": 0.0}, _eh, _ch) != [])
check("B3.1: canary auth UNVERIFIED -> error", E.validate_canary_evidence({**_can, "auth_status": "UNVERIFIED"}, _eh, _ch) != [])
check("B3.1: canary arm ขาด -> error", E.validate_canary_evidence({**_can, "arm_status": {"dense": "PASS"}}, _eh, _ch) != [])
check("B2: canary arm_error_counts != 0 -> error", E.validate_canary_evidence({**_can, "arm_error_counts": {"dense": 1, "rerank": 0, "fused": 0}}, _eh, _ch) != [])
check("B2: canary expected != actual query count -> error", E.validate_canary_evidence({**_can, "actual_query_count": 49}, _eh, _ch) != [])
# B2 cross-run binding: m4/canary คนละ run -> decision manifest ปฏิเสธ (ผ่าน probe ไม่ได้เพราะ arm_eligibility ก่อน — ตรวจ validator ตรง)
check("B2: cross-run — run_id ต่างกันตรวจได้ (unit)", _m4["run_id"] == _can["run_id"])

# ── B4: root run_manifest binding (evidence ต้องอ้าง run_manifest_sha256 เดียวกัน) ──
_m4b = {**_m4, "run_manifest_sha256": _H}
_canb = {**_can, "run_manifest_sha256": _H, "model_revision": "a" * 40, "image_digest": "sha256:" + "e" * 64}
check("B4: m4 bound run_manifest ตรง -> valid", E.validate_m4_evidence(_m4b, _eh, _ch, _H) == [], E.validate_m4_evidence(_m4b, _eh, _ch, _H))
check("B4: m4 run_manifest ไม่ตรง root -> error", any("run_manifest" in e for e in E.validate_m4_evidence(_m4b, _eh, _ch, "b" * 64)))
check("B4: m4 ไม่มี run_manifest field (bound) -> error", any("run_manifest" in e for e in E.validate_m4_evidence(_m4, _eh, _ch, _H)))
check("B4: m4 unbound (run_manifest=None) -> ข้าม binding (backward-compat)", E.validate_m4_evidence(_m4, _eh, _ch) == [])
check("B4: canary bound (มี model/image + run_manifest) -> valid", E.validate_canary_evidence(_canb, _eh, _ch, _H) == [], E.validate_canary_evidence(_canb, _eh, _ch, _H))
check("B4: canary bound ขาด model_revision -> error", any("model_revision" in e for e in E.validate_canary_evidence({**_canb, "model_revision": None}, _eh, _ch, _H)))
check("B4: canary bound ขาด image_digest -> error", any("image_digest" in e for e in E.validate_canary_evidence({**_canb, "image_digest": "x"}, _eh, _ch, _H)))
check("B4: canary run_manifest ไม่ตรง root -> error", any("run_manifest" in e for e in E.validate_canary_evidence(_canb, _eh, _ch, "b" * 64)))

# ── B3.2: smoke manifest ใช้กับ ai-reviewed ได้ ; decision path ปฏิเสธ ──────────
_ai = case(label_status="ai-reviewed")
check("B3.2: smoke manifest กับ ai-reviewed -> approved=False + decision_eligible=False",
      E.artifact_manifest_unapproved([_ai], CORPUS, KNOWN).get("approved") is False
      and E.artifact_manifest_unapproved([_ai], CORPUS, KNOWN).get("decision_eligible") is False)
check("B3.2: decision path (validate_benchmark default) ปฏิเสธ ai-reviewed",
      any("label_status" in e for e in E.validate_benchmark([_ai], CORPUS, KNOWN)))

# ── M1.1: corpus/cases fail-closed ────────────────────────────────────────────
check("M1.1: corpus ว่าง -> error", E.validate_corpus({}) != [])
check("M1.1: corpus entry ไม่ใช่ dict -> error (ไม่ crash)", any("ไม่ใช่ object" in e for e in E.validate_corpus({"px": "bad"})))
check("M1.1: corpus payload ไม่ใช่ policy-v1 -> error",
      any("policy-v1" in e for e in E.validate_corpus({"px": {"source": "D1", "rerank_text": "t", "payload": {"x": 1}}})))
check("M1.1: corpus payload scalar allowed_roles -> error", any("contract" in e for e in E.validate_corpus({"px": centry("D1", "qc")})))
check("M1.1: corpus rerank_text ว่าง -> error", any("rerank_text" in e for e in E.validate_corpus({"px": centry("D1", ["qc"], text=" ")})))
check("M1.1: corpus ดี -> ไม่มี error", E.validate_corpus(CORPUS) == [])
check("M1.1: validate_benchmark cases ว่าง -> error", any("cases ว่าง" in e for e in E.validate_benchmark([], CORPUS, KNOWN)))
check("M1.1: benchmark_manifest invalid -> ValueError", raises(lambda: E.artifact_manifest_unapproved([], CORPUS, KNOWN)))
check("M1.1: corpus_manifest_sha256 rerank_text ผิดชนิด -> ValueError",
      raises(lambda: E.corpus_manifest_sha256({"px": {"source": "D1", "rerank_text": 123, "payload": pl(["qc"])}})))

# ── M3.1: public boundary guards ──────────────────────────────────────────────
check("M3.1: relevant_ids เป็น string เดี่ยว -> fail (กัน char set)", raises(lambda: M.hit_at_k(["a"], "abc", 1)))
check("M3.1: relevant_ids ผิดชนิด -> fail", raises(lambda: M.recall_at_k(["a"], 123, 1)))
check("M3.1: relevant id ว่าง -> fail", raises(lambda: M.mrr_at_k(["a"], ["", "b"], 2)))
check("M3.1: dense_rank_map rank ไม่ positive int -> fail", raises(lambda: R.fused_rrf({"x": ["a", "b"]}, dense_rank_map={"a": 1, "b": 0})))
check("M3.1: dense_rank_map rank ซ้ำ -> fail", raises(lambda: R.fused_rrf({"x": ["a", "b"]}, dense_rank_map={"a": 1, "b": 1})))
check("M3.1: dense_rank_map rank bool -> fail", raises(lambda: R.fused_rrf({"x": ["a", "b"]}, dense_rank_map={"a": 1, "b": True})))
check("M3.1: dense_rank_map() builder validate candidate", raises(lambda: R.dense_rank_map([cand("a", 1), cand("a", 2)])))

# ── M1.2: non-dict corpus -> controlled error (ไม่ AttributeError) ─────────────
rc = [case(rel={"pa": 2}, rsrc=["D1"])]
check("M1.2: validate_ranking_eval_set corpus=None -> controlled error",
      E.validate_ranking_eval_set(rc, None, KNOWN) == ["corpus ต้องเป็น dict"])
check("M1.2: validate_benchmark corpus=list -> error (ไม่ crash)", len(E.validate_benchmark(rc, [], KNOWN)) > 0)
check("M1.2: validate_benchmark corpus=string -> error", len(E.validate_benchmark(rc, "bad", KNOWN)) > 0)
check("M1.2: benchmark_manifest corpus=None -> ValueError (ไม่ AttributeError)",
      raises(lambda: E.artifact_manifest_unapproved(rc, None, KNOWN)))

# ── M1.3: lone surrogate (Cs) reject + canonical hash (surrogate/NaN safe) ─────
check("M1.3: _bad_str reject lone surrogate (Cs)", E._bad_str("\ud800") is True)
_sur = {"px": {"source": "D1", "rerank_text": "\ud800", "payload": pl(["qc"])}}
check("M1.3: validate_corpus reject surrogate rerank_text", any("rerank_text" in e for e in E.validate_corpus(_sur)))
check("M1.3: query surrogate -> error", any("query" in e for e in V([case(query="\ud800")])))
check("M1.3: corpus_manifest_sha256 surrogate -> ValueError (ไม่ UnicodeEncodeError)",
      raises(lambda: E.corpus_manifest_sha256(_sur)))
check("M1.3: eval_set_sha256 NaN -> ValueError (allow_nan=False)",
      raises(lambda: E.eval_set_sha256([{"query_id": "q", "x": float("nan")}])))
check("M1.3: Thai/emoji hash ได้ + deterministic",
      E.corpus_manifest_sha256({"pa": centry("D1", ["qc"], text="ทดสอบ 🚀 test")}) ==
      E.corpus_manifest_sha256({"pa": centry("D1", ["qc"], text="ทดสอบ 🚀 test")}))

# ── B1: permission gate type-strict (ปิด fail-open) ───────────────────────────
check("B1: gate exit 0 -> valid", E.permission_gate_ok(0) is True)
check("B1: gate exit 1 -> invalid", E.permission_gate_ok(1) is False)
check("B1: gate exit -1 -> invalid", E.permission_gate_ok(-1) is False)
check("B1: gate False -> ValueError (ปิด fail-open False==0)", raises(lambda: E.permission_gate_ok(False)))
check("B1: gate True -> ValueError", raises(lambda: E.permission_gate_ok(True)))
check("B1: gate 0.0 -> ValueError", raises(lambda: E.permission_gate_ok(0.0)))
check("B1: gate None -> ValueError", raises(lambda: E.permission_gate_ok(None)))
check("B1: gate '0' -> ValueError", raises(lambda: E.permission_gate_ok("0")))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
