# Codex Targeted Confirm — P2 Slice 1 fix round 3

**Commit reviewed:** `45ea2f8`  
**Input:** `KB_P2_SLICE1_FIX3_HANDOFF.md`  
**Scope:** M1.2 / M1.3 / N1 เท่านั้น  
**Verdict:** **GO Slice 2 infrastructure — Slice 1 pure/offline CLOSED**

## Trace และผลยืนยัน

### M1.2 — CLOSED

`validate_ranking_eval_set()` ตรวจ `corpus` เป็น dict ก่อนเข้าลูปและก่อน `corpus.get()` (`p2_eval.py:69-72`) จึงไม่มีเส้นทาง non-dict corpus ไปจบที่ `AttributeError` แล้ว

Codex rerun ยืนยัน `None`, list และ string คืน controlled validation error และ `benchmark_manifest()` แปลงผล invalid เป็น `ValueError`

### M1.3 — CLOSED

- `_bad_str()` ปฏิเสธ Unicode `Cc` และ `Cs` (`p2_eval.py:45-49`)
- eval/corpus manifest ใช้ `_canonical_json()` ชุดเดียว: sorted keys, ASCII escaping, no NaN/Inf และ compact separators (`p2_eval.py:52-55`, `p2_eval.py:183-201`)
- text hash แปลง `UnicodeEncodeError` เป็น controlled `ValueError`

Codex rerun ยืนยัน surrogate ใน query/rerank text ถูก reject, NaN ถูก reject และ Thai/emoji hash ได้แบบ deterministic

### N1 — disposition ถูกต้อง

คง metric helper ให้คืน `None`/`0` เมื่อ relevance ว่างได้ แต่ ranking benchmark ปฏิเสธ relevance ว่างที่ `validate_benchmark()` boundary การแก้เอกสารให้ตรงกับ behavior นี้ถูกต้องและไม่ต้องเปลี่ยน helper

## Independent verification

- `test_p2.py` — **116/116 PASS**
- `test_policy.py` — **69/69 PASS**
- `test_p5b_fixtures.py` — **11/11 PASS**
- `test_eval_contract.py` — **64/64 PASS**
- `test_ask_eval_harness.py` — **12/12 PASS**
- `test_auth.py` — **11/11 PASS**

## Go boundary

ไฟเขียวให้เริ่ม Slice 2 เฉพาะ **isolated/local/synthetic infrastructure**:

1. pinned local `bge-reranker-v2-m3` container/model/tokenizer
2. candidate provider รับ trusted `EffectiveAccess` และ compiled filter เดียวกับ API
3. durable eval/corpus/index manifests
4. P5b canaries ทุก arm
5. M4 sentinel จริงใน isolated Qdrant โดย independent oracle และ spy scorer ต้องยืนยันว่า unauthorized point ID/text ไม่ถึง cross-encoder

ไฟเขียวนี้ไม่เท่ากับอนุมัติ eval labels หรือผล benchmark การ freeze/publish arm verdict ยังต้องผ่าน `KB_P2_EVALSET_CODEX_REVIEW_241DA10.md` และ human sign-off ก่อน

ยังคง **NO-GO production/deploy/cloud/real company data**

## Final verdict

**SHIP Slice 1 / GO Slice 2 infrastructure.** M1.2 และ M1.3 ปิดครบตามเส้นทางจริง ไม่ต้อง review findings เก่าซ้ำ
