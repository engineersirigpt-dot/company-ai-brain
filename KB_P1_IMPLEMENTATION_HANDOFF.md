# P1 — Policy compiler + effective ACL (local + synthetic) → handoff review

> **สืบเนื่อง:** `KB_P5A_REV2_1_CODEX_REVIEW.md` — GO P1 local+synthetic, นิยาม §1-8
> **ขอบเขต:** implement scope ข้อ 7 · **ไม่ deploy · ไม่ retag Qdrant prod · ไม่ flip AUTH_MODE · ไม่เพิ่ม group/confidentiality AND** · hardening claim รอ P5b

## Implement ตาม §7 (approved slice)
| §7 | ทำแล้ว | ที่ไหน |
|---|---|---|
| 1. policy module/data contracts (pure) | `ServicePrincipal`, `EffectiveAccess`, `DocumentPolicy` + constants (`ACL_SCHEMA_VERSION=1`, `POLICY_VERSION="poc-v1"`, `ACTIVE`/`QUARANTINED`) | [policy.py](policy.py) |
| 2. auth คืน `ServicePrincipal` | `authenticate_service()` แยก identity ออกจาก decision (ไม่ raise ที่ authenticate) | policy.py + app/main.py |
| 3. `resolve_effective_access()` + explicit filter compiler | `resolve_effective_access` (role เดียว ไม่ union, fail-closed) + `compile_retrieval_filter` (4 เงื่อนไข AND) | policy.py |
| 4. document-policy resolver/validator (ยังไม่เขียน Qdrant จริง) | `resolve_document_policy` + `validate_document_policy` + quarantine; wire เข้า `ingest.py` แต่**ไม่รัน ingest จริง** | policy.py + [ingest.py](ingest.py) |
| 5. `/search` และ `/ask` ใช้ shared authorized path | `authorized_points()` เรียกจากทั้งสอง endpoint; filter อยู่ใน `query_points()` ก่อน retrieval | app/main.py |
| 6. unit/contract tests + fake Qdrant ตรวจ exact filter | `test_policy.py` — FakeQdrant + `matches_policy` = executable filter semantics | [test_policy.py](test_policy.py) |
| 7. matrix tests ทุก role + missing/stale/quarantine/admin-spoof | ครบใน test_policy.py (เทียบ independent oracle) | test_policy.py |

## นิยามที่บังคับใช้ (map §1-6)
- **§1 compiler:** `X-API-Key → authenticate_service → resolve_effective_access → compile_retrieval_filter → query_points`. request body `role` ใช้ได้เฉพาะหลัง resolve; LLM/Router แก้ filter ไม่ได้
- **§2 principal:** `enforce`: key หาย/ผิด→401, role นอก scope→403; role ว่าง/ไม่รู้จัก→deny ทุก mode; หลาย role ต้องเลือก 1 (ไม่ union); `warn/off` → `verified=False` (ห้ามใช้เป็นหลักฐาน hardened)
- **§3 effective ACL:** payload v1 = `acl_schema_version` + `policy_version` + `policy_status` + `collection_group` + `confidentiality_level` + `allowed_roles`. `allowed_roles` = canonical read ACL; `confidentiality_level` = egress signal **ยังไม่** เป็น query-time clearance; `collection_group` = diagnostic
- **§5 compiler (explicit เสมอ):** filter = `acl_schema_version==1 AND policy_version==poc-v1 AND policy_status==ACTIVE AND allowed_roles ∋ effective_role`. **admin ก็มี filter (`allowed_roles ∋ admin`) — ไม่มี `None` bypass**. field หาย/stale/malformed ไม่ match เอง (fail-closed)
- **§4 resolver:** source รู้จัก→effective ACL; source ไม่รู้จักแต่โครงถูก→`UNCLASSIFIED`/admin-only (ACTIVE); mapping ผิด/ACL ว่าง/role แปลก/source หาย→`QUARANTINED` (ไม่เข้า active). UNCLASSIFIED(valid, admin-only) ≠ QUARANTINED(contract ผิด)
- **§6 fail-closed:** ทุก deny path ไม่ fallback เป็น admin/no-filter/legacy เงียบ ๆ

## ผลรัน (offline, ไม่มี stack)
```
test_policy.py           35/35 passed   (auth + compiler + resolver + fake-Qdrant matrix)
test_auth.py             11/11 passed   (enforce/warn/off + fail-open regression; load_api_keys skip เพราะ heavy deps)
test_eval_contract.py    64/64 passed   (P5a regression — ยังเขียว)
test_ask_eval_harness.py 11/11 passed   (P5a regression — ยังเขียว)
app/main.py, ingest.py   py_compile OK
```
matrix ยืนยัน: qc เห็น {RECALL, PRODUCTION}, sales เห็น {SALES}, admin เห็นทุก ACTIVE **แต่ไม่เห็น stale/quarantine**, it เห็น 0 (fail-closed), admin-spoof (principal ไม่มี admin ขอ admin ใน enforce) → 403

## ยังไม่ทำ (จงใจ — เข้า P5b / รอบถัดไป)
- **ไม่รัน ingest จริง / ไม่ retag corpus:** corpus ปัจจุบันไม่มี `acl_schema_version/policy_version/policy_status` → ถ้าเปิด filter ใหม่กับ collection เดิมทันที เอกสารหายหมด (fail-closed แต่ Voicebot outage §8). ต้อง P5b/test collection + backfill + atomic cutover ก่อน prod
- **spoof matrix (non-blocking จากรีวิว):** CLI ยังสร้าง spoof จาก key 2 ตัวแรก — P5b ต้องทำ ≥1 forbidden-role spoof ต่อ **ทุก** role-scoped key
- **P1-specific canaries (P5b):** `UNCLASSIFIED`→admin-only, missing/malformed ACL→ไม่มีใครเห็น, stale `acl_schema_version`→ไม่เห็น, `QUARANTINED`→ไม่เห็นแม้ admin
- ยังไม่ทำ: Keycloak/user OIDC, group/clearance ABAC, egress/redaction, deploy

## ขอ Codex review
1. policy.py contract (§1-6) ตรงนิยามที่ยืนยันไหม — จุดที่ compiler/resolver ยัง fail-open หรือ semantics เพี้ยน
2. `matches_policy` (executable filter semantics ใน test) สมมูลกับ Qdrant `Filter(must=[...])` ที่ `to_qdrant_filter` สร้างจริงไหม — มี edge (เช่น payload `allowed_roles` ไม่ใช่ list, field เป็น null) ที่ Qdrant จะ match ต่างจาก matches_policy ไหม
3. ตำแหน่ง quarantine gate ที่ ingest (skip ไม่ upsert) พอไหม หรือควรมี quarantine store แยกใน P1
4. ไฟเขียวไป **P5b** (ingest canary + role-scoped keys บน test collection) ได้ไหม หรือมีอะไรต้องปิดใน P1 ก่อน
