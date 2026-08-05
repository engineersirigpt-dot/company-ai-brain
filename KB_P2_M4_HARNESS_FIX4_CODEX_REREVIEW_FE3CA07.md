# Codex targeted re-review — P2 M4 harness FIX4 (`fe3ca07`)

วันที่รีวิว: 2026-08-05  
ขอบเขต: `KB_P2_M4_HARNESS_FIX4_HANDOFF.md`, `p2_m4_harness.py`, `p2_eval.py`, `p2_runplan.py`, `p2_reranker.py` และ regression tests ที่เกี่ยวข้อง  
ข้อจำกัดที่รักษาไว้: pure/offline เท่านั้น — ไม่เขียน real-path runner, ไม่แตะ Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Verdict

**FIX-THEN-GO real-path runner**

ทิศทางหลักปิดถูกแล้ว 3 ส่วน:

- `evidence_body_sha256` ครอบ top-level evidence ทั้งชุด (ยกเว้น circular fields) และ receipt ผูกกลับแบบ two-way จริง — post-run proof swap ใช้ receipt เดิมไม่ผ่านแล้ว
- `OracleProof.observed_visibility` มี observed body, exact case coverage, pair equality, index/collection binding และ digest recomputation ครบในระดับ schema
- `PinnedCrossEncoder.metadata()` ตรงกับ scorer provenance contract แล้ว

ยังเหลือ **1 blocker ใน producer boundary**: `IsolationProof` สามารถสร้าง PASS ได้โดยไม่รับ runtime observation จริงแม้แต่ค่าเดียว จึงยังไม่ควรเริ่มเขียน runner บน interface นี้

## Findings

### B1 — `build_isolation_proof()` สังเคราะห์ PASS observation จาก default ได้ทั้งหมด (blocker)

ตำแหน่ง: `p2_m4_harness.py:199-211`

signature ปัจจุบันกำหนด:

```python
initial_point_count=0
network_published_ports=0
endpoint_is_production=False
marker_readback=None  # แล้วแทนด้วย marker_written อัตโนมัติ
```

ดังนั้น caller ส่งแค่ชื่อ resource กับ marker ก็ได้ proof ที่ดูเหมือนผ่าน interlock ครบ ทั้งที่ยังไม่ได้ count collection, inspect network, classify endpoint หรืออ่าน marker กลับจาก target จริงเลย

negative probe ที่รัน:

```python
p = build_isolation_proof(
    project_id="p", network_id="n", volume_id="v",
    collection_id="c", marker="m",
)
validate_m4_isolation_proof(p)  # []
```

นี่ขัดกับ trust boundary ที่ handoff ระบุว่า proof ต้องมาจาก “observed body จริง” และทำให้ runner ที่ลืม wire interlock บางขั้นยัง emit PASS artifact ได้

สิ่งที่ต้องแก้ก่อน GO runner:

1. ตัด PASS-default ทั้ง 4 ค่าออก ให้เป็น required keyword-only arguments:
   `initial_point_count`, `network_published_ports`, `endpoint_is_production`, `marker_readback`.
2. ห้าม `marker_readback=None` แล้ว copy ค่า written; readback ต้องถูกส่งเข้ามาอย่าง explicit จากผลอ่าน target.
3. validate resource IDs และ marker ว่าเป็น non-blank scalar ที่ยอมรับได้ก่อน hash (อย่างน้อยไม่ยอม `""`/whitespace/control-only) เพื่อไม่ให้ identity proof ว่างแต่ดูเหมือน valid.
4. เพิ่ม regression ว่า omit observation field ใด field หนึ่งต้อง `TypeError`/fail ก่อนสร้าง proof และ happy path ต้องส่งค่าครบแบบ explicit.

public validator ที่ตรวจ exact `0/0/False` และ marker equality ทำถูกแล้ว; finding นี้อยู่ที่ **producer interface** ไม่ใช่ validator.

### N1 — canonicalize ลำดับ `observed_visibility` ก่อนออก durable digest (non-blocking แต่ควรปิดก่อน runner)

ตำแหน่ง: `p2_m4_harness.py:217-228`

builder sort pair lists ภายใน case แล้ว แต่ยังคงลำดับ outer observations ตาม caller. ข้อเท็จจริงชุดเดียวกันที่ direct-scroll/runner ส่งมาเป็นคนละลำดับจึงให้ `observation_sha256`, `oracle_proof_sha256`, `evidence_body_sha256` และ receipt root คนละค่า ทั้งที่ semantics เหมือนกัน

แนะนำ sort `obs` ด้วย `case_id_sha256` ก่อนคำนวณ digest และเพิ่ม permutation regression. ไม่ใช่ permission bypass แต่ช่วยให้ durable evidence reproducible จริง

### N2 — จำนวน offline checks ใน handoff ไม่สอดคล้องกัน (evidence-summary correction)

รายการตัวเลขใน handoff บวกกันได้ **645** ไม่ใช่ 655 และบน checkout นี้ `test_p2_adapter.py` รายงาน **21/21** ไม่ใช่ 22/22 จึงทำให้ผลตามรายไฟล์ที่ระบุมีแนวโน้มรวม **644** checks

Codex รันยืนยันได้:

- ชุดแกน M4: `41/41 + 56/56 + 95/95 + 166/166 = 358/358`
- เพิ่ม `pin 14`, `adapter 21`, `dockerbuild 41`, `policy 69`, `eval 64`, `ask_eval 12`, `auth 11`, `p5b 11` — ทุกชุดที่รันได้ผ่าน
- `test_p2_provider.py` และ `test_p2_harness.py` รันไม่ได้ใน environment ของ Codex เพราะไม่มี optional dependency `qdrant_client`; นี่คือ **environment limitation ไม่ใช่ test failure**

ให้แก้ summary/count ตาม stdout จริงจาก environment ที่รันครบก่อนใช้เป็น durable evidence; ไม่ block การแก้ B1

## คำตอบ 4 ข้อจาก handoff

1. **Durable root:** รับ — coverage และ two-way receipt binding ปิด post-run top-level proof swap ได้ตามเป้าหมาย
2. **OracleProof:** รับในระดับ schema — observed body/exact equality/collection binding ครบ; การพิสูจน์ว่า body มาจาก independent direct-scroll จริงต้อง trace ใน real runner review ต่อไปตามแผน
3. **IsolationProof:** fields ใน schema พอเป็น first cut แต่ producer ยังไม่ fail-closedเพราะ PASS-default ตาม B1; ต้องแก้ก่อน
4. **GO runner:** **ยังไม่ GO** จน B1 ปิดและมี regression; หลังปิด B1 ให้ re-review แบบ targeted เฉพาะ producer signature/tests แล้วจึง GO เขียน runner + atomic writer ได้ ส่วน **M4a run ยังคง NO-GO** จน real runner, observed interlock/direct-scroll provenance, negative controls และ atomic-write review ผ่าน

## Gate หลังรีวิวนี้

- Real-path runner: **FIX-THEN-GO — B1 ค้าง**
- M4a isolated run: **NO-GO**
- N-sweep: รอ validated M4a PASS
- Decision benchmark: **NO-GO** จน Data Owner sign-off + M4b + validated canary

