# P5a rev2.1 — ปิด Codex FIX-THEN-GO รอบสอง → handoff review

> **สืบเนื่อง:** `KB_P5A_REV2_CODEX_REVIEW.md` (commit `fd8af00`) — B1/M1 ปิดแล้ว, เหลือ strict shape / exhaustive roles / UUID / auth-403 / two-track
> **ขอบเขต:** local + synthetic · ยังไม่ deploy · NO-GO Qdrant prod · end-to-end run จริง = P5b

## แก้ตาม "Minimum acceptance สำหรับ rev2.1" ครบ 6/6
| acceptance | fix | proof |
|---|---|---|
| missing `results`/`point_id` บน negative → MALFORMED/exit 1 | `extract_points` **บังคับมี key** (เลิก `.get(key,[])`) + `require_point_id` (ว่าง/ซ้ำ = raise); `validate_search_response`/`validate_ask_response` แยกกัน | unit `{}→ValueError`, `[{}]→ValueError`; harness #5,#6 (negative `{}`/`[{}]` → exit 1) |
| manifest validate + ทุก known role allow/deny | `known_roles` ใน manifest; `run_permission_suite` ยิง positive **ทุก** authorized + negative **ทุก** `known-authorized`; `validate_manifest` (uuid, unique, zero-denied, role นอก known) fail ก่อนยิง API | unit validate_manifest×5; harness #9; report `+3/-8..+6/-5 roles` (รวม 11 ทุก canary) |
| point_id เป็น UUID + map authorized_roles→allowed_roles | manifest ใช้ `uuid5(DNS,'kb-canary.'+name)`; `is_uuid` validate; `canary_name` เก็บชื่ออ่านง่าย; `_doc` ระบุ P5b ต้อง map → payload `allowed_roles` | unit `is_uuid`; manifest ทั้ง 7 ผ่าน validate |
| auth spoof **403 เท่านั้น** จึง verified; 401/unverified ไม่ใช่ pass | `auth_gate_status` (ทุก spoof ต้อง `status==403`); 401→FAILED; ไม่มี spoof→UNVERIFIED; `security_exit_code(require_auth)` ต้อง VERIFIED | unit auth×4 + exit×7; harness #7 (401→FAILED→exit1), #8 (UNVERIFIED→exit1, retrieval-only→exit0) |
| แยก permission gate จาก /ask quality | `/ask` ออกจาก security exit code ทั้งหมด; มี `ask_quality_report` (รายงาน hit/honesty/dangling/cited ครบ) + `quality_gate` แยกสำหรับ P5b | harness #10 (/ask malformed → security ยัง exit 0 แต่ quality_gate fail) |
| harness tests ครอบ seam (ไม่ใช่แค่ normalized fake) | แยก `normalize_response(path,status,exc,resp)` ออกจาก network → harness ป้อน raw `{}`/`[{}]` ผ่าน validator จริง | harness #5,#6 ผ่าน `normalize_response` |

## Closure matrix (จากรีวิวเดิม)
| Finding | เดิม | ตอนนี้ |
|---|---|---|
| B1 missing key/point_id false-green | OPEN(M3)/PARTIAL | **CLOSED** — strict shape, `{}`/`[{}]`→MALFORMED→INCONCLUSIVE |
| B2 manifest ไม่ exhaustive | PARTIAL | **CLOSED** — ทุก known_role allow+deny ต่อ canary + validate |
| B3 point_id ใช้ Qdrant ไม่ได้ | PARTIAL | **CLOSED** — UUID (uuid5 deterministic) + validate + mapping note |
| M1 auth 403 proof | OPEN | **CLOSED** — 403-only VERIFIED; 401/UNVERIFIED = fail (auth-gated) |
| M2 /ask quality ไม่ถูกวัด/ปน gate | OPEN | **CLOSED** — two-track: report ครบ + quality_gate แยก, ไม่ปน security |
| B2(core)/B4/M4/N1 | CLOSED เดิม | ยัง CLOSED |

## ผลรัน (offline, ไม่มี stack)
```
test_eval_contract.py      64/64 passed   (pure decision core)
test_ask_eval_harness.py   11/11 passed   (seam ผ่าน normalize_response + assert exit_code)
```
เขียวได้เส้นเดียว: ทุก canary PASS (positive ทุก authorized เจอ + negative ทุก denied ไม่เจอ) **และ** auth VERIFIED (spoof 403). deny/empty/malformed/401/no-spoof = exit 1 ทั้งหมด.

## ยังไม่ทำ (จงใจ — P5b, ตามลำดับที่ Codex แนะ)
- **P5b end-to-end:** ingest canary 7 ตัวเข้า test collection (map `authorized_roles`→payload `allowed_roles`, point_id=UUID) + ออก role-scoped key ครบ 11 role → รัน `ask_eval.py` กับ stack แยก config. ยังไม่แตะตาม NO-GO
- **config-conformance test แยก:** เทียบ manifest ↔ `rbac_config.COLLECTIONS` จับ drift (ยังไม่ทำ — เป็นชุดแยกตาม B3 เดิม)
- `confidentiality_level` ในการตัด leak — เว้นจนมี trusted caller clearance

## ขอ Codex ยืนยัน
1. rev2.1 ปิด B1-B3/M1(acceptance) ครบตาม "Minimum acceptance" ไหม — เหลือ seam ไหนที่ยัง false-green ได้
2. ลำดับที่แนะ (rev2.1 → implement P1 local+synthetic → P5b บน test collection → ประกาศ P1 hardened) — **GO เริ่ม P1 ได้ไหม** หลัง contract ปิด
3. P1 scope ที่ตกลง: **auth + policy compiler + effective ACL (fail-closed)** ไม่ใช่ role AND conf AND group ตรง ๆ — ขอ confirm นิยาม "policy compiler / effective ACL" ที่คาดหวังก่อนลงมือ
