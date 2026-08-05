# Codex Targeted Re-review — P2 M4 harness fix round 3

**Commit reviewed:** `2be538a`
**Input:** `KB_P2_M4_HARNESS_FIX3_HANDOFF.md`
**Verdict:** **FIX-THEN-GO runner**
**Scope:** pure/offline review + targeted tamper probes; ไม่รัน Docker/Qdrant/model, ไม่เขียน runner และไม่แก้ `STATUS.md`

## Intent และทางที่เล็กที่สุด

เป้าหมายคือให้ receipt ยืนยัน run เดียวที่ใช้ pinned scorer, isolated resources, independent oracle และ per-case model tracesจริง ก่อนปลด M4a

โครง `run_m4_cases()` เป็น trusted producer path ที่เหมาะแล้ว ไม่ต้องพยายามทำ Python DTO ให้ต้าน process ที่จงใจปลอมทุกแบบ สิ่งที่ยังขาดคือให้ **receipt commit canonical security bundle ทั้งก้อน** และให้ proof bodiesบันทึกผลที่ runnerสังเกตจริง ไม่ใช่ hash เฉพาะ expected inputs

ทางเล็กที่สุด:

```text
runner observations
  → scorer proof + isolation observation + oracle observed body + per_case
  → canonical evidence_body_sha256 (ไม่รวม run_receipt_sha256)
  → receipt อ้าง evidence_body_sha256
  → evidence อ้าง receipt digest
  → public gate recompute ทั้งสองทิศ
```

วิธีนี้แก้ post-run proof swap โดยไม่ต้องเพิ่ม receipt fieldแยกทีละ proof และทำให้ atomic writerมี artifact rootเพียงตัวเดียว

## Findings

### B1 — receipt ยัง bind เฉพาะ `per_case`; Isolation/Oracle/scorer provenance เปลี่ยนหลังรันได้โดย receipt เดิมยังผ่าน

**ตำแหน่ง:** `p2_m4_harness.py:253-285`; `p2_eval.py:781-792,856-892`; `p2_runplan.py:501-513`

`raw_evidence_sha256` hash เฉพาะ `case_records` ส่วน receipt บันทึก hashนี้, marker, model revision, image และ index แต่ไม่ได้ commit full evidence top-level หรือ `isolation_proof_sha256`/`oracle_proof_sha256`/inference config/scorer proof

targeted tamper probe:

1. ใช้ bundleที่ผ่านเดิม;
2. เปลี่ยน project/network/volume/collection IDs ทั้งสี่ใน IsolationProof;
3. คง markerเดิมและ recompute `isolation_proof_sha256`;
4. ไม่แก้ receipt และไม่แก้ `run_receipt_sha256`

ผล:

```text
RECEIPT_UNCHANGED = True
validate_m4_preflight_bundle(...) = []
```

ดังนั้น receipt ยังไม่ใช่ durable receipt ของ proof bundle ตามข้อความในแผน สามารถสลับ resource identityหลังรันโดยไม่มี tamper signal

**ต้องแก้ก่อน runner:**

- เพิ่ม canonical `evidence_body_sha256` ที่ครอบ security-relevant top-level bodyทั้งหมด: run metadata, scorer proof/pins, isolation proof, oracle proof และ per-case records โดย exclude เฉพาะ `run_receipt_sha256` เพื่อไม่ให้วงกลม;
- receipt ต้องเก็บ `evidence_body_sha256`; public gate recompute evidence body → compare receipt → recompute receipt digest → compare evidence reference;
- จะคง `raw_evidence_sha256` เป็น per-case digestแยกก็ได้ แต่ห้ามใช้มันเป็น rootของทั้ง run;
- negative tests: เปลี่ยน isolation resource ID, oracle observation, scorer/inference metadata หรือ top-level verdictแล้ว recompute inner proof digest แต่ไม่ออก receiptใหม่ ต้อง failทุกกรณี

ถ้า exact schema v5 ยังถือว่า unreleasedเพราะยังไม่มี real artifact สามารถเติม fieldนี้ก่อน M4aแล้วคง v5; ถ้ามี durable v5 artifactออกแล้วต้อง bump schema/receipt versionใหม่

### B2 — `OracleProof` เป็น digest ของ expected manifest ไม่ได้มีผลจาก independent direct scroll

**ตำแหน่ง:** `p2_m4_harness.py:207-212`; `p2_eval.py:697-713`; `KB_P2_M4_REAL_RUN_PLAN.md:47-52,114`

signature ปัจจุบันคือ:

```text
build_oracle_proof(*, frozen, index_sha256)
```

function derive frozen manifest digest และ case setจาก `frozen` ชุดเดียวกับที่ validatorใช้ ไม่มี observed rows/pairs/payload hashes/collection inventoryจาก direct scrollเลย Public gateจึง compare expectedกับค่าที่ deriveจาก expectedเอง แล้วประกาศ `independent_oracle=PASS`

ข้อความใน handoff ว่า runnerจะ “เติม independent read” ทำไม่ได้บน schemaนี้ เพราะ `_M4_ORACLE_KEYS` เป็น exact setและไม่มี fieldรับ observed body

**ต้องแก้:**

- OracleProof ต้องรับ canonical hash-only observationจาก independent reader เช่น exact observed point/pair inventory, per-case visibility result และ collection/index identity;
- validator recompute observation digestและ compare observed case/pair setกับ frozen manifestแบบ exact;
- builderห้ามสร้าง PASS proofจาก `frozen` อย่างเดียว;
- negative controls: direct scroll ขาด point, มี extra point, text hashผิด, visibility matrixผิด, caseขาด/ซ้ำ หรือ index identityผิดต้อง fail;
- runnerต้องใช้ client/read pathที่แยกจาก filtered providerจริงตามแผน แล้วส่ง observed bodyเข้า proof builder

### B3 — `IsolationProof` ยังเป็น identity manifest ไม่ใช่ผล interlock ที่แผนกำหนด

**ตำแหน่ง:** `p2_m4_harness.py:199-204`; `p2_eval.py:681-694`; `KB_P2_M4_REAL_RUN_PLAN.md:25-28`

proof ปัจจุบันตรวจเพียง hashครบและสี่ค่า distinct `build_isolation_proof()` รับ intหรือ stringใดก็ได้; probeใช้ `1,2,3,4` แทน UUIDแล้ว `validate_m4_isolation_proof()` คืน `[]`

ยังไม่มีผลตรวจ:

- exact compose project/network/volume/collection labels/IDsเป็นของ runนี้;
- networkเป็น internal/no published port;
- targetไม่ใช่ known production endpoint/collection;
- collectionว่างก่อน seed;
- synthetic markerถูก write/readกลับจาก targetเดียวกัน

**ต้องแก้:** ให้ IsolationProofรับผล observationจาก runner ไม่ใช่แค่ names: exact typed resource identities, endpoint/collection guard result, initial count exact zero, internal-network/no-publish assertion และ marker readback binding แล้ว includeทั้งหมดใน canonical evidence bodyจาก B1

### M1 — scorer factory path ปิด accidental mock แล้ว แต่ real adapter ยังใช้ metadata contractเก่า

**ตำแหน่ง:** `p2_m4_harness.py:65-88`; `p2_reranker.py:81-86`

`validate_scorer_metadata()` ปฏิเสธ mock/no metadata/wrong pinก่อน delegateได้จริง และ `run_case()` ไม่เปิด public trace seamแบบเดิมแล้ว แต่ `PinnedCrossEncoder.metadata()` ปัจจุบันยังไม่มี `kind`, `model_file_manifest_sha256`, `inference_config` และใช้ `file_manifest_sha256` ชื่อเก่า จึงยัง wire real classเข้ากับ harnessไม่ได้

ข้อนี้ตรงกับงานที่ handoff deferไว้ให้ runner sliceและไม่ใช่เหตุให้เปลี่ยน proof schemaอีก แต่ต้องมี negative/positive contract testกับ `PinnedCrossEncoder.metadata()` shapeจริงก่อนถือว่า runner implementation complete

## สิ่งที่ยืนยันว่าปิดแล้ว

- `run_m4_cases()`/`run_case()` validate scorer metadataกับ M4RunRequestก่อน delegate
- mock/no metadata, wrong kind/revision/file manifest/inference configถูก reject
- query text/vector/candidate authorizationถูก validateก่อน scorer call
- queryจริงเข้า scorerและ query text/vectorถูก bindกับ frozen case
- public `score_case`/`build_case_record` seamถูกเอาออก; traceเป็น implementation detail
- whitespace/control/surrogate queryใช้กติกา eval contractและไม่ถึง scorer
- schema bumpเป็น `p2-m4-v5`; v4ถูก rejectและ real-run plan sync query text/proof fieldsแล้ว
- run metadataไม่สามารถทับ scorer pinหรือ verdictได้
- malformed proof digest, duplicate identity hash, frozen/index/case-set mismatch และ receipt marker mismatchถูก rejectตาม contractที่เขียนไว้

## Verification

รันผ่านใน environment นี้:

- `test_p2_m4_harness.py`: **34/34**
- `test_p2_m4.py`: **47/47**
- `test_p2_runplan.py`: **95/95**
- `test_p2.py`: **166/166**
- `test_p2_adapter.py`: **21/21**; Qdrant integrationถูก skipเพราะไม่มี optional `qdrant_client`

targeted probesที่ suiteปัจจุบันยังไม่ครอบ:

- เปลี่ยน IsolationProof resource IDs + recompute proof digest โดย receiptเดิม → **public gateผ่านผิด (`[]`)**
- IsolationProof สร้างจาก non-UUID integers `1,2,3,4` → validator **ผ่าน (`[]`)**
- OracleProofสร้างได้โดยมีเพียง `frozen` + `index_sha256`; ไม่มี oracle observation

หมายเหตุ non-blocking: `p2_eval.py:718` docstring ยังเขียน “v4” และ `KB_P2_M4_REAL_RUN_PLAN.md:131` รายงาน harness 33/33 ขณะที่ suiteจริงเป็น 34/34

## Gate

| งาน | Verdict |
|---|---|
| full evidence-body receipt binding + observed OracleProof/IsolationProof + negative tests | **GO NOW — pure/offline** |
| เขียน real-path runner | **FIX-THEN-GO** หลัง targeted re-review ผ่าน |
| M4a run บน isolated Qdrant | **NO-GO** จน runner + real interlock/oracle + atomic-write reviewผ่าน |
| N-sweep | **NO-GO** จน validated M4a PASS |
| decision benchmark | **NO-GO** จน Data Owner sign-off + M4b + validated canary/evidenceครบ |

## Final verdict

**FIX-THEN-GO runner.** scorer/query/case boundaryปิดได้แล้ว แต่ durable chainยัง bindเพียง per-case body และ proof objectsยังไม่มี runtime observationsตามชื่อของมัน จุดใหญ่ที่สุดคือสลับ IsolationProof resource identitiesหลังรันแล้ว receiptเดิมยังผ่าน ปิด full-bundle digestและเพิ่ม observed oracle/interlock bodyก่อนเริ่ม runnerจริง
