# Codex Review — P2 Slice 2 infrastructure

**Commits reviewed:** `30c09b5`, `b183fa5`  
**Input:** `KB_P2_SLICE2_INFRA_HANDOFF.md`  
**Verdict:** **FIX-THEN-GO Slice 2 real run** — เขียน cross-encoder adapter/harness แบบ offline ต่อได้ แต่ยังไม่ควรรัน model/N-sweep เพื่อสร้างหลักฐานตัดสินใจจนปิด B1–B3 และ M1

## Outcome ก่อน

ทิศทางหลักถูกต้อง: candidate set ถูกสร้างก่อน rerank, ใช้ compiled filter เส้นเดียวกับ API, dense/rerank/fused ใช้ universe เดียว และแยก mechanics smoke ออกจาก decision benchmark ชัดเจน

อย่างไรก็ตาม contract ปัจจุบันยังพิสูจน์ไม่ได้ว่า `EffectiveAccess` ที่เข้าถึง provider ผ่านการยืนยันจริง และยังไม่มี fail-closed postcondition ก่อนส่งข้อความให้ cross-encoder นอกจากนี้ entry point ที่สร้าง benchmark manifest ยัง bypass combined decision gate ได้ จึงยังไม่ควรเรียกผล Slice 2 ว่า M4/decision evidence แม้ real-model run จะเขียว

## Intent / ทางเล็กที่สุด

ไม่ต้องรื้อ P2 หรือสร้าง policy framework ใหม่ ให้ทำเพียง:

1. ล็อก access invariant ที่ขอบลึกสุดของ candidate provider
2. ตรวจ candidate payload หลัง Qdrant และเทียบ independent oracle ก่อนเรียก model
3. ทำ decision-manifest entry point เดียวที่ bypass Data Owner/M4/canary gate ไม่ได้
4. แล้วค่อยเปิด Docker เพื่อรัน real M4 + N sweep

สำหรับ human review ไม่จำเป็นต้องให้ Data Owner อ่าน JSON 132 records ดิบ ควรสร้าง review sheet 1 แถวต่อ intent โดยวาง paraphrases สองแบบ, relevant chunks, grades/rationales และ hard negatives ไว้ด้วยกัน จากนั้นผูกผลอนุมัติกับ final hashes

## Findings

### B1 — `EffectiveAccess` เป็น trusted แค่ชื่อ; forged และ unverified access ผ่าน provider ได้

**ตำแหน่ง:** `p2_provider.py:32-45`, `p2_provider.py:60-64`, `policy.py:103-121`

`build_candidates()` ตรวจเพียง `isinstance(access, EffectiveAccess)` แต่ dataclass นี้สร้างตรงได้ และ function ไม่ตรวจว่า principal verified หรือ effective role อยู่ใน server-controlled scope จริง ส่วน `resolve_and_build()` เรียก resolver ได้ถูกทาง แต่ใน `warn/off` resolver ตั้งใจคืน unverified access ซึ่ง provider ก็รับต่อทันที

Codex targeted probe ยืนยันทั้งสองเส้นทาง:

```text
forged EffectiveAccess(authenticated=False, mode=enforce, role=sales) -> accepted
resolve_and_build(unauthenticated principal, mode=warn, role=sales)    -> accepted
```

นี่ขัดกับ acceptance ของ P2/P5b ที่ evidence ต้องเป็น `AUTH_MODE=enforce` และ auth `VERIFIED` หาก harness ต่อเข้าฟังก์ชันนี้ผิดทาง ผล M4/quality จะดูเขียวทั้งที่ identity gate ไม่ได้ทำงาน

**Required change:** เพิ่ม invariant guard ที่ `build_candidates()` หรือสร้าง opaque/validated constructor อย่างเดียว โดยอย่างน้อยต้องบังคับ:

- `access.principal.verified is True`
- `access.effective_role` อยู่ใน `principal.allowed_roles`
- role อยู่ใน `KNOWN_ROLES`
- benchmark harness เรียก server-controlled principal path เท่านั้น และมี negative tests สำหรับ forged, warn, off, unauthenticated และ role mismatch

การมี public `resolve_and_build()` อย่างเดียวไม่พอ หาก `build_candidates()` ยังเป็น callable boundary ที่รับ object สร้างมือได้

### B2 — M4 pure test ใช้ matcher ฝั่งเดียวกันและ provider ไม่มี authorization postcondition

**ตำแหน่ง:** `p2_provider.py:43-57`, `test_p2_provider.py:33-38`, `test_p2_provider.py:49`, `rerank.py:50-55`

Fake Qdrant ใช้ `policy.matches_policy()` กับ spec เดียวกับ code under test และ inject `IDENTITY` แทน adapter จริง ดังนั้น test 13/13 พิสูจน์เพียงว่า “ถ้า fake บังคับ filter ถูก ผลที่ส่ง scorer ไม่มี sentinel” แต่ตรวจไม่พบกรณี adapter/backend คืน point นอกสิทธิ์

Codex targeted probe ใช้ client ที่คืน sales-only point ให้ qc request แล้ว `build_candidates()` ส่ง point นั้นออกเป็น candidate ได้ เพราะ function ไม่ตรวจ stored policy หรือ matched access หลัง query:

```text
backend_filter_bypass -> candidate point_id=S, rerank_text="H secret"
```

ใน `rerank.py` มี `assert_candidates_authorized()` อยู่แล้ว แต่ provider/handoff path ยังไม่เรียก

**Required change ก่อน real M4:** 

- validate ทุก returned payload ว่าเป็น policy-v1 ที่ shape ถูก (`payload_is_policy_v1` + `validate_stored_payload`)
- verify payload ตรง compiled access อีกครั้งและ **fail ทั้ง batch** หากพบ mismatch; ห้าม drop เงียบเพราะจะซ่อน policy drift
- real M4 harness ต้องสร้าง `authorized_ids` จาก independent raw-scroll/seed expectation ที่ไม่เรียก compiler/matcher ตัวเดียวกับ provider แล้วเรียก `assert_candidates_authorized()` **ก่อน** cross-encoder
- spy รอบ model/tokenizer จริงต้อง assert exact input pairs/texts และ sentinel IDs/text hashes ไม่ปรากฏ; การค้นคำว่า `SENTINEL` ใน text อย่างเดียวไม่พอพิสูจน์ ID

Qdrant filter ยังคงเป็นด่านหลักก่อน retrieval; postcondition นี้เป็น fail-closed detector ไม่ใช่การย้าย permission enforcement ไปไว้หลัง retrieval

### B3 — `benchmark_manifest()` ยัง bypass combined decision gate

**ตำแหน่ง:** `p2_eval.py:291-301`, `p2_eval.py:327-340`

`decision_benchmark_errors()` รวม structural + human labels + coverage + Data Owner sign-off ถูกทิศแล้ว แต่ `benchmark_manifest()` ยังเรียกแค่ `validate_benchmark()` จึงไม่ตรวจ arm eligibility, dev-role coverage หรือ Data Owner sign-off

เมื่อมีคนเปลี่ยน labels เป็น `human-reviewed` แล้ว caller เรียก `benchmark_manifest()` ตรง ๆ จะได้ artifact ที่ดูเหมือน frozen benchmark โดยยังไม่มี approved sign-off นี่คือ footgun เดิมในรูป entry point อีกตัว และมีโอกาสถูก harness Slice 2 ใช้เพราะชื่อ function ตรงกับงาน

**Required change:** มี entry point สำหรับ decision/freeze เพียงตัวเดียว เช่น `decision_benchmark_manifest(...)` ซึ่ง:

- เรียก combined gate และ fail ถ้ามี error
- รับ/บันทึก sign-off hashes
- ต่อ evidence IDs ของ real M4 และ P5b canary PASS
- แยกชื่อ manifest สำหรับ mechanics smoke ชัดเจน เช่น `artifact_manifest_unapproved` และห้ามใช้สร้าง arm verdict

เพิ่ม negative tests ว่า Data Owner sign-off หาย/hash ผิด, coverage ผิด หรือ M4/canary evidence หายแล้วสร้าง decision manifest ไม่ได้

### M1 — combined gate crash ได้เมื่อ sign-off non-empty แต่ artifacts malformed

**ตำแหน่ง:** `p2_eval.py:272-301`

`validate_signoff()` hash cases/corpus ทันทีเมื่อ signoff เป็น dict ที่ไม่ว่าง แม้ `validate_benchmark()` จะพบว่า artifacts ผิดแล้ว และ list concatenation ใน `decision_benchmark_errors()` ไม่ short-circuit

Codex probes:

```text
decision_benchmark_errors(cases=[], corpus=None, signoff={decision: approved})
  -> TypeError: 'NoneType' object is not iterable

decision_benchmark_errors(cases containing NaN, corpus={}, signoff={decision: approved})
  -> ValueError: Out of range float values are not JSON compliant
```

measurement gate ควรคืน controlled NO-GO ไม่ใช่ crash ซึ่งอาจถูก runner จับรวมเป็น infrastructure error หรือเขียน evidence ค้างครึ่งชุด

**Required change:** short-circuit เมื่อ structural validation ไม่ผ่าน หรือทำ `validate_signoff()` คืน error list สำหรับ hashing failure ทุกชนิด เพิ่ม tests สำหรับ non-dict/empty corpus, NaN, lone surrogate และ malformed sign-off field types

### M2 — Qdrant payload normalization ยังมี malformed-input edges ก่อน scorer

**ตำแหน่ง:** `p2_provider.py:20-29`, `p2_provider.py:47-56`

- `heading`/`text` ที่ truthy แต่ไม่ใช่ string ทำ `.strip()` แล้ว crash
- `source=None` ถูกแปลงเป็น string `"None"` และผ่าน candidate validation
- `max_chars` ไม่ตรวจ positive exact int
- `top_n` ไม่มี upper bound แม้ acceptance sweep ล็อกไว้ที่ `{10,20,30,50}`

สำหรับ frozen synthetic corpus ปัจจุบันไม่ชน แต่ real integration boundary ควร reject shape แบบ controlled ก่อน model ไม่ควร coerce ค่าผิดให้ดู valid

**Required change:** strict type validation ของ payload fields, positive `max_chars`, และ internal cap/allowlist ของ `top_n` (อย่างน้อย harness ต้องรับเฉพาะ sweep ที่ lock) พร้อม negative tests

## Eval-set re-review

| Finding เดิม | ผลรอบนี้ |
|---|---|
| B2.1 dev role coverage | **CLOSED structurally** — 2 dev intents ต่อ 8 evaluated roles; test ยังมี 50 independent intents |
| B4.1 q-0013 | **CLOSED** — เปลี่ยนเป็นถามสิ่งที่ answer chunk ระบุจริง |
| B5.1 graded content/rationale | **CLOSED for pre-signoff structure/content** — primary มีคำตอบจริงและ rationale ผูกกับแต่ละ pid; ความถูกต้องทางธุรกิจยังต้อง Data Owner ยืนยัน |
| B6.1 human review | **OPEN BY DESIGN** — artifacts ยัง `ai-reviewed`; combined validator มีแล้วแต่ B3/M1 ต้องปิดก่อนใช้เป็น final decision boundary |

ไม่มีเหตุให้ block การทำ review sheet หรือให้ Data Owner เริ่มตรวจ content หลังปิด B3/M1 แต่ AI ห้ามเปลี่ยน `label_status`, `reviewed_by`, `review_revision` หรือสร้าง approved sign-off แทนมนุษย์

## Independent verification

- `test_p2.py` — **140/140 PASS**
- `test_p2_provider.py` — **13/13 PASS** ของ pure logic โดย inject stub เฉพาะ `qdrant_client.models`; host Python ปัจจุบันไม่มี package `qdrant-client` จึงยังไม่ได้ยืนยัน adapter จริง
- `p2_build_eval_set.py` — corpus **166**, cases **132** (dev 32/test 100), dev intents **16**, test intents **50**, corpus valid, non-label structural errors **0**, arm eligibility PASS, dev-role coverage PASS
- decision benchmark BLOCKED ถูกต้องบน artifacts ปัจจุบัน: **133 errors** (132 labels + sign-off)
- hashes ที่ rerun: eval `e9ca542e86ef18df...`, corpus `f844199b9ffb0dbc...`
- targeted access/postcondition probes ยืนยัน B1/B2 ตามข้อความด้านบน
- targeted malformed-artifact probes ยืนยัน M1

ไม่มี Docker/model/Qdrant จริงถูกเปิดหรือแตะในการ review รอบนี้ และไม่ได้แก้ code/`STATUS.md`

## Go / No-Go

| งานถัดไป | Verdict |
|---|---|
| เขียน cross-encoder adapter + benchmark harness แบบ pure/offline และ unit tests | **GO** — แต่ wire ผ่าน access/postcondition/decision entry points ที่แก้แล้ว |
| แก้ B1–B3/M1/M2 | **GO NOW** |
| เปิด Docker/build pinned model/seed isolated collection | **FIX-THEN-GO** — ทำหลัง B1–B3/M1; M2 ควรปิดในรอบเดียว |
| real M4 + P5b canary ทุก arm | **MANDATORY GATE** |
| exploratory mechanics smoke บน dev ก่อน human sign-off | **GO หลัง fixes** — ต้องติดป้าย `UNAPPROVED / NON-DECISION` |
| N selection, freeze, arm verdict, hardware/business decision | **NO-GO** จน Data Owner sign-off hash ตรง + real M4 PASS + P5b canary leak=0 ทุก arm |
| production/deploy/cloud/ข้อมูลบริษัทจริง | **NO-GO** ตาม gates เดิม |

## Final verdict

**FIX-THEN-GO Slice 2 real run.** โครง candidate provider และ eval-set เดินมาถูกทาง แต่ให้ปิด trust invariant, authorization postcondition และ single decision-manifest boundary ก่อนเปิด Docker เพื่อให้รอบ model จริงสร้างหลักฐานที่เชื่อถือได้ ไม่ใช่เพียงผลคะแนนที่รันสำเร็จ
