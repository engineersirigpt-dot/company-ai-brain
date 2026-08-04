# Codex Final Closure Review — P1 / P5b (`d120935`)

**วันที่:** 2026-08-04  
**Target:** `KB_P5B_FIX_EVIDENCE_HANDOFF.md`, commit `d120935`  
**Scope:** final closure ของ P1 เฉพาะ PoC local + synthetic + single-writer  
**ไม่ได้อนุมัติ:** production deploy, corpus จริง, concurrent writer, OIDC หรือ cloud egress

## Verdict: **GO / CLOSE P1 PoC TRACK**

อนุญาตให้ประกาศสถานะนี้ได้:

> **P1 hardened — PoC local/synthetic/single-writer**

G1/M1/M2/M3/N1 ปิดเพียงพอตาม acceptance เดิมแล้ว ไม่พบ blocker หรือ product fail-open ใหม่จากเส้นทางที่รีวิว และเดิน **P2 reranker offline/shadow** ต่อได้

คำว่า hardened นี้จำกัดเฉพาะ test stack และ policy-v1 path ที่พิสูจน์แล้ว ไม่แปลว่า production-ready และไม่ลด deploy gates ที่ระบุไว้ท้าย handoff

## Intent / simpler-boundary check

เป้าหมายจริงคือพิสูจน์ว่า identity ที่เชื่อถือได้ถูกลดเป็น effective role ฝั่ง server แล้ว policy filter ถูกบังคับก่อน retrieval รวมถึง malformed/stale/quarantined data ต้องไม่เปิดประตู

ขอบเขตหลักฐานที่เล็กและชัดที่สุดคือ:

- direct Qdrant `scroll` เป็น authoritative check ของ filter semantics รวม malformed payload
- API canary เป็น authoritative check ของ auth/effective-role/wiring
- resolver-driven UNCLASSIFIED point เป็น end-to-end check ของ default-deny mapping

run2 มีหลักฐานทั้งสามชั้นนี้แล้ว จึงไม่จำเป็นต้องเพิ่มระบบ production หรือข้อมูลจริงเพื่อปิด P1 PoC

## Closure verification

### G1 — CLOSED: default-deny ผ่าน resolver และ API จริง

เส้นทางที่ trace:

`p5b_default_deny.py:48-54` → `resolve_document_policy(..., get_rbac)` → `store_in_qdrant(..., rbac_lookup=get_rbac)` → policy-v1 payload ใน Qdrant → `/search` → auth/effective role → compiled filter

หลักฐาน `evidence/run2/default_deny.txt` แสดง:

- unknown source ถูกเก็บเป็น ACTIVE `UNCLASSIFIED`, `allowed_roles=[admin]`
- admin พบ exact point; อีก 10 roles ไม่พบ
- missing ACL, stale schema และ quarantine ไม่มี role ใดพบรวม admin
- suite `fails=0`

จุดนี้ไม่ได้สร้าง payload admin-only ด้วยมือ จึงพิสูจน์ default-deny mapping ที่ต้องการจริง

### M1 — CLOSED: marker ถูกอ่านกลับและเทียบ exact run id

เส้นทางที่ trace:

- seeder สร้าง marker point จาก `P5B_RUN_ID` (`p5b_seed.py:53-60`)
- resume paths อ่าน marker จริงด้วย `read_marker()` (`p5b_seed.py:19-25`)
- guard ยอม target ไม่ว่างเฉพาะ `stored_marker == expected_marker` และ expected ต้องไม่ว่าง (`policy.py:276-292`)
- lifecycle/default-deny อ่าน marker จริงก่อน mutation (`p5b_lifecycle.py:47-50`, `p5b_default_deny.py:43-45`)
- `--recreate`/`--allow-nonempty` ถูกถอดออก

ผลนี้ปิดปัญหา boolean self-assertion เดิม และยังคง fail-closed เมื่ออ่าน marker ไม่ได้

### M2 — CLOSED สำหรับ PoC evidence

`evidence/run2/` เก็บผล A/B/G1/C/auth, structured permission result และ `run_metadata.json`; Qdrant ถูก pin ด้วย digest เดียวกับ compose และ metadata ทุก gate เป็น exit 0 ขณะที่ `permission_eval.json` เป็น 7/7 PASS, auth VERIFIED, exit 0

หลักฐานเพียงพอสำหรับการย้อนตรวจ PoC run และไม่มี plaintext key ถูก commit

### M3 — CLOSED สำหรับ immutable P5b image

- `Dockerfile:21` copy `rbac_config.py`, `policy.py`, `qdrant_filter.py` เข้า image
- `docker-compose.p5b.yml` ไม่มี source-code bind mount แล้ว
- `health.txt`, auth matrix และ API canary มาจาก image ดังกล่าว

จึงปิดข้อค้างว่า P5b ผ่านเพราะ mount source จาก host อย่างเดียว แต่ production packaging/smoke ยังเป็น gate แยกตามที่ handoff ระบุถูกต้อง

### N1 — CLOSED

ถ้อยคำถูกลดจาก “Qdrant จริงทุกจุด” เป็น “ทุก case ใน fixture matrix ปัจจุบัน” และระบุชัดว่าไม่ใช่ oracle ของทุก JSON/Qdrant edge

## Non-blocking follow-ups

รายการนี้ไม่เปิด P1 กลับและไม่ block P2 แต่ควรเก็บก่อนใช้ P5b เป็น regression gate ระยะยาว

### N2 — source provenance ยังไม่ใช่ bit-for-bit clean-commit proof

`evidence/run2/run_metadata.json:4-5` บันทึก `git_head_at_run=7c8cbb3` แล้วอธิบายว่า runtime working tree ถูก commit ภายหลังเป็น `d120935` จึงเพียงพอเชิง PoC แต่ยังผูก API image id เข้ากับ clean source commit แบบ cryptographic ไม่ได้

รอบถัดไปให้ commit ก่อน build แล้วใส่ `org.opencontainers.image.revision=<HEAD>`/source tree hash ใน image และ metadata จากนั้น assert label ตรง HEAD ก่อนรัน gate ไม่ต้อง rerun run2 เพื่อปิด P1 ตอนนี้

### N3 — negative API probe ไม่ควรเป็นหลักฐานเดี่ยวเมื่อใช้ vector `top_k=10`

`p5b_default_deny.py:62-66` ตัดสิน “ไม่พบ” จากผล top 10 หาก role มองเห็นเกิน 10 จุด จุดที่ leak อาจอยู่ลำดับ 11 แล้วเกิด false-clean ได้ นอกจากนี้ `roles = sorted(keys)` (`:59`) จะไม่ฟ้องว่า registry ขาด role หาก environment ส่ง key มาไม่ครบ

run2 ยังไม่เสีย closure เพราะ:

- direct conformance A ใช้ `scroll` และพิสูจน์ missing/stale/quarantine แบบไม่พึ่ง ranking
- raw auth/eval และ `auth_matrix.txt` ยืนยัน run นี้มี key ครบ 11 roles จริง
- UNCLASSIFIED positive อยู่ใน known bounded fixture corpus และ admin พบ exact id จริง

ก่อน reuse ให้ assert `set(keys) == KNOWN_ROLES` และให้ malformed no-match ใช้ direct filtered scroll เป็น authoritative result หรือรันบน collection ที่จำนวน authorized candidates ต่ำกว่า limit อย่างพิสูจน์ได้

### N4 — runbook test counts เก่า

`P5B_RUNBOOK.md:69-70` ยังเขียน 8/8 และ 68/68 แต่ผลใหม่คือ 11/11 และ 69/69 ควรแก้ตัวเลขเพื่อไม่ให้ handoff สับสน

### N5 — production restart note ต้องเปลี่ยนเมื่อถึง deploy track

`docker-compose.yml:29` ยังบอกว่า `git pull + docker restart` พอ แต่ root modules `policy.py`/`qdrant_filter.py` อยู่ใน image ไม่ได้ bind-mount ดังนั้นการแก้สองไฟล์นี้ต้อง rebuild image ข้อนี้คงอยู่ใน production packaging gate ห้ามใช้ restart-only หลัง P1

## Decision สำหรับ P2

**GO P2 — reranker offline/shadow + hybrid arm** โดยมี guardrail:

1. ทุก arm ต้องใช้ effective ACL/Qdrant filter **ก่อน**ได้ candidates; reranker ห้ามเห็น point นอกสิทธิ์แม้เป็น shadow log
2. ใช้ candidate set เดียวกันเปรียบเทียบ dense baseline กับ reranked/hybrid เพื่อไม่ให้ permission behavior ต่างกัน
3. แยก metric retrieval quality (Hit@k/MRR/citation/latency) ออกจาก permission suite; `TOP_K=10` ของ P5b ห้ามนับเป็น retrieval-quality result
4. คง synthetic permission canaries และ leak=0 เป็น regression gate ของทุก arm
5. ยังไม่แตะ production collection/cutover และไม่ส่ง context ไป cloud ใน P2 offline/shadow

## Shared-status handoff

หลังรับ verdict นี้ ให้ Claude อัปเดต `STATUS.md` ด้วยข้อความ exact scope:

> `P1 permission policy hardened สำหรับ PoC local/synthetic/single-writer ที่ d120935; evidence/run2 PASS. ยังไม่ production-ready และ deploy gates ทั้งหมดยังเปิด.`

จากนั้นเปิด P2 เป็นงาน in-progress ได้ โดยห้ามเปลี่ยนข้อความ production reality ปัจจุบัน (`AUTH_MODE=warn`, user auth/OIDC และ egress ยังไม่ปิด)

## Verification note

Codex trace committed code end-to-end, parse structured evidence และตรวจ SHA/digest/secret surface ใน repo; ไม่ได้ rerun Docker/Python อิสระใน environment นี้ จึงรับรองขอบเขตจาก retained run2 evidence + static path verification ไม่ได้อ้างว่า reproduce infrastructure run ซ้ำอีกครั้ง
