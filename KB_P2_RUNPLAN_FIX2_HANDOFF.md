# P2 — ปิด Codex re-review B1/B2/B3/B4/M1 (authoritative plan + single approval) → re-review

> **สืบเนื่อง:** `KB_P2_RUNPLAN_FIX_CODEX_REREVIEW_6AEF5F9.md` (FIX-THEN-GO — ค่าที่ hash ยังไม่ authoritative + approval มีเส้นทางข้าม decide_p2)
> **ทั้งหมด pure/offline** — ไม่แตะ Docker/model/Qdrant · ยังไม่เลือก model commit · ยังไม่สร้าง Dockerfile.p2
> **decision benchmark ยัง NO-GO** จน Data Owner sign-off + validated real M4/canary (external gates เดิม)

## แนวทาง (ตามที่ review แนะนำ — จุดแก้ที่เล็กที่สุด)
ทำให้ `plan` เป็น **authoritative จริง**, เพิ่ม **SelectionManifest digest** ของผลเลือก N,
recompute raw digest จาก body จริง, และเหลือ **public approval surface เดียว = `decide_p2()`**

## Finding → fix → proof

| # | Finding (re-review) | Fix | proof (negative test) |
|---|---|---|---|
| **B1** | threshold/gate ที่ตัดสินไม่ได้มาจาก RunPlan ที่ hash (`decide_p2` ใช้ `DEFAULT_THRESHOLDS`/`gate_tags` เป็น argument) | `thresholds` schema+range validated ; **`gate_tags`+`evaluated_roles` ย้ายเข้า plan (ถูก hash)** ; `decide_p2` อ่าน `plan["thresholds"]/["gate_tags"]/["evaluated_roles"]` เท่านั้น (ลบ `thr`/`gate_tags` ออกจาก signature) ; reject empty gate set | `plan.min_delta_ndcg=0.99` → rerank ไม่ผ่าน → **arm=dense** (ค่าจาก plan ถูกใช้จริง) · gate_tags=[] → plan invalid · threshold ผิด schema/range → invalid |
| **B2** | root digests ไม่ถูกเทียบ artifact/evidence จริง (happy-path เดิม `artifact_digests=_H` ก็ยัง DECISION) | ก่อน analysis `decide_p2` เทียบ **exact**: `eval_set_sha256(cases)/corpus_manifest_sha256(corpus)` ↔ root ; `m4` model_revision/tokenizer_revision/**model_file_manifest**/image_digest/**inference_config**/index ↔ root ; canary model/image/index ↔ root | เปลี่ยน root field เดียว (eval/corpus/model/file-manifest/config/index) โดยคง evidence จริง → **NOT_DECISION_ELIGIBLE** ทุกกรณี |
| **B3** | มี approval entry point ตัวสอง `decision_benchmark_manifest(..., run_manifest_sha256=None)` คืน `approved=True` ข้าม decide_p2 | **ลบ** `decision_benchmark_manifest` ทิ้ง → แทนด้วย `decision_evidence_errors(...)` (คืน **error list เท่านั้น**, `run_manifest_sha256` เป็น required ไม่มี default None) ; **เจ้าเดียวที่ stamp `approved=True` คือ `decide_p2`** | `not hasattr(E,"decision_benchmark_manifest")` · `decision_evidence_errors` คืน list เสมอ · เรียก approval นอก decide_p2 ไม่มีทางได้ approved |
| **B4** | quality/latency ไม่ผูก N ที่เลือก ; raw digest ตรวจแค่ format ; hard-neg เป็น naked dict | **`SelectionManifest digest`** = sha256(root + dev-result digest + selected N) — quality/latency/M4/canary ต้องอ้าง ; `raw_result_digest`/`raw_latency_digest`/dev digest ถูก **recompute จาก body** (safe, malformed→error ไม่ crash) ; **hard-neg deltas derive จาก per-query rows** (challenge_tags) ไม่รับ dict ลอย | quality selection_digest จาก N อื่น → not eligible · raw digest = `f*64` → reject · category ไม่มี evidence → reject · derive negation=+0.4/table-row=-0.1 ถูกต้อง |
| **M1** | `{k … type(k) is int} == N_SET` ยอม extra non-int key (`"10x"`, `False`) | `all(type(k) is int for k in by_n) and set(by_n) == set(N_SET)` (exact key set) | extra key `"10x"` → reject |

## ผลรัน (offline — `rfqv` python + `PYTHONIOENCODING=utf-8`)
```
test_p2         179/179   test_p2_runplan  80/80   provider 22/22   harness 21/21
policy 69 · eval 64 · ask_eval 12 · auth 11 · p5b 11
```

## ตรงกับ acceptance ของ re-review
1. plan threshold `0.99` ถูกใช้จริง → rerank ไม่ผ่าน (arm=dense) ; caller override threshold/gate ไม่ได้ (ไม่มีใน signature) ✔
2. root eval/corpus/index/model/tokenizer/file-manifest/image/config ไม่ตรง actual แม้ field เดียว → not eligible ✔
3. ไม่มี approval path ที่ให้ `approved=True` นอก `decide_p2` (ลบ builder เดิม, evidence gate คืน list) ✔
4. quality/latency จาก N อื่น หรือ raw digest ไม่ตรง canonical body → reject ✔
5. empty gate tags (preregistration) + extra `by_n` key → reject ✔

## หมายเหตุ scope
- ไม่แตะ Docker/model/Qdrant ; ไม่เลือก model commit ; ไม่สร้าง `Dockerfile.p2`
- `decide_p2` happy-path DECISION ใน test = **synthetic fixtures** (human-reviewed จำลอง + signoff จำลอง) พิสูจน์กลไกเท่านั้น — ไม่ใช่ decision จริง, AI ไม่ได้กรอก human sign-off และไม่เปลี่ยน label_status ของ eval-set จริง
- raw digest = recompute จาก evidence body (self-consistent) ; verify กับ durable external raw artifact จะทำตอน container run (นอก pure boundary)
- M1 loader resolved-commit assertion พิสูจน์เต็มตอนโหลด snapshot ใน container

## ขอ Codex re-review
1. B1–B4/M1 ปิดครบใน pure boundary ไหม (โดยเฉพาะ plan authoritative + SelectionManifest + single approval)
2. อนุมัติ **เลือก immutable model commit (full 40-hex) + สร้าง `Dockerfile.p2`/compose (pinned, ยังไม่รัน)** ได้ไหม
3. หลังนั้น → model-load smoke (assert resolved commit) → real M4 → N sweep (ผล UNAPPROVED จน sign-off/evidence ครบ)
