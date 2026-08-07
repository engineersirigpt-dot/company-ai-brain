# Codex bounded re-review — P2 M4 Qdrant adapter FIX (`a2c0f9e`)

วันที่รีวิว: 2026-08-07

ขอบเขตที่เจ้าของงานล็อกใน `a79d0b8`: ตรวจเฉพาะ Definition of Done ของ B1/B2/M1 ใน `KB_P2_M4_QDRANT_ADAPTER_FIX_HANDOFF.md` — client/session target binding, case-scoped oracle และ approved probe allowlist

ข้อจำกัดที่รักษาไว้: pure/offline; ไม่แก้ source/tests/`STATUS.md`, ไม่แตะ Qdrant/Docker/model และไม่รัน M4a จริง; ไม่เปิด hardening finding ใหม่ เว้นแต่พิสูจน์ leak-to-model, touch-production, false-PASS หรือ cleanup failure ที่กระทบ run ถัดไป

## Verdict

**GO/SHIP — B1/B2/M1 CLOSED ตาม bounded DoD**

อนุมัติให้ **freeze safety/provenance v1** ตาม owner decision และเดินต่อ isolation/Docker adapter slice ได้ ไม่พบ blocker/major ใหม่ในขอบเขตที่ล็อก และไม่พบหนึ่งในสี่ exception ที่อนุญาตให้เปิด hardening gate ใหม่

M4a synthetic mechanics อยู่ใน track **GO โดยไม่ต้อง Data Owner sign-off** ตาม `STATUS.md`/`a79d0b8`; การรันจริงต้องรอ concrete isolation/Docker/scorer adapters พร้อมและผ่าน review ของ slice นั้น ส่วน M4b/N-sweep/ข้อมูลจริงยังคง NO-GO ตาม gate ด้านล่าง

## Simpler-alternative check

ทางแก้ปัจจุบันเป็น surface ต่ำสุดที่ปิดสามช่องเดิมแล้ว:

- session factory สร้าง target จาก handle โดยไม่เพิ่ม discovery service
- runner ส่ง case ID ที่มีอยู่แล้ว ไม่สร้าง oracle state machine ใหม่
- approved probe factory แยกจาก production auth โดยไม่ลาก OIDC/API-key เข้ามาใน synthetic evaluation

ไม่พบทางที่เล็กกว่านี้ซึ่งยังรักษา target binding, per-case evidence และ explicit probe authorization ครบ

## DoD verification

### B1 CLOSED — session ที่ query ถูกสร้างจาก exact isolated endpoint และ identity ไม่ใช่ handle echo

ตำแหน่ง: `p2_m4_qdrant.py:57-94`, provider query `p2_m4_qdrant.py:120-129`, oracle query/scroll `p2_m4_qdrant.py:167-176`, `p2_m4_qdrant.py:201-212`; runner check `p2_m4_runner.py:155-162`

เส้นทางจริง:

```text
bind(handle)
→ client_factory(handle["endpoint"])
→ session.observed_target_identity(handle["collection_id"])
→ exact compare กับ handle
→ เก็บ session + observed identity ชุดเดียวกัน
→ filtered query / unfiltered query / scroll ใช้ session นั้นผ่าน _require()
```

`bind()` ซ้ำถูกปฏิเสธ จึงเปลี่ยน handle โดยคง session เก่าไม่ได้ Negative tests ยืนยัน client ที่รายงาน production endpoint, collection อื่น และ rebind ถูก abort ก่อน runner seed

ข้อสรุปนี้อนุมัติ **port/interface และ injectable implementation** ที่ commit นี้; concrete real-Qdrant session ใน slice ถัดไปยังต้องสร้าง `QdrantClient` จาก endpoint ที่ได้รับและพิสูจน์ identity ผ่าน session เดียวกันตาม contract นี้

### B2 CLOSED — oracle observation ผูก case และ runner ส่ง case identity จริง

ตำแหน่ง: plan validation `p2_m4_qdrant.py:132-147`, observation `p2_m4_qdrant.py:178-199`, runner call `p2_m4_runner.py:167-185`

`observation_plan` เปลี่ยนเป็น `{case_id: {effective_role, point_ids}}`; adapter reject case หาย, role ไม่ตรง, point set ว่าง/ซ้ำ และ point หายจาก collection ก่อนสร้าง visibility result ขณะที่ runner ส่ง `cid` ซึ่งผ่าน `_preflight_frozen_cases()` แล้วเข้า `observe_visibility(cid, role)`

Regression test สอง case ของ role `qc` แต่คนละ authorized/sentinel set คืน observation คนละชุดจริง จึงปิดข้อจำกัดเดิมที่หนึ่ง role ใช้ได้เพียงหนึ่ง case Port change ใน runner/fake ports สอดคล้องและ downstream OracleProof ยังคงผูก `case_id_sha256` แบบ exact

### M1 CLOSED — ไม่มี default principal mint; probe role ต้องผ่าน approved set

ตำแหน่ง: `p2_m4_qdrant.py:32-48`, provider constructor/use `p2_m4_qdrant.py:111-125`, oracle constructor/use `p2_m4_qdrant.py:157-187`

provider/oracle บังคับ inject callable `principal_factory`; ไม่มี default จาก raw role อีกแล้ว `approved_probe_principal_factory()` สร้าง principal ได้เฉพาะ role ใน frozen approved set และ role นอกชุดถูกปฏิเสธก่อน resolve/build candidates

integration test สร้าง factory จาก `PLAN["evaluated_roles"]`; runner ผูก case roles กับ frozen/RunPlan ก่อน provision อยู่แล้ว จึงไม่มี caller-controlled role หลุดเข้ามาเพิ่มเอง Test ยืนยัน `admin` ซึ่งไม่อยู่ approved set ถูกปฏิเสธ แม้เป็น `KNOWN_ROLE`

นี่เป็น **evaluation-only probe authorization** ไม่ใช่ production authentication และไม่อนุมัติให้ reuse adapter นี้เป็น public request path

## Exception audit ตาม freeze policy

- ข้อมูลข้ามสิทธิ์ถึงโมเดล: **ไม่พบ** — provider postcondition และ harness sentinel guard คงทำงาน
- adapter แตะ production ได้: **ไม่พบใน injectable contract** — mismatched session identity abort; concrete session ยังเป็นงาน slice ถัดไป
- evidence รายงาน PASS เท็จ: **ไม่พบ** — case-scoped observation และ public bundle validator ผ่านจาก body จริง
- cleanup failure กระทบ run ถัดไป: **ไม่พบจาก diff/targeted path นี้**

ไม่เปิด finding นอก bounded DoD ตาม owner decision

## Verification

targeted offline suites ที่ Codex รันจริงด้วย dependency environment ของโปรเจกต์:

```text
test_p2_m4_qdrant.py  31/31 PASS
test_p2_m4_runner.py   44/44 PASS
test_p2_m4_ops.py      32/32 PASS
test_p2_provider.py    22/22 PASS
test_policy.py         69/69 PASS
test_p2_m4.py          59/59 PASS
รวม                   257/257 PASS
```

ไม่ได้รัน full 20-suite `850/850`; ตัวเลขนั้นคงเป็นหลักฐานจาก handoff ไม่ใช่ผลรันใหม่ของ Codex

## Gate หลัง review

- adapter B1/B2/M1: **CLOSED / GO**
- safety/provenance v1: **FREEZE** — finding ใหม่ลง backlog เว้นแต่เข้า exception 4 ข้อใน `STATUS.md`
- isolation/Docker + scorer adapter: **GO ให้เริ่ม slice ถัดไป**
- M4a synthetic mechanics: **GO track**; ไม่ต้อง Data Owner sign-off และไม่ใช้เอกสารบริษัท
- M4b / N-sweep / decision benchmark / ข้อมูลจริง: **NO-GO** จน Data Owner sign-off แบบ hash-bound + classification + human-reviewed labels
- production: **NO-GO** จน auth + deployment approval + governance ครบ; AI ห้ามสร้าง/กรอก sign-off หรือเปลี่ยน label เป็น human-reviewed เอง

