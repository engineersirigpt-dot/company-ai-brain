# Codex Review — P2 RunPlan before pinned Docker/model run

**Commit reviewed:** `825a3b7`  
**Input:** `KB_P2_RUNPLAN_HANDOFF.md`  
**Verdict:** **FIX-THEN-GO**

โครง pure/offline เดินถูกทาง และของเดิมที่สำคัญปิดแล้วจริงสองส่วน: `run_ranking` เป็น zero-skip และ timestamp ใช้ semantic parser แล้ว แต่ RunPlan ปัจจุบันยังไม่ใช่ fail-closed decision contract ตามที่ handoff อ้าง เพราะ input ที่ไม่ได้ preregister/ผลที่ประเมินไม่ครบยังนำไปเลือก N หรือเลือก arm ได้ จึงยังไม่ควรเลือก pin แล้วสร้าง Docker image ภายใต้สถานะว่า “RunPlan พร้อมรัน”

ทางแก้ที่เล็กที่สุดคือไม่เพิ่ม decision helper แยกหลายชั้น แต่สร้าง **validated root run manifest + decision entry point เดียว** แล้วให้ N-sweep, quality, latency, M4 และ canary อ้าง `run_manifest_sha256` เดียวกัน

## Findings

### B1 — `select_n()` ไม่ได้บังคับ preregistered N set หรือ dev-only evidence

**ตำแหน่ง:** `p2_runplan.py:77-88`

ฟังก์ชันวนตาม key ที่ caller ส่งมาโดยตรง จึงรับทั้ง N นอก `{10,20,30,50}` และชุดผลที่ไม่ครบได้ อีกทั้งคำว่า “dev เท่านั้น” อยู่ใน docstring แต่ไม่มี split/run-manifest/count binding ในข้อมูลจริง

Codex probe:

```text
select_n({1: {point_recall:1, doc_recall:1, candidate_hit:1}})
-> {selected_n: 1, status: SELECTED}
```

ผลคือ runner สามารถเลือก N ที่ไม่เคย preregister หรือเลือกจาก test split แล้วรายงานว่า SELECTED ได้

**Required before Docker/model run:** รับ evidence object ที่ผูก `split="dev"` + `run_manifest_sha256`; บังคับ exact keys เท่ากับ `N_SET`; metric ทุกค่าต้องเป็น finite number ใน `[0,1]`; expected/completed query และ intent counts ต้องตรงก่อนเลือก N

### B2 — paired analysis ตัด intent ที่ไม่ครบออกเงียบ ๆ

**ตำแหน่ง:** `p2_runplan.py:92-114`

`paired_deltas()` ใช้ intersection ของ intent IDs และ `per_intent_ndcg()` ข้าม `None` จึงทำ CI จาก subset ที่เหลือได้ โดยไม่ตรวจ exact test-intent set จาก RunPlan ตัวอย่างที่มีสอง intents แต่ baseline ขาดหนึ่ง intentคืน delta เพียงหนึ่งค่าและไม่ fail

```text
expected intents = {i1, i2}
dense มีเฉพาะ i1; rerank มี i1+i2
paired_deltas(...) -> [0.3]
```

นี่เปิดทางให้ query/intent ที่โมเดลทำแย่หรือ pipeline พังหายจากผลสถิติ

**Required:** บังคับ exact intent-set equality ทุก arm, exact count เท่ากับ frozen test set, finite `nDCG@5` ทุก ranking case และห้าม duplicate/missing query IDs; `paired_bootstrap()` ต้องรับเฉพาะ finite deltas, exact configured seed/resamples และ reject invalid/partial evidence

### B3 — final arm decision ไม่ได้ gate latency, candidate generation หรือ evidence completeness

**ตำแหน่ง:** `p2_runplan.py:120-159`; `p2_eval.py:429-465`

`decide_arm()` ดูเฉพาะ delta/CI/hard-negative dict และ `hn_ok({})` เป็น True แบบ vacuous จึงเลือก rerank ได้เมื่อไม่มี hard-negative evidence ส่วน latency ถูกคำนวณแยกแต่ไม่ถูกใช้ตัดสิน arm เลย และ `decision_benchmark_manifest()` ไม่รับ RunPlan, N-selection, quality result หรือ latency result จึง approve manifest ได้โดยไม่พิสูจน์ acceptance เหล่านี้

Codex probe:

```text
decide_arm(good_delta, ..., hardneg_rerank={}, hardneg_fused={})
-> arm=rerank
```

**Required:** มี decision entry point เดียวที่ fail-closed หากข้อใดข้อหนึ่งไม่ครบ: selected N จาก dev, exact test counts/pairs, hard-negative categories ตาม frozen gate set, zero run errors/OOM, latency ของ arm ที่เลือกอยู่ใน budget, M4 PASS, canary PASS และ Data Owner sign-off ถ้ายังไม่ครบต้องคืน `NOT_DECISION_ELIGIBLE` ไม่ใช่ arm verdict

### B4 — root evidence binding ที่ review รอบก่อนขอยังปิดไม่ครบ

**ตำแหน่ง:** `p2_runplan.py:36-63`; `p2_eval.py:359-465`

RunPlan docstring ระบุ model artifact แต่ validator บังคับเพียง eval/corpus/index digest; model/tokenizer commit, model file-manifest, image digest, dependency/config และ raw-result digest ไม่ได้เป็น required fields ส่วน M4/canary cross-check กันเพียง `run_id` กับ index; canary ไม่มี model/image binding และทั้งคู่ไม่อ้าง `run_manifest_sha256`

ดังนั้น evidence จาก model/image คนละชุดยังประกอบกันได้ หาก callerใช้ run ID/index เดียวกัน

**Required:** root manifest ต้อง require และ hash อย่างน้อย contract version, split/counts, thresholds, N set, seed/resamples, eval/corpus/index, exact model+tokenizer commit, model file-manifest, image digest และ inference config; M4/canary/quality/latency/raw result ต้องอ้าง digest นี้และ validator ต้อง cross-check exact equality

### M1 — model pin ดีขึ้นมาก แต่ยังไม่ยืนยัน full resolved commit

**ตำแหน่ง:** `p2_reranker.py:15-27`, `p2_reranker.py:117-129`

allowlist, offline load และ actual metadata ปิดช่องเดิมส่วนใหญ่แล้ว แต่ regex ยังรับ abbreviated SHA 7 ตัว และ metadata บันทึกค่า `revision` จาก caller โดยไม่ assert full resolved snapshot commit จาก path/result ที่โหลดจริง ก่อน bake image ควรบังคับ full immutable commit (สำหรับ Hugging Face Git snapshot นี้คือ full 40-hex) แล้ว verify snapshot ที่โหลด resolve ตรงค่านั้น

### M2 — latency evidence ยังประกอบจากคนละจำนวน sample ได้

**ตำแหน่ง:** `p2_runplan.py:144-159`

แต่ละ stage ถูกสรุปแยกโดยไม่ตรวจ required stage set, warm-up range, จำนวน sample เท่ากัน หรือ expected count ตัวอย่าง `candidate/rerank/rrf/total` เหลือหลัง warm-up 1/2/3/4 samples แต่ `within_budget=True`

**Required:** exact stage set, finite non-negative samples, warm-up เป็น non-negative exact int และต้องเหลือ sample ตาม expected countเท่ากันทุก stage; bind error/OOM count = 0 และ raw latency evidence digest ก่อนถือว่า within budget

## Re-review ของ findings เดิม

| Finding เดิม | สถานะรอบนี้ |
|---|---|
| B1 model pin/metadata | **PARTIAL** — allowlist/offline/file manifest ปิดแล้ว; full resolved commit ยังขาด |
| B2 evidence binding | **PARTIAL** — exact counters/hash/run+index ดีขึ้น; root run-manifest/model-image/raw-result binding ยังขาด |
| B3 ranking zero-skip | **CLOSED** |
| M1 analysis contract | **PARTIAL** — pure functionsมีแล้ว แต่ validation/final gating/integration ยังไม่ fail-closed |
| M2 timestamp semantic validation | **CLOSED** |

## Independent verification

- `test_p2_runplan.py` — **24/24 PASS**
- `test_p2.py` — **170/170 PASS**
- targeted probes ยืนยัน B1, B2, B3 และ M2 ตามด้านบน
- provider/harness suitesไม่ได้ rerun อิสระใน host interpreter รอบนี้ เพราะ environment ไม่มี `qdrant_client`; ไม่ได้ตีความ environment failure เป็น code failure
- ไม่ได้เปิด Docker, Qdrant หรือโหลด model และไม่ได้แก้ code/`STATUS.md`

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| แก้ validation + root manifest + final decision gate และเพิ่ม negative tests | **GO NOW — pure/offline** |
| เลือก model commit / สร้าง `Dockerfile.p2` ภายใต้ run ที่จะใช้เป็น evidence | **FIX-THEN-GO หลัง B1-B4/M1** |
| เปิด container ทำ model-load smoke / real M4 / N sweep | **NO-GO จน targeted re-review ผ่าน** |
| arm/decision benchmark | **NO-GO จน Data Owner sign-off + validated M4/canary + complete decision bundle** |
| production/deploy/ข้อมูลจริง | **NO-GO ตาม external gates เดิม** |

## Acceptance สำหรับ targeted re-review รอบถัดไป

1. Negative tests ต้องพิสูจน์ว่า unknown/partial N set, test split, NaN/string metric, missing intent/arm และ empty hard-negative set ถูก reject
2. latency over budget หรือ sample/error countไม่ครบต้องทำให้ arm นั้นเลือกไม่ได้
3. root manifest ขาด model/image/config/raw-result binding ต้อง hash/approve ไม่ได้
4. full model commit ต้อง resolve ตรงกับ snapshot ที่ bake จริง
5. final decision entry point ต้องไม่มีเส้นทางคืน arm verdictจาก partial bundle

**Final verdict:** **FIX-THEN-GO.** กลไกคำนวณ happy path ถูก แต่ decision boundary ยัง fail-open ต่อ incomplete/unregistered evidence จึงควรปิด pure contract ให้เสร็จก่อนเสียเวลาสร้าง image และรันโมเดลจริง
