# P2 — Qdrant provider/oracle adapter FIX รอบ 1 : session-from-handle identity + case-scoped oracle + approved-probe auth

> **สืบเนื่อง:** `KB_P2_M4_QDRANT_ADAPTER_CODEX_REVIEW_2A907D7.md` (FIX-THEN-GO — 2 blocker + 1 major)
> **pure/offline ทั้งหมด** — fake session · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_m4_qdrant.py`, `test_p2_m4_qdrant.py` · **port change**: `p2_m4_runner.py` (observe_visibility case-scoped) + FakeOracle ใน `test_p2_m4_runner.py`/`test_p2_m4_ops.py`

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B1** | blocker | `observed_target_identity()` แค่สะท้อน handle ที่ copy มา — client (injected ready-made) อาจชี้ production แต่ runner cross-check ผ่านโดยนิยาม ; query ใช้ client ที่ไม่เคย cross-check | **รับ `client_factory(endpoint)` แทน ready-made client** — `bind(handle)` สร้าง session จาก **exact `handle["endpoint"]`** + cross-check `session.observed_target_identity(collection_id)` == handle มิฉะนั้น abort ; identity ที่คืน = ของ session ที่ยิง query จริง ; **rebind = fail** (session ผูก endpoint เดิม) ; provider/oracle คนละ session |
| **B2** | blocker | oracle `observe_visibility(role)` key ด้วย role แต่ OracleProof/frozen เป็น**ราย case** → role เดียวหลาย case (คนละ authorized/sentinel set) ได้ observation เหมือนกัน → อย่างน้อยหนึ่ง case fail | port เป็น **`observe_visibility(case_id, effective_role)`** ; plan **case-scoped** `{case_id: {effective_role, point_ids}}` ; reject case หาย / role ไม่ตรง entry / point_ids ว่าง/ซ้ำ / point หายจาก collection ; runner ส่ง `cid` (ผ่าน preflight แล้ว) |
| **M1** | major | `default_principal_factory(role)` mint verified principal จาก role ที่รับเข้ามาเอง → `_assert_trusted_access` ผ่านทุก KNOWN_ROLE (รวม admin) โดยไม่มีหลักฐานภายนอก | **เอา default ออก** — บังคับ inject `principal_factory` ; เพิ่ม `approved_probe_principal_factory(approved_roles)` = **evaluation-only approved probe authorization** ที่จำกัด role ด้วย approved set (ผูก `evaluated_roles` ของ RunPlan/frozen ; deployment จริงผูก Data Owner sign-off hash) ; role นอกชุด (รวม admin) → `PermissionError` |

## port change (จำเป็นเพื่อปิด B2)

- `p2_m4_runner.py`: `orac.observe_visibility(role)` → **`orac.observe_visibility(cid, role)`** (docstring อัปเดต) — runner มี `cid` ที่ผ่าน preflight (role/query bind frozen) แล้ว
- FakeOracle ใน `test_p2_m4_runner.py` / `test_p2_m4_ops.py`: `observe_visibility(self, case_id, role)` (สองไฟล์นี้ 1 case/role อยู่แล้ว → รับ case_id เพิ่ม)

## behavior tests (offline) ที่เพิ่ม/แก้

- **B1**: (1) client ชี้ production (`endpoint_override`) แต่ bind handle isolated → AdapterError ; (2) session ชี้คนละ collection → AdapterError ; (3) rebind → AdapterError ; identity มาจาก session
- **B2**: สอง case role `qc` (คนละ authorized/sentinel set) → observation **ต่างกันตาม case** ; role ไม่ตรง entry / case หาย / point_ids ว่าง/ซ้ำ → AdapterError
- **M1**: `filtered_candidates("admin")` (นอก approved set) → PermissionError แม้เป็น KNOWN_ROLE ; approved set ว่าง → AdapterError ; factory mint เฉพาะ role ใน set ; ไม่ inject principal_factory → AdapterError
- คงเดิม: authorized-only + fail-closed detector, unfiltered รวม sentinel, tamper detect, pagination, integration
- **integration**: adapter จริง (client_factory + case-scoped + approved-probe ผูก `PLAN["evaluated_roles"]`) เสียบเข้า `RUN.run_m4a` → **PUBLISHED/PASS** + bundle ผ่าน public gate

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2_m4_qdrant 31/31   test_p2_m4_runner 44/44   test_p2_m4_ops 32/32   test_p2_m4 59/59
test_p2_provider 22/22   test_policy 69/69   test_p2 166/166   ... (20 suites)
```

- **รวมเครื่องนี้ (20 suites): 850/850** (adapter 20→31 ; ไม่มี regression จาก port change)
- fake session เท่านั้น — **ไม่แตะ Qdrant/model/docker จริง**

## trust boundary สรุป (หลังแก้)

- **identity ผูก session ที่ยิง query จริง**: session สร้างจาก exact handle endpoint → `observed_target_identity` มาจาก session (production/mismatch → abort ก่อน seed) ; ไม่มี copy-handle self-attestation
- **oracle independence + case-scoped**: อ่าน payload/classify จาก collection จริง (ไม่เชื่อ frozen) ; plan บอกแค่ universe ต่อ case → exact OracleProof comparison ผูกผลกลับ frozen ; หลาย case/role ได้
- **authorization = approved probe (evaluation-only)**: verified probe principal เฉพาะ approved evaluated_roles ; ไม่ใช่ authenticated principal จริง ; ห้าม reuse เป็น production request adapter

## ยัง NO-GO / ถัดไป

- **isolation/Docker adapter** + **scorer/model adapter** = slice ถัดไป (หลัง review นี้ผ่าน)
- **รัน M4a จริง = NO-GO** จน adapter provenance/isolation review ผ่าน + Data Owner sign-off (hash-bound) + validated real M4 PASS + P5b canary — AI จะไม่สร้าง/กรอก sign-off เอง
- real-run: `client_factory` ต้องสร้าง `qdrant_client.QdrantClient(url=endpoint)` + session `observed_target_identity` ยืนยัน endpoint/collection จริง (เช่น ping server + get_collection) — ยกให้ isolation adapter review

## ขอ Codex review — **targeted re-review หนึ่งรอบ** (bounded scope, owner decision 2026-08-07)

ตรวจเฉพาะ **Definition of Done 3 ข้อ** ของ B1/B2/M1 เท่านั้น:
1. **(B1)** client ถูกสร้าง/bind จาก endpoint ใน isolated handle จริง — production/mismatch/rebind abort ; identity มาจาก session ที่ยิง query
2. **(B2)** oracle observation ผูก **ราย case** (`observe_visibility(case_id, role)`) — หลาย case/role เดียวได้ ; port change ใน runner สอดคล้อง
3. **(M1)** probe roles มาจาก **approved allowlist** ผูก RunPlan/frozen — ไม่ mint admin เอง

> **Freeze policy หลังผ่าน DoD (owner decision — บันทึกใน `STATUS.md` 2026-08-07):**
> **freeze safety/provenance v1 ; ห้ามขยาย hardening เพิ่ม** ; finding ใหม่ → **backlog** เว้นแต่พิสูจน์ได้ว่าเกิดหนึ่งใน:
> **(1)** ข้อมูลข้ามสิทธิ์ถึงโมเดล · **(2)** adapter แตะ production ได้ · **(3)** evidence รายงาน PASS เท็จ · **(4)** resource cleanup ล้มจนมีผลต่อการรันถัดไป
> — finding นอกสี่ข้อนี้ ให้ลง backlog ไม่บล็อกการเดินต่อ

**Gate (ปรับตาม owner decision 2026-08-07 — ดู `STATUS.md`):**
- adapter re-review รอบ 2 = **FIX-THEN-GO/GO** (bounded DoD) → ผ่านแล้ว **freeze v1**
- **M4a synthetic mechanics = GO** (corpus สังเคราะห์ + isolated Qdrant ; ไม่ต้อง sign-off) → ทำ isolation/Docker adapter + รัน synthetic ต่อได้
- **M4b / N-sweep / decision benchmark = NO-GO** จน Data Owner sign-off (hash-bound) + classification + human-reviewed labels
- **Production = NO-GO** จน auth + deployment approval + governance ; AI ห้ามสร้าง/กรอก sign-off เอง
