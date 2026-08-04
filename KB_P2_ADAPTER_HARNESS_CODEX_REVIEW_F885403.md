# Codex Review — P2 adapter + harness before Slice 2 real run

**Commits reviewed:** `85cc3cf`, `f885403`  
**Input:** `KB_P2_ADAPTER_HARNESS_HANDOFF.md`  
**Verdict:** **FIX-THEN-GO Docker/model run**

โครง scoring ถูกทิศและ unit-test mechanics ผ่าน แต่ยังไม่ควรเปิด Docker เพื่อสร้าง real M4/N-sweep evidence เพราะ loader ยังไม่ได้ pin จริง, evidence สองชุดยังประกอบข้าม run/index กันได้ และ harness เปลี่ยน retrieval failure เป็น skipped result ได้ นอกจากนี้ pure analysis contract สำหรับ N selection/paired bootstrap/latency ยังไม่ได้ implement แม้ทำได้โดยไม่ต้อง Docker

## Intent / ทางเล็กที่สุด

เป้าหมายคือรันโมเดลครั้งเดียวแล้วได้ evidence ที่ reproducible, ตรวจย้อนกลับได้ และห้ามกลายเป็น decision result หาก query/run ใดล้มเหลว

ทางเล็กที่สุดคือสร้าง root `BenchmarkRunManifest` หนึ่งตัวก่อนเปิด Docker ซึ่ง pin artifact/config ทั้งหมด แล้วให้ M4, canary และ quality results อ้าง digest ของ root เดียวกัน ไม่ควรส่ง string/hash อิสระหลายชุดให้ validator แล้วหวังว่า caller จะจับคู่ถูกเอง

ลำดับที่เล็กและปลอดภัย:

1. pin model snapshot + container/dependencies จริง
2. implement pure run plan/N-sweep/bootstrap/result-schema ให้ครบ
3. fail run เมื่อ query ใด error/skip
4. ค่อยเปิด Docker ทำ model-load smoke → real M4 → N sweep

## Findings

### B1 — `PinnedCrossEncoder` ยังไม่ pinned และ metadata สามารถรายงานโมเดลไม่ตรงกับที่โหลด

**ตำแหน่ง:** `p2_reranker.py:10`, `p2_reranker.py:56-78`

`load_pinned_cross_encoder()` default `revision="main"` ซึ่งเคลื่อนได้ และไม่ปฏิเสธ branch/tag ส่วน `tokenizer_revision` ใช้ `tokenizer.name_or_path` ซึ่งเป็นชื่อ/path ไม่ใช่ resolved commit นอกจากนี้ caller ส่ง `model_name` อื่นได้ แต่ `metadata()` รายงาน `BAAI/bge-reranker-v2-m3` จาก constant เสมอ

Codex probe:

```text
load_pinned_cross_encoder(model_name="evil/model", revision="main")
metadata = {
  model: "BAAI/bge-reranker-v2-m3",
  model_revision: "main",
  tokenizer_revision: "evil/model"
}
```

**ผลกระทบ:** image/run สองรอบอาจโหลด weights ต่างกันแต่ evidence ดูเหมือนโมเดลเดียวกัน หรือโหลด repo อื่นแล้วรายงานชื่อ BAAI ทำให้ N/quality/latency comparison ทำซ้ำไม่ได้

**Required change ก่อน build model container:**

- ปฏิเสธ `main`/branch/tag; รับ exact immutable Hugging Face commit SHA หรือ local snapshot digestเท่านั้น
- บังคับ model name exact `BAAI/bge-reranker-v2-m3` สำหรับ experiment นี้ หรือเก็บ actual `model_name` ที่ส่งเข้ามาและ validate allowlist
- resolve และ assert model/tokenizer commit เดียวกัน; `name_or_path` ห้ามใช้แทน revision
- bake/download snapshot ที่ pinned ระหว่าง build แล้ว runtime ใช้ `local_files_only=True`, `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`
- บันทึก actual model/tokenizer file-manifest SHA-256, dtype, torch/transformers versions, device, max length, batch size และ immutable image digest
- validate `max_length`/`batch_size` เป็น positive exact int; cast logits เป็น float32 ก่อนแปลง listเพื่อให้ตรง official inference recipe

วิธีใช้ `AutoTokenizer` + `AutoModelForSequenceClassification` กับ query/passage pairs และเรียงจาก logit สูงไปต่ำตรงกับ [BAAI model card ทางการ](https://huggingface.co/BAAI/bge-reranker-v2-m3) ดังนั้นไม่ต้องเปลี่ยน library stack; ต้องแก้ pinning/metadata เท่านั้น

### B2 — evidence validators ยังยอมค่าที่ไม่ใช่ hash/exact int และไม่ bind M4 กับ canary run เดียวกัน

**ตำแหน่ง:** `p2_eval.py:334-401`, `test_p2.py` ชุด B3.1

การตรวจ `status=PASS`, sentinel false, arm exact set ปิด truthy FAIL จากรอบก่อนแล้ว แต่ fields ที่เรียกว่า hash/revision ตรวจเพียง non-blank string และ zero ตรวจด้วย equality

Codex probe ยืนยัน:

```text
M4: unauthorized_in_model_inputs = 0.0,
    model_revision="main", image_digest="d", index="idx-A", run_id="run-A"
Canary: leak_count = 0.0, index="idx-B", run_id="run-B"

validate_m4_evidence(...)     -> []
validate_canary_evidence(...) -> []
```

`decision_benchmark_manifest()` ไม่ cross-check M4/canary `run_id` หรือ index digest จึงประกอบ M4 จาก corpus/index A กับ canaryจาก B ได้ และ schema ไม่มี sentinel ID/text hash sets หรือ raw evidence digestที่ handoff ระบุว่าจะพิสูจน์

**Required change:**

- ใช้ `type(x) is int and x == 0` สำหรับ counters
- validate SHA-256 เป็น 64 lowercase hex; image digestเป็น canonical `sha256:<64hex>`; model revisionเป็น immutable commit format
- เพิ่ม root `benchmark_run_id`/`run_manifest_sha256` เดียวกันให้ M4 และ canary; cross-check index/model/tokenizer/image/eval/corpus digestsตรงกัน
- evidence แต่ละชุดมี raw-evidence canonical digest/path + observed candidate/model-input ID hashes, unauthorized sentinel ID/text hashes และ exact set-disjoint assertion
- canary ระบุ per-arm ERROR/INCONCLUSIVE count = 0 และ query countที่คาด/รันจริงตรงกัน
- decision manifestเก็บ immutable/canonical copiesหรือ evidence digest references ไม่เก็บ arbitrary mutable dict เป็นหลักฐานหลัก

### B3 — retrieval/config failure ถูกนับเป็น `skipped` แล้ว `run_smoke()` ยังคืนผลสำเร็จรูปได้

**ตำแหน่ง:** `p2_harness.py:54-74`, `test_p2_harness.py:75-85`

`run_smoke()` catch `Exception` รอบ embed/provider/Qdrant แล้ว continue Test เองถือ out-of-scope role เป็น skip ที่ยอมรับได้ แต่ ranking benchmark ทุก case ควรมี verified principal ที่ถูกต้อง หาก auth/Qdrant/embedding/filter พัง นั่นคือ run failure ไม่ใช่ case ที่ข้ามได้

Codex probe เมื่อ Qdrant ล้มทุก query:

```text
n_queries=0, n_skipped=1, approved=False,
aggregate={dense:{}, rerank:{}, fused:{}}
```

แม้ output ติดป้าย unapproved แต่โครงนี้ไม่มี `status=FAIL` และง่ายต่อการถูกนำไปสร้าง summary/evidence ต่อโดยดูเพียงไฟล์ที่ถูกสร้างสำเร็จ

**Required change:**

- ranking/N-sweep path ต้อง zero-skip: exception ใด ๆ fail run และไม่ออก PASS evidence
- ถ้าต้องการ mechanics diagnostic ให้แยก `run_diagnostic(allow_errors=True)` ชัดเจนและ output `status=FAIL/INCOMPLETE` เมื่อ error>0
- จับเฉพาะ exception ที่คาดหมายเพื่อเพิ่ม contextแล้ว re-raise; ห้าม catch `Exception` แล้วเดินต่อ
- enforce expected query/intent counts และ `n_completed == n_expected`; empty aggregate เป็น failure
- permission-denial probes อยู่ canary suite แยก ไม่ปนกับ ranking quality cases

### M1 — “Slice 2 run” analysis contract ยังไม่ได้ implement; เปิด Docker ตอนนี้จะกลายเป็น manual/ad-hoc run

**ตำแหน่ง:** `p2_harness.py:18-74`; acceptance ที่ lock ใน `KB_P2_PLAN.md:54-80`

scaffold ปัจจุบันทำ dense/rerank/fused และ point metricsพื้นฐานได้ แต่ real-run requirements ที่ตกลงไว้ยังไม่มี:

- N sweep `{10,20,30,50}` และเลือก N บน **dev เท่านั้น**; test ต้อง untouchedจน freeze N
- point + document CandidateRecall@N, CandidateHit@N=1.00
- paired bootstrap 10,000 ครั้งด้วย fixed seedและ groupingตาม `intent_id` ไม่ใช่นับ paraphrasesเป็น independent samples
- ΔnDCG@5 acceptance/CI, fused-vs-rerank delta, hard-negative category regression
- latencyแยก candidate retrieval/rerank/RRF/total, warm-up exclusion, p50/p95 และ OOM/error=0
- raw candidate/ranking IDs, scores, per-query category/split/challenge tags, model-input hashes และ config/artifact manifests
- `run_smoke()` ยังไม่เรียก/แนบ `artifact_manifest_unapproved()` จึงไม่ bind outputกับ eval/corpus hashes

สิ่งเหล่านี้ส่วนใหญ่เป็น pure code/test และควร pre-register ก่อนเห็นผลโมเดล เพื่อลดทั้งการ rerunราคาแพงและ post-hoc decision

**Required change ก่อน Docker benchmark:** ทำ `RunPlan` immutable ที่ล็อก split, N set, seed, resamples, metrics/thresholds, expected counts และ artifact digests; implement/test sweep + bootstrap + latency/result schemaแบบ offline แล้วให้ real runnerเพียงเติม observationsจาก Qdrant/model

### M2 — timestamp validation ตรวจเพียง regex ไม่ตรวจว่าวันเวลามีอยู่จริง

**ตำแหน่ง:** `p2_eval.py:34`, `p2_eval.py:306-307`

`2026-99-99T99:99:99+99:99` ผ่าน `validate_signoff()` เพราะตรง regex แม้ไม่ใช่ timestampจริง

**Required change:** parse ด้วย `datetime.fromisoformat()` (`Z` normalizeเป็น `+00:00`) แล้วบังคับ `tzinfo/utcoffset` ไม่เป็น `None`; canonicalizeก่อน hash/commit

## Findings เดิม

| Finding | Re-review |
|---|---|
| B3.1 truthy FAIL evidence | **PARTIAL** — exact PASS flagsปิดแล้ว แต่ counters/hash/cross-run bindingยังไม่ปิด |
| B3.2 smoke labels | **CLOSED** — `ai-reviewed` สร้าง unapproved/non-decision manifestได้; decision defaultยัง human-only |
| M1.1 sign-off types | **PARTIAL** — primitive types/control chars/commit shapeดีขึ้น; timestamp semantic validationยังขาด |
| Adapter interface | **DIRECTION CORRECT** — official model supports Transformers pair scoring; immutable pin/metadataยัง block real run |
| Candidate universe/metrics mechanics | **CLOSED สำหรับ unit scaffold** — dense/rerank/fusedเป็น permutationsของ candidate setเดียวกัน |

## Independent verification

- `test_p2.py` — **161/161 PASS**
- `test_p2_provider.py` — **22/22 PASS**
- `test_p2_harness.py` — **15/15 PASS**
- targeted probes ยืนยัน B1/B2/B3/M2 ตามด้านบน
- ตรวจ model usage เทียบ [official BAAI model card](https://huggingface.co/BAAI/bge-reranker-v2-m3): Transformers pair-scoring pathถูกประเภท
- ไม่มี Docker/model/Qdrantจริงถูกเปิดหรือแตะในการ review และไม่ได้แก้ code/`STATUS.md`

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| แก้ model pin/metadata + evidence binding + fail-on-error | **GO NOW — pure/config work** |
| implement RunPlan/N sweep/bootstrap/result+latency schema offline | **GO NOW** |
| สร้าง Dockerfile/compose แยกสำหรับ P2 แบบ pinned โดยยังไม่รัน benchmark | **GO หลังเลือก immutable model commit** |
| เปิด container ทำ model-load compatibility smoke | **FIX-THEN-GO หลัง B1** |
| real M4 + canary + N sweep evidence run | **NO-GO จน B1–B3/M1 ปิดและ runnerครบ** |
| เลือก N/freeze/arm verdict/decision benchmark | **NO-GO จน Data Owner sign-off + validated real M4/canary PASS** |
| production/deploy/cloud/ข้อมูลบริษัทจริง | **NO-GO ตาม gatesเดิม** |

## Final verdict

**FIX-THEN-GO Docker/model run.** Adapter เรียกโมเดลถูกประเภท แต่ยังไม่ pin artifactจริงและ runnerสามารถสร้างไฟล์ผลลัพธ์จาก runที่ประเมินไม่ครบได้ การแก้ pure run contractให้เสร็จก่อนเปิด Dockerจะลดทั้งความเสี่ยงและค่า rerunมากที่สุด
