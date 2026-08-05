# P2 — ปิด B1 blocker (fail-closed IsolationProof producer) + N1/N2 (round 5) → targeted re-review ก่อน GO runner

> **สืบเนื่อง:** `KB_P2_M4_HARNESS_FIX4_CODEX_REREVIEW_FE3CA07.md` (FIX-THEN-GO runner — B1 ค้าง)
> **ทั้งหมด pure/offline** — ไม่แตะ Docker/Qdrant/model · real-path runner ยัง NO-GO

## Codex FIX4: ปิดถูก 3 ส่วน (durable root / OracleProof observed / metadata contract) เหลือ **1 blocker** — `build_isolation_proof()` สังเคราะห์ PASS observation จาก default ได้

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B1** ⭐ (blocker) | `build_isolation_proof()` มี default `initial_point_count=0`/`network_published_ports=0`/`endpoint_is_production=False` และ `marker_readback=None`→copy จาก written → caller ส่งแค่ชื่อ+marker ก็ได้ proof ที่ดูผ่าน interlock ครบทั้งที่ยังไม่ได้ observe จริง (runner ที่ลืม wire ขั้นใดขั้นหนึ่งยัง emit PASS) | ทำ producer ให้ **fail-closed**: 4 observation + `marker_readback` เป็น **required keyword-only (ไม่มี default)** ; `marker_readback` ต้องส่ง explicit (ห้าม auto-copy จาก written — ต้องเป็นค่าที่อ่านกลับจาก target จริง) ; validate resource id/marker เป็น **non-blank scalar** (reject `""`/whitespace/control/surrogate/bool) ก่อน hash |
| **N1** | builder sort inner pair lists แล้ว แต่ **outer `observed_visibility` ตามลำดับ caller** → scroll คนละลำดับ = `observation_sha256`/`oracle_proof_sha256`/`evidence_body_sha256`/receipt root คนละค่า ทั้งที่ semantics เดียวกัน | sort `obs` ด้วย `case_id_sha256` ก่อนคำนวณ digest → durable evidence reproducible |
| **N2** | handoff FIX4 รายงานรวม 655 (บวกเลขจริงได้ 645) — arithmetic error | รายงานตาม stdout จริง (ดูตาราง) + แยก env ที่มี/ไม่มี `qdrant_client` |

> validator (`validate_m4_isolation_proof`) ที่ตรวจ exact `0/0/False` + marker equality **ถูกอยู่แล้ว** — รอบนี้แก้เฉพาะ **producer interface** ตามที่ Codex ชี้

## negative tests (pure) ที่เพิ่ม
- **B1**: omit observation field → **TypeError** (ไม่มี PASS default) · resource id `"   "` → **ValueError** · `marker_readback=True` (bool) → **ValueError** · (คงเดิม) `initial_point_count!=0` / `network_published_ports!=0` / `endpoint_is_production=True` / marker readback != written → isolation invalid
- **N1**: `observed_visibility` ลำดับสลับ (reversed) → `oracle_proof_sha256` เท่าเดิม (reproducible)

## ผลรัน (offline — stdout จริง เครื่องนี้มี `qdrant_client`/`torch` ครบ)
| suite | ผล | | suite | ผล |
|---|---|---|---|---|
| test_p2 | 166/166 | | test_eval_contract | 64/64 |
| test_p2_m4 | 56/56 | | test_ask_eval_harness | 12/12 |
| test_p2_m4_harness | 46/46 | | test_auth | 11/11 |
| test_p2_runplan | 95/95 | | test_p5b_fixtures | 11/11 |
| test_p2_pin | 14/14 | | test_p2_provider | 22/22 |
| test_p2_adapter | 22/22 | | test_p2_harness | 21/21 |
| test_p2_dockerbuild | 41/41 | | test_policy | 69/69 |

- **รวมเครื่องนี้ (14 suites, มี qdrant_client): 650/650**
- **clean env (ไม่มี qdrant_client): adapter = 21 (integration skip) · provider/harness ไม่รัน → core 12 suites = 606/606** (ตรงกับที่ Codex รันได้)

## ยังไม่ได้ทำ (รอ targeted re-review รอบนี้ก่อน)
- **real-path runner** — wire `resolve_effective_access → provider → run_case(PinnedCrossEncoder จริง)` บน isolated Qdrant + **ป้อน observed body จริง**:
  - IsolationProof: create isolated project/network/volume/collection → count collection (=0) → inspect network (no publish) → classify endpoint (non-prod) → write marker + **อ่านกลับจาก target** → ส่งค่าทั้งหมด explicit เข้า `build_isolation_proof`
  - OracleProof: **direct scroll แยกจาก filtered provider** → observed visibility ต่อ case
- **atomic evidence/receipt write** (temp→rename ; artifact root = evidence_body) + failure controls

## ขอ Codex review (targeted — producer signature/tests เท่านั้น ตามที่ระบุใน FIX4)
1. `build_isolation_proof` fail-closed (ทุก observation required + explicit readback + scalar validation) ปิด B1 ครบไหม
2. N1 canonicalization + permutation regression พอไหม
3. หลังผ่าน → **GO เขียน runner + atomic writer** ; M4a run ยังคง NO-GO จน real interlock/direct-scroll provenance + negative controls + atomic-write review ผ่าน

**Gate:** real-path runner = FIX-THEN-GO (B1 ปิดแล้ว รอ targeted re-review) · M4a run = NO-GO · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน Data Owner sign-off + M4b + validated canary
