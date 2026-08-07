# P2 — SQLite hardening รอบ 11 : in-transaction row verify (ตัด post-COMMIT fresh-read failure surface)

> **สืบเนื่อง:** `KB_P2_M4_SAFETY_FIX10_CODEX_REREVIEW_74FFF70.md` (B1.1/B1.2/M1 CLOSED ; FIX-THEN-GO — 1 blocker B1.3)
> **pure/offline ทั้งหมด** — ยังไม่แตะ Qdrant/docker/model · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์ที่แก้: `p2_provenance.py`, `p2_m4_ops.py`, `test_p2_provenance.py`, `test_p2_m4_ops.py`

## Finding → fix → proof

| # | ระดับ | ช่องเดิม | Fix |
|---|---|---|---|
| **B1.3** | blocker | post-COMMIT defense (`_row_exists` fresh connection ที่เพิ่มรอบ 10) ถ้า **fresh read ล้ม** (OperationalError) หลัง COMMIT สำเร็จ → leak เป็น ordinary error ทั้งที่ row commit แล้ว → caller retry/สถานะไม่ตรง ledger (result-alignment ใหม่) | **ย้าย verification เข้า transaction เดิมก่อน COMMIT** (Codex option 1): หลัง INSERT ตรวจ `last_insert_rowid()` + `body_sha256` ในทรานแซกชันเดียวกัน — swallow (trigger/constraint) = ProvenanceError **pre-commit → rollback สะอาด** ; **ถอด fresh post-COMMIT read ออกจาก normal path** → COMMIT success = durability boundary ; ack-loss resolver คง fresh-verify เดิม (map read error → indeterminate อยู่แล้ว) ; wrapper STARTED map `ProvenanceIndeterminate → PROVENANCE_INDETERMINATE` (ไม่ใช่ FAILED retryable) |

## ทำไมทางนี้ปิดสนิท (ไม่ใช่ย้ายที่)

- normal append **ไม่มี fresh-connection read** อีกแล้ว → ไม่มี failure/ambiguity surface หลัง COMMIT (COMMIT ของ SQLite คือ durability boundary จริง)
- swallow ที่หลุด schema-verify → ตรวจเจอ **ในทรานแซกชัน ก่อน commit** → rollback → uncommitted (ledger ไม่ถูกแตะ)
- fresh-verify เหลือแค่ **ack-loss resolver** (กรณี COMMIT โยน exception) ซึ่ง catch `sqlite3.Error` → `indeterminate` อยู่แล้ว (ไม่ leak OperationalError)
- STARTED indeterminate → wrapper คืน `PROVENANCE_INDETERMINATE` + **ไม่ provision** → ไม่ชวน retry ทับ STARTED ที่อาจ commit แล้ว

## behavior tests (offline) ที่เพิ่ม/แก้

- **B1.3 low-level (1)**: trigger `RAISE(IGNORE)` ที่หลุด schema-verify (monkeypatch) → in-transaction verify จับ **pre-commit** (ProvenanceError) + rollback สะอาด (raw count = แค่ seed row)
- **B1.3 low-level (2)**: normal append + `_row_exists` monkeypatch ให้ raise → append **ยังสำเร็จ** (normal path ไม่พึ่ง fresh read) → พิสูจน์ตัด failure surface
- **B1.3 wrapper**: STARTED append raise `ProvenanceIndeterminate` → wrapper คืน `PROVENANCE_INDETERMINATE`/`provenance_started` + **ไม่ provision** (ไม่ใช่ FAILED retryable)
- คงเดิม: trigger/extra-index/application_id reject, WAL verify-only, COMMIT outcome (retryable/ack-lost/indeterminate), snapshot receipt, injective export

## ผลรัน (offline — เครื่องนี้มี qdrant_client/torch)

```
test_p2 166   test_p2_m4 59   test_p2_m4_harness 47   test_p2_m4_runner 44   test_p2_atomic 25
test_p2_fs_probe 12   test_p2_provenance 52   test_p2_m4_ops 32   test_p2_runplan 95   test_p2_pin 14
test_p2_adapter 22   test_p2_dockerbuild 41   test_policy 69   test_eval_contract 64   test_ask_eval_harness 12
test_auth 11   test_p5b_fixtures 11   test_p2_provider 22   test_p2_harness 21
```

- **รวมเครื่องนี้ (19 suites): 819/819** (รอบ 10 = 817 → provenance +1, m4_ops +1)
- provenance suite **เสถียร 6/6 รันซ้ำ**
- ไม่ได้รัน Docker/Qdrant/model/M4a จริง

## contract สรุป (append durability)

- **INSERT + in-transaction verify (last_insert_rowid + body_sha256) → COMMIT** ; COMMIT success = durable, ไม่มี post-check
- pre-commit fail (swallow/validation/integrity) → rollback → uncommitted (ประเภท exception เดิม, retry ได้)
- COMMIT โยน exception → ack-loss resolver: tx active → uncommitted ; ack lost → verify row → committed/uncommitted/**indeterminate** (fresh read error → indeterminate)
- wrapper: STARTED/terminal indeterminate → `PROVENANCE_INDETERMINATE` (operator ตรวจ) ไม่ใช่ clean retryable FAILED

## ขอ Codex review (safety-pieces slice รอบ 12)

1. in-transaction row verify (B1.3) — ตัด post-COMMIT fresh-read failure surface จริงไหม ; STARTED wrapper mapping (indeterminate ≠ FAILED) ครบไหม
2. หลังผ่าน → เริ่ม **Qdrant/docker adapter slice** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off (hash-bound)

**Gate:** safety-pieces review รอบ 12 = **FIX-THEN-GO/GO** · Qdrant/docker adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
