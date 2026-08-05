"""
P2 eval-set validation + freeze (Codex B2/M1/M2/M5) — pure, offline
- authorization ใช้ P1 policy path เดียวกับ candidate provider (compile_retrieval_filter + matches_policy)
  ไม่ reimplement membership check (B2)
- freeze ทั้ง eval cases และ corpus manifest (M1) — "frozen corpus" ต้องมี corpus hash ไม่ใช่แค่ cases
- ranking dataset = case_type "ranking" เท่านั้น, relevance ไม่ว่าง ; no-answer แยก abstention suite (M2)
"""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime

import policy as P

BENCHMARK_CONTRACT_VERSION = "p2-bench-v2"      # v2: intent_id/challenge_tags/hard_negative_ids/rubric
RERANK_TEXT_VERSION = "heading+child-v1"
LOCKED_GRADES = (1, 2, 3)                       # graded relevance allowlist (exact int)
# grade rubric (B5) — multi-relevance case ต้องมี rationale ต่อ pid
GRADE_RUBRIC = {3: "ตอบครบโดยลำพัง (fully answers alone)",
                2: "supporting/partial", 1: "contextual"}
REQUIRED_CASE_FIELDS = ("query_id", "intent_id", "query", "role", "lang", "category",
                        "challenge_tags", "split", "case_type", "relevance",
                        "hard_negative_ids", "relevant_sources", "label_status",
                        "reviewed_by", "review_revision")   # B6.1: provenance บังคับ
SIGNOFF_FIELDS = ("eval_set_sha256", "corpus_manifest_sha256", "benchmark_contract_version",
                  "git_commit", "reviewer", "data_owner_role", "reviewed_at", "decision")
VALID_SPLITS = frozenset({"dev", "test"})
VALID_LABEL_STATUS = frozenset({"human-reviewed"})     # decision gate (B6: AI review ไม่พอ)
AI_LABEL_STATUS = frozenset({"draft", "ai-reviewed"})  # ระหว่าง AI review เท่านั้น
SMOKE_LABEL_STATUS = VALID_LABEL_STATUS | AI_LABEL_STATUS   # B3.2: smoke ยอม ai-reviewed ได้
MIN_TEST_INTENTS = 50                                  # arm-eligibility (B2): independent intent groups
ARMS_EXACT = ("dense", "rerank", "fused")              # B3.1: canary ต้องครอบทุก arm
_ISO8601_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$")


def _good_str(x) -> bool:
    return not _bad_str(x)


def _is_hex_commit(x) -> bool:
    return isinstance(x, str) and 7 <= len(x) <= 64 and all(c in "0123456789abcdef" for c in x.lower())


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(x) -> bool:
    return isinstance(x, str) and bool(_SHA256.match(x))


def _is_image_digest(x) -> bool:
    return isinstance(x, str) and x.startswith("sha256:") and _is_sha256(x[7:])


def _valid_iso_tz(x) -> bool:
    """M2: ต้อง parse เป็น datetime จริงที่มี timezone (regex อย่างเดียวยอม 99:99 ได้)"""
    if not isinstance(x, str):
        return False
    try:
        dt = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.utcoffset() is not None


def _exact_zero_int(x) -> bool:
    return type(x) is int and x == 0


# M4 evidence stages (M1): preflight (unlock N-sweep) vs selected-N (bind final decision)
M4_STAGE_PREFLIGHT = "preflight-n50"
M4_STAGE_SELECTED = "selected-n"
M4_STAGES = (M4_STAGE_PREFLIGHT, M4_STAGE_SELECTED)
M4_SCHEMA_VERSION = "p2-m4-v4"           # v4: per_case[] authoritative + recompute-from-body + manifest binding
N_SET_M4 = (10, 20, 30, 50)


def _pair_sha256(point_id_sha256, text_sha256) -> str:
    """canonical pair formula (B3) — ต้อง recompute ตรงกับ pair_sha256 ที่ evidence อ้าง"""
    return hashlib.sha256(f"{point_id_sha256}:{text_sha256}".encode("utf-8")).hexdigest()


# exact hash-only schema (M1) — reject unknown/raw fields ทุกระดับ
_M4_TOP_KEYS = frozenset({"schema_version", "status", "isolated_interlock", "independent_oracle", "sentinel_reached_model",
                          "unauthorized_in_model_inputs", "scorer_kind", "evidence_stage", "selected_n", "selection_digest",
                          "decision_eligible", "m4_case_manifest_sha256", "per_case", "raw_evidence_sha256",
                          "model_revision", "tokenizer_revision", "image_digest", "model_file_manifest_sha256",
                          "inference_config", "retrieval_index_manifest_sha256", "run_id", "eval_set_sha256",
                          "corpus_manifest_sha256", "run_manifest_sha256", "run_receipt_sha256"})
_M4_CASE_KEYS = frozenset({"case_id_sha256", "role_identity_sha256", "effective_role", "category", "selected_n",
                           "query_vector_sha256", "unfiltered_query_vector_sha256", "filtered_query_vector_sha256",
                           "unfiltered_limit", "filtered_limit", "pair_components", "unfiltered_topn_pairs",
                           "observed_sentinel_ranks", "provider_pairs", "model_input_pairs", "rerank_output_pairs",
                           "model_call_count", "model_input_count", "score_count", "all_scores_finite", "status"})
_M4_COMP_KEYS = frozenset({"point_id_sha256", "rerank_text_sha256", "pair_sha256"})
_M4_FROZEN_KEYS = frozenset({"cases", "required_categories", "evaluated_roles", "m4_case_manifest_sha256"})
_M4_FCASE_KEYS = frozenset({"role_identity_sha256", "effective_role", "category", "query_vector_sha256",
                            "authorized_pairs", "sentinel_pairs"})
# M4 run request (frozen pin/image/index) — validate_m4_run_evidence เทียบ exact ทุก field (B2)
_M4_EXPECTED_KEYS = ("model_revision", "tokenizer_revision", "model_file_manifest_sha256",
                     "image_digest", "inference_config", "retrieval_index_manifest_sha256")
# M4RunReceipt (durable receipt — body-validated, hash-only, recompute digest จาก body)
M4_RECEIPT_SCHEMA_VERSION = "p2-m4-receipt-v1"
_M4_RECEIPT_KEYS = frozenset({"schema_version", "run_id", "run_manifest_sha256", "m4_case_manifest_sha256",
                              "raw_evidence_sha256", "command_sha256", "started_utc", "finished_utc", "exit_code",
                              "stdout_sha256", "stderr_sha256", "isolation_marker_sha256",
                              "retrieval_index_manifest_sha256", "model_revision", "image_digest", "status"})


def _extra_keys(d, allowed, tag) -> list:
    if not isinstance(d, dict):
        return [f"{tag}: ไม่ใช่ dict"]
    extra = set(d) - allowed
    # M2: sort ด้วย repr กัน TypeError เมื่อ key ต่างชนิด (เช่น None กับ int)
    return [f"{tag}: unknown/raw fields {sorted(repr(k) for k in extra)} (hash-only exact schema)"] if extra else []


def validate_m4_frozen_manifest(frozen) -> list:
    """
    B2/B3/M1: ตรวจ frozen M4 case/visibility manifest **ก่อน hash** (fail-closed, ไม่ให้ crash) —
    exact types/keys, sha256 case keys/pairs, non-blank/unique roles+categories, authorized/sentinel ไม่ว่าง+disjoint,
    ทุก evaluated_role + required_category มี case (coverage), case-role set == evaluated_roles
    """
    if not isinstance(frozen, dict):
        return ["frozen manifest ต้องเป็น dict"]
    errs = _extra_keys(frozen, _M4_FROZEN_KEYS, "frozen")
    req, roles, cases = frozen.get("required_categories"), frozen.get("evaluated_roles"), frozen.get("cases")
    if not (isinstance(req, list) and req and all(_good_str(x) for x in req) and len(set(req)) == len(req)):
        errs.append("frozen required_categories ต้องเป็น list ของ str ไม่ว่าง/ไม่ซ้ำ")
        req = []
    if not (isinstance(roles, list) and roles and all(_good_str(x) for x in roles) and len(set(roles)) == len(roles)):
        errs.append("frozen evaluated_roles ต้องเป็น list ของ str ไม่ว่าง/ไม่ซ้ำ")
        roles = []
    if not isinstance(cases, dict) or not cases:
        return errs + ["frozen cases ต้องเป็น dict ไม่ว่าง"]
    seen_roles, seen_cats = set(), set()
    for cid, fc in cases.items():
        tag = f"frozen case[{cid!r}]"
        if not _is_sha256(cid):
            errs.append(f"{tag}: case_id key ต้องเป็น sha256")
        errs += _extra_keys(fc, _M4_FCASE_KEYS, tag)
        if not isinstance(fc, dict):
            continue
        if not _is_sha256(fc.get("role_identity_sha256")):
            errs.append(f"{tag}: role_identity_sha256 ต้องเป็น sha256")
        er, cat = fc.get("effective_role"), fc.get("category")
        if not _good_str(er) or er not in roles:
            errs.append(f"{tag}: effective_role ต้องเป็น str ใน evaluated_roles")
        else:
            seen_roles.add(er)
        if not _good_str(cat) or cat not in req:
            errs.append(f"{tag}: category ต้องเป็น str ใน required_categories")
        else:
            seen_cats.add(cat)
        if not _is_sha256(fc.get("query_vector_sha256")):
            errs.append(f"{tag}: query_vector_sha256 ต้องเป็น sha256 (frozen QueryProbe, B1)")
        auth, sent = fc.get("authorized_pairs"), fc.get("sentinel_pairs")
        if not _is_hash_list(auth):
            errs.append(f"{tag}: authorized_pairs ต้องเป็น sha256 list ไม่ว่าง")
        elif len(set(auth)) != len(auth):
            errs.append(f"{tag}: authorized_pairs มีค่าซ้ำ (frozen oracle ต้อง unique)")
        if not _is_hash_list(sent):
            errs.append(f"{tag}: sentinel_pairs ต้องเป็น sha256 list ไม่ว่าง")
        elif len(set(sent)) != len(sent):
            errs.append(f"{tag}: sentinel_pairs มีค่าซ้ำ (frozen oracle ต้อง unique)")
        if _is_hash_list(auth) and _is_hash_list(sent) and (set(auth) & set(sent)):
            errs.append(f"{tag}: authorized/sentinel pairs ปนกัน (ต้อง disjoint)")
    if req and seen_cats != set(req):
        errs.append(f"frozen required_categories ไม่ครบ case (missing={sorted(set(req) - seen_cats)})")
    if roles and seen_roles != set(roles):
        errs.append(f"frozen evaluated_roles ไม่ครบ case (missing={sorted(set(roles) - seen_roles)})")
    return errs


def m4_case_manifest_sha256(frozen: dict) -> str:
    """digest ของ frozen M4 case/visibility manifest — bind เข้า RunPlan (B2). เรียกหลัง validate_m4_frozen_manifest"""
    body = {"cases": frozen.get("cases"),
            "required_categories": sorted(frozen.get("required_categories", [])),
            "evaluated_roles": sorted(frozen.get("evaluated_roles", []))}
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def _safe_m4_manifest_digest(frozen):
    """digest แบบไม่ crash (malformed → None) สำหรับ fail-closed gate"""
    try:
        return m4_case_manifest_sha256(frozen)
    except (TypeError, ValueError):
        return None


def _is_hash_list(x, allow_empty=False) -> bool:
    if not isinstance(x, list):
        return False
    if not x and not allow_empty:
        return False
    return all(_is_sha256(h) for h in x)


def _ms_subset(a, b) -> bool:            # multiset a ⊆ b (นับซ้ำ)
    ca, cb = Counter(a), Counter(b)
    return all(ca[k] <= cb[k] for k in ca)


def _ms_equal(a, b) -> bool:             # permutation (multiset เท่ากัน)
    return Counter(a) == Counter(b)


def _ms_disjoint(a, b) -> bool:
    return not (set(a) & set(b))


def _effective_access(role: str):
    return P.EffectiveAccess(P.ServicePrincipal("p2-eval", (role,), True, "enforce"), role)


def is_authorized(payload: dict, role: str) -> bool:
    """
    P1 policy path เดียวกับ retrieval **บวก stored-shape validation** (B2.1):
    matches_policy เลียนแบบ Qdrant MatchAny → scalar `allowed_roles:"qc"` จะ match ได้ แต่ผิด
    policy-v1 contract (write boundary ต้อง quarantine) → ต้องผ่าน validate_stored_payload ก่อน
    (admin ไม่มี bypass; stale/quarantine/wrong-version/scalar/non-list/bad-schema ไม่ผ่าน)
    """
    if role not in P.KNOWN_ROLES:
        return False
    if not P.payload_is_policy_v1(payload):
        return False
    valid, _ = P.validate_stored_payload(payload)
    if not valid:
        return False
    return P.matches_policy(payload, P.compile_retrieval_filter(_effective_access(role)))


def _bad_str(s) -> bool:
    """ว่าง/ผิดชนิด/มี control char (Cc) หรือ lone surrogate (Cs) — Thai/emoji ปกติผ่าน"""
    if not isinstance(s, str) or not s.strip():
        return True
    return any(unicodedata.category(ch) in ("Cc", "Cs") for ch in s)


def _canonical_json(obj) -> bytes:
    """canonical hashing bytes เดียวสำหรับ eval/corpus: escape non-ASCII (surrogate-safe), reject NaN/Inf"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _is_grade(g) -> bool:
    return type(g) is int and g in LOCKED_GRADES


def validate_ranking_eval_set(cases, corpus: dict, known_roles,
                              allowed_label_status=VALID_LABEL_STATUS) -> list:
    """
    คืน list ของ error (ว่าง = ผ่าน). corpus[point_id] = {"source": str, "rerank_text": str, "payload": {v1}}
    fail เมื่อ: field บังคับหาย/ผิดชนิด/control char · query_id/query/source/point ซ้ำ-ว่าง ·
      case_type != ranking · split/label_status ผิด · grade นอก {1,2,3} · relevance ว่าง ·
      relevant point ไม่อยู่ frozen corpus · **ไม่ authorized (P1 policy) สำหรับ role** ·
      source ของ relevant point ไม่อยู่ใน relevant_sources
    """
    if not isinstance(cases, list):
        return ["cases ต้องเป็น list"]
    if not isinstance(corpus, dict):          # M1.2: กัน corpus.get() บน non-dict (AttributeError)
        return ["corpus ต้องเป็น dict"]
    known = set(known_roles)
    errs, seen_qid, intent_splits = [], set(), []
    for i, c in enumerate(cases):
        tag = f"case[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{tag}: ไม่ใช่ object")
            continue
        for f in REQUIRED_CASE_FIELDS:
            if f not in c:
                errs.append(f"{tag}: field '{f}' หาย")
        qid = c.get("query_id")
        if isinstance(qid, str) and qid.strip():
            tag = qid
        if _bad_str(qid):
            errs.append(f"{tag}: query_id ว่าง/ผิดชนิด/control char")
        elif qid in seen_qid:
            errs.append(f"query_id ซ้ำ: {qid}")
        else:
            seen_qid.add(qid)
        if _bad_str(c.get("query")):
            errs.append(f"{tag}: query ว่าง/ผิดชนิด/control char")
        if c.get("case_type") != "ranking":
            errs.append(f"{tag}: case_type ต้อง 'ranking' (no-answer -> abstention suite แยก)")
        if c.get("split") not in VALID_SPLITS:
            errs.append(f"{tag}: split ผิด {c.get('split')!r}")
        if c.get("label_status") not in allowed_label_status:
            errs.append(f"{tag}: label_status ต้องอยู่ใน {sorted(allowed_label_status)}")
        if _bad_str(c.get("lang")):
            errs.append(f"{tag}: lang ว่าง")
        if _bad_str(c.get("category")):
            errs.append(f"{tag}: category ว่าง")
        ctags = c.get("challenge_tags")           # B3: semantic challenge แยกจาก lang
        if not isinstance(ctags, list) or not ctags or any(_bad_str(t) for t in ctags):
            errs.append(f"{tag}: challenge_tags ว่าง/ผิดชนิด")
        iid = c.get("intent_id")                  # B2: paraphrases share intent_id
        if _bad_str(iid):
            errs.append(f"{tag}: intent_id ว่าง/ผิดชนิด/control char")
        elif c.get("split") in VALID_SPLITS:
            intent_splits.append((iid, c.get("split")))
        role = c.get("role")
        if role not in known:
            errs.append(f"{tag}: role ไม่รู้จัก {role!r}")
        rsrc = c.get("relevant_sources")
        rsrc_ok = isinstance(rsrc, list) and rsrc and not any(_bad_str(s) for s in rsrc)
        if not rsrc_ok:
            errs.append(f"{tag}: relevant_sources ว่าง/ผิดชนิด/control char")
        elif len(set(rsrc)) != len(rsrc):
            errs.append(f"{tag}: relevant_sources มี source ซ้ำ")
        rel = c.get("relevance")
        if not isinstance(rel, dict) or not rel:
            errs.append(f"{tag}: relevance ว่าง (ranking ต้องมี relevant point)")
            continue
        derived = set()
        for pid, grade in rel.items():
            if _bad_str(pid):
                errs.append(f"{tag}: relevant point id ว่าง")
                continue
            if not _is_grade(grade):
                errs.append(f"{tag}: grade นอก allowlist {LOCKED_GRADES} ({pid}={grade!r})")
            entry = corpus.get(pid)
            if not isinstance(entry, dict):
                errs.append(f"{tag}: relevant point {pid} ไม่อยู่ใน frozen corpus")
                continue
            if role in known and not is_authorized(entry.get("payload", {}), role):
                errs.append(f"{tag}: relevant point {pid} ไม่ authorized (P1 policy) สำหรับ role {role}")
            if isinstance(entry.get("source"), str):
                derived.add(entry["source"])
        # M5.1: relevant_sources ต้อง exact set-equality กับ source ที่ derive จาก relevant points
        if rsrc_ok:
            want = set(rsrc)
            if derived != want:
                errs.append(f"{tag}: relevant_sources ไม่ตรง exact "
                            f"(missing={sorted(derived - want)} extra={sorted(want - derived)})")
        # B3: hard_negative_ids — point ที่ role เห็นได้ (authorized) แต่ไม่ใช่คำตอบ
        hn = c.get("hard_negative_ids")
        if not isinstance(hn, list):
            errs.append(f"{tag}: hard_negative_ids ต้องเป็น list")
        else:
            for hid in hn:
                if _bad_str(hid):
                    errs.append(f"{tag}: hard_negative id ว่าง")
                    continue
                he = corpus.get(hid)
                if not isinstance(he, dict):
                    errs.append(f"{tag}: hard_negative {hid} ไม่อยู่ใน corpus")
                elif role in known and not is_authorized(he.get("payload", {}), role):
                    errs.append(f"{tag}: hard_negative {hid} ไม่ authorized สำหรับ role {role}")
                if hid in rel:
                    errs.append(f"{tag}: hard_negative {hid} ห้ามอยู่ใน relevance")
        # B5/B5.1: multi-relevance ต้องมี grade_rationale case-specific ต่อทุก relevant pid (exact)
        if len(rel) > 1:
            gr = c.get("grade_rationale")
            generic = {v.strip() for v in GRADE_RUBRIC.values()}
            if not isinstance(gr, dict) or set(gr) != set(rel):
                errs.append(f"{tag}: grade_rationale ต้องครอบทุก relevant pid แบบ exact (B5)")
            else:
                for pid in rel:
                    r = gr.get(pid)
                    if _bad_str(r):
                        errs.append(f"{tag}: grade_rationale ของ {pid} ว่าง")
                    elif r.strip() in generic or len(r.strip()) < 12:
                        errs.append(f"{tag}: grade_rationale ของ {pid} generic/สั้นเกิน (B5.1: ต้อง case-specific)")
    # B2: paraphrase ของ intent เดียวกันต้องอยู่ split เดียว
    by_intent = {}
    for iid, sp in intent_splits:
        by_intent.setdefault(iid, set()).add(sp)
    for iid, sps in sorted(by_intent.items()):
        if len(sps) > 1:
            errs.append(f"intent_id {iid}: paraphrase ข้าม split {sorted(sps)} (ต้อง split เดียว)")
    return errs


def count_test_intents(cases) -> int:
    """จำนวน independent intent group ใน split=test (B2: arm-eligibility ต้อง >= MIN_TEST_INTENTS)"""
    return len({c.get("intent_id") for c in cases
                if isinstance(c, dict) and c.get("split") == "test" and c.get("intent_id")})


# ── frozen-corpus validation (M1.1) — fail-closed ก่อนคำนวณ/hash ────────────────
def validate_corpus(corpus) -> list:
    """
    frozen corpus ต้อง fail-closed: dict ไม่ว่าง ; แต่ละ entry มี point_id/source/rerank_text เป็น
    non-blank str ไม่มี control char ; payload เป็น policy-v1 ที่ validate_stored_payload ผ่าน
    """
    if not isinstance(corpus, dict) or not corpus:
        return ["corpus ว่าง/ไม่ใช่ dict"]
    errs = []
    for pid, e in corpus.items():
        tag = f"corpus[{pid!r}]"
        if _bad_str(pid):
            errs.append(f"{tag}: point_id ว่าง/control char")
        if not isinstance(e, dict):
            errs.append(f"{tag}: entry ไม่ใช่ object")
            continue
        if _bad_str(e.get("source")):
            errs.append(f"{tag}: source ว่าง/control char")
        if _bad_str(e.get("rerank_text")):
            errs.append(f"{tag}: rerank_text ว่าง/ผิดชนิด/control char")
        pay = e.get("payload")
        if not P.payload_is_policy_v1(pay):
            errs.append(f"{tag}: payload ไม่ใช่ policy-v1")
        else:
            ok, reason = P.validate_stored_payload(pay)
            if not ok:
                errs.append(f"{tag}: payload ผิด contract ({reason})")
    return errs


def validate_benchmark(cases, corpus, known_roles, allowed_label_status=VALID_LABEL_STATUS) -> list:
    """รวม corpus + cases validation ; benchmark valid ก็ต่อเมื่อคืน []"""
    errs = validate_corpus(corpus)
    if not isinstance(cases, list) or not cases:
        errs.append("ranking cases ว่าง")
    errs += validate_ranking_eval_set(cases, corpus, known_roles, allowed_label_status)
    return errs


def arm_eligibility_errors(cases, gate_tags, min_intents: int = MIN_TEST_INTENTS,
                           min_per_tag: int = 5) -> list:
    """
    gate แยกสำหรับ **decision benchmark / เลือก arm** (B2/B3) — structural valid ไม่พอ:
      - independent test intents >= min_intents
      - แต่ละ gate challenge tag มี >= min_per_tag independent test intents
    (smoke run ข้าม gate นี้ได้ แต่ห้ามอ้างเทียบ acceptance)
    """
    errs = []
    test = [c for c in cases if isinstance(c, dict) and c.get("split") == "test"]
    n = len({c.get("intent_id") for c in test if c.get("intent_id")})
    if n < min_intents:
        errs.append(f"test independent intents = {n} < {min_intents} (decision benchmark ไม่ได้)")
    for tag in gate_tags:
        cnt = len({c.get("intent_id") for c in test
                   if c.get("intent_id") and isinstance(c.get("challenge_tags"), list)
                   and tag in c["challenge_tags"]})
        if cnt < min_per_tag:
            errs.append(f"gate challenge '{tag}' มี {cnt} test intents < {min_per_tag}")
    return errs


def dev_role_coverage_errors(cases, evaluated_roles, min_dev_per_role: int = 1) -> list:
    """B2.1: dev ต้องมี intent ครอบทุก evaluated role (เลือก N ต่อ role ได้)"""
    dev = {}
    for c in cases:
        if isinstance(c, dict) and c.get("split") == "dev" and c.get("intent_id"):
            dev.setdefault(c.get("role"), set()).add(c["intent_id"])
    return [f"dev role coverage: {r} มี {len(dev.get(r, set()))} dev intents < {min_dev_per_role}"
            for r in evaluated_roles if len(dev.get(r, set())) < min_dev_per_role]


def validate_signoff(signoff, cases, corpus) -> list:
    """
    B6.1: Data Owner sign-off manifest (มนุษย์สร้าง — AI ห้ามกรอกแทน) ต้องผูก hash artifacts จริง
    decision benchmark จะ fail ถ้า sign-off hash ไม่ตรง eval/corpus ปัจจุบัน
    """
    if not isinstance(signoff, dict) or not signoff:
        return ["ไม่มี Data Owner sign-off manifest (human sign-off ต้องมีก่อน decision benchmark)"]
    errs = [f"signoff field '{f}' หาย" for f in SIGNOFF_FIELDS if not signoff.get(f)]
    if signoff.get("decision") not in ("approved", "rejected"):
        errs.append(f"signoff decision ต้องเป็น enum approved/rejected ({signoff.get('decision')!r})")
    elif signoff.get("decision") != "approved":
        errs.append("signoff decision != approved")
    if signoff.get("benchmark_contract_version") != BENCHMARK_CONTRACT_VERSION:
        errs.append("signoff benchmark_contract_version ไม่ตรง")
    # M1.1: exact field types — กัน bool/list/dict/control char ที่ audit ไม่ได้
    if not _good_str(signoff.get("reviewer")):
        errs.append("signoff reviewer ต้องเป็น non-blank str")
    if not _good_str(signoff.get("data_owner_role")):
        errs.append("signoff data_owner_role ต้องเป็น non-blank str")
    if not _is_hex_commit(signoff.get("git_commit")):
        errs.append("signoff git_commit ต้องเป็น hex commit id (7-64)")
    if not _valid_iso_tz(signoff.get("reviewed_at")):
        errs.append("signoff reviewed_at ต้องเป็น ISO-8601 timestamp จริงพร้อม timezone")
    # M1: hashing อาจ crash เมื่อ artifacts malformed (NaN/surrogate/None) → คืน controlled error
    try:
        if signoff.get("eval_set_sha256") != eval_set_sha256(cases):
            errs.append("signoff eval_set_sha256 ไม่ตรง artifacts ปัจจุบัน")
        if signoff.get("corpus_manifest_sha256") != corpus_manifest_sha256(corpus):
            errs.append("signoff corpus_manifest_sha256 ไม่ตรง artifacts ปัจจุบัน")
    except (ValueError, TypeError, AttributeError) as e:
        errs.append(f"artifacts hash ไม่ได้ (malformed): {type(e).__name__}")
    return errs


def decision_benchmark_errors(cases, corpus, known_roles, evaluated_roles,
                              gate_tags, signoff) -> list:
    """
    B6.1: **decision gate เดียว** ที่รวมทุกด่าน — คืน [] ก็ต่อเมื่อผ่านหมด:
      structural + human-reviewed labels + sample coverage + dev role coverage + Data Owner sign-off hash ตรง
    M1: short-circuit เมื่อ structural ไม่ผ่าน — ไม่ไป hash artifacts ที่ผิดรูป (กัน crash)
    """
    structural = validate_benchmark(cases, corpus, known_roles)
    if structural:
        return structural
    return (arm_eligibility_errors(cases, gate_tags)
            + dev_role_coverage_errors(cases, evaluated_roles)
            + validate_signoff(signoff, cases, corpus))


def _evidence_hash_binding(ev, eval_hash, corpus_hash, prefix) -> list:
    """B2: fields ที่เป็น hash/digest/commit ต้องถูก format + ผูก eval/corpus/index/model/image จริง"""
    errs = []
    if not _is_sha256(ev.get("retrieval_index_manifest_sha256")):
        errs.append(f"{prefix} retrieval_index_manifest_sha256 ต้องเป็น 64-hex sha256")
    if not _good_str(ev.get("run_id")):
        errs.append(f"{prefix} run_id หาย/ผิดชนิด")
    if ev.get("eval_set_sha256") != eval_hash:
        errs.append(f"{prefix} eval_set_sha256 ไม่ตรง artifacts")
    if ev.get("corpus_manifest_sha256") != corpus_hash:
        errs.append(f"{prefix} corpus_manifest_sha256 ไม่ตรง artifacts")
    return errs


def _bind_run_manifest(ev, run_manifest_sha256, prefix) -> list:
    """B4: evidence ต้องอ้าง root run_manifest_sha256 เดียวกัน (bind เมื่อ decision path ส่ง root มา)"""
    if run_manifest_sha256 is None:
        return []
    if ev.get("run_manifest_sha256") != run_manifest_sha256:
        return [f"{prefix} run_manifest_sha256 ไม่ตรง root run manifest"]
    return []


_M4_PAIR_LISTS = ("provider_pairs", "model_input_pairs", "rerank_output_pairs", "unfiltered_topn_pairs")


def _m4_case_errors(i, c, fc, required, top_n) -> list:
    """B1 authoritative — ตรวจ security invariant **ภายใน case/role เดียว** (ก่อน aggregate)"""
    tag, errs = f"m4 per_case[{i}]", []
    if not isinstance(c, dict):
        return [f"{tag}: ไม่ใช่ dict"]
    errs += _extra_keys(c, _M4_CASE_KEYS, tag)   # M1: exact hash-only schema (reject raw/unknown)
    # B3 recompute: pair_sha256 = _pair_sha256(point_id, text) ; ทุก pair ต้อง derive จาก components
    comps, valid = c.get("pair_components"), set()
    if not isinstance(comps, list) or not comps:
        errs.append(f"{tag}: pair_components ว่าง/ผิดชนิด")
    else:
        for comp in comps:
            if not isinstance(comp, dict):
                errs.append(f"{tag}: pair_component ไม่ใช่ dict")
                continue
            errs += _extra_keys(comp, _M4_COMP_KEYS, f"{tag} component")
            pid, txt, pd = comp.get("point_id_sha256"), comp.get("rerank_text_sha256"), comp.get("pair_sha256")
            if not (_is_sha256(pid) and _is_sha256(txt) and _is_sha256(pd)):
                errs.append(f"{tag}: pair_component ต้องเป็น sha256 ครบ")
            elif _pair_sha256(pid, txt) != pd:
                errs.append(f"{tag}: pair_sha256 ไม่ตรงสูตร (point_id:text)")
            else:
                valid.add(pd)
    got = {}
    for name in _M4_PAIR_LISTS:
        v = c.get(name)
        if not _is_hash_list(v):
            errs.append(f"{tag}: {name} ต้องเป็น sha256 pair list (ไม่ว่าง)")
            got[name] = None
        else:
            if valid and any(p not in valid for p in v):
                errs.append(f"{tag}: {name} มี pair ที่ไม่ได้ derive จาก pair_components")
            got[name] = v
    exp_auth = fc.get("authorized_pairs") if isinstance(fc, dict) else None
    exp_sent = fc.get("sentinel_pairs") if isinstance(fc, dict) else None
    if not _is_hash_list(exp_auth):
        errs.append(f"{tag}: frozen authorized_pairs ว่าง")
        exp_auth = None
    if not _is_hash_list(exp_sent):
        errs.append(f"{tag}: frozen sentinel_pairs ว่าง")
        exp_sent = None
    # identity/role/category ผูก frozen manifest (B2)
    if isinstance(fc, dict):
        if c.get("role_identity_sha256") != fc.get("role_identity_sha256"):
            errs.append(f"{tag}: role_identity ไม่ตรง frozen manifest")
        if c.get("effective_role") != fc.get("effective_role"):
            errs.append(f"{tag}: effective_role ไม่ตรง frozen manifest")
        if c.get("category") != fc.get("category"):
            errs.append(f"{tag}: category ไม่ตรง frozen manifest")
        if c.get("query_vector_sha256") != fc.get("query_vector_sha256"):
            errs.append(f"{tag}: query_vector_sha256 ไม่ตรง frozen QueryProbe (เลือก vector หลังเห็นผลไม่ได้)")
    if c.get("category") not in required:
        errs.append(f"{tag}: category ไม่อยู่ใน required_categories")
    if c.get("selected_n") != top_n:
        errs.append(f"{tag}: selected_n ไม่ตรง top-level")
    if not _is_sha256(c.get("case_id_sha256")):
        errs.append(f"{tag}: case_id_sha256 ต้อง sha256")
    if not _is_sha256(c.get("query_vector_sha256")):
        errs.append(f"{tag}: query_vector_sha256 ต้อง sha256")
    # M2: same-query control — unfiltered/filtered call ต้องใช้ query vector + limit ชุดเดียว
    qv = c.get("query_vector_sha256")
    if c.get("unfiltered_query_vector_sha256") != qv or c.get("filtered_query_vector_sha256") != qv:
        errs.append(f"{tag}: unfiltered/filtered query vector ไม่ตรง query_vector_sha256 (ต้อง probe เดียว)")
    if c.get("unfiltered_limit") != top_n or c.get("filtered_limit") != top_n:
        errs.append(f"{tag}: unfiltered/filtered limit ต้อง == selected_n")
    prov, minp, rer, unf = got["provider_pairs"], got["model_input_pairs"], got["rerank_output_pairs"], got["unfiltered_topn_pairs"]
    # within-case security invariants
    if prov is not None and exp_auth is not None and not _ms_subset(prov, exp_auth):
        errs.append(f"{tag}: provider ไม่ ⊆ authorized (case นี้)")
    if minp is not None and prov is not None and not _ms_subset(minp, prov):
        errs.append(f"{tag}: model_input ไม่ ⊆ provider")
    if rer is not None and minp is not None and not _ms_equal(rer, minp):
        errs.append(f"{tag}: rerank ไม่ใช่ permutation ของ model_input")
    if minp is not None and exp_sent is not None and not _ms_disjoint(minp, exp_sent):
        errs.append(f"{tag}: sentinel ถึง model_input (LEAK) — category {c.get('category')!r}")
    if exp_auth is not None and exp_sent is not None and not _ms_disjoint(exp_auth, exp_sent):
        errs.append(f"{tag}: authorized/sentinel oracle ปนกัน")
    # B1 load-bearing: unfiltered ordered ไม่ซ้ำ, len <= N ; sentinel ⊆ unfiltered ; observed rank == ตำแหน่งจริง (exact)
    pos = {}
    if unf is not None:
        if len(unf) != len(set(unf)):
            errs.append(f"{tag}: unfiltered_topn_pairs มี pair ซ้ำ")
        if type(top_n) is int and len(unf) > top_n:
            errs.append(f"{tag}: unfiltered_topn_pairs ยาวเกิน selected_n")
        pos = {p: idx + 1 for idx, p in enumerate(unf)}
    if unf is not None and exp_sent is not None and not _ms_subset(exp_sent, unf):
        errs.append(f"{tag}: sentinel ไม่ติด unfiltered top-N (filter อาจไม่ load-bearing)")
    ranks = c.get("observed_sentinel_ranks")
    if not isinstance(ranks, list) or not ranks:
        errs.append(f"{tag}: observed_sentinel_ranks ว่าง")
    elif exp_sent is not None:
        seen, ok = {}, True
        for r in ranks:
            if not (isinstance(r, list) and len(r) == 2 and _is_sha256(r[0]) and type(r[1]) is int):
                errs.append(f"{tag}: observed_sentinel_ranks ต้องเป็น [pair_sha256, int]")
                ok = False
                break
            if r[0] in seen:
                errs.append(f"{tag}: observed_sentinel_ranks มี pair ซ้ำ")
                ok = False
                break
            seen[r[0]] = r[1]
        if ok:
            if set(seen) != set(exp_sent):
                errs.append(f"{tag}: observed_sentinel_ranks ไม่ครอบ sentinel ทุกตัว (exact)")
            elif any(pos.get(p) != rk for p, rk in seen.items()):
                errs.append(f"{tag}: sentinel rank ไม่ตรงตำแหน่งจริงใน unfiltered top-N (rank เท็จ)")
    # counts/finite/status (B3 ต่อ case)
    mcc, mic, scc = c.get("model_call_count"), c.get("model_input_count"), c.get("score_count")
    if type(mcc) is not int or mcc < 1:
        errs.append(f"{tag}: model_call_count ต้อง positive int")
    if type(mic) is not int or mic < 1:
        errs.append(f"{tag}: model_input_count ต้อง positive int")
    if type(scc) is not int or type(mic) is not int or scc != mic:
        errs.append(f"{tag}: score_count == model_input_count")
    if minp is not None and type(mic) is int and len(minp) != mic:
        errs.append(f"{tag}: model_input_count != len(model_input_pairs)")
    if c.get("all_scores_finite") is not True:
        errs.append(f"{tag}: all_scores_finite ต้อง True")
    if c.get("status") != "PASS":
        errs.append(f"{tag}: status != PASS (zero-skip)")
    return errs


def validate_m4_run_evidence(m4, frozen, expected, eval_hash, corpus_hash, run_manifest_sha256=None, require_stage=None) -> list:
    """
    real M4 run evidence v4 — **per_case[] เป็นหลักฐาน authoritative** (permission เป็น invariant ต่อ query/role):
    - B1: subset/permutation/disjoint + unfiltered load-bearing ตรวจ **ภายในแต่ละ case** + QueryProbe ผูก frozen
    - B2: bind frozen M4 manifest + **exact pin/image/index/inference_config จาก `expected` (M4RunRequest)** ทั้ง M4a/M4b
    - B3: recompute raw_evidence_sha256 จาก per_case body + recompute pair_sha256 จาก components + run_receipt_sha256
    - M2: per-case category + observed rank ตรงตำแหน่งจริง ; required-category + evaluated-role coverage ครบ
    - M1/M3: stage contract + pin/index/run_manifest binding + exact hash-only schema (no crash)
    frozen = {cases:{cid:{role_identity_sha256,effective_role,category,query_vector_sha256,authorized_pairs,sentinel_pairs}}, ...}
    expected = {model_revision,tokenizer_revision,model_file_manifest_sha256,image_digest,inference_config,retrieval_index_manifest_sha256[,run_id]}
    """
    if not isinstance(m4, dict):
        return ["m4 run evidence ต้องเป็น dict"]
    if not isinstance(expected, dict):
        return ["M4 run request (expected) จำเป็น — ไม่มี format-only mode ที่ปลด gate"]
    # B3/M1: validate frozen manifest ก่อน hash (fail-closed, ไม่ crash) — require frozen (ไม่มี fail-open)
    ferrs = validate_m4_frozen_manifest(frozen)
    if ferrs:
        return ["frozen M4 manifest invalid (public gate ห้าม fail-open)"] + ferrs[:6]
    errs = _extra_keys(m4, _M4_TOP_KEYS, "m4")       # M1: exact hash-only schema top-level
    if m4.get("schema_version") != M4_SCHEMA_VERSION:
        errs.append(f"m4 schema_version ต้องเป็น {M4_SCHEMA_VERSION}")
    for f in ("status", "isolated_interlock", "independent_oracle"):
        if m4.get(f) != "PASS":
            errs.append(f"m4 {f} != PASS ({m4.get(f)!r})")
    if m4.get("sentinel_reached_model") is not False:
        errs.append("m4 sentinel_reached_model ต้องเป็น False (exact)")
    if not _exact_zero_int(m4.get("unauthorized_in_model_inputs")):
        errs.append("m4 unauthorized_in_model_inputs ต้องเป็น 0 (exact int)")
    if m4.get("scorer_kind") != "pinned-cross-encoder":
        errs.append("m4 scorer_kind ต้อง 'pinned-cross-encoder'")

    stage, sel_n = m4.get("evidence_stage"), m4.get("selected_n")
    if stage not in M4_STAGES:
        errs.append(f"m4 evidence_stage ต้องอยู่ใน {M4_STAGES}")
    if require_stage is not None and stage != require_stage:
        errs.append(f"m4 evidence_stage ต้องเป็น {require_stage!r} (ได้ {stage!r})")
    if stage == M4_STAGE_PREFLIGHT:
        if m4.get("decision_eligible") is not False:
            errs.append("m4 preflight ต้อง decision_eligible=False (exact)")
        if sel_n != 50:
            errs.append("m4 preflight selected_n ต้อง == 50")
        if m4.get("selection_digest") is not None:
            errs.append("m4 preflight ห้ามมี selection_digest")
    elif stage == M4_STAGE_SELECTED:
        if sel_n not in N_SET_M4:
            errs.append(f"m4 selected-n selected_n ต้องอยู่ใน {N_SET_M4}")
        if not _is_sha256(m4.get("selection_digest")):
            errs.append("m4 selected-n ต้องมี selection_digest (sha256)")

    # B2: frozen manifest binding (safe digest recompute + m4 อ้างตรง)
    man_digest = _safe_m4_manifest_digest(frozen)
    if man_digest is None:
        return errs + ["frozen manifest canonicalize/hash ไม่ได้ (malformed)"]
    if m4.get("m4_case_manifest_sha256") != man_digest:
        errs.append("m4 m4_case_manifest_sha256 != frozen manifest (recompute)")
    if frozen.get("m4_case_manifest_sha256") not in (None, man_digest):
        errs.append("frozen m4_case_manifest_sha256 ไม่ตรง cases (manifest ปนเปื้อน)")
    required = frozen.get("required_categories") or []

    # B3: recompute raw_evidence_sha256 จาก per_case body
    pcs = m4.get("per_case")
    if not isinstance(pcs, list) or not pcs:
        errs.append("m4 per_case ว่าง/ไม่ใช่ list")
        pcs = []
    try:
        recomputed = hashlib.sha256(_canonical_json(pcs)).hexdigest()
    except (ValueError, TypeError):
        recomputed = None
        errs.append("m4 per_case ไม่ canonicalizable")
    if recomputed is not None and m4.get("raw_evidence_sha256") != recomputed:
        errs.append("m4 raw_evidence_sha256 != recompute จาก per_case body")

    # B1/B2/M2: per-case authoritative + case set exact + category & role coverage
    fcases, seen_ids, cats, ev_roles = frozen["cases"], [], set(), set()
    for i, c in enumerate(pcs):
        cid = c.get("case_id_sha256") if isinstance(c, dict) else None
        seen_ids.append(cid)
        fc = fcases.get(cid) if isinstance(cid, str) else None
        if fc is None:
            errs.append(f"m4 per_case[{i}] case_id ไม่อยู่ frozen manifest")
        errs += _m4_case_errors(i, c, fc, required, sel_n)
        if isinstance(c, dict):
            if isinstance(c.get("category"), str):
                cats.add(c["category"])
            if isinstance(c.get("effective_role"), str):
                ev_roles.add(c["effective_role"])
    if set(x for x in seen_ids if x is not None) != set(fcases):
        errs.append("m4 case set != frozen manifest (exact)")
    if len(seen_ids) != len(set(seen_ids)):
        errs.append("m4 per_case มี case_id ซ้ำ")
    if set(required) - cats:
        errs.append(f"m4 required category ไม่ครบ (missing={sorted(set(required) - cats)})")
    # B2: ทุก evaluated_role ต้องมี case จริง (กัน manifest อ้าง role ที่ไม่เคยทดสอบ)
    froles = set(frozen.get("evaluated_roles") or [])
    if ev_roles != froles:
        errs.append(f"m4 case roles != frozen evaluated_roles (case roles={sorted(ev_roles)} vs {sorted(froles)})")

    # pin/index/binding (format)
    if not _is_hex_commit(m4.get("model_revision")):
        errs.append("m4 model_revision ต้องเป็น immutable commit (hex)")
    if not _is_hex_commit(m4.get("tokenizer_revision")):
        errs.append("m4 tokenizer_revision ต้องเป็น immutable commit (hex)")
    if not _is_image_digest(m4.get("image_digest")):
        errs.append("m4 image_digest ต้องเป็น sha256:<64hex>")
    if not _is_sha256(m4.get("model_file_manifest_sha256")):
        errs.append("m4 model_file_manifest_sha256 ต้องเป็น sha256")
    if not _is_sha256(m4.get("run_receipt_sha256")):
        errs.append("m4 run_receipt_sha256 ต้องเป็น sha256 (durable run receipt reference, M1)")
    # B2: exact compare กับ M4RunRequest (pin/image/index/inference_config) — ทั้ง M4a/M4b (ไม่ใช่ format-only)
    for k in _M4_EXPECTED_KEYS:
        if k not in expected:
            errs.append(f"expected (M4RunRequest) ขาด {k}")
        elif m4.get(k) != expected.get(k):
            errs.append(f"m4 {k} != expected M4RunRequest (frozen pin/image/index)")
    if "run_id" in expected and m4.get("run_id") != expected.get("run_id"):
        errs.append("m4 run_id != expected M4RunRequest")
    errs += _evidence_hash_binding(m4, eval_hash, corpus_hash, "m4")
    errs += _bind_run_manifest(m4, run_manifest_sha256, "m4")
    return errs


def m4_run_receipt_sha256(receipt) -> str:
    """digest ของ M4RunReceipt — recompute จาก canonical body (M4Evidence.run_receipt_sha256 ต้องตรงค่านี้)"""
    return hashlib.sha256(_canonical_json(receipt)).hexdigest()


def validate_m4_run_receipt(receipt, run_manifest, m4_case_manifest, expected, evidence) -> list:
    """
    M4RunReceipt v1 — body-validated durable receipt (ไม่ใช่ SHA self-stamp), hash-only ไม่มี secret/raw text:
    exact keys/types · status=PASS · exit_code=0 (exact int) · started/finished timestamp ISO+tz ·
    bind run/root/request/frozen/evidence hashes ให้ตรงชุดเดียวกัน
    """
    if not isinstance(receipt, dict):
        return ["m4 receipt ต้องเป็น dict"]
    errs = _extra_keys(receipt, _M4_RECEIPT_KEYS, "m4 receipt")
    if receipt.get("schema_version") != M4_RECEIPT_SCHEMA_VERSION:
        errs.append(f"m4 receipt schema_version ต้องเป็น {M4_RECEIPT_SCHEMA_VERSION}")
    if receipt.get("status") != "PASS":
        errs.append("m4 receipt status != PASS")
    if not _exact_zero_int(receipt.get("exit_code")):
        errs.append("m4 receipt exit_code ต้อง 0 (exact int)")
    for f in ("command_sha256", "stdout_sha256", "stderr_sha256", "isolation_marker_sha256"):
        if not _is_sha256(receipt.get(f)):
            errs.append(f"m4 receipt {f} ต้องเป็น sha256")
    if not _valid_iso_tz(receipt.get("started_utc")):
        errs.append("m4 receipt started_utc ต้องเป็น ISO-8601 + tz")
    if not _valid_iso_tz(receipt.get("finished_utc")):
        errs.append("m4 receipt finished_utc ต้องเป็น ISO-8601 + tz")
    # bind ชุดเดียวกันกับ RunPlan/frozen/request/evidence
    if not _good_str(receipt.get("run_id")) or receipt.get("run_id") != (evidence.get("run_id") if isinstance(evidence, dict) else None):
        errs.append("m4 receipt run_id != evidence.run_id")
    if receipt.get("run_manifest_sha256") != run_manifest:
        errs.append("m4 receipt run_manifest_sha256 != root")
    if receipt.get("m4_case_manifest_sha256") != m4_case_manifest:
        errs.append("m4 receipt m4_case_manifest_sha256 != plan")
    if not isinstance(evidence, dict) or receipt.get("raw_evidence_sha256") != evidence.get("raw_evidence_sha256"):
        errs.append("m4 receipt raw_evidence_sha256 != evidence")
    if not isinstance(expected, dict):
        errs.append("m4 receipt: expected M4RunRequest จำเป็น")
    else:
        if receipt.get("model_revision") != expected.get("model_revision"):
            errs.append("m4 receipt model_revision != M4RunRequest")
        if receipt.get("image_digest") != expected.get("image_digest"):
            errs.append("m4 receipt image_digest != M4RunRequest")
        if receipt.get("retrieval_index_manifest_sha256") != expected.get("retrieval_index_manifest_sha256"):
            errs.append("m4 receipt retrieval_index_manifest_sha256 != M4RunRequest")
    return errs


def validate_canary_evidence(canary, eval_hash, corpus_hash, run_manifest_sha256=None) -> list:
    """B3.1/B2/B4: P5b canary — leak=0/VERIFIED/ทุก arm PASS + per-arm ERROR/INCONCLUSIVE=0 + count ตรง
    + (เมื่อ decision path) ผูก root run_manifest_sha256 และ bind model/image ให้มาจาก run เดียวกัน"""
    if not isinstance(canary, dict):
        return ["canary_evidence ต้องเป็น validated dict summary"]
    errs = []
    if canary.get("status") != "PASS":
        errs.append(f"canary status != PASS ({canary.get('status')!r})")
    if not _exact_zero_int(canary.get("leak_count")):
        errs.append("canary leak_count ต้องเป็น 0 (exact int)")
    if canary.get("auth_status") != "VERIFIED":
        errs.append(f"canary auth_status != VERIFIED ({canary.get('auth_status')!r})")
    arm_status = canary.get("arm_status")
    if not isinstance(arm_status, dict) or set(arm_status) != set(ARMS_EXACT) \
            or any(arm_status.get(a) != "PASS" for a in ARMS_EXACT):
        errs.append(f"canary arm_status ต้องมี {ARMS_EXACT} = PASS ครบ")
    ac = canary.get("arm_error_counts")
    if not isinstance(ac, dict) or set(ac) != set(ARMS_EXACT) or any(not _exact_zero_int(ac.get(a)) for a in ARMS_EXACT):
        errs.append(f"canary arm_error_counts ต้องมี {ARMS_EXACT} = 0 (ERROR/INCONCLUSIVE)")
    exp, act = canary.get("expected_query_count"), canary.get("actual_query_count")
    if type(exp) is not int or type(act) is not int or exp != act or exp < 1:
        errs.append("canary expected_query_count == actual_query_count (positive int)")
    errs += _evidence_hash_binding(canary, eval_hash, corpus_hash, "canary")
    errs += _bind_run_manifest(canary, run_manifest_sha256, "canary")
    # B4: canary ต้อง bind model/image (มาจาก image เดียวกับ M4) เมื่ออยู่ใน decision path
    if run_manifest_sha256 is not None:
        if not _is_hex_commit(canary.get("model_revision")):
            errs.append("canary model_revision ต้องเป็น immutable commit (hex)")
        if not _is_image_digest(canary.get("image_digest")):
            errs.append("canary image_digest ต้องเป็น sha256:<64hex>")
    return errs


def decision_evidence_errors(cases, corpus, known_roles, evaluated_roles, gate_tags, signoff,
                             m4_evidence, canary_evidence, run_manifest_sha256,
                             m4_frozen, m4_expected, eval_hash=None, corpus_hash=None) -> list:
    """
    B3/B3.1/B4: **evidence+signoff gate (คืน error list เท่านั้น — ไม่ประกาศ approved เอง)**.
    `run_manifest_sha256` + `m4_frozen` (frozen M4 case/visibility manifest) เป็น required → ไม่มี fail-open.
    เจ้าเดียวที่ประกาศ approved=True คือ `p2_runplan.decide_p2()` หลังผ่านทุกด่าน (N/quality/latency/hard-neg)
    """
    errs = decision_benchmark_errors(cases, corpus, known_roles, evaluated_roles, gate_tags, signoff)
    if errs:                                            # structural/gate ไม่ผ่าน → ไม่ไป hash/เทียบ evidence
        return errs
    eh = eval_hash if eval_hash is not None else eval_set_sha256(cases)
    ch = corpus_hash if corpus_hash is not None else corpus_manifest_sha256(corpus)
    # M1: decision path ใช้ **M4b (selected-n)** ผูก frozen manifest + exact M4RunRequest — M4a preflight ห้ามแทน
    errs += validate_m4_run_evidence(m4_evidence, m4_frozen, m4_expected, eh, ch, run_manifest_sha256, require_stage=M4_STAGE_SELECTED)
    errs += validate_canary_evidence(canary_evidence, eh, ch, run_manifest_sha256)
    # B2/B4: M4 กับ canary ต้องมาจาก run/index/model/image เดียวกัน (กันประกอบข้าม run)
    if isinstance(m4_evidence, dict) and isinstance(canary_evidence, dict):
        if m4_evidence.get("run_id") != canary_evidence.get("run_id"):
            errs.append("m4/canary run_id ไม่ตรงกัน (คนละ run)")
        if m4_evidence.get("retrieval_index_manifest_sha256") != canary_evidence.get("retrieval_index_manifest_sha256"):
            errs.append("m4/canary retrieval_index_manifest_sha256 ไม่ตรงกัน (คนละ index)")
        if m4_evidence.get("model_revision") != canary_evidence.get("model_revision"):
            errs.append("m4/canary model_revision ไม่ตรงกัน (คนละ model)")
        if m4_evidence.get("image_digest") != canary_evidence.get("image_digest"):
            errs.append("m4/canary image_digest ไม่ตรงกัน (คนละ image)")
    return errs


# ── freeze (M1) — ต้องมี corpus hash ไม่ใช่แค่ cases ─────────────────────────────
def eval_set_sha256(cases) -> str:
    return hashlib.sha256(_canonical_json(cases)).hexdigest()


def corpus_manifest_sha256(corpus: dict, rerank_text_version: str = RERANK_TEXT_VERSION) -> str:
    def _text_hash(rt):
        if not isinstance(rt, str):
            raise ValueError("rerank_text ต้องเป็น str (validate_corpus ก่อน hash)")
        try:
            return hashlib.sha256(rt.encode("utf-8")).hexdigest()   # M1.3: lone surrogate -> controlled ValueError
        except UnicodeEncodeError:
            raise ValueError("rerank_text มี lone surrogate (encode utf-8 ไม่ได้)")
    rows = [{
        "point_id": pid,
        "source": corpus[pid].get("source"),
        "rerank_text_sha256": _text_hash(corpus[pid].get("rerank_text")),
        "payload": corpus[pid].get("payload"),
        "rerank_text_version": rerank_text_version,
    } for pid in sorted(corpus)]
    return hashlib.sha256(_canonical_json(rows)).hexdigest()


def artifact_manifest_unapproved(cases, corpus: dict, known_roles) -> dict:
    """
    B3: manifest สำหรับ **mechanics smoke เท่านั้น** (structural valid) — ติดป้าย approved=False
    **ห้าม**ใช้ตัดสิน/freeze arm ; decision/freeze ต้องใช้ p2_runplan.decide_p2() เท่านั้น
    """
    # B3.2: smoke ยอม draft/ai-reviewed ได้ (structural เท่านั้น) แต่ output ต้องไม่ decision-eligible
    errs = validate_benchmark(cases, corpus, known_roles, allowed_label_status=SMOKE_LABEL_STATUS)
    if errs:
        raise ValueError(f"artifacts ยังไม่ valid structurally ({len(errs)} errors): {errs[:3]}")
    return {
        "kind": "mechanics-smoke-unapproved", "approved": False, "decision_eligible": False,
        "benchmark_contract_version": BENCHMARK_CONTRACT_VERSION,
        "eval_set_sha256": eval_set_sha256(cases),
        "corpus_manifest_sha256": corpus_manifest_sha256(corpus),
        "rerank_text_version": RERANK_TEXT_VERSION,
    }


def permission_gate_ok(exit_code) -> bool:
    """
    quality report valid เฉพาะเมื่อ permission suite เขียว (leak=0, auth VERIFIED). B1: type-strict —
    รับเฉพาะ exact int (False/True/0.0/None/"0" -> ValueError กัน fail-open จาก `False == 0`)
    """
    if type(exit_code) is not int:
        raise ValueError(f"permission exit code must be exact int, got {type(exit_code).__name__}")
    return exit_code == 0
