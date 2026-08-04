# P5b Runbook — real-Qdrant conformance + lifecycle + auth (isolated, synthetic)

> **Codex GO P5b** (`KB_P1_FIX_BEFORE_P5B_CODEX_REVIEW_809CB60.md`) — local + synthetic เท่านั้น
> **isolation interlock (P5B-B1):** test collection แยก · volume/port แยก · `AUTH_MODE=enforce` · seeder ปฏิเสธ production name
> **NO-GO:** deploy · retag corpus จริง · flip AUTH_MODE ของ service จริง · ประกาศ hardened ก่อน P5b PASS

## ต้องมีก่อนรัน (ยังไม่พร้อมในเครื่องนี้ตอนเขียน)
- **Docker Desktop เปิดอยู่** (ตอน commit นี้ daemon ไม่ทำงาน → รันไม่ได้)
- python env ที่มี `qdrant-client` (สำหรับ seed/conformance/lifecycle):
  `pip install qdrant-client`  ← rfqv/KB .venv ปัจจุบันยังไม่มี
- สำหรับ acceptance C (API `/search`) — API container build เอง (torch/transformers/BGE-M3 ผ่าน Dockerfile)

## ขั้นตอน (สั่งจาก repo root)

```bash
# 0) synthetic keys (offline, ไม่ลับ) — เขียน api_keys.p5b.json + ได้ KB_EVAL_KEYS
python p5b_gen_keys.py            # stderr: wrote api_keys.p5b.json ; stdout: KB_EVAL_KEYS=...
export KB_EVAL_KEYS='<ค่าที่พิมพ์ออกมา>'

# 1) unique run/collection (marker จริง — ห้ามชื่อ company_docs, ต้องมี 'p5b')
export P5B_RUN_ID="run_$(date +%s)"
export P5B_COLLECTION="company_docs_p5b_$P5B_RUN_ID"
export P5B_QDRANT_URL="http://localhost:6401"

# 2) ยก stack isolated (immutable image — Dockerfile COPY policy.py/qdrant_filter.py, qdrant pinned digest)
docker compose -f docker-compose.p5b.yml -p brain_p5b up -d --build

# 3) seed fixtures (marker + conformance + canary) — guard ด้วย marker จริง, ไม่มี --recreate
python p5b_seed.py

# === Mandatory acceptance ===
# A) real-Qdrant filter conformance (scroll, ไม่ใช่ vector top-k)
python p5b_conformance.py         # ต้อง exit 0 (model == Qdrant สำหรับ fixture matrix)

# B) actual writer lifecycle (เรียก store_in_qdrant จริง)
python p5b_lifecycle.py           # ต้อง exit 0 (ACTIVE→QUARANTINED / broad→narrow revoke)

# G1) default-deny end-to-end ผ่าน API (UNCLASSIFIED resolver-driven + missing/stale/quarantine)
python p5b_default_deny.py --api http://localhost:8402   # admin พบ UNCLASSIFIED, อีก 10 ไม่พบ; deny targets ไม่มีใครพบ

# C) API auth + permission canaries (AUTH_MODE=enforce, /search, $0 ไม่ egress)
python ask_eval.py --api http://localhost:8402
#   ต้อง: SECURITY exit_code=0, ทุก canary PASS, auth VERIFIED (spoof ทุก key ได้ 403)

# preflight auth ตรง ๆ (Codex acceptance C)
curl -s -o /dev/null -w "%{http_code}\n" -XPOST localhost:8402/search \
  -H 'content-type: application/json' -d '{"query":"x","role":"qc","top_k":3}'         # → 401 (no key)
curl -s -o /dev/null -w "%{http_code}\n" -XPOST localhost:8402/search \
  -H 'content-type: application/json' -H 'X-API-Key: p5b-sales-synthetic-key' \
  -d '{"query":"x","role":"qc","top_k":3}'                                             # → 403 (out-of-scope)
curl -s -o /dev/null -w "%{http_code}\n" -XPOST localhost:8402/search \
  -H 'content-type: application/json' -H 'X-API-Key: p5b-qc-synthetic-key' \
  -d '{"query":"x","role":"qc","top_k":3}'                                             # → 200 (in-scope)

# 4) teardown — ลบเฉพาะ stack/volume ของ run นี้ (ไม่ใช้ wildcard)
docker compose -f docker-compose.p5b.yml -p brain_p5b down -v
```

## Acceptance gate (ประกาศ hardened เฉพาะ PoC ได้ต่อเมื่อ **ครบทุกข้อ**)
- [ ] A conformance: `exit 0` — list match · scalar native-match (store-integrity) · null/missing/unknown/stale/`true`/`1.0`/QUARANTINED → no-match แม้ admin
- [ ] B lifecycle: `exit 0` — ACTIVE→QUARANTINED role เดิมเห็น 0 · broad→narrow revoked role เห็น 0, retained ยังเห็น
- [ ] C auth: no-key→401 · out-of-scope→403 · in-scope→200 · `UNCLASSIFIED`→admin-only · missing/stale/quarantine→ไม่มีใครเห็นแม้ admin
- [ ] ask_eval: SECURITY `exit_code=0` + auth **VERIFIED** (spoof ครบทุก role-scoped key)
- ผลใด LEAK/ERROR/INCONCLUSIVE หรือ auth ไม่ VERIFIED → **FAIL, ห้ามขยับ deploy**

## Pre-verified offline แล้ว (ก่อนแตะ infra)
รันได้ทันทีโดยไม่ต้อง Docker/Qdrant — พิสูจน์ fixtures + expectation ตรงกับ conservative model:
```
python test_p5b_fixtures.py    # 11/11 — matches_policy == expect_roles + marker + UNCLASSIFIED resolver
python test_policy.py          # 69/69 (รวม P5B-B1 marker guard)
python test_eval_contract.py (64) / test_ask_eval_harness.py (12) / test_auth.py (11)
```

## ยังเป็น deploy gate (ไม่อยู่ใน P5b)
production staging→backfill→atomic alias cutover · concurrent-writer fencing (P5b = single-writer serial) ·
durable quarantine review workflow · full legacy-writer refactor · user OIDC · egress/redaction
