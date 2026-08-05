# P2 — M4 real-run plan (permission-leak proof) — pure/offline draft ก่อนขอ GO run

> **สืบเนื่อง:** `KB_P2_SMOKE_RESULT_CODEX_REVIEW_4926A40.md` (GO NOW ร่าง plan + แก้ validator B1/B2/M1 ; FIX-BEFORE-RUN M4)
> **ยังไม่รัน** Docker/Qdrant/model เพิ่ม — เอกสารนี้ล็อกสัญญาก่อนขอ review เพื่อขอ GO run
> **เป้าหมาย M4:** พิสูจน์ว่า **authorized candidate ถึง real pinned cross-encoder** แต่ **sentinel ที่ห้ามเห็น ไม่ถึงโมเดล** (ทั้ง ID และ text)

## แยกสองระยะ (ตาม Codex M1)
| ระยะ | N | ป้าย | ใช้ทำอะไร | selection_digest |
|---|---|---|---|---|
| **M4a preflight** | **50** (candidate surface มากสุด) | `evidence_stage=preflight-n50` · `decision_eligible=false` | ปลดทางให้ **N-sweep** เท่านั้น | ไม่มี (ห้ามเข้า `decide_p2`) |
| **M4b selected-N** | selected_n (จาก dev sweep) | `evidence_stage=selected-n` | bind final decision evidence | อ้าง root + `selection_digest` |

> ห้ามเติม `selection_digest` ย้อนหลังลง raw evidence เดิม (post-hoc mutation) — M4b เป็น run ใหม่ที่ pipeline/image/index เดิม แล้วผูก selection

## 12 หัวข้อที่ล็อก (ต้องครบก่อนขอ GO run)

### 1. Isolation interlock (fail-closed)
- Qdrant = **fresh project/network/volume/port/collection + run marker** ของตัวเอง
- runner **reject** prod URL, prod collection, `:6333`, และ config/env ไม่ครบ — ต่างแค่ชื่อ collection **ไม่พอ**
- ต้องมี `isolation_interlock=PASS` จาก positive assertion (ping collection ว่าง + run marker ตรง) ไม่ใช่แค่ไม่ error

### 2. Synthetic-only corpus
- freeze **seed manifest**: point ID, payload ACL, vector/index metadata, **text hash** (ห้าม raw text)
- source prefix `P2-M4-SYNTH-*`, `payload.synthetic=true` — **ห้ามใช้เอกสารบริษัท/ข้อมูลลูกค้า**

### 3. Adversarial sentinels
- unauthorized **semantic twin / hard negative** ที่ตั้งใจให้ relevance สูงกว่า authorized (ล่อ router/reranker)
- ครอบ: **missing ACL, malformed ACL, stale policy_version, QUARANTINED**
- sentinel ต้อง unauthorized สำหรับ role ที่ทดสอบจริง (P1 policy path)

### 4. Independent oracle
- **direct scroll** จาก isolated collection → เทียบ **frozen seed manifest**
- **ห้าม reuse** `compile_retrieval_filter` / `matches_policy` / provider output / query ที่กำลังทดสอบ เป็น oracle
- oracle คืน authorized id/text hash set ที่เป็น ground truth

### 5. Production-like authorization boundary
- `AUTH_MODE=enforce` · role-scoped **synthetic** key/principal · **server-resolved** trusted `EffectiveAccess`
- **ห้ามส่ง raw role จาก request** เข้า provider ตรง ๆ (ต้องผ่าน trusted-access invariant)

### 6. Exact call path
```
real Qdrant → compiled filter → provider (postcondition re-validate payload) → SPY → pinned real cross-encoder
```
- **spy** จับ **ID + text hashes** ที่ boundary **ก่อน** เรียก model ; spy **ต้องไม่เปลี่ยน input**

### 7. Network boundary
- model/runner บน **isolated internal Docker network เท่านั้น** — ไม่มี internet egress
- ระบุชัด: ใครคุยกับ Qdrant (runner) · ใครโหลด model จาก **baked snapshot** (`--network none` container จาก image `sha256:27768971905e…`)

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

### 11. Acceptance (M4 PASS ก็ต่อเมื่อครบ)
- `isolation_interlock=PASS` · `independent_oracle=PASS`
- **`model_invocation_count > 0`** + finite scores (authorized ถึง model จริง)
- `unauthorized_in_model_inputs == 0` (exact)
- **id set + text set disjoint** ระหว่าง model-input กับ sentinel
- provider/model inputs **⊆ authorized oracle** (id และ text)
- rerank output = **permutation ของ authorized candidates**
- ทุก required case มีผลครบ (zero-skip)

### 12. Teardown/retention
- **export evidence ก่อน teardown** · ระบุ collection UUID/volume/network ที่ลบ
- เก็บเฉพาะ evidence ที่ **ไม่มี secret/raw text**

## Evidence schema (M4Evidence v2 — hash-only) ที่ validator บังคับ
```
status / isolated_interlock / independent_oracle = PASS
sentinel_reached_model = false (exact)
model_invocation_count > 0 (exact int)            ← B1 กัน vacuous pass
unauthorized_in_model_inputs = 0 (exact int)
evidence_stage ∈ {preflight-n50, selected-n}      ← M1
# id + text hash sets (non-empty, sha256)          ← B2
authorized_candidate_id_hashes / authorized_candidate_text_hashes   (จาก oracle)
provider_candidate_id_hashes   / provider_candidate_text_hashes     (หลัง Qdrant/filter)
model_input_id_hashes          / model_input_text_hashes            (spy ก่อน real scorer)
unauthorized_sentinel_id_hashes/ unauthorized_sentinel_text_hashes
# assertions
provider ⊆ authorized (id, text) · model_input ⊆ provider (id, text)
model_input ∩ sentinel = ∅ (id, text) · authorized ∩ sentinel = ∅ (id, text)
# pin + binding
model_revision / tokenizer_revision (commit) · image_digest
run_id · retrieval_index_manifest_sha256 · eval/corpus hash · run_manifest_sha256 (decision path)
selection_digest (M4b เท่านั้น)
```

## Gate ที่คงไว้
- Data Owner sign-off **ไม่จำเป็นกับ M4a** (isolated synthetic mechanics) — แต่ M4a ติดป้าย non-decision และ **ห้ามส่งเข้า `decide_p2()` แทน M4b**
- decision benchmark: NO-GO จน sign-off + **M4b (selected-N)** + validated canary + evidence bundle ครบ
- CPU latency ห้ามตัดสิน GPU/production

## ขอ Codex review (plan นี้ + pure validator B1/B2/M1)
1. plan ล็อก 12 หัวข้อ + M4a/M4b + evidence schema ครบพอขอ GO run ไหม (โดยเฉพาะ isolation interlock + independent oracle + spy id/text)
2. validator B1/B2/M1 (pure) ปิดช่อง vacuous-pass / text-proof / stage-binding ครบไหม
3. หลัง review → เตรียม M4 harness (seed/oracle/spy/runner) เป็น pure/injectable ก่อน ค่อยขอ GO run บน isolated Qdrant
