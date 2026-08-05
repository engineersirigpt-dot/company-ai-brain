# P2 — ปิด provenance seam B1/B2/M1/M2 (round 3) → review ก่อนเขียน runner จริง

> **สืบเนื่อง:** `KB_P2_M4_HARNESS_FIX2_CODEX_REREVIEW_3E61C33.md` (FIX-THEN-GO runner — provenance seam)
> **ทั้งหมด pure/offline** — ไม่รัน Docker/Qdrant/model · real-path runner ยัง NO-GO

## ประเด็นหลัก (Codex): "หลักฐานยังไม่พิสูจน์ว่า scorer ถูกเรียกจริงหรือเป็น pinned model จริง — happy path ที่ใช้ mock/no-metadata ยังได้ evidence ชื่อ pinned-cross-encoder"

## Finding → fix → proof

| # | ช่องที่ Codex เจอ | Fix |
|---|---|---|
| **B1** ⭐ | (a) `CaseTrace` เป็น public NamedTuple → caller สร้าง/`_replace()` เองได้, `build_case_record` เช็คแค่ `isinstance` ; (b) `assemble_evidence` **hardcode** `scorer_kind="pinned-cross-encoder"` + รับ pin จาก `run_meta` — ไม่มีจุดอ่าน `scorer.metadata()` → mock/no-metadata ก็ถูกบันทึกเป็น pinned PASS | **boundary เดียว** `run_case()`/`run_m4_cases()` : `validate_scorer_metadata(scorer, expected)` เทียบ **kind + model/tokenizer revision + file-manifest + inference_config (max_length/batch_size/device/dtype/model_name)** ตรง M4RunRequest **ก่อน delegate** → mock/wrong pin **raise** ; `_CaseTrace`/`_score_case`/`_build_case_record` เป็น **private** (ไม่มี public consumer รับ trace ที่ caller ปั้น) ; `scorer_kind`/pin ใน evidence มาจาก **ScorerProof** เท่านั้น (ลบ pin ออกจาก `run_meta` allowlist) |
| **B2** | `build_verdicts()` รับ `isolation="PASS"`/counts เป็น string/int ลอย, ตั้ง `sentinel_reached=False` เอง, ไม่มี proof object ; `case_count=True` คืน PASS (bool==int) ; receipt marker caller ส่งเอง unbound | `IsolationProof` (project/network/volume/collection UUID **distinct** + marker + `isolation_proof_sha256`=recompute body) · `OracleProof` (frozen_manifest + retrieval_index + **exact case_set** + recompute) · `build_run_verdicts(expected, iso, oracle, case_records, frozen)` **derive** status/counts จาก records+proof จริง (`type(x) is int` guard) · public gate **recompute proof bodies + coverage** (ไม่ตรวจแค่คำว่า PASS) · **marker load-bearing**: `isolation_proof.marker_sha256 == receipt.isolation_marker_sha256` |
| **M1** | schema เปลี่ยน shape (บังคับ `query_text_sha256`) แต่ชื่อยัง `p2-m4-v4` — ชื่อเดียว shape สองแบบ ; plan doc ไม่ sync | bump **`p2-m4-v5`** (v4 artifact ถูก **reject** ไม่ reinterpret) · sync `KB_P2_M4_REAL_RUN_PLAN.md` (query_text_sha256 + proof + scorer provenance) |
| **M2** | `_text_hash()` ปฏิเสธแค่ `""` → `"   "` ผ่านถึง scorer | ใช้กติกาเดียวกับ eval contract: `E._bad_str` (non-blank หลัง strip + reject control `Cc`/lone-surrogate `Cs`) |

## execution/provenance flow (หลัง fix)

```
run_m4_cases(expected, frozen, scorer, inputs, selected_n):
  validate_scorer_metadata(scorer, expected)  ← mock/no-metadata/wrong pin → raise (ไม่มี evidence)
  ต่อ case: run_case(...)  → validate scorer + query bind frozen + validate input ก่อน delegate → guard sentinel
            → scorer.score(query จริง, ...) → _CaseTrace (private, one-shot) → case record
  คืน (case_records, ScorerProof)
build_isolation_proof(...) / build_oracle_proof(frozen, index)      ← proof body + recompute digest
build_run_verdicts(expected, iso, oracle, case_records, frozen)     ← status/counts derive จาก proof+records จริง
assemble_evidence(records, run_meta[allowlist ไม่มี pin], scorer_proof, iso, oracle, verdicts)
  → scorer_kind/pin จาก scorer_proof · verdict เขียนทับ run_meta · embed proof
public gate (validate_m4_preflight_bundle / validate_m4_run_evidence):
  recompute isolation_proof_sha256/oracle_proof_sha256 จาก body · oracle case_set == frozen cases ·
  marker == receipt.isolation_marker · pin/inference_config == M4RunRequest exact
```

## negative tests (pure) ที่เพิ่ม
- **B1**: mock (ไม่มี metadata) → raise · kind=mock → raise · model_revision/model_file_manifest/inference_config ผิด → raise · `run_case` ด้วย mock → raise ก่อน delegate · run_meta ใส่ pin/verdict → raise · **ไม่มี public `build_case_record`/`score_case`** · scorer ได้ query ของ case จริง (`[QT1, QT2]`)
- **B2**: isolation_proof recompute ไม่ตรง / UUID ไม่ distinct / oracle frozen_manifest ผิด / oracle case_set ไม่ครอบ / oracle index != M4RunRequest → gate fail · receipt marker != evidence marker → gate fail · sentinel ใน model_input → `sentinel_reached_model=True`+status FAIL (derive จริง)
- **M2**: query `"   "` → raise ก่อน delegate + underlying call = 0 · vector NaN → raise ก่อน delegate

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch ครบ)
```
test_p2_m4_harness 34/34   test_p2_m4 47/47   test_p2_runplan 95/95   test_p2 166/166
test_p2_provider 22/22     test_p2_harness 21/21   test_p2_pin 14/14   test_p2_adapter 22/22
test_p2_dockerbuild 41/41  test_policy 69/69   test_eval_contract 64/64   test_ask_eval_harness 12/12
test_auth 11/11            test_p5b_fixtures 11/11        รวม 629 checks ผ่านหมด
```

## ยังไม่ได้ทำ (รอ review รอบนี้ก่อน — real-path)
- **runner จริง** — wire `resolve_effective_access → provider → run_case(PinnedCrossEncoder ที่มี metadata() จริง) → build_isolation/oracle_proof` บน isolated Qdrant
- **PinnedCrossEncoder.metadata()** — expose kind/model/tokenizer revision/file-manifest/inference_config จาก snapshot ที่ load จริง (ผูก p2_pin)
- **oracle จริง** — direct scroll เทียบ frozen manifest (ตอนนี้ OracleProof ผูก structure/coverage ; runner ต้องเติม independent read)
- **atomic evidence/receipt write** (temp→rename) + failure controls (partial write / non-zero exit / exception → ไม่มี PASS artifact)

## ขอ Codex review
1. scorer provenance (`validate_scorer_metadata` == M4RunRequest ก่อน delegate + scorer_kind/pin จาก ScorerProof) ปิด happy-path-mock ครบไหม
2. single `run_case` boundary + private `_CaseTrace` ปิด fabricated-trace seam (accidental wiring) ครบไหม
3. `IsolationProof`/`OracleProof` + `build_run_verdicts` derive + gate recompute/coverage + marker load-bearing — proof-object contract พอสำหรับ runner ไหม หรือควรเพิ่ม field/binding ใด
4. หลังผ่าน → เขียน real-path runner + PinnedCrossEncoder.metadata() + independent oracle read + atomic writer แล้วขอ **GO M4a run**

**Gate:** real-path runner = FIX-THEN-GO หลัง targeted re-review ผ่าน · M4a run = NO-GO จน runner + interlock/oracle/atomic review · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน Data Owner sign-off + M4b + validated canary
