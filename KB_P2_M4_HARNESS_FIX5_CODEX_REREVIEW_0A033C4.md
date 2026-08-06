# Codex targeted re-review — P2 M4 harness FIX5 (`0a033c4`)

วันที่รีวิว: 2026-08-06  
ขอบเขตที่ล็อก: producer signature + B1/N1 tests ตาม `KB_P2_M4_HARNESS_FIX5_HANDOFF.md` เท่านั้น  
ข้อจำกัดที่รักษาไว้: pure/offline — ไม่เขียน runner, ไม่แตะ Docker/Qdrant/model และไม่แก้ `STATUS.md`

## Verdict

**GO เขียน real-path runner + atomic writer**

**B1 CLOSED, N1 CLOSED, N2 CLOSED ในระดับ evidence summary** ไม่มี blocker/major ใหม่ใน targeted scope นี้

นี่เป็นไฟเขียวให้ **เขียนและทดสอบ runner แบบ injectable** เท่านั้น ยังไม่ใช่ไฟเขียวให้รัน M4a บน Qdrant จริง

## Intent และทางเลือกที่เล็กที่สุด

เป้าหมายคือทำให้ `IsolationProof` สร้างไม่ได้หาก caller ไม่ส่งผล interlock ที่สังเกตมาอย่าง explicit และทำให้ OracleProof digest ไม่ขึ้นกับลำดับ scroll

การเปลี่ยน required keyword-only arguments โดยไม่เพิ่ม abstraction ใหม่เป็นวิธีที่เล็กและตรงที่สุดแล้ว; ยังไม่จำเป็นต้องเพิ่ม class/dataclass สำหรับ observation ก่อนมี runner จริง

## Trace ที่ยืนยัน

### 1. B1 — fail-closed producer

เส้นทาง: `build_isolation_proof()` → `_good_id()` → hash observed fields → `validate_m4_isolation_proof()` → `build_run_verdicts()`/public gate

- `p2_m4_harness.py:206-207` กำหนด `marker_readback`, `initial_point_count`, `network_published_ports`, `endpoint_is_production` เป็น required keyword-only ทั้งหมด ไม่มี PASS-default เหลืออยู่
- `p2_m4_harness.py:213-217` ตรวจ project/network/volume/collection/marker/readback ผ่าน `_good_id()` ก่อน hash; bool, non-scalar, blank, whitespace, control และ lone-surrogate ถูก reject
- `p2_m4_harness.py:218-222` ไม่ copy marker written ไปเป็น readback อีกแล้ว; ทั้งสองค่าถูก hash จาก argument คนละตัว
- public validator เดิมยังตรวจ exact `initial_point_count == 0`, `network_published_ports == 0`, `endpoint_is_production is False`, marker equality และ proof digest จึงยัง fail-closed หลัง producer เปลี่ยน
- `rg` พบ call site ปัจจุบันเฉพาะ test helper ซึ่งส่ง observation ครบ; ยังไม่มี runner เก่าที่แตกจาก signature change

targeted signature probe:

```text
(*, project_id, network_id, volume_id, collection_id, marker, marker_readback,
    initial_point_count, network_published_ports, endpoint_is_production)

required/no-default:
marker_readback=True
initial_point_count=True
network_published_ports=True
endpoint_is_production=True
```

ผล: runner ที่ลืม wire observation อย่างน้อยหนึ่ง field จะ fail ตั้งแต่ call boundary ด้วย `TypeError`; B1 ปิดตามข้อกำหนด

### 2. N1 — deterministic OracleProof

- `p2_m4_harness.py:232-236` sort inner authorized/sentinel pair lists และ sort outer observations ด้วย `case_id_sha256` ก่อนคำนวณ `observation_sha256`
- regression สลับ `observed_visibility` ด้วย `reversed()` แล้วได้ `oracle_proof_sha256` เท่าเดิม
- เพราะ evidence body และ receipt root derive ต่อจาก OracleProof เดียวกัน การ canonicalize จุดนี้ทำให้ downstream durable digests คงที่ด้วย

ผล: N1 ปิดพอสำหรับเริ่ม runner

### 3. N2 — test evidence summary

ตัวเลขใน FIX5 บวกได้ถูกต้อง:

- full environment ตาม stdout ที่ handoff รายงาน: **650/650**
- clean environment ที่ไม่มี `qdrant_client`: adapter integration skip หนึ่ง check และ provider/harness ไม่รัน → **606/606**

Codex รัน clean-environment 12 suites ซ้ำและได้ **606/606** ตรง handoff:

```text
test_p2 166             test_eval_contract 64
test_p2_m4 56           test_ask_eval_harness 12
test_p2_m4_harness 46   test_auth 11
test_p2_runplan 95      test_p5b_fixtures 11
test_p2_pin 14          test_p2_adapter 21
test_p2_dockerbuild 41  test_policy 69
```

`test_p2_provider` และ `test_p2_harness` ไม่ได้ถูกรันซ้ำโดย Codex เพราะ environment นี้ไม่มี optional dependency `qdrant_client`; จึงยืนยัน full `650/650` จาก arithmetic + handoff stdout ไม่ได้อ้างว่า Codex รันสอง suite นี้เอง

## คำตอบ 3 ข้อจาก handoff

1. **`build_isolation_proof` ปิด B1 ครบไหม:** ครบใน producer contract — required observations, explicit readback และ scalar validation ทำงานตามที่ขอ
2. **N1 canonicalization + permutation regression พอไหม:** พอสำหรับ interface นี้; outer/inner ordering ถูก canonicalize ก่อน digest
3. **GO ขั้นถัดไปไหม:** **GO เขียน real-path runner + atomic writer** และเพิ่ม unit/integration tests แบบ injectable ได้

## Load-bearing checks สำหรับ review รอบ runner (ไม่ใช่ finding ของ FIX5)

required arguments บังคับให้ caller “ส่งค่า” แต่ไม่ได้พิสูจน์ด้วยตัวมันเองว่าค่านั้นมาจาก target จริง ดังนั้น review รอบถัดไปต้อง trace source ของทุก field:

1. `initial_point_count` มาจาก count ก่อน seed จริง
2. `network_published_ports` มาจาก inspected isolated network จริง
3. `endpoint_is_production` derive จาก endpoint/interlock policy ไม่ใช่ hardcode `False`
4. `marker_readback` มาจาก read-after-write ผ่าน target เดียวกัน ไม่ใช่ reuse ตัวแปร written
5. Oracle observation มาจาก independent direct scroll แยกจาก filtered provider
6. atomic writer ต้องไม่ทิ้ง PASS artifact เมื่อ exception/non-zero/partial write และต้อง validate public bundle ก่อน publish final receipt/evidence

## Gate หลังรีวิวนี้

- Real-path runner + atomic writer implementation: **GO**
- M4a isolated run: **NO-GO** จน runner provenance, negative controls และ atomic-write implementation review ผ่าน
- N-sweep: รอ validated M4a PASS
- Decision benchmark: **NO-GO** จน Data Owner sign-off + M4b + validated canary

