# P1 fix round (ก่อน P5b) — ปิด Codex FIX-THEN-GO (`ae5d605` → รอบแก้)

> **สืบเนื่อง:** `KB_P1_CODEX_REVIEW_AE5D605.md` verdict **FIX-THEN-GO P5b**
> **ขอบเขต:** ยัง local + synthetic · **ไม่ deploy · ไม่ retag corpus จริง · ไม่ flip AUTH_MODE**

## Findings → fix → proof
| # | Finding | Fix | Proof |
|---|---|---|---|
| **B1** (blocker) | quarantine ไม่ revoke point รุ่นเก่า → ACTIVE→QUARANTINED / broad→narrow ยังค้นเจอของเก่า | `plan_source_replacement` + ingest **delete-by-source ก่อน upsert** (replace generation) | test_policy: ACTIVE→QUARANTINED sales ค้นไม่เจอ; broad→narrow qc ที่ถูกถอนเห็น 0, production ยังเห็น |
| **D1** (decision) | `allowed_roles:"qc"` (scalar) filter ได้แต่ผิด contract | canonical = **array-only**; scalar/`[]`/non-str → **QUARANTINED ที่ write boundary** (filter พิสูจน์ shape เองไม่ได้ — ยอมรับตรง) | test: scalar/empty/non-str → QUARANTINED |
| **M1** | `matches_policy` over-match (`True==1`, `1.0==1`) + overclaim "exact semantics" | `_type_exact_eq` **type-aware** (bool≠int, float≠int); null→ไม่ match; แก้ docstring เป็น "conservative model, conformance = P5b" | test: true/1.0/null/scalar table |
| **M2** | writer เก่า (ocr_reingest/retag_rbac/migrate) bypass gate | ingest = allowlisted writer เดียว; เพิ่ม `assert_legacy_writer_allowed` (fail-fast ถ้าเจอ policy-v1) ใน ocr_reingest+retag; `validate_stored_payload` กัน malformed v1 ใน migrate | test: guard raise/pass + stored-payload valid/invalid |
| **M3** | quarantine stdout อย่างเดียว + `[OK] Done!` แม้ไม่มี active | durable `ingest_manifest.jsonl` (source/hash/reason/version/run_id/ts/outcome); ingest exit 2 ถ้า active=0 (ไม่ success กำกวม) | test: manifest terminal outcome ACTIVE/QUARANTINED |
| **M4** | `int(level)` ยอม bool/str/float; ไม่ตรวจ range | strict: `confidentiality_level` = int 0..3 (ไม่ coerce), `collection_group` = non-empty str, ไม่งั้น QUARANTINED | test: true/'3'/2.9/9/123 → QUARANTINED |
| **N1** | fail-closed อยู่แค่ FastAPI startup; typo `"enfore"` ไหลเหมือน warn | `resolve_effective_access` treat auth_mode นอก `VALID_AUTH_MODES` เป็น enforce (fail-closed ใน pure boundary) | test: typo → 401/403 |

## เปลี่ยน claim ให้ซื่อตรง (ตาม M1)
- `matches_policy` = **conservative model** ของ Qdrant filter (type-aware) — **ไม่ใช่** exact oracle ทุก JSON type. **conformance กับ Qdrant จริง = P5b** (real test-collection semantic table)
- P1 lifecycle: replace-by-source (delete เก่า → upsert ใหม่) ปิด regression ระดับ source. **generation/staging/atomic-cutover เต็มรูป ยังเป็น deploy gate** (production ห้ามแก้ filter live ก่อน backfill — §8)

## ผลรัน (offline, ไม่มี stack)
```
test_policy.py           62/62   (auth + compiler + resolver + fake-Qdrant matrix + B1 lifecycle + M2/M3/M4/N1)
test_auth.py             11/11
test_eval_contract.py    64/64   (P5a regression — เขียว)
test_ask_eval_harness.py 11/11   (P5a regression — เขียว)
6 modules (policy/main/ingest/ocr_reingest/retag_rbac/migrate) py_compile OK
```

## ตรง "Minimum acceptance สำหรับรอบแก้ก่อน P5b"
- [x] ACTIVE→QUARANTINED role เดิมค้น point เก่าไม่เจอ (B1 lifecycle test)
- [x] ACL broad→narrow role ที่ถูกถอนค้นไม่เจอ (B1 lifecycle test)
- [x] scalar `allowed_roles` contract ชัด + ingestion strict canonical array (D1)
- [~] semantic table scalar/list/null/missing/type-mismatch — **ทำที่ fake (type-aware) แล้ว**; **real-Qdrant conformance = รันใน P5b** (ยอมรับตรงว่ายังไม่ใช่ oracle)
- [ ] P5b process `AUTH_MODE=enforce` preflight (no-key→401/out-of-scope→403/in-scope→200) — **เป็น P5b run**
- [x] writer allowlist ชัด (ingest เดียว; legacy fail-fast) — test collection แยกทำตอน P5b

## ยังเป็น P5b / deploy gate
- real-Qdrant semantic conformance run + P1-specific canaries (UNCLASSIFIED/missing-ACL/stale/quarantine) บน fresh test collection + `AUTH_MODE=enforce`
- production: staging/new-generation → validate point-count/ACL-coverage → atomic alias/cutover (ห้ามแก้ filter live ก่อน backfill)
- durable quarantine review workflow (UI/queue), full legacy-writer refactor ผ่าน shared builder

## ขอ Codex ยืนยัน
1. B1 replace-by-source ปิด 2 regression พอสำหรับ "ไม่ทิ้ง ACL เก่า" ระดับ source ไหม (ก่อน generation/alias เต็มรูปที่เป็น deploy gate)
2. type-aware `matches_policy` + การยอมรับตรงว่า real-Qdrant conformance = P5b — พอปิด M1 ไหม
3. D1 (scalar → quarantine ที่ write boundary, ไม่หวัง filter) เป็น contract ที่ยอมรับได้ไหม
4. **GO P5b** (fresh isolated collection + `AUTH_MODE=enforce` + synthetic canaries) ได้หรือยัง
