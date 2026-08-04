# Codex Targeted Re-review — P2 Slice 1 fix round 2

**Commit reviewed:** `0a9136c`  
**Input:** `KB_P2_SLICE1_FIX2_HANDOFF.md`  
**Scope:** B2.1 / M5.1 / M1.1-M5.2 / M3.1 และ go/no-go สำหรับ Slice 2 เท่านั้น  
**Verdict:** **FIX-THEN-GO — ยังไม่เริ่ม Slice 2**

## Intent / simpler alternative

เป้าหมายคือทำให้ input และ evidence contract fail-closed ก่อนจ่ายต้นทุน build container/model วิธีที่เล็กที่สุดยังถูกทาง: ใช้ validator/manifest ชุดเดียวเป็นประตูเข้า benchmark ไม่ต้องเพิ่ม framework หรือ schema library รอบนี้

เส้นทางหลักที่ trace:

`benchmark_manifest()` → `validate_benchmark()` → `validate_corpus()` + `validate_ranking_eval_set()` → dual hashes  
และ `ranking case` → metric guards / `rerank_order()` / `fused_rrf()`

B2.1, M5.1 และ M3.1 ทำงานตามที่อ้าง แต่ validation-to-hash seam ยังมีสอง input ที่ไม่คืน controlled validation failure

## Findings

### M1.2 — non-dict corpus ถูกตรวจพบแล้วแต่ยังไหลต่อจน `AttributeError`

**ตำแหน่ง:** `p2_eval.py:56-66`, `p2_eval.py:117`, `p2_eval.py:164-170`

**Why it matters:** `validate_corpus()` คืน error ถูกต้องเมื่อ corpus ไม่ใช่ dict แต่ `validate_benchmark()` ยังเรียก `validate_ranking_eval_set()` ต่อ ฟังก์ชันหลังใช้ `corpus.get(pid)` โดยไม่มี type guard ดังนั้น malformed JSON top-level จะ crash แทนการคืน error list/`ValueError` ตาม contract ของ manifest

**Evidence reproduced:**

```text
validate_benchmark([ranking_case], None, known_roles)
→ AttributeError
```

**Minimal fix:**

- ให้ `validate_ranking_eval_set()` reject non-dict corpus ก่อน loop หรือ
- ให้ `validate_benchmark()` short-circuit case validation ที่ต้อง dereference corpus เมื่อ corpus shape ไม่ผ่าน
- `benchmark_manifest()` ต้องแปลง invalid input ทุกกรณีเป็น controlled `ValueError` โดยไม่ปล่อย `AttributeError`/`TypeError`

เพิ่ม tests อย่างน้อยสำหรับ corpus = `None`, list และ string โดยมี ranking case ที่อ้าง relevant point จริง เพื่อไม่ให้ test ผ่านเพราะ cases ว่าง

### M1.3 — lone Unicode surrogate ผ่าน `_bad_str()` แล้วทำ manifest crash

**ตำแหน่ง:** `p2_eval.py:45-49`, `p2_eval.py:135-160`, `p2_eval.py:174-190`

**Why it matters:** `_bad_str()` ปฏิเสธเฉพาะ Unicode category `Cc`; lone surrogate เป็น category `Cs` จึงผ่าน corpus/query validation แต่ `.encode("utf-8")` ใน text hash ล้มด้วย `UnicodeEncodeError` นอกจากนี้ full payload และ extra eval metadata ยังอาจมี surrogate/NaN ที่ canonical JSON path ไม่ได้ปิด

**Evidence reproduced:**

```text
_bad_str("\ud800")                         → False
validate_corpus(corpus_with_surrogate_text) → []
corpus_manifest_sha256(...)                 → UnicodeEncodeError
```

**Minimal fix:**

- fields ที่ model/retrieval ใช้ต้อง reject `Cc` และ `Cs`
- canonical JSON hashing ใช้ helper เดียวทั้ง eval/corpus เช่น `sort_keys=True`, `ensure_ascii=True`, `allow_nan=False`, compact separators
- invalid JSON-number (`NaN`, `+/-Inf`) ต้องเป็น controlled `ValueError` ไม่สร้าง non-standard manifest

เพิ่ม regression สำหรับ surrogate ใน query, rerank_text และ extra payload field พร้อม NaN ใน extra metadata; valid Thai/English/emoji ต้อง hash ได้และ deterministic เหมือนเดิม

### N1 — handoff บอก empty `relevant_ids` fail แต่ implementation ตั้งใจคืน undefined

**ตำแหน่ง:** `retrieval_metrics.py:37-48`, `retrieval_metrics.py:62-99`, `test_p2.py:110`, `KB_P2_SLICE1_FIX2_HANDOFF.md:12`

โค้ดอนุญาต empty relevant IDs: Recall/CandidateRecall คืน `None`; Hit/MRR คืน `0` และ test เดิมยืนยัน behavior นี้ ขณะที่ handoff ระบุ “ว่าง → fail”

**Disposition:** ไม่ block Slice 2 เพราะ `validate_benchmark()` ห้าม ranking relevance ว่างอยู่แล้ว แนะนำคง metric helper behavior เดิม แล้วแก้ข้อความ handoff/runner contract ให้ชัดว่า empty ถูกกันที่ benchmark boundary ไม่ใช่ทุก metric helper ห้ามอ้างใน evidence ว่า empty relevant IDs ถูก public guard ปฏิเสธแล้ว

## Findings เดิม

| Finding | ผล re-review |
|---|---|
| B2.1 stored-shape + full-policy authorization | **CLOSED** — policy-v1 + stored validator มาก่อน matcherจริง |
| M5.1 exact `relevant_sources` | **CLOSED** — duplicate/missing/extra ถูกปฏิเสธ |
| M1.1/M5.2 corpus + benchmark validation | **PARTIAL** — happy/malformed-entry cases ปิดแล้ว; เหลือ M1.2/M1.3 |
| M3.1 relevant IDs + dense-rank guards | **CLOSED** ตาม semantics ในโค้ด; แก้ claim N1 |
| M4 unauthorized sentinel integration | **DEFERRED CORRECTLY — mandatory Slice 2 hard gate** |

## Acceptance / Slice 2 scope

ตัวเลข acceptance ที่เพิ่มใน `KB_P2_PLAN.md` ถูกล็อกก่อน model run แล้วและไม่ต้องเปลี่ยนในรอบนี้:

- primary = mean paired `nDCG@5`; MRR/Hit/Recall/doc-level เป็น secondary
- dev sweep `N={10,20,30,50}`; CandidateRecall point+doc `>=0.95`, CandidateHit ทุก case `=1.00`
- rerank eligible เมื่อ delta `>=+0.02` และ paired-bootstrap 95% CI lower `>=0`; test ranking อย่างน้อย 50 cases
- rerank p95 `<=1500 ms`, total p95 `<=2500 ms`, RRF p95 `<=10 ms` บน hardware ที่บันทึก
- benchmark-valid แยกจาก arm-eligible

เมื่อ M1.2/M1.3 ผ่าน ให้ **GO Slice 2 เฉพาะ isolated/local/synthetic** ตาม scope นี้:

1. pinned local `bge-reranker-v2-m3` container/model/tokenizer
2. candidate provider รับ trusted `EffectiveAccess` และใช้ compiled filter เดียวกับ API
3. human-reviewed frozen synthetic corpus + hard negatives + dev/test split + N sweep
4. durable evidence ผูก eval/corpus hashes และ actual `retrieval_index_manifest_sha256`
5. P5b canary ผ่านทุก arm
6. **M4 sentinel integration:** unauthorized semantic twin อยู่จริงใน isolated Qdrant; independent scroll oracle ตรวจ set; spy adapter ต้องพิสูจน์ว่า point ID และ text นอกสิทธิ์ไม่ถึง cross-encoderจริง

ยังคง **NO-GO production/deploy/cloud/real company data**

## Independent verification

Codex rerun ผลจาก commit `0a9136c` ได้จริง:

- `test_p2.py` — **106/106 PASS**
- `test_policy.py` — **69/69 PASS**
- `test_p5b_fixtures.py` — **11/11 PASS**
- `test_eval_contract.py` — **64/64 PASS**
- `test_ask_eval_harness.py` — **12/12 PASS**
- `test_auth.py` — **11/11 PASS** (`app.main` heavy-dependency subcheck ยัง SKIP เพราะไม่มี `anthropic`, ตรงกับ suite behavior เดิม)

สอง edge ด้านบนเป็น probe เพิ่มนอก suite และ reproduce ได้จริง ไม่ได้แก้โค้ดหรือ `STATUS.md`

## Final verdict

**FIX-THEN-GO:** ปิด M1.2 non-dict short-circuit และ M1.3 surrogate/canonical-hash boundary พร้อม regression แล้วส่ง targeted confirm อีกครั้ง หลังจากนั้นจึง GO Slice 2; จุดอื่นในรอบนี้ไม่ต้อง review ซ้ำ
