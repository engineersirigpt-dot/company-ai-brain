# Codex Targeted Re-review — P2 RunPlan fail-closed fix

**Commit reviewed:** `6aef5f9`  
**Input:** `KB_P2_RUNPLAN_FIX_HANDOFF.md`  
**Verdict:** **FIX-THEN-GO**

## Intent และทางแก้ที่เล็กที่สุด

เป้าหมายคือให้ผล `DECISION` เกิดได้จาก artifact, policy และ evidence ชุดเดียวที่ preregister ไว้เท่านั้น

ไม่ต้องรื้อ analysis functions หรือเพิ่ม orchestration อีกระบบ จุดแก้ที่เล็กที่สุดคือทำให้ `plan` เป็น authoritative จริง, เพิ่ม digest ของผลเลือก N และเหลือ public approval surface เพียง `decide_p2()` ตัวเดียว ปัจจุบันมี root hash แล้ว แต่ค่าบางส่วนที่อยู่ใน root ไม่ถูกนำไปเทียบหรือใช้ตัดสินจริง

## Findings

### B1 — Threshold และ hard-negative gate ที่ใช้ตัดสินไม่ได้มาจาก RunPlan ที่ hash ไว้

**ตำแหน่ง:** `p2_runplan.py:84-87`, `p2_runplan.py:367-411`

`validate_run_plan()` ตรวจเพียงว่ามี threshold keys ครบ แต่ไม่ตรวจชนิด/ช่วง และ `decide_p2()` ใช้ argument `thr=DEFAULT_THRESHOLDS` แทน `plan["thresholds"]` ดังนั้น caller เปลี่ยนเกณฑ์หลัง hash หรือค่าใน plan ไม่ถูกใช้จริงได้ `gate_tags` ก็ถูกส่งเป็น argument อิสระและไม่ได้อยู่ใน root plan

Codex probes:

```text
plan.thresholds.min_delta_ndcg = 0.99
decide_p2(...) -> DECISION, arm=rerank

gate_tags=[] + hardneg={invented: 0.0}
decide_p2(...) -> DECISION, arm=rerank
```

**ผลกระทบ:** RunPlan อ้างว่า preregister acceptance แล้ว แต่ decision ใช้เกณฑ์/หมวด gate คนละชุดได้ จึงยังเกิด post-hoc decision ได้

**แก้ขั้นต่ำ:** validate exact threshold schema + finite/range/relationship; freeze `gate_tags` และ evaluated roles ใน plan; เอา `thr`/`gate_tags` ออกจาก public decision signature แล้วใช้ค่าจาก plan เท่านั้น รวมถึง reject empty frozen gate set

### B2 — Root artifact digests ไม่ถูกเทียบกับ artifact/evidence จริง

**ตำแหน่ง:** `p2_runplan.py:90-112`, `p2_runplan.py:416-431`; fixture ที่ `test_p2_runplan.py:212-252`

root เก็บ eval/corpus/index digests, tokenizer commit, model file-manifest และ inference config แต่ decision path เทียบกับ evidence เพียง model revision และ image digest ส่วน `decision_benchmark_manifest()` ตรวจ eval/corpus เทียบกับ cases/corpus จริงโดยไม่เทียบกลับไปยัง root

existing happy-path test เป็นหลักฐานของช่องนี้เอง:

```text
BPLAN.artifact_digests.eval_set_sha256 != eval_set_sha256(CASES)
decide_p2(...) -> DECISION
```

กล่าวคือ evidence แค่รู้ค่า root hash ไม่ได้พิสูจน์ว่า artifact fields ใน root ตรงกับของที่รันจริง นอกจากนี้ tokenizer commit, model file-manifest และ inference config ยังไม่ถูก cross-check กับ runtime metadata

**แก้ขั้นต่ำ:** ก่อน analysis ให้เทียบ exact:

- root eval/corpus/index digests ↔ actual cases/corpus + M4/canary index
- root model/tokenizer commit, model file-manifest, image digest, inference config ↔ scorer/container metadata จริง
- M4/canary/quality/latency ต้องอ้างค่าหรือ digest ของ metadata ชุดเดียวกัน

เพิ่ม negative test ที่เปลี่ยน root field ทีละตัวโดยคง evidence จริงเดิมไว้ แล้วต้อง `NOT_DECISION_ELIGIBLE`

### B3 — ยังมี approval entry point ตัวที่สองซึ่ง bypass RunPlan/quality/latency ได้

**ตำแหน่ง:** `p2_eval.py:375-380`, `p2_eval.py:448-477`; `p2_eval.py:508`

`decision_benchmark_manifest(..., run_manifest_sha256=None)` ยังเป็น public function และ `_bind_run_manifest()` ตั้งใจข้าม binding เมื่อค่าเป็น `None` ฟังก์ชันนี้คืน `approved=True` โดยไม่รับ N selection, paired quality หรือ latency เลย ขณะที่ comment ของ `artifact_manifest_unapproved()` ยังบอกให้ใช้ฟังก์ชันนี้เป็น decision/freeze entry point

Codex probe:

```text
decision_benchmark_manifest(valid labels/signoff/M4/canary, run_manifest_sha256 omitted)
-> approved=True, run_manifest_sha256=None
```

**ผลกระทบ:** คำกล่าวว่า `decide_p2` เป็น entry point เดียวไม่เป็นจริง และ caller เดิมสามารถประกาศ approved โดยข้าม contract ใหม่ทั้งหมด

**แก้ขั้นต่ำ:** ให้ function นี้เป็น private validator/manifest builder ที่ไม่คืน `approved=True` เอง หรือบังคับ root + complete decision bundle โดยไม่มี default `None`; public path ที่คืน approval ต้องเหลือ `decide_p2()` ตัวเดียว และเพิ่ม regression test ว่าการเรียกตรงถูก reject

### B4 — ผล test run ยังไม่ bind กับ N ที่เลือก และ digest เป็น format-only

**ตำแหน่ง:** `p2_runplan.py:138-176`, `p2_runplan.py:193-230`, `p2_runplan.py:313-345`

หลัง dev เลือก N แล้ว `quality_evidence`/`latency_evidence` ไม่มี `selected_n` หรือ selection digest จึงใช้ผลจาก N อื่นได้ ตัวอย่างเปลี่ยน dev ให้เลือก N=50 แต่ใช้ quality fixture เดิมซึ่งไม่มี top-N metadata ระบบยังคืน `DECISION`

`raw_result_digest` และ `raw_latency_digest` ตรวจเพียงรูปแบบ 64-hex ไม่ได้ recompute เทียบ payload/raw artifact; เปลี่ยน digest เป็นค่า `f*64` ใด ๆ ก็ยัง `DECISION` ส่วน hard-negative delta เป็น dict แยกที่ไม่ได้ derive/bind กับ per-query evidence

**แก้ขั้นต่ำ:** สร้าง canonical `SelectionManifest` จาก root + dev-result digest + selected N แล้วให้ test quality/latency/M4/canary อ้าง selection digest นี้; recompute canonical digest จาก evidence body หรือ verify กับ durable raw artifact จริง; derive hard-negative category deltas จาก bound per-query rows แทนการรับ naked dict

### M1 — “exact N keys” ยังยอม extra non-int keys

**ตำแหน่ง:** `p2_runplan.py:143-146`

การตรวจ `{k for k in by_n if type(k) is int} == set(N_SET)` มองข้าม key เช่น `"10"` หรือ `False` จึงไม่ใช่ exact key set ตาม contract แม้ extra key ไม่ถูกใช้เลือก N แต่ทำให้ validator กับหลักฐานที่อ้างว่า exact ไม่ตรงกัน

**แก้ขั้นต่ำ:** ใช้ `set(by_n.keys()) == set(N_SET)` และยืนยัน `type(k) is int` ทุก key

## สถานะ findings เดิม

| Finding เดิม | Re-review |
|---|---|
| B1 N set/split/count/metric | **PARTIAL** — เส้นหลักปิด; extra non-int key และ selection binding ยังขาด |
| B2 paired completeness | **CLOSED ใน pure analysis** — intent/arm/count/finite checks ปิดแล้ว |
| B3 single fail-closed decision | **OPEN** — `decision_benchmark_manifest()` ยังเป็น bypass และ plan gatesไม่ authoritative |
| B4 root evidence binding | **OPEN** — มี root hash แต่ root artifact values ไม่ได้ cross-check ของจริง |
| M1 full model commit | **CLOSED ใน pure boundary** — full SHA + resolved snapshot assertionถูกทาง; runtime proofรอ containerตามแผน |
| M2 latency shape/count/error | **CLOSED ใน pure boundary** — เหลือ bind selected N/raw artifact ใน B4 |

## Independent verification

- `test_p2_runplan.py` — **72/72 PASS**
- `test_p2.py` — **178/178 PASS**
- targeted probes ยืนยัน B1–B4 และ M1 ตามข้อความด้านบน
- ไม่ได้เปิด Docker, Qdrant หรือโหลด model
- ไม่ได้แก้ code หรือ `STATUS.md`; ไฟล์ `tmp/` เดิมไม่ได้แตะ

## Acceptance สำหรับ targeted re-review ถัดไป

1. plan threshold `0.99` ต้องถูกใช้จริงและทำให้ rerankไม่ผ่าน; caller override threshold/gate tagsไม่ได้
2. root eval/corpus/index/model/tokenizer/file-manifest/image/config ไม่ตรง actual evidence แม้เพียง fieldเดียวต้องไม่ eligible
3. เรียก approval path โดยไม่ผ่าน `decide_p2` หรือไม่มี root/selection digest ต้องไม่ได้ `approved=True`
4. test quality/latency จาก N อื่น หรือ raw digest ที่ไม่ตรง canonical artifact ต้องถูก reject
5. empty/ลด gate tags หลัง preregistration และ extra `by_n` key ต้องถูก reject

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| ปิด B1-B4/M1 ด้วย pure code + negative tests | **GO NOW** |
| เลือก/freeze model commit และสร้าง `Dockerfile.p2` สำหรับ evidence run | **FIX-THEN-GO** |
| เปิด container / model-load smoke / real M4 / N sweep | **NO-GO จน targeted re-review ผ่าน** |
| decision benchmark จริง | **NO-GO จน Data Owner sign-off + validated M4/canary + bound complete bundle** |

**Final verdict:** **FIX-THEN-GO** — โครง root manifest และ validation ดีขึ้นมาก แต่ biggest reason ที่ยัง ship ไม่ได้คือค่าที่ hash ใน root ยังไม่ใช่ค่าบังคับจริง และ approval ยังมีเส้นทางข้าม `decide_p2`
