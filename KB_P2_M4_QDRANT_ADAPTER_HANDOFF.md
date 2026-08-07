# P2 — Qdrant provider + oracle adapter slice (injectable/offline) — ขอ adapter provenance/isolation review

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX11_CODEX_REREVIEW_EC7B7BE.md` — safety-pieces **GO/SHIP**, อนุมัติเขียน adapter slice (injectable/offline) แล้วนำกลับมา review provenance/isolation ก่อนรัน
> **เจ้าของงานเลือก:** provider + oracle ก่อน (RBAC core ของ M4 permission-leak proof)
> **pure/offline ทั้งหมด** — fake qdrant client · **รัน M4a จริงบน Qdrant/model/docker = ยัง NO-GO**
> ไฟล์ใหม่: `p2_m4_qdrant.py`, `test_p2_m4_qdrant.py`

## สิ่งที่สร้าง

M4 ports สองตัว (`ports.provider` / `ports.oracle`) แบบ **injectable client + runtime `bind(handle)`** ต่อยอด building block เดิม:

| Adapter | ทำอะไร | reuse / กลไก |
|---|---|---|
| **`QdrantM4Provider`** | `filtered_candidates(role, qv, limit)` → `[(point_id, rerank_text)]` เฉพาะ authorized | resolve role → **verified** `EffectiveAccess` (`resolve_effective_access`, enforce) → `p2_provider.build_candidates` (compile RBAC filter เดียวกับ production API + **fail-closed detector**: payload ที่ backend คืนต้อง match filter ซ้ำ ไม่งั้น fail ทั้ง batch) → project dict→tuple ; candidates ว่าง = AdapterError |
| **`QdrantM4Oracle`** | `unfiltered_topn(qv, limit)` (raw top-N รวม sentinel, **ไม่มี RBAC filter**) + `observe_visibility(role)` | **independent** client แยก ; `observe_visibility` = **direct-scroll** อ่าน payload จริงจากคอลเลกชัน → classify authorized/sentinel ด้วย `matches_policy` เอง (ไม่เชื่อ frozen) — `observation_plan={role:[point_id,...]}` บอกแค่ universe ต่อ case |

- ทั้งคู่ `bind(handle)` (จาก `iso.provision()`) + `observed_target_identity()` → `{collection_id, endpoint}` ให้ runner cross-check identity ก่อน seed (mismatch → runner abort)
- `point_id` str-coerce ตรง convention `build_candidates` (`str(p.id)`)
- injected client interface: provider = `query_points(collection_name, query, query_filter, limit, with_payload)` ; oracle = `query_points(...)` + `scroll(collection_name, with_payload, limit, offset)`

## คุณสมบัติเชิงความปลอดภัยที่พิสูจน์ (offline)

- **provider ไม่รั่ว**: sentinel (score สูงสุด) ถูก RBAC filter ก่อน retrieval → ไม่เข้า candidates → ไม่ถึง scorer ; ถ้า backend รั่ว sentinel (bypass filter) → `build_candidates` detector ยิง `PermissionError` ผ่าน adapter (fail batch ไม่ drop เงียบ)
- **oracle independent + tamper-detect**: `observe_visibility` classify จาก payload จริง → ตรง frozen ; ถ้า sentinel ถูกแก้ payload ให้ authorize role → oracle เห็นเป็น authorized (ไม่ใช่ sentinel) → ไม่ตรง frozen → `validate_m4_oracle_proof` fail → publish refused
- **verified access เท่านั้น**: provider resolve ผ่าน `resolve_effective_access` + `build_candidates._assert_trusted_access` (principal.verified + role ∈ KNOWN_ROLES ∩ scope) — ไม่รับ raw role
- **fail-closed lifecycle**: unbound / role นอก plan / point ใน plan หายจากคอลเลกชัน / plan ว่าง → `AdapterError`

## end-to-end (offline) เสียบเข้า runner จริง

`test_p2_m4_qdrant.py` เอา `QdrantM4Provider`/`QdrantM4Oracle` (backed by fake qdrant client) เสียบเข้า **`RUN.run_m4a` ตัวจริง** (scorer/iso/clock = fake) → ได้ **PUBLISHED + evidence PASS** + bundle re-validate ผ่าน public gate + scorer เห็นเฉพาะ authorized text (ta/tb) ไม่เห็น sentinel (ts) → พิสูจน์ว่า adapter จริง plug เข้า contract ได้และ permission-leak proof ยังผ่าน

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2_m4_qdrant 20/20   test_p2_m4_runner 44/44   test_p2_m4_ops 32/32   test_p2_provenance 52/52
test_p2_provider 22/22   test_p2_m4 59/59   test_p2_m4_harness 47/47   test_p2 166/166   ... (20 suites)
```

- **รวมเครื่องนี้ (20 suites): 839/839** (เพิ่ม test_p2_m4_qdrant 20 ; ไม่มี regression)
- fake qdrant client เท่านั้น — **ไม่แตะ Qdrant/model/docker จริง**

## ยัง NO-GO / ยังไม่ได้ทำ (ต้อง review + ก่อนรันจริง)

- **รัน M4a จริง = NO-GO** จน (1) adapter provenance/isolation review ผ่าน (2) Data Owner sign-off แบบ hash-bound (3) validated real M4 PASS (4) validated P5b canary — AI จะไม่สร้าง/กรอก sign-off เอง
- **isolation/Docker adapter** (provision/marker/seed/teardown/observe) + **scorer/model adapter** (pinned bge-reranker) = ยังไม่เขียน (slice ถัดไป)
- `matches_policy` เป็น conservative model ไม่ใช่ Qdrant oracle เต็ม → conformance กับ Qdrant filter จริง = **P5b real-collection canary** (ยัง NO-GO)
- threat model: directory/collection identity binding เชิง fs/infra (rename→replace) = ยกให้ isolation adapter review

## ขอ Codex review (adapter provenance/isolation slice)

1. provider/oracle port contract ตรง runner/harness ครบไหม (bind/identity cross-check, filtered=authorized-only + fail-closed detector, oracle independent + tamper-detect, str-id convention)
2. `default_principal_factory` (verified single-role principal สำหรับ M4 internal context) เหมาะสมไหม หรือควรบังคับ inject principal จาก auth layer
3. `observation_plan` (บอก universe ต่อ case) กระทบ "independence" ของ oracle ไหม — oracle อ่าน classification จาก payload จริง แต่ถูกบอกว่าจะดู point ไหน
4. หลังผ่าน → เขียน isolation/Docker adapter ต่อ ; รัน M4a จริงยัง NO-GO จน sign-off

**Gate:** adapter provenance review = **รอบใหม่** · isolation/Docker + scorer adapter = ทำต่อหลัง review นี้ · M4a real run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
