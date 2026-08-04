# P5b Results — real-Qdrant conformance + lifecycle + auth (PASS)

**วันที่:** 2026-08-04 · **run:** `company_docs_p5b_run1` · **stack:** `docker-compose.p5b.yml` project `brain_p5b`
**ขอบเขต:** local + synthetic + single-writer · isolated (Qdrant :6401, API :8402, volume/network แยก) · `AUTH_MODE=enforce`

## สรุป: **P5b PASS ครบทุก gate** → รองรับการประกาศ **P1 hardened เฉพาะ PoC (local/synthetic/single-writer)**

| Gate | ผล | หลักฐาน |
|---|---|---|
| Interlock (P5B-B1) | ✅ | Qdrant/API isolated (network `brain_p5b_default`, volume `brain_p5b_qdrant_data_p5b`, port 6401/8402); seeder ปฏิเสธ prod name |
| A — filter conformance | ✅ PASS | real Qdrant `scroll` ด้วย compiled filter เดียวกับ API |
| B — writer lifecycle | ✅ PASS | เรียก `store_in_qdrant()` จริง |
| C — API auth + canary | ✅ PASS | curl matrix + `ask_eval.py` (auth VERIFIED) |

---

## A — Real-Qdrant filter conformance (`p5b_conformance.py`)
```
[ok] role=qc       match=2 expect=2      (list-qc + scalar-qc)
[ok] role=admin    match=1 expect=1      (list-qc เท่านั้น)
[ok] role=sales    match=0 expect=0
[CONFORMANCE] roles=['qc','admin','sales'] fails=0 (PASS)
```
ยืนยันกับ Qdrant **ของจริง** (ไม่ใช่ model จำลอง):
- `allowed_roles=["qc","admin"]` → qc+admin match · `allowed_roles="qc"` (scalar) → **qc match** (store-integrity → ต้อง quarantine ที่ write boundary, D1)
- `acl_schema_version=true` / `1.0` → **ไม่ match** (type-aware M1 ถูกต้อง) · `null`/missing/unknown-role/stale → ไม่ match · `QUARANTINED` → **admin ก็ไม่เห็น**
→ ปิดข้อค้าง Codex: conservative model (`matches_policy`) **สอดคล้อง Qdrant จริงทุกจุด**

## B — Writer lifecycle (`p5b_lifecycle.py`, `store_in_qdrant` จริง)
```
[STORE] active=1 ... replaced_sources=1        # gen1 SALES active → sales เห็น
[QUARANTINE] 1 chunks ... reason=allowed_roles_not_str_list:str
[STORE] active=0 quarantined=1 replaced_sources=1   # gen2 scalar → quarantine + revoke เก่า
[STORE] active=1 ... (broad) → qc เห็น
[STORE] active=1 ... (narrow) → qc ถูกถอน
[LIFECYCLE] regressions=2 fails=0 (PASS)
```
- ACTIVE→QUARANTINED: role เดิม (sales) เห็น **0** point · broad→narrow: revoked (qc) เห็น **0**, retained (production) ยังเห็น
- serial single-writer (concurrency/atomicity = deploy gate)

## C — API auth + permission canaries (`AUTH_MODE=enforce`)
**health:** `{"collection":"company_docs_p5b_run1","vectors":16,"model":"BAAI/bge-m3"}` — ชี้ isolated collection เท่านั้น

**auth matrix (curl):** `no-key → 401` ; ทุก 11 role-scoped key: **in-scope → 200**, **forbidden → 403** (pass 11/11)

**`ask_eval.py --api http://localhost:8402`:**
```
CANARY-RECALL/SALES/HR/PURCHASING/LOGISTICS-001  [ok] (+3/-8 roles)
CANARY-ENGINEERING-001 [ok] (+4/-7)   CANARY-PRODUCTION-001 [ok] (+6/-5)
totals: PASS=7 LEAK=0 INCONCLUSIVE=0
auth-gate: VERIFIED — spoof 403 ครบทั้ง 11 role-scoped key
>>> SECURITY exit_code = 0 (GREEN)
```
- canary **ทุกตัว PASS** (positive ครบทุก authorized role, negative ไม่รั่วทุก denied role)
- **LEAK=0 · INCONCLUSIVE=0 · auth VERIFIED** (ครอบ registry ทั้งชุด ไม่ใช่แค่ 2 key)

## ตรง acceptance ที่ตกลง (ครบ 5/5)
- [x] no key → 401
- [x] ทุก role-scoped key: in-scope → 200, forbidden → 403
- [x] ask_eval: canary ทุกตัว PASS, ไม่มี LEAK/ERROR/INCONCLUSIVE, auth VERIFIED
- [x] API ชี้ isolated collection (`company_docs_p5b_run1`) เท่านั้น
- [x] เก็บ report (ไฟล์นี้) → teardown เฉพาะ `brain_p5b` stack

## หมายเหตุระหว่างรัน (แก้ระหว่าง P5b)
- `ask_eval TOP_K` 5→10: authorized role เห็น canary ได้ถึง 8 จุด; top_k เดิม 5 ทำให้ positive control อาจ miss เพราะ **vector top-k ไม่ใช่ filter ผิด** (ตรง Codex เตือน) — API cap ที่ 10, visible สูงสุด 8 จึงปลอดภัย
- `docker-compose.p5b.yml`: mount `policy.py`+`qdrant_filter.py` เข้า container (main.py import แต่ Dockerfile เดิม copy แค่ app/+rbac_config); `hf_cache_p5b` volume แยก

## ยังเป็น deploy gate (P5b ไม่ครอบ — ตาม Codex)
production staging→backfill→atomic alias cutover · concurrent-writer fencing · durable quarantine review workflow · full legacy-writer refactor · user OIDC · egress/redaction · flip AUTH_MODE ของ service จริง

---

## ขอ Codex ยืนยัน (ปิด P1 track เป็นทางการ)
1. P5b PASS ครบ acceptance (A conformance + B lifecycle + C auth 5/5) — **ปิด P1 track + ประกาศ P1 hardened เฉพาะ PoC (local/synthetic/single-writer) ได้เต็มปากไหม** หรือมีอะไรค้าง
2. runtime fixes ที่แก้ระหว่างรัน (`ask_eval TOP_K` 5→10 เพราะ vector top-k, mount `policy.py`/`qdrant_filter.py`) — กระทบ validity ของผลไหม / ต้อง re-run ซ้ำไหม
3. `commit 16dc96a` (+ harness `ff9732e`) — จุดที่ยัง fail-open หรือ evidence ไม่พอ ก่อนไปงานถัดไป
4. ลำดับถัดไป: เดิน **P2 (reranker offline/shadow + hybrid arm)** ตามโรดแมป REORDER-THEN-GO หรือควรปิด deploy-gate ตัวใดก่อน
