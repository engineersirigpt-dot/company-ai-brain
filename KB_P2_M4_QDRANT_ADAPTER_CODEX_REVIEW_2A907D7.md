# Codex review — P2 M4 Qdrant provider/oracle adapter (`2a907d7`)

วันที่รีวิว: 2026-08-07

ขอบเขต: `KB_P2_M4_QDRANT_ADAPTER_HANDOFF.md`, `p2_m4_qdrant.py`, `test_p2_m4_qdrant.py` และเส้นทางจริงผ่าน `p2_provider.py` → `p2_m4_runner.py` → `p2_m4_harness.py` → `p2_eval.py`

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้ source/tests/`STATUS.md`, ไม่แตะ Qdrant/Docker/model และไม่รัน M4a จริง

## Verdict

**FIX-THEN-GO — ยังไม่อนุมัติ isolation/Docker adapter slice**

RBAC-filtered provider และ fail-closed postcondition ต่อเข้ากับ runner/harness ได้จริง แต่ target identity check เป็น self-attestation ที่ bypass ได้ และ oracle observation ถูก scope ด้วย role ทั้งที่หลักฐาน authoritative เป็นราย case จึงยังใช้กับ eval ที่มีหลาย case ต่อ role ไม่ได้

M4a real run ยังคง **NO-GO** ตาม gate เดิม

## Simpler-alternative check

ไม่ต้องเพิ่ม discovery service หรือ auth layer ใหม่ ทางแก้เล็กสุดคือ:

1. ให้ `bind(handle)` สร้าง/รับ client session จาก **exact endpoint ใน handle** ผ่าน injected `client_factory(endpoint)` และให้ identity มาจาก session ที่ใช้ query จริง ไม่ใช่ copy handle
2. เปลี่ยน oracle observation จาก role-scoped เป็น case-scoped: `observe_visibility(case_id, effective_role)` พร้อม plan `{case_id: {effective_role, point_ids}}`
3. ให้ M4 probe principal เป็น dependency ที่ caller ส่งอย่าง explicit และผูก allowed probe roles กับ RunPlan/frozen ที่อนุมัติ แทน default ที่ mint verified principal จาก raw role

ยังควรแยก provider/oracle คนละ client/session ตามเดิม เพราะเป็น independence boundary ที่มีประโยชน์

## Findings

### B1 — `observed_target_identity()` สะท้อน handle ไม่ได้พิสูจน์ endpoint ของ client ที่ยิง query จริง (blocker)

ตำแหน่ง: `p2_m4_qdrant.py:66-79`, `p2_m4_qdrant.py:100-116`; query paths `p2_m4_qdrant.py:85`, `p2_m4_qdrant.py:121-122`, `p2_m4_qdrant.py:154-155`; runner check `p2_m4_runner.py:155-162`

เส้นทางจริง:

```text
adapter ถูกสร้างด้วย injected client ซึ่งมี target ของตัวเอง
→ bind(handle) แค่ copy dict ไป self._bound
→ observed_target_identity() คืน collection_id/endpoint จาก self._bound
→ runner เทียบกับ handle เดิม จึงผ่านโดยนิยาม
→ query/scroll กลับใช้ self._client ซึ่งไม่เคยถูก bind หรือ cross-check กับ endpoint นั้น
```

Codex fault probe สร้าง client ที่ประกาศ target `http://production:6333` แล้ว bind handle `http://isolated:6333`; ผลคือ adapter รายงาน:

```text
client_endpoint           = http://production:6333
adapter_claimed_identity  = {collection_id: isolated-coll, endpoint: http://isolated:6333}
```

ผลกระทบ: provider/oracle อาจ query collection ชื่อเดียวกันบน endpoint อื่น—including production—แต่ B3 runner cross-check ยังผ่านและ evidence อ้าง isolated handle ผิดตัว นี่ชน trust boundary หลักของ M4 โดยตรง

แก้ขั้นต่ำ:

- รับ `client_factory`/session factory แทน ready-made client; `bind(handle)` ต้องสร้าง client จาก exact `handle["endpoint"]`
- session ที่ใช้ `query_points`/`scroll` ต้องคืน immutable observed target identity ของตัวเอง และ adapter reject ถ้าไม่ตรง handle
- rebind ต้องสร้าง session ใหม่หรือ fail; ห้ามเพียงเปลี่ยน `_bound` ขณะที่ client เดิมยังชี้ endpoint เก่า
- provider/oracle ต้องได้คนละ session และเพิ่ม negative test: production-bound/mismatched client + isolated handle ต้อง abort ก่อน seed

### B2 — oracle plan key ด้วย role แต่ OracleProof/frozen เป็นราย case จึงรองรับหลาย case ต่อ role ไม่ได้ (blocker)

ตำแหน่ง: `p2_m4_qdrant.py:93-105`, `p2_m4_qdrant.py:129-147`; runner call `p2_m4_runner.py:167-185`; per-case validator `p2_eval.py:743-783`; fixture `test_p2_m4_qdrant.py:133-150`

`QdrantM4Oracle.observe_visibility()` รับเพียง `effective_role` และอ่าน ID ชุดเดียวจาก `observation_plan[role]` แต่ runner นำผลไปติด `case_id_sha256` ของทุก case หาก role เดิมมีสอง query ที่ authorized/sentinel pair คนละชุด ทั้งสอง case จะได้ observation เหมือนกัน และอย่างน้อยหนึ่ง case fail exact frozen comparison

fault probe ใช้สอง case ของ role `qc`:

```text
case-1 expected authorized=A, sentinel=S
case-2 expected authorized=C, sentinel=T
observation_plan[qc]=[A,S]
```

ผลจริง:

```text
same_role_observations_equal = True
validator = observed_authorized_pairs != frozen
            observed_sentinel_pairs != frozen
```

test integration ปัจจุบันผ่านเพราะมีหนึ่ง case ต่อ role (`qc` หนึ่ง, `sales` หนึ่ง) จึงไม่ครอบเส้นนี้

แก้ขั้นต่ำ:

- เปลี่ยน port เป็น `observe_visibility(case_id, effective_role)`
- key observation plan ด้วย case identity และเก็บ expected role ร่วม เช่น `{case_id: {effective_role, point_ids}}`
- adapter ต้อง reject case หาย, role ไม่ตรง entry, duplicate/empty point set และ point หายจาก collection
- runner ส่ง `cid` ที่ผ่าน `_preflight_frozen_cases()` แล้ว และเพิ่ม regression สอง case role เดียวกันแต่คนละ authorized/sentinel set
- oracle ยังต้องอ่าน payload/classification จาก collection จริง; plan บอกเพียง universe ต่อ case จึงไม่ทำลาย independence และ exact OracleProof comparison จะผูกผลกลับ frozen อยู่แล้ว

### M1 — default principal factory mint `verified` principal จาก role ที่รับเข้ามาเอง (major)

ตำแหน่ง: `p2_m4_qdrant.py:27-33`, provider use `p2_m4_qdrant.py:84`, oracle use `p2_m4_qdrant.py:134`; trusted-access guard `p2_provider.py:21-35`

`default_principal_factory(role)` ตั้ง `authenticated=True`, `auth_mode="enforce"` และ `allowed_roles=(role,)` จาก input เดียวกัน ทำให้ `_assert_trusted_access()` ผ่านสำหรับ known role ทุกตัว—including `admin`—โดยไม่มีหลักฐาน identity/authorization ภายนอก

ใน M4 synthetic probe การจำลอง role เป็นสิ่งจำเป็น และ runner ได้ผูก role กับ frozen/RunPlan แล้ว จึงไม่จำเป็นต้องลาก user auth/OIDC เข้ามา แต่ provenance ควรเรียกสิ่งนี้ว่า **approved probe authorization** ไม่ใช่ authenticated principal ที่ adapter mint เองเงียบ ๆ

แก้ขั้นต่ำ:

- เอา default ออกและบังคับ inject `principal_factory`/prebuilt access map อย่าง explicit
- factory สำหรับ M4 ต้องจำกัด role ด้วย evaluated-role set ที่ผูก RunPlan/frozen และท้ายสุดผูก Data Owner sign-off hash; role นอกชุดต้อง fail
- ตั้งชื่อ/เอกสารชัดว่า evaluation-only และห้าม reuse เป็น production request adapter
- เพิ่ม test ว่า raw `admin` หรือ role นอก approved probe set ถูกปฏิเสธ แม้เป็น `KNOWN_ROLES`

## สิ่งที่ยืนยันว่าทำงานแล้ว

- provider ใช้ `compile_retrieval_filter`/Qdrant filter ก่อน retrieval และ `p2_provider.build_candidates()` ตรวจ payload ซ้ำแบบ fail-whole-batch
- backend จำลองที่เพิกเฉย filter ทำให้ `PermissionError` ก่อน scorer
- oracle unfiltered query ใช้ `query_filter=None` และ direct-scroll classification ไม่อ่าน expected classification จาก frozen โดยตรง
- observation plan ที่ omit/เปลี่ยน pair ไม่สามารถสร้าง valid OracleProof ได้ เพราะ validator เทียบ authorized/sentinel pair แบบ exact ต่อ case
- runner preflight ผูก case role/query กับ frozen ก่อน provision และ scorer boundary กัน sentinel ก่อน model

## Verification

targeted offline suites ที่ Codex รันจริงด้วย dependency environment ของโปรเจกต์:

```text
test_p2_m4_qdrant.py  20/20 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_m4_ops.py      32/32 PASS
test_p2_provider.py    22/22 PASS
test_policy.py         69/69 PASS
test_p2_m4.py          59/59 PASS
รวม                   246/246 PASS
```

เพิ่ม fault probe ชั่วคราวสองกรณี: mismatched client endpoint และ two-cases/same-role observation; probe ยืนยัน B1/B2 และถูกลบแล้ว

ไม่ได้รัน full 20-suite `839/839`; ตัวเลขนั้นคงเป็นหลักฐานจาก handoff ไม่ใช่ผลรันใหม่ของ Codex

## Gate หลัง review

- Qdrant provider/oracle adapter provenance/isolation: **OPEN — FIX-THEN-GO (B1/B2/M1)**
- isolation/Docker + scorer adapter coding: **NO-GO** จน targeted re-review ปิด B1/B2 และ M1
- M4a real run: **NO-GO** จน adapter provenance/isolation review ผ่าน + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน sign-off + M4b + validated canary

