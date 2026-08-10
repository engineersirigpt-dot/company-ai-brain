# Codex review — P2 M4a real-run evidence (`7de8574`)

วันที่รีวิว: 2026-08-07  
ขอบเขต: `KB_P2_M4_REAL_RUN_EVIDENCE.md`, real-run path และ raw bundle ใน `.p2_m4_out/`  
ไม่ได้แก้ source, tests หรือ `STATUS.md` และไม่ได้รัน Docker/Qdrant/model ซ้ำ

## Verdict

**ผลเชิง capability: ACCEPTED — ระบบสร้างผลลัพธ์จริงแล้ว**  
มีน้ำหนักเพียงพอที่จะพูดว่า pinned `bge-reranker-v2-m3` ถูกโหลดและให้ finite score, provider/oracle ใช้ Qdrant จริง, policy filter กัน sentinel ก่อนถึง model และ full M4a pipeline สร้าง bundle ที่ validator รับเป็น `PASS`

**การปิด isolation/scorer slice อย่างเป็นทางการ: FIX-EVIDENCE-THEN-CLOSE**  
ยังไม่ควรประกาศ evidence chain ว่า fail-closed เพราะค่าที่ใช้ยืนยัน image/isolation/cleanup บางส่วนเป็นค่าที่ evaluator รับหรือกำหนดเอง ไม่ใช่ observation จาก Docker controller ที่ถูกเก็บใน durable receipt ข้อค้นพบด้านล่างเข้า freeze exception ที่ตกลงไว้โดยตรง: **false-PASS** และ **cleanup-failure**

นี่ไม่ใช่เหตุให้กลับไปวน hardening อีกหลายรอบ ทางที่สั้นที่สุดคือ **bounded evidence rerun เพียงหนึ่งรอบ** หลังเพิ่ม controller receipt ที่ load-bearing; ไม่ต้องออกแบบ safety/provenance v2 ใหม่

## สิ่งที่ตรวจยืนยันได้

- raw bundle `.p2_m4_out/run-1.bundle.json` มีอยู่ และ public preflight validator ผ่านโดยไม่มี error
- SHA-256 ของ raw bundle ที่ตรวจ: `03330e39aef12cddc709befa765a35e0740e2d9d8e24223bb976c48483cd00d1`
- evidence ภายในสอดคล้องกัน: `status=PASS`, `decision_eligible=false`, oracle ผ่าน, unauthorized-to-model เป็นศูนย์ และ sentinel ปรากฏเฉพาะฝั่ง unfiltered/oracle ไม่อยู่ใน model input
- real run ทำให้เจอ Qdrant point-id contract และ marker pollution ซึ่งเป็นหลักฐานสนับสนุนอย่างมีนัยสำคัญว่าเส้นทาง Qdrant จริงถูกใช้งาน ไม่ใช่แค่ fixture เดิม
- targeted offline suites ผ่าน **82/82**: isolation 32, launch 6, runner 44
- ข้อจำกัด synthetic corpus, dummy vectors, bridge network และ runtime `pip` ถูกเปิดเผยในเอกสารตรงไปตรงมา

## Findings

### B1 — Executed-image attestation ยังเป็น self-assertion จึงสร้าง false PASS ได้

`p2_m4_evaluator.py:22-25` รับ `M4_EVAL_IMAGE_DIGEST` จาก environment แล้วนำค่านั้นไปสร้าง plan; ต่อมา bundle ยืนยันเพียงว่าผลลัพธ์ตรงกับ plan ที่ evaluator สร้างเอง ไม่มี Docker-observed container image ID/digest ใน raw bundle

`p2_m4_launch.run_m4a_locked()` มี check ที่ถูกทิศ แต่ real path ที่ commit นี้ไม่ได้เรียกผ่าน concrete controller implementation ดังกล่าว ดังนั้นผู้เรียกสามารถใส่ pinned digest ที่ถูกต้องใน env แม้ evaluator จะรันจาก image/source อื่น และยังได้ PASS

**ต้องปิด:** controller ต้องอ่าน actual evaluator container image จาก Docker inspect และ bind observation นั้นเข้ากับ receipt/bundle โดย evaluator ไม่มีสิทธิ์ประกาศค่าดังกล่าวเอง

### B2 — Isolation proof ผูกกับค่าที่ controller ส่งมา แต่ไม่มี controller observation ให้ตรวจย้อนกลับ

ใน `p2_m4_evaluator.py:79-82` ค่า `published_ports=0` และ `endpoint_is_production=False` ถูกกำหนดตรง ๆ ส่วน endpoint/network/volume/project มาจาก environment; `QdrantSessionDriver` เพียงส่งค่าพวกนี้กลับให้ proof (`p2_m4_isolation.py:237-275`)

จึงพิสูจน์ได้ว่า evaluator **ใช้ค่าตาม contract** แต่ยังพิสูจน์ไม่ได้จาก artifact ว่า Docker network/port/target ที่รันจริงตรงกับค่าดังกล่าว เอกสารบอกตามจริงว่าใช้ user-defined bridge ไม่ใช่ `--internal`; สำหรับ synthetic M4a ยอมรับได้ แต่ห้ามตีความ `published_ports=0` ว่าเป็นหลักฐานของ network isolation ทั้งหมด

**ต้องปิด:** receipt ฝั่ง host ต้องเก็บ observation จาก Docker inspect ได้แก่ network/container identity, published-port map, Qdrant image ref, endpoint binding และ production-deny result แล้ว hash-bind กับ run/bundle

### B3 — PASS ถูก publish ก่อน host cleanup และไม่มี durable cleanup proof

container-side `QdrantSessionDriver.teardown()` เป็น no-op โดยตั้งใจ เพราะ host controller เป็นผู้ลบ resource แต่ bundle ถูกสร้างเป็น `PUBLISHED/PASS` ภายใน evaluator ก่อน host cleanup เอกสารเล่าว่า teardown สำเร็จ ทว่า raw evidence ไม่มี controller receipt หรือ post-cleanup inspect ที่พิสูจน์ว่า container/network/volume หายจริง

ถ้า evaluator PASS แล้ว host cleanup ล้มเหลว artifact ปัจจุบันยังคงดูเป็น clean PASS ซึ่งตรงกับ freeze exception เรื่อง cleanup affecting next run

**ต้องปิด:** outer controller เป็นผู้ให้ terminal verdict หลัง cleanup verification เท่านั้น หรือออก signed/hash-bound outer receipt ที่ downgrade เป็น `DEGRADED/FAILED` เมื่อ cleanup ยืนยันไม่ได้ โดย receipt ต้องอ้าง bundle SHA-256 และ attempt ID เดียวกัน

### M1 — Process receipt เป็น synthetic metadata ไม่ใช่ operational receipt

evaluator ส่ง `argv=["python","p2_m4_evaluator.py"]`, `stdout=b"real"`, `stderr=b""` และ deterministic clock เข้า `run_m4a` (`p2_m4_evaluator.py:91-106`) จึงทำให้ receipt schema ผ่าน แต่ไม่พิสูจน์ Docker command, wall-clock execution, exit code, stdout/stderr จริง หรือขั้น runtime `pip`

นอกจากนี้ current source ถูก mount เข้า pinned image และ `qdrant-client` ถูกติดตั้ง runtime ดังนั้น image digest ยังไม่ bind evaluator source/dependency ที่รันจริง สำหรับ M4a mechanics อาจ defer การ bake ได้ แต่ controller receipt ต้องอย่างน้อย bind `git commit/tree digest`, installed package/version digest และคำสั่งจริง เพื่อไม่ให้คำว่า pinned/reproducible overclaim

## Definition of done แบบ bounded — หนึ่ง rerun พอ

เพิ่ม concrete host controller/script ตัวเดียวแล้วรัน M4a synthetic ซ้ำหนึ่งครั้ง โดยต้องทำครบ:

1. provision network/Qdrant/evaluator และเก็บ Docker observations จากระบบจริง ไม่รับ identity/security assertions จาก evaluator env
2. เรียก evaluator พร้อม plan/frozen/corpus ที่ hash-bound และบันทึก command, exit code, timestamps, stdout/stderr digests จริง
3. teardown แล้ว inspect ยืนยันว่า container/network/volume ที่เป็นเจ้าของ run หายจริง
4. atomically publish outer receipt หลังข้อ 3 โดย bind: attempt ID, bundle SHA-256, evaluator image observation, Qdrant image ref, source/dependency identity และ cleanup result
5. validator fail-closed เมื่อ observation ขาด/ไม่ตรง และมี negative test อย่างน้อยสามกรณี: image env ปลอม, isolation env ปลอม และ cleanup failure หลัง inner bundle PASS

bridge network + runtime `pip` คงไว้ใน M4a rerun นี้ได้ถ้าระบุเป็นข้อจำกัดและ bind dependency/source ใน receipt; ไม่จำเป็นต้องขยายงานไป production packaging ก่อนปิด PoC mechanics

## Gate หลังรีวิว

- **M4a real mechanics capability:** `DEMONSTRATED`
- **formal isolation/scorer evidence closure:** `FIX-EVIDENCE-THEN-CLOSE` ด้วย bounded rerun ข้างต้น
- **N-sweep / M4b / decision benchmark:** ยังคง `NO-GO` ตาม `STATUS.md` จน Data Owner sign-off, classification และ human-reviewed labels ครบ
- **Data Owner pack:** ทำขนานได้ทันทีและเป็นคอขวดทางองค์กรที่ควรเร่งกว่าเพิ่ม edge-case hardening
- **production:** `NO-GO`; ผล P2 นี้วัด retrieval/reranker mechanics เท่านั้น ไม่ใช่หลักฐานตัดสิน GPU สำหรับ generation/context/concurrency/e2e ทั้งระบบ

## สรุปสำหรับผู้บริหาร

ผลรอบนี้คือความก้าวหน้าจริง: ระบบไม่ได้อยู่แค่ design/test แล้ว แต่รัน Qdrant และ reranker จริงจนสร้างผลลัพธ์สำเร็จ สิ่งที่ยังขาดไม่ใช่ capability แต่คือหลักฐานฝั่ง host ที่ยืนยันว่า image, isolation และ cleanup ที่เกิดขึ้นจริงตรงกับคำประกาศ ให้ปิดด้วย evidence rerun รอบเดียว จากนั้น freeze slice และย้ายแรงไป Data Owner pack ตามแผน
