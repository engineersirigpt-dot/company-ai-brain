# Codex Confirmation — P5a rev2.1 (`5517641`) + P1 Contract

**วันที่:** 2026-08-04  
**Target:** `KB_P5A_REV2_1_FIX_HANDOFF.md`  
**ขอบเขต:** review/contract เท่านั้น — ไม่แก้ implementation, `STATUS.md`, Qdrant หรือ deploy

## Verdict

**GO P1 — local + synthetic only.**

Rev2.1 ปิด minimum acceptance จากรอบก่อนครบในระดับ contract/offline harness แล้ว ลำดับต่อไปถูกต้อง:

1. implement P1 local+synthetic
2. review P1 implementation
3. รัน P5b บน separate test collection/API config
4. ผ่าน P5b จึงประกาศ P1 hardened

คำว่า GO นี้ **ไม่ใช่** GO deploy, ไม่ใช่ GO แตะ Qdrant production และยังไม่อนุญาตให้กล่าวว่า auth/permission ของ serverจริง hardened

## Targeted closure

| Finding | Verdict | หลักฐาน |
|---|---|---|
| B1 — malformed negative false PASS | **CLOSED** | `/search` บังคับ `results`; point ID ว่าง/ซ้ำ → MALFORMED; seam `{}`/`[{}]` exit 1 |
| B2 — manifest/roles ไม่ exhaustive | **CLOSED** | `known_roles`; positive ทุก authorized และ negative ทุก denied role; invalid manifest fail ก่อน call |
| B3 — point ID/oracle ใช้งานจริงไม่ได้ | **CLOSED** | deterministic UUID + validation; manifest independent จาก runtime config |
| B4 — leak ไม่ตัดด้วย point identity | **CLOSED** | `canary_found()` ตรวจ exact point ID และ token; collection ไม่ใช่ตัวตัด verdict |
| M1 — positive/auth proof | **CLOSED สำหรับ contract** | positive ทุก allowed roleต้องพบ; auth exact 403 เท่านั้น VERIFIED; 401/ไม่มี spoof ไม่ผ่าน auth-gated run |
| M2 — quality ปน security | **CLOSED** | security exit และ `/ask` quality gate แยกกัน |
| M3/M4/N1 | **CLOSED** | strict shape, blank source regression และ Windows testผ่าน |

## Verification

- `py -3 test_eval_contract.py` → **64/64 passed**
- `py -3 test_ask_eval_harness.py` → **11/11 passed**
- trace: manifest validation → exhaustive role probes → response normalization → canary verdict → auth gate → security exit

## Non-blocking follow-up ที่ต้องเข้า P1/P5b

CLI ปัจจุบันสร้าง spoof pairs จาก key สองตัวแรกเท่านั้น (`ask_eval.py:237-241`) แม้ core `auth_gate_status()` รองรับหลายผลแล้ว เมื่อทำ P1/P5b ให้สร้างอย่างน้อยหนึ่ง forbidden-role spoof ต่อ **ทุก role-scoped key** หรือ full cross-role matrix ไม่ควรใช้สองตัวแรกเป็นหลักฐานว่า registry ทั้งชุดถูก scope

เพิ่ม P1-specific canaries ตอน P5b สำหรับ:

- `UNCLASSIFIED` → admin-only ตาม policy ที่ตกลง
- missing/malformed ACL → ไม่มี standard principal ใดค้นเจอ
- stale `acl_schema_version` → ไม่ถูก retrieve
- `policy_status=QUARANTINED` → ไม่ถูก standard retrieval แม้ admin

สองข้อนี้เป็น acceptance ของ P1/P5b ไม่ block การเริ่ม implement P1

---

# นิยาม P1 ที่ยืนยันร่วมกัน

## 1. “Policy compiler” คืออะไร

Policy compiler คือ **pure deterministic server-side code** ที่รับ identity/claims ที่ระบบเชื่อถือได้ แล้วแปลงเป็นข้อจำกัดค้นหา Qdrant โดย caller และ LLM แก้สิทธิ์เองไม่ได้

```text
X-API-Key
   ↓
authenticate_service()          → ServicePrincipal (trusted identity + role scopes)
   ↓
resolve_effective_access()      → EffectiveAccess (role ที่ request นี้ใช้จริง)
   ↓
compile_retrieval_filter()      → explicit Qdrant Filter
   ↓
Qdrant query_points(filter=...) → authorized candidates เท่านั้น
```

Router/LLM ไม่มีสิทธิ์สร้างหรือผ่อน filter และห้ามใช้ request body `role` ตรง ๆ หลังผ่านขั้น resolve

## 2. Authenticated principal

ขั้นต่ำสำหรับ PoC:

```text
ServicePrincipal
  service_id        stable server-side identity
  allowed_roles     roles จาก API-key registry
  authenticated     true เฉพาะ key valid
  auth_mode         enforce/warn/off เพื่อ audit
```

กติกา:

- `AUTH_MODE=enforce`: key หาย/ผิด → 401; requested role นอก `allowed_roles` → 403
- role ไม่รู้จัก/ว่าง → deny ก่อน retrieval
- service ที่มีหลาย roleต้องเลือก role สำหรับ request และ serverตรวจว่าอยู่ใน scope; ห้าม union ทุก roleให้อัตโนมัติ เพราะจะขยายสิทธิ์โดยไม่จำเป็น
- `warn/off` อาจคงไว้เพื่อ migration แต่ principal ต้องถูก mark ว่า unverified และห้ามใช้เป็นหลักฐาน P1 hardened
- P1 นี้ยังเป็น **service authentication** ไม่ใช่ user authentication; Keycloak/OIDC ภายหลังต้องสามารถสร้าง Principal contract เดียวกันได้

## 3. “Effective ACL” คืออะไร

Effective ACL คือผลอนุญาตสุดท้ายของเอกสารที่ resolve แล้วตอน ingestion ไม่ใช่ rule ดิบหลาย field ที่เอามา AND ตอน query

Payload v1 ที่คาดหวังอย่างน้อย:

```json
{
  "acl_schema_version": 1,
  "policy_version": "poc-v1",
  "policy_status": "ACTIVE",
  "collection_group": "SALES",
  "confidentiality_level": 3,
  "allowed_roles": ["sales", "management", "admin"]
}
```

ความหมาย:

- `allowed_roles` = canonical effective read ACL ที่ policy resolver คำนวณแล้ว
- `confidentiality_level` = classification/egress signal ใน P1 **ยังไม่ใช่** query-time clearance condition
- `collection_group` = routing/diagnostic metadata ไม่ใช่สิทธิ์โดยตัวมันเอง
- `acl_schema_version` + `policy_version` = ป้องกัน payloadเก่า/กติกาเก่าหลุดเข้า query
- `policy_status=ACTIVE` เท่านั้นที่ standard retrieval เห็น

ห้ามเพิ่ม `role AND confidentiality AND group` จนกว่าจะมี trusted user clearance/group claims, semantics AND/OR และ Business Owner อนุมัติ

## 4. Document policy resolver ตอน ingestion

ทำฟังก์ชัน deterministic เช่น:

```text
resolve_document_policy(source_metadata) → DocumentPolicy
validate_document_policy(policy)         → valid หรือ quarantine
```

กติกา v1:

- mapping source/collection ที่รู้จัก → effective `allowed_roles` ตาม policy config
- source ไม่รู้จักแต่โครงสร้างถูก → `UNCLASSIFIED`, `allowed_roles=[admin]`
- payload/mapping ผิดชนิด, role ไม่รู้จัก, ACL ว่าง, version หาย → `QUARANTINED`; ห้าม upsert เข้า active search generation
- child chunk และ `parent_text` ของ documentเดียวกันต้องใช้ policy/version เดียวกัน
- AI/Router ห้ามตัดสิน role หรือแก้ policy

แยกให้ชัด:

- **UNCLASSIFIED** = เอกสารจริงที่ยังรอ admin classify แต่มี policyที่ valid (admin-only)
- **QUARANTINED** = record/payloadผิด contract ไม่ควรปรากฏใน standard retrieval แม้ admin; adminตรวจผ่าน workflowแยก

## 5. Query-time compiler

หลัง auth/resolve แล้ว compiler สร้าง filterแบบ explicit ทุก role รวม admin:

```python
Filter(must=[
    FieldCondition(key="acl_schema_version", match=MatchValue(value=1)),
    FieldCondition(key="policy_version", match=MatchValue(value="poc-v1")),
    FieldCondition(key="policy_status", match=MatchValue(value="ACTIVE")),
    FieldCondition(key="allowed_roles", match=MatchAny(any=[effective_role])),
])
```

หลักบังคับ:

- admin ใช้ `allowed_roles contains admin` เช่นกัน; **ห้ามคืน `None` filter**
- missing/stale/malformed policy field ต้องไม่ match โดยธรรมชาติ
- `/search` และ `/ask` ต้องเรียก shared retrieval function/compiler เดียวกัน
- permission filterต้องอยู่ใน `query_points()` ก่อน retrieval; ห้ามค้นทั้งหมดแล้ว filterใน Python
- rerankerในอนาคตเห็นได้เฉพาะ candidates ที่ผ่าน filterแล้ว

## 6. Fail-closed contract

P1 ต้อง deny หรือ quarantine เมื่อ:

- key/identity พิสูจน์ไม่ได้ใน enforce mode
- requested role ไม่อยู่ใน principal scope
- role/policy/version/status ไม่รู้จัก
- document ACL หาย, ว่าง, ผิดชนิด หรือมี unknown role
- compilerสร้าง filterไม่ได้

ห้าม fallback เป็น production, admin, no filter หรือ legacy payloadโดยเงียบ

## 7. P1 implementation slice ที่อนุมัติ

ทำ local+synthetic ได้ดังนี้:

1. สร้าง policy module/data contracts แบบ pure
2. refactor auth ให้คืน `ServicePrincipal` แทนคืนแค่ service string
3. ทำ `resolve_effective_access()` และ explicit filter compiler
4. ทำ document-policy resolver/validator โดยยังไม่เขียน Qdrantจริง
5. ให้ `/search` และ `/ask` ใช้ shared authorized retrieval path
6. unit/contract tests ด้วย fake Qdrant ตรวจ exact filter
7. เพิ่ม matrix tests ทุก role + missing/stale/quarantine/admin-spoof

ยังไม่ทำใน P1 รอบนี้:

- Keycloak/user OIDC จริง
- group/department/clearance ABAC
- egress decision/redaction/local routing
- Qdrant production retag/cutover
- deploy/flip serverจริง

## 8. Deploy implication ที่ต้องจำไว้

corpus ปัจจุบันยังไม่มี `acl_schema_version/policy_version/policy_status`; หากเปิด filterใหม่กับ collectionเดิมทันที เอกสารทั้งหมดจะหายจากผลค้น ซึ่งเป็น fail-closed แต่ทำ Voicebot outage

ดังนั้นหลัง P1 code review ต้องใช้ P5b/test collectionก่อน ส่วน production ภายหลังต้องทำ staging/new generation → validate point count/ACL coverage → atomic alias/cutover หรือแผน migrationที่ rollback ได้ ห้ามแก้ filter live ก่อน backfill

## P1 Go/No-Go

**GO:** implement scope ข้อ 7 แบบ local+synthetic  
**NO-GO:** deploy, retag Qdrant production, flip `AUTH_MODE`, เพิ่ม group/confidentiality AND filter หรือประกาศ hardenedก่อน P5b

**Final verdict:** **SHIP P5a contract / GO P1 implementation** — Rev2.1 เป็น measuring contractที่พอสำหรับเริ่มสร้าง policy compiler; hardening claim รอ P5b ตามลำดับเดิม
