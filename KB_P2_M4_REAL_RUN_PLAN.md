# P2 — M4 real-run plan (permission-leak proof) — pure/offline draft ก่อนขอ GO run (rev2)

> **สืบเนื่อง:** `KB_P2_M4_REAL_RUN_PLAN_CODEX_REVIEW_27C0221.md` (FIX-THEN-GO harness — ปิด B1-B3/M1-M3 ก่อนเขียน runner)
> **ยังไม่รัน** Docker/Qdrant/model เพิ่ม — เอกสารนี้ล็อกสัญญาก่อนขอ review เพื่อขอ GO run
> **เป้าหมาย M4:** พิสูจน์ว่า permission filter เป็น **ด่านที่มีผลจริง (load-bearing)** — sentinel ที่ห้ามเห็น **ต้องมีโอกาสถูกค้นเจอถ้าไม่มี filter** (ติด unfiltered top-N) แต่ **ไม่ผ่าน provider และไม่ถึง real cross-encoder** ; authorized ต้องถึง model จริง

## สรุปที่แก้ตาม review (rev2)
- **B1 unfiltered control:** เพิ่ม raw unfiltered Qdrant query (bypass provider/compiler/matcher) — sentinel ทุก required category ต้องติด **unfiltered top-N** ก่อน แล้วพิสูจน์ว่าหายจาก filtered provider
- **B2 pair-bound:** ผูก **point↔text เป็น pair digest** (`sha256(point_id_sha256:rerank_text_sha256)`) เทียบ **ordered/multiset** ข้าม oracle→provider→model_input→rerank_output (ไม่ใช่ id/text set แยก)
- **B3 counts:** model_call/input/score counts + all_scores_finite + scorer_kind + expected==completed case + error/skip=0 (runner derive จาก trace จริง)
- **M1 network:** Qdrant บน Docker network `internal:true` (`qdrant:6333`, ไม่ publish port, ไม่มี egress) ; `--network none` ใช้เฉพาะ smoke ; **port ไม่ใช่ trust signal**
- **M2 oracle:** frozen manifest ประกาศ `expected_visible_roles` matrix ; oracle ใช้ matrix + pair hashes เท่านั้น (ไม่ reimplement policy จาก payload)
- **M3 stage contract:** preflight (decision_eligible=false/N=50/ไม่มี selection_digest) vs selected-n (N∈N_SET/มี selection_digest) + run_id/index/raw_evidence_sha256/schema_version + exact expected pin/image/index

## แยกสองระยะ (ตาม Codex M1)
| ระยะ | N | ป้าย | ใช้ทำอะไร | selection_digest |
|---|---|---|---|---|
| **M4a preflight** | **50** (candidate surface มากสุด) | `evidence_stage=preflight-n50` · `decision_eligible=false` | ปลดทางให้ **N-sweep** เท่านั้น | ไม่มี (ห้ามเข้า `decide_p2`) |
| **M4b selected-N** | selected_n (จาก dev sweep) | `evidence_stage=selected-n` | bind final decision evidence | อ้าง root + `selection_digest` |

> ห้ามเติม `selection_digest` ย้อนหลังลง raw evidence เดิม (post-hoc mutation) — M4b เป็น run ใหม่ที่ pipeline/image/index เดิม แล้วผูก selection

## 12 หัวข้อที่ล็อก (ต้องครบก่อนขอ GO run)

### 1. Isolation interlock (fail-closed)
- Qdrant = **fresh compose project / internal network ID / fresh volume / collection UUID+name + synthetic run marker** ของตัวเอง
- interlock **allow exact** ชุดข้างต้น + reject known **production endpoint/collection** เป็น defense — **port ไม่ใช่ trust signal** (ดูหัวข้อ 7: endpoint isolated ที่ถูกต้องคือ `qdrant:6333`)
- ต้องมี `isolated_interlock=PASS` จาก positive assertion (ping collection ว่าง + run marker ตรง) ไม่ใช่แค่ไม่ error

### 2. Synthetic-only corpus
- freeze **seed manifest**: point ID, payload ACL, vector/index metadata, **text hash** (ห้าม raw text)
- source prefix `P2-M4-SYNTH-*`, `payload.synthetic=true` — **ห้ามใช้เอกสารบริษัท/ข้อมูลลูกค้า**

### 3. Adversarial sentinels
- unauthorized **semantic twin / hard negative** ที่ตั้งใจให้ relevance สูงกว่า authorized (ล่อ router/reranker)
- ครอบ: **missing ACL, malformed ACL, stale policy_version, QUARANTINED**
- sentinel ต้อง unauthorized สำหรับ role ที่ทดสอบจริง (P1 policy path)
- **จัด deterministic vectors ให้ sentinel rank สูงกว่า authorized positive** (เพื่อให้ติด unfiltered top-N แน่นอน)

### 3.1 Unfiltered relevance control (B1 — พิสูจน์ว่า filter load-bearing) ⭐
- ต่อ case ยิง **raw unfiltered Qdrant query** ด้วย **QueryProbe เดียว (query vector + limit=N)** กับ **filtered provider call** โดย unfiltered **ไม่ผ่าน provider/compiler/matcher** (M2: validator บังคับ `unfiltered_query_vector_sha256 == filtered_query_vector_sha256 == query_vector_sha256` + `limit == selected_n`)
- บังคับ: sentinel ของ **ทุก required category ปรากฏใน unfiltered top-N** (`sentinel_pairs ⊆ unfiltered_topn_pairs`)
- `unfiltered_topn_pairs` = **ordered, ไม่ซ้ำ, len ≤ N** ; `observed_sentinel_ranks` ต้อง **== ตำแหน่งจริง (1-based)** ใน unfiltered list (validator สร้าง position map + reject rank เท็จ/ซ้ำ/ขาด)
- จากนั้นพิสูจน์ว่า sentinel **หายไปก่อน model** (`sentinel_pairs ∩ model_input_pairs = ∅` ต่อ case)
- ถ้า sentinel ไม่ติด unfiltered top-N หรือ rank ไม่ตรงตำแหน่งจริง → `leak=0` **ไม่มีความหมาย** → FAIL

### 4. Independent oracle (M2 — expected authorization เป็นอิสระจริง)
- frozen seed manifest **ประกาศ `expected_visible_roles` / visibility matrix ต่อ point/case โดยคนเขียน fixture ตรง ๆ**
- oracle ตัดสิน authorized ต่อ role จาก **matrix นี้ + point↔text pair hashes เท่านั้น** — **ห้าม reimplement policy semantics** จาก Qdrant payload
- **ห้าม reuse** `compile_retrieval_filter` / `matches_policy` / provider output / query ที่กำลังทดสอบ เป็น oracle
- **direct scroll** = ตรวจว่า collection ตรง manifest (inventory) ; **unfiltered query** (หัวข้อ 3.1) = พิสูจน์ sentinel competitiveness — คนละหน้าที่

### 5. Production-like authorization boundary
- `AUTH_MODE=enforce` · role-scoped **synthetic** key/principal · **server-resolved** trusted `EffectiveAccess`
- **ห้ามส่ง raw role จาก request** เข้า provider ตรง ๆ (ต้องผ่าน trusted-access invariant)

### 6. Exact call path
```
real Qdrant → compiled filter → provider (postcondition re-validate payload) → SPY → pinned real cross-encoder
```
- **spy** จับ **ID + text hashes** ที่ boundary **ก่อน** เรียก model ; spy **ต้องไม่เปลี่ยน input**

### 7. Network boundary (M1 — แก้ contradiction เดิม)
- **หนึ่ง container** (pinned runner+model image เดิม) + Qdrant บน Docker network `internal: true`
- Qdrant ฟัง `qdrant:6333` **ภายใน network** — **ไม่ publish port ออก host**, ไม่มี internet egress ; runner เชื่อม `qdrant:6333` ได้
- **`--network none` ใช้เฉพาะ model-load smoke ที่ผ่านแล้ว — ไม่ใช้กับ M4** (M4 ต้องคุย Qdrant)
- **port ไม่ใช่ trust signal** — interlock allow exact compose project/run ID + internal network ID + fresh volume + collection UUID/name + synthetic marker ; reject known prod endpoint/collection เป็น defense เพิ่ม (ไม่ใช้เลข port)

### 8. Fail-closed outcomes
auth/filter/oracle/index mismatch · partial query · exception · empty/vacuous input · sentinel ถึง boundary · evidence เขียนไม่ครบ → **FAIL/INCONCLUSIVE + exit non-zero** (ไม่ใช่ PASS)

### 9. Negative controls (ต้องพิสูจน์ validator จับได้)
- empty model trace → ไม่ PASS
- sentinel injection (id/text) → FAIL ก่อนส่ง unauthorized text เข้า real model
- wrong index/run/image/hash → FAIL
- oracle mismatch → FAIL
> negative control ต้อง**หยุดก่อน**ส่ง unauthorized text เข้า real model

### 10. Durable evidence (hash-only, ไม่มี raw text/secret)
canonical raw receipt ของ: seed manifest, index, oracle output, provider candidates, **spy trace (id+text hashes)**, model metadata, command/timestamps/exit, stdout/stderr hashes
- summary อ้าง **raw-evidence digest/path** ไม่พึ่งไฟล์ gitignored ชุดเดียว

### 11. Acceptance (M4 PASS ก็ต่อเมื่อครบ — validator `p2_eval.validate_m4_run_evidence` **v5** + `p2_runplan.validate_m4_preflight_bundle` บังคับแล้ว)
- `isolated_interlock=PASS` (มาคู่ **IsolationProof** ที่ recompute ตรง) · `independent_oracle=PASS` (มาคู่ **OracleProof** ครอบ exact case set) · `schema_version=p2-m4-v5`
- **B1:** `sentinel_pairs ⊆ unfiltered_topn_pairs` (sentinel ติด unfiltered top-N) แล้ว `∩ model_input = ∅`
- **B3:** `model_call_count>0` · `model_input_count>0` · `score_count==model_input_count` · `all_scores_finite=True` · `scorer_kind=pinned-cross-encoder` · `expected_case_count==completed_case_count>0` · `error_count==skip_count==0`
- `unauthorized_in_model_inputs == 0` (exact int) · `sentinel_reached_model=False`
- **B2 (pair-bound multiset):** `provider ⊆ authorized` · `model_input ⊆ provider` · `rerank_output = permutation(model_input)` · `model_input/authorized ∩ sentinel = ∅`
- **M3:** stage contract + `run_id`/index/`raw_evidence_sha256` + **exact** compare pin/image/index/case set กับ frozen run request

### 12. Teardown/retention
- **export evidence ก่อน teardown** · ระบุ collection UUID/volume/network ที่ลบ
- เก็บเฉพาะ evidence ที่ **ไม่มี secret/raw text**

## Evidence schema (M4Evidence **v5** — per_case authoritative + provenance-bound) ที่ validator บังคับแล้ว
> **compat:** v5 = v4 + `query_text_sha256` (frozen+per_case) + `isolation_proof`/`oracle_proof` + scorer_kind/pin จาก **ScorerProof**.
> artifact v4 เก่า **ถูก reject** (validator ใหม่ไม่ reinterpret) — ต้อง re-run ด้วย v5 producer
```
schema_version = p2-m4-v5 · status/isolated_interlock/independent_oracle = PASS
scorer_kind = pinned-cross-encoder ← มาจาก ScorerProof (validate_scorer_metadata == M4RunRequest) ไม่ hardcode/ไม่รับจาก run_meta
evidence_stage ∈ {preflight-n50, selected-n} · sentinel_reached_model = false · unauthorized_in_model_inputs = 0
m4_case_manifest_sha256   ← ต้อง == RunPlan.m4_case_manifest_sha256 (frozen binding, B2)
raw_evidence_sha256       ← recompute จาก canonical(per_case) (B3, ไม่ใช่ self-stamp)
per_case[]:               ← **หลักฐาน authoritative** (security invariant ตรวจต่อ case/role — B1)
  case_id_sha256 · role_identity_sha256 · category · selected_n · query_text_sha256 · query_vector_sha256
  pair_components[{point_id_sha256, rerank_text_sha256, pair_sha256}]  ← recompute pair (B3)
  unfiltered_topn_pairs[]  (raw unfiltered query) · observed_sentinel_ranks[[pair, rank≤N]]  (B1/M2)
  provider_pairs[] ⊆ authorized(frozen) · model_input_pairs[] ⊆ provider · rerank_output_pairs = permutation(model_input)
  model_input ∩ sentinel(frozen) = ∅ · sentinel ⊆ unfiltered_topn
  model_call_count/model_input_count/score_count > 0 (score==input) · all_scores_finite · status = PASS
# run-level provenance proofs (B2 — verdict derive จาก proof จริง ไม่ใช่ string/count)
isolation_proof{ **interlock observation จาก runner** : project/network/volume/collection_id_sha256 (distinct) ·
   initial_point_count==0 (collection ว่างก่อน seed) · network_published_ports==0 (internal/no publish) ·
   endpoint_is_production==False · marker_written_sha256==marker_readback_sha256 (write→read กลับ target เดียวกัน) · isolation_proof_sha256=recompute(body)}
   ← marker_written_sha256 == receipt.isolation_marker_sha256 (marker load-bearing, ตรวจใน preflight bundle)
oracle_proof{ **independent direct-scroll observation** : frozen_manifest_sha256==m4_manifest · retrieval_index_manifest_sha256==M4RunRequest ·
   collection_id_sha256==isolation_proof.collection_id (อ่าน collection ที่ isolate) ·
   observed_visibility[{case_id_sha256, observed_authorized_pairs==frozen authorized, observed_sentinel_pairs==frozen sentinel}] ·
   observation_sha256=recompute(observed) · oracle_proof_sha256=recompute(body)}   ← builder สร้าง PASS จาก frozen อย่างเดียวไม่ได้
evidence_body_sha256   ← durable root ทั้ง bundle (ทุก top-level ยกเว้น run_receipt_sha256) · receipt commit ค่านี้ → post-run proof swap = mismatch
# pin + durable binding — scorer_kind/model_revision/tokenizer_revision/model_file_manifest_sha256/inference_config มาจาก ScorerProof
model_revision / tokenizer_revision (commit) · image_digest · model_file_manifest_sha256 · inference_config
run_id · retrieval_index_manifest_sha256 · eval/corpus hash · run_manifest_sha256 (decision) · selection_digest (M4b)
# per_case: effective_role · QueryProbe (query_text_sha256 == frozen ; query_vector_sha256 == frozen ; unfiltered/filtered vector == query_vector ; limit==N)
#   · run_receipt_sha256 (durable receipt reference — ไม่ใส่ command/raw log ตรง ๆ) · exact hash-only keys (reject raw/unknown)
# M4RunRequest (expected) — จาก RunPlan/frozen run request : model_revision · tokenizer_revision · model_file_manifest_sha256
#   · image_digest · inference_config · retrieval_index_manifest_sha256 [· run_id]  → validator เทียบ **exact ทั้ง M4a/M4b**
#   scorer จริงต้องประกาศ metadata() == M4RunRequest ก่อน delegate (mock/wrong pin → run_case raise, ไม่มี evidence)
# frozen M4 manifest (fixture/oracle — expected_visible_roles matrix):
#   cases{case_id_sha256: {role_identity_sha256, effective_role, category, query_text_sha256, query_vector_sha256, authorized_pairs[], sentinel_pairs[]}}
#   required_categories[] · evaluated_roles[]  → digest = m4_case_manifest_sha256 (ผูกเข้า RunPlan)
# validate_m4_frozen_manifest (ก่อน hash, ไม่ crash): exact types/keys · sha256 · non-blank/unique pairs ·
#   authorized/sentinel disjoint · **ทุก required_category + evaluated_role มี case**
# validator: exact case set · case roles == evaluated_roles == plan · required_categories == plan · QueryProbe==frozen · zero missing
```
> validator `validate_m4_run_evidence` v5 + `validate_m4_frozen_manifest` + `validate_m4_isolation_proof`/`validate_m4_oracle_proof` + M4RunRequest + RunPlan binding พร้อมแล้ว
> (test_p2_m4.py 56/56 · test_p2_m4_harness.py 41/41 — scorer provenance · single run_case boundary · evidence_body durable root (post-run swap fail) · IsolationProof interlock observation · OracleProof observed visibility · marker load-bearing · cross-role swap · QueryProbe(text+vector)-ผูก-frozen · M4a exact pin)
> harness ผลิต evidence ผ่าน **boundary เดียว** `run_case`/`run_m4_cases` (validate scorer + input ก่อน delegate) + `build_isolation_proof`/`build_oracle_proof`/`build_run_verdicts` + `M4RunReceipt` แยกที่ `run_receipt_sha256` อ้างถึง

## Gate ที่คงไว้
- Data Owner sign-off **ไม่จำเป็นกับ M4a** (isolated synthetic mechanics) — แต่ M4a ติดป้าย non-decision และ **ห้ามส่งเข้า `decide_p2()` แทน M4b**
- decision benchmark: NO-GO จน sign-off + **M4b (selected-N)** + validated canary + evidence bundle ครบ
- CPU latency ห้ามตัดสิน GPU/production

## ขอ Codex review (plan นี้ + pure validator B1/B2/M1)
1. plan ล็อก 12 หัวข้อ + M4a/M4b + evidence schema ครบพอขอ GO run ไหม (โดยเฉพาะ isolation interlock + independent oracle + spy id/text)
2. validator B1/B2/M1 (pure) ปิดช่อง vacuous-pass / text-proof / stage-binding ครบไหม
3. หลัง review → เตรียม M4 harness (seed/oracle/spy/runner) เป็น pure/injectable ก่อน ค่อยขอ GO run บน isolated Qdrant
