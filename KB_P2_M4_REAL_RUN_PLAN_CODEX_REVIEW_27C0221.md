# Codex Review — P2 M4 real-run plan + validator v2

**Commit reviewed:** `27c0221`  
**Input:** `KB_P2_M4_REAL_RUN_PLAN.md` และ implementation ใน `p2_eval.py`/tests  
**Verdict:** **FIX-THEN-GO harness** — ทิศทาง M4a/M4b ถูก แต่ยังไม่ควรล็อก schema หรือเขียน runner จนปิด B1–B3 และ M1–M3 ด้านล่าง  
**ขอบเขต:** review/pure tests เท่านั้น; ไม่รัน Docker, Qdrant หรือ model และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

เป้าหมายของ M4 คือพิสูจน์ว่า permission filter เป็นด่านที่มีผลจริง: point ที่ห้ามเห็นต้องมีโอกาสถูกค้นเจอหากไม่มี filter แต่ต้องไม่ผ่าน provider และไม่ถึง real cross-encoder ขณะที่ authorized point ต้องถึง model จริง

ไม่จำเป็นต้องสร้าง service/process ใหม่หลายชั้น ทางที่เล็กที่สุดคือ runner เฉพาะ `p2_m4` หนึ่งตัวใน pinned image เดิม โดย reuse:

```text
manifest oracle (independent) ──┐
                               ├─ compare → evidence per case
resolve_effective_access → p2_provider → spy → PinnedCrossEncoder
```

แยก M4 security proof ออกจาก `p2_harness.py` ซึ่งเป็น quality/metrics harness จะทำให้ failure semantics และหลักฐานอ่านง่ายกว่า และลดโอกาสเอา mechanics result ไปปนกับ N-sweep

## Trace ที่ยืนยันแล้ว

เส้นทาง production-like ที่มีอยู่จริงคือ `p2_provider.resolve_and_build()` → `policy.resolve_effective_access()` → `build_candidates()` → `query_points(..., query_filter=..., limit=top_n)` → validate payload + `matches_policy()` postcondition (`p2_provider.py:57-99`) จากนั้น ranking path เรียก scorer ผ่าน `p2_harness.rank_arms()` → `rerank_order()` (`p2_harness.py:19-24`)

ดังนั้น spy ต้องครอบ `PinnedCrossEncoder.score()` ที่ seam หลัง `build_candidates()` โดยตรง และ independent oracle ต้องเป็น branch แยกที่ไม่เรียก compiler/matcher/provider ตามที่ plan ระบุ

## Findings

### B1 — ยังไม่มี negative control ว่า sentinel “จะถูกค้นเจอ” หากไม่มี filter

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md:26-34,64-71`

plan กำหนด semantic twin และ direct scroll แต่ scroll พิสูจน์เพียงว่า point อยู่ใน collection ไม่ได้พิสูจน์ว่ามันติด candidate top-N สำหรับ query เดียวกัน หาก sentinel มี score ต่ำจนไม่ติด top 50 ผล `leak=0` จะเขียวแม้เอา permission filter ออกทั้งหมด

**ต้องแก้:** independent control ต้องยิง **raw unfiltered Qdrant query** ด้วย query vector และ N เดียวกับ case โดยไม่ผ่าน provider/compiler/matcher แล้วบังคับว่า sentinel ของทุก required category ปรากฏใน unfiltered top-N (ควรจัด deterministic vectors ให้ sentinel rank สูงกว่า authorized positive) จากนั้นจึงยิง filtered provider และพิสูจน์ว่า sentinel หายไปก่อน model

evidence ต่อ case ต้องเก็บ hash ของ ordered unfiltered IDs + ranks และ sentinel expected/observed rank; direct scroll ใช้ตรวจ inventory/manifest แยกต่างหาก

### B2 — hash sets แยก ID กับ text ทำให้ความสัมพันธ์ point↔text และ permutation พิสูจน์ไม่ได้

**ตำแหน่ง:** `p2_eval.py:398-466`, `KB_P2_M4_REAL_RUN_PLAN.md:84-91`

validator แปลงรายการ ID และ text เป็นคนละ `set` แล้วตรวจ subset แยกกัน จึงยอมรับกรณี ID ของ point A ถูกจับคู่กับ text ของ point B ตราบใดที่ทั้งสองค่าอยู่ใน authorized sets อีกทั้ง set ทำลายลำดับและจำนวนซ้ำ จึงไม่สามารถพิสูจน์คำกล่าว “rerank output เป็น permutation” หรือว่า spy เห็นคู่ input เดิมจริง

**ต้องแก้:** ใช้ ordered records ต่อ candidate เช่น `{point_id_sha256, rerank_text_sha256}` แล้วสร้าง canonical `pair_sha256`; เปรียบเทียบ ordered/multiset pair digests ระหว่าง oracle → provider → model input → rerank output อย่าใช้ ID/text sets แยกเป็นหลักฐาน authoritative ส่วน aggregate sets เก็บไว้เพื่อรายงานได้แต่ไม่ใช่ gate

### B3 — B1 vacuous-pass ปิดเพียงบางส่วน; validator ยังไม่พิสูจน์ finite score และ zero-skip

**ตำแหน่ง:** `p2_eval.py:421-475`, `test_p2.py:289-324`, `KB_P2_M4_REAL_RUN_PLAN.md:64-71`

`model_invocation_count > 0` และ model-input list ไม่ว่างช่วยกัน empty pass แล้ว แต่ count ไม่ผูกกับ input/output ใด ๆ; fixture เองใช้ invocation count `3` กับ model input เพียง `1` รายการ (`test_p2.py:291-297`) และ evidence ไม่มี `score_count`, `all_scores_finite`, expected/completed case count หรือ per-case status ทั้งที่ acceptance อ้าง finite scores + zero-skip

**ต้องแก้:** เพิ่มและตรวจ exact type/relationship อย่างน้อย:

- `expected_case_count == completed_case_count > 0` และ case IDs ตรง frozen manifest แบบ exact set;
- `model_call_count > 0`, `model_input_count > 0`, `score_count == model_input_count`;
- `all_scores_finite is True` โดย runner derive จาก score จริง;
- ทุก case มี terminal `PASS` และ error/skip count เป็น exact 0;
- positive control ระบุ scorer metadata `kind=pinned-cross-encoder` และ pin/manifest/image ตรง build receipt

ค่าพวกนี้ต้องถูกสร้างจาก trace/scorer โดย runner ไม่รับเป็น boolean/count ที่ callerกรอกเอง

### M1 — network contract ขัดกันเอง และการ reject `:6333` จะปฏิเสธ Qdrant ที่ isolated ถูกต้อง

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md:17-20,46-48`

container ที่ใช้ `--network none` ติดต่อ Qdrant ไม่ได้ ขณะเดียวกัน Qdrant บน private Docker network ปกติฟัง `qdrant:6333`; การ reject port 6333 จึงชนกับ architecture ที่ plan เลือก

**ต้องแก้:** ใช้ pinned runner/model image หนึ่ง container กับ Qdrant บน Docker network ที่ตั้ง `internal: true` และไม่ publish Qdrant port ออก host; runner เชื่อม `qdrant:6333` ภายในได้แต่ไม่มี internet egress ส่วน `--network none` ใช้เฉพาะ model-load smoke ที่ผ่านแล้ว ไม่ใช้กับ M4

interlock ให้ allow exact compose project/run ID, internal network ID, fresh volume, collection UUID/name และ synthetic marker; reject known production endpoint/collection เป็น defense เพิ่มเติม แต่อย่าใช้เลข port เป็น trust signal

### M2 — independent oracle ยังไม่กำหนดแหล่ง “expected authorization” ที่เป็นอิสระ

**ตำแหน่ง:** `KB_P2_M4_REAL_RUN_PLAN.md:22-34`

direct scroll คืน payload ทั้งหมด แต่ plan ยังไม่ระบุว่า oracle ตัดสิน authorized ต่อ role จากอะไร หาก harness ไปตีความ `allowed_roles`/policy fields เอง มีโอกาสทำซ้ำ bug เดียวกับ compiler/matcher และเรียกผลนั้นว่า independent

**ต้องแก้:** frozen seed manifest ต้องประกาศ `expected_visible_roles` หรือ expected visibility matrix ต่อ point/case โดยคนเขียน fixture กำหนดตรง ๆ Oracle ใช้ matrix นี้ + point/text pair hashes เท่านั้น ไม่ reimplement policy semantics จาก Qdrant payload จากนั้น direct scroll มีหน้าที่ตรวจว่า collection ตรง manifest และ unfiltered query มีหน้าที่พิสูจน์ sentinel competitiveness

### M3 — stage และ durable-evidence invariants ใน plan ยังไม่ถูก validator บังคับครบ

**ตำแหน่ง:** `p2_eval.py:404-475`, `test_p2.py:321-324`, `KB_P2_M4_REAL_RUN_PLAN.md:60-62,77-95,98-100`

ส่วนที่ปิดแล้วจริง: decision path บังคับ `selected-n` ผ่าน `decision_evidence_errors()` และ test ยืนยัน M4a เข้า final decision ไม่ได้

ส่วนที่ยังเปิด: standalone preflight validator ยอม `decision_eligible=true`, ยอมมี `selection_digest`, และไม่บังคับ `run_id`, `retrieval_index_manifest_sha256`, raw evidence digest/path หรือ exact pin equality ปัจจุบัน test เปลี่ยนเพียง `evidence_stage` แล้วคาดว่า valid (`test_p2.py:324`)

**ต้องแก้:** stage-specific contract:

- `preflight-n50`: `decision_eligible is False`, `selected_n == 50`, ไม่มี `selection_digest`;
- `selected-n`: `selected_n ∈ N_SET`, มี valid `selection_digest` และเมื่ออยู่ decision path ต้องตรง root/dev result;
- ทั้งคู่บังคับ non-blank `run_id`, SHA-256 ของ retrieval index, `raw_evidence_sha256`, evidence schema/version และ durable path/reference;
- validator สำหรับ run ต้องรับ expected pin/image/index จาก frozen run request แล้วเปรียบเทียบ exact ไม่ใช่ตรวจแค่รูปแบบ 40/64-hex

## สิ่งที่ผ่านแล้ว

- แนวคิดแยก **M4a preflight N=50** กับ **M4b selected-N** ถูกทาง และ final `decide_p2()` ไม่รับ M4a
- trusted access + Qdrant filter-before-retrieval + postcondition มี seam ที่นำมาทำ real M4 ได้โดยไม่ต้องเปลี่ยน provider
- synthetic-only, no raw text/secret, fail-closed error, negative controls และ teardown-before-delete เป็น requirement ที่เหมาะสม
- pure suites ที่รันยืนยันใน review นี้: `test_p2.py` **188/188**, `test_p2_runplan.py` **95/95**

ผลเทสต์ยืนยัน behavior ที่เขียนอยู่ แต่ไม่ปิด findings ข้างต้นเพราะเป็น contract/coverage ที่ suite ยังไม่ได้กำหนด

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| แก้ plan + M4Evidence schema/tests ตาม B1–B3/M1–M3 | **GO NOW** — pure/offline |
| เขียน seed/oracle/spy/runner harness | **FIX-THEN-GO** หลัง schema รอบนี้ผ่าน เพื่อไม่เขียนทิ้ง |
| รัน M4a บน isolated Qdrant | **NO-GO** จน harness ได้ review + negative controls ผ่าน |
| N-sweep | **NO-GO** จน M4a PASS |
| M4b/final decision | **NO-GO** จน selected N + Data Owner sign-off + validated canary/evidence ครบ |
| ใช้ CPU latency ตัดสิน GPU/production | **NO-GO** |

## Final verdict

**FIX-THEN-GO harness.** โครง M4a/M4b ถูก แต่ blocker ใหญ่สุดคือยังไม่มี unfiltered relevance control จึงอาจประกาศ permission proof ทั้งที่ sentinel ไม่เคยมีโอกาสติด candidate อยู่แล้ว ปิด control นี้พร้อม pair-bound/per-case evidence ก่อน แล้วค่อยสร้าง pure/injectable harness จะลดงานแก้ซ้ำที่สุด
