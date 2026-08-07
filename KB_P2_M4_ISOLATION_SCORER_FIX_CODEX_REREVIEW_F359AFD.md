# Codex targeted re-review — isolation/scorer FIX @ `f359afd`

**Verdict: FIX-THEN-GO**

Targeted result:

- **M1 CLOSED** — dtype canonicalization ทำให้ real-shaped scorer metadata ตรง RunPlan
- **B2 CLOSED** — cleanup error ถูกสะสมและ propagate ถึง runner; happy path จะไม่ publish เมื่อ cleanup ยืนยันไม่ได้
- **B3 DIRECTION ACCEPTED** — standard Qdrant facade ใช้งานได้กับ in-memory Qdrant; pinned-image guard ถูกทาง แต่ real network/readiness wiring ยังคงเป็น slice ถัดไปตาม handoff
- **B1 OPEN** — identity ที่เรียกว่า transport-derived ยังเป็น constructor self-report
- **B4 OPEN** — `decision_eligible=False` ไม่แก้ false scorer/image attribution และ runtime digest ยังไม่ถูก bind

ไม่มีการเปิด hardening ประเด็นใหม่: สอง blocker ที่เหลือคือ B1/B4 เดิมและเข้า freeze exceptions #2/#3 โดยตรง

## Simpler path

สำหรับ real path ให้มี launcher เดียวที่ไม่รับ arbitrary `session_factory`/scorer loader: launcher สร้าง pinned evaluator/scorer container บน exact internal network, สร้าง `QdrantClient` จาก handle เอง, inspect container/network/image จาก Docker แล้วค่อยประกอบ ports วิธีนี้เล็กกว่าและชัดกว่าให้แต่ละ object self-report identity/digest แล้วพยายาม cross-check ภายหลัง

## Remaining findings

### B1 — `observed_target_identity()` ping target แต่ยังคืน identity ที่ constructor ป้อนมาเอง

**ตำแหน่ง:** `p2_m4_isolation.py:157-173,224-237`

เส้นทางปัจจุบันคือ:

1. `DockerQdrantDriver` เรียก caller-supplied `session_factory(endpoint, collection, size)`
2. `QdrantSession.observed_target_identity()` เรียก `get_collections()` เพื่อพิสูจน์เพียงว่า client ติดต่อ server บางตัวได้
3. function คืน `self._endpoint/self._collection` ซึ่งเป็นค่าที่ constructor รับมา ไม่ได้ derive จาก transport/server
4. driver จึง compare expected กับค่าที่ copy จาก expected เอง แล้วเริ่ม `recreate_collection()`

**Fault probe:** inject client ที่ตอบว่าเป็น production ผ่าน `get_collections()` แต่สร้าง `QdrantSession(... endpoint="http://m4qd-isolated:6333" ...)`; `observed_target_identity()` ยังคืน isolated endpoint/collection และผ่าน identity comparison ได้

`endpoint_is_production()` ที่ `p2_m4_isolation.py:188-190` ก็ยังเช็กเพียง generated URL กับ denylist ซึ่ง default ว่าง จึงไม่ทดแทน target proof

**แก้แบบ bounded:** real launcher ต้องล็อก factory เป็น concrete `QdrantSession.connect` (ห้าม config/plugin inject client), verify จาก Docker inspect ว่า container id, exact internal network, DNS alias/IP และ collection namespace มาจาก resource ที่ launcher เพิ่งสร้าง ก่อน Qdrant mutation แรก แล้วเพิ่ม negative test ว่า misbound client/session ไม่สามารถถึง `recreate_collection()` ได้ การเก็บ injectable factoryไว้เฉพาะ test path ทำได้ แต่ real entry point ต้องไม่มีช่องเลือกมัน

### B4 — mechanics bundle ยังอ้าง fake scorer เป็น pinned และ runtime image digest ไม่ได้ถูกใช้

**ตำแหน่ง:** `test_p2_m4_isolation.py:183-238`, `p2_m4_scorer.py:30-56`, `p2_m4_runner.py:191-217`, `p2_m4_isolation.py:177-179`

การ assert `decision_eligible is False` ถูกต้องสำหรับ M4a แต่ไม่ได้ปิด finding เดิมเรื่อง **ความจริงของ evidence**:

- capstone ยังใช้ `PinnedScorer` ปลอมที่ self-report metadata แล้วได้ public-valid `PUBLISHED`, `status=PASS`, `scorer_kind=pinned-cross-encoder`
- `image_digest` ใน evidence/receipt ยังมาจาก RunPlan ที่ `p2_m4_runner.py:191-214`
- `DockerQdrantDriver.observed_image_digest()` ไม่มี caller (`rg` พบเฉพาะ definition/docs) และ inspect **Qdrant container** ไม่ใช่ evaluator/scorer container ซึ่งเป็น image ที่ RunPlan ต้อง bind

ดังนั้น bundle ใช้ตัดสินใจไม่ได้จริง แต่ยังสามารถรายงานเท็จว่า mechanics PASS ด้วย pinned scorer/image ที่ไม่ได้รัน ซึ่งตรง freeze exception #3

**แก้แบบ bounded:** real launcher สร้าง scorer ด้วย real loader เอง, inspect evaluator/scorer runtime image digest, compare exact กับ RunPlan ก่อน provision/model call และส่ง observed attestation เข้า receipt/evidence path เพิ่ม regression ว่า fake loader หรือ wrong runtime image digest ทำให้ publish ถูกปฏิเสธ ส่วน offline capstone ให้ติดชนิด `synthetic-fake/non-evidence` ที่ public real-M4a gate ไม่รับ หรือไม่ publish public-valid bundle

## Confirmed closures

### M1 — CLOSED

`p2_reranker.py:94-97,152-155` canonicalize `torch.float32` เป็น `float32` ที่ loader boundary และ exact scorer check ยัง fail-closed เมื่อไม่ normalize

### B2 — CLOSED

`p2_m4_isolation.py:201-212` พยายามลบทุก resource, ยอมเฉพาะ not-found แบบ idempotent และ raise `CleanupUnconfirmed` สำหรับ error อื่น; adapter/runner propagation ป้องกัน PASS ตาม `p2_m4_runner.py:205-209`

### B3 — direction accepted, execution gate unchanged

in-memory Qdrant probe ยืนยัน `recreate_collection(VectorParams)`, `count().count`, marker `upsert/retrieve` และ corpus UUID seed ทำงานกับ installed client จริง แต่ handoff ระบุถูกแล้วว่า exact internal-network topology, service readiness และ real provider/oracle session wiring ยังไม่ถูกพิสูจน์ จึงยังไม่ใช่ authorization ให้ execute real run

## Verification

- `test_p2_m4_isolation.py` — **29/29 PASS**
- `test_p2_m4_scorer.py` — **10/10 PASS**
- `test_p2_m4_runner.py` — **44/44 PASS**
- targeted total — **83/83 PASS**
- in-memory Qdrant facade probe: recreate/count/marker/seed PASS
- B1 misbinding probe: client ติดต่อได้แต่ identity ยัง copy constructor — reproduced
- ไม่รัน Docker, external Qdrant หรือ model จริง; ไม่แก้ source/tests/`STATUS.md`

## Gate

- **M1/B2:** CLOSED
- **B3 real-facade implementation:** GO ต่อได้ภายใต้ real-run slice
- **B1/B4:** FIX ก่อนปลด execution
- **real M4a synthetic execution:** **NO-GO จน B1/B4 ปิดและ targeted confirm ผ่าน**
- **M4b/N-sweep/ข้อมูลจริง:** NO-GO ตาม Data Owner gate เดิม

