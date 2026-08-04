# KB Next Steps — ideation/review handoff (Knowledge Brain, Module 12)

> **บทบาทของ GPT/Codex:** reviewer/advisor — เสนอความเห็น จัดลำดับ ชี้ความเสี่ยง (ไม่ต้องเขียนโค้ดเต็ม)
> **บริบท:** `company-ai-brain` = **Knowledge Brain (Module 12) เท่านั้น** แล้ว — งาน RFQ/ENQ (Module 01/02) ถูกลบออก 2026-08-04 (ไปอยู่ `../RFQ_Estimate`)
> **ขอบเขต:** local + synthetic ; **ยังไม่ deploy / ยังไม่รับข้อมูลจริง** (real data/cloud/user auth จริง = รอ DPO/Legal) ; ห้ามแตะ `.env`, Qdrant prod, ข้อมูลลูกค้าจริง
> **หลักคิดแม่:** *"Router ช่วยเลือกทาง แต่ Policy Filter ต้องเป็นคนล็อกประตู"* + *"Permission ต้อง enforce ก่อน retrieval เสมอ"*

## สถานะปัจจุบัน (จาก `app/main.py` 412 บรรทัด — source จริง)
| ส่วน | มีแล้ว | หมายเหตุ |
|---|---|---|
| Endpoints | `GET /health` · `POST /search` (retrieval) · `POST /ask` (retrieval+Claude+citation) · `GET /collections` | — |
| Auth | service-level `X-API-Key` → service + `allowed_roles` ; `AUTH_MODE=off/warn/enforce` (ตอนนี้ **warn**) | กัน role-spoofing (client เลือก role นอก scope ของ key ไม่ได้) ; **ยังไม่มี user auth** |
| RBAC | `make_rbac_filter(role)`: admin→เห็นหมด, อื่นๆ→filter `allowed_roles MatchAny [role]` | **เช็คแค่ `allowed_roles`** — ไม่เช็ค `confidentiality_level`/`allowed_groups` ตามที่ CLAUDE.md วางไว้ |
| Retrieval | BGE-M3 embed → Qdrant `query_points` (RBAC filter) | **ไม่มี reranker** (ทั้งที่ stack ระบุ `bge-reranker-v2-m3`) — คืน raw vector hits |
| Audit | `print("[AUDIT] ...")` → stdout | **ไม่ persist / query ไม่ได้** |
| /ask egress | ส่ง retrieved context → Claude (`generate_answer`) | ไม่มี egress gate ตาม confidentiality |
| Cache | — | ไม่มี Redis (stack ระบุ permission-aware key) |
| สถานะรวม | PoC รันจริง (2,263 chunks) | **ยังไม่ production-ready** |

---

## ข้อเสนองานถัดไป (จัดลำดับ) — ทำได้ local + synthetic ทั้งหมด

### P1 (สูงสุด) — Permission-leakage hardening + automated test
**ปัญหา:** `make_rbac_filter` enforce แค่ `allowed_roles` มิติเดียว ; ถ้าเอกสารมี `confidentiality_level` (CONFIDENTIAL/RESTRICTED) แต่ tag role ไว้ → filter ปัจจุบันไม่กันตาม confidentiality → **เสี่ยง leak**. นี่คือหัวใจของหลักคิดแม่ ("Policy Filter ล็อกประตู")
**เสนอทำ:**
1. ยืนยัน tagging model บน payload จริง (มี field อะไรบ้าง: `allowed_roles`, `confidentiality_level`, `department`, `allowed_groups`?)
2. harden filter ให้ enforce **หลายมิติ** (role **และ** confidentiality **และ** group) — fail-closed ถ้า field หาย
3. **automated permission-leakage test**: วน role ทุกตัว → query หาเอกสารของ role อื่น/confidentiality สูงกว่า → assert **leak = 0** (ตรง PoC metric "permission leakage rate" + eval "คำถามที่ user ไม่มีสิทธิ์")
**ทำไม:** security-critical, ตรงหลักคิดแม่, ทดสอบได้ทันทีด้วย synthetic corpus

### P2 — Reranker integration + retrieval eval
**ปัญหา:** /search /ask คืน raw Qdrant hits ; stack มี `bge-reranker-v2-m3` แต่ยังไม่ wire → precision อาจต่ำ
**เสนอทำ:** เพิ่ม rerank step (retrieve top-K → rerank → top-N) ที่ `generate_answer`/`search` path ; วัด **retrieval hit rate + citation accuracy** ก่อน/หลัง
**ทำไม:** ตรง PoC goal *"วัดผลก่อนตัดสินใจ hardware"* — retrieval quality เป็น input หลักของการตัดสินซื้อ GPU

### P3 — Persisted audit log
**ปัญหา:** `[AUDIT]` เป็น stdout print — query/forensic ไม่ได้
**เสนอทำ:** persist audit (ts, service, role, query, **retrieved doc_ids**, **สิ่งที่ส่งไป Claude**) ลง store (PostgreSQL/ไฟล์ append-only) → เป็นฐานของ permission-leakage forensic + data-egress accountability
**ทำไม:** required ก่อนรับข้อมูลจริง ; ต่อยอด P1 (พิสูจน์ว่าไม่มี leak เกิดขึ้นจริง)

### P4 — /ask data-egress gate (ก่อนข้อมูลจริง)
**ปัญหา:** /ask ส่ง context ไป Claude โดยไม่ดู confidentiality (ตอนนี้ corpus = SOP/Internal จึงยอมรับได้ ตาม tripwire ใน STATUS)
**เสนอทำ:** egress gate — ก่อนส่ง Claude เช็ค confidentiality ของ chunk ที่ retrieve (block/redact/route local-LLM) ; อย่างน้อย flag+log
**ทำไม:** ตรง tripwire "ทันทีที่ข้อมูลจริง (PII/trade secret) ไหลเข้า ต้องเปิด redaction/local path"

### P5 — Eval hardening (วัด PoC metrics ให้ครบ)
**เสนอทำ:** ขยาย/ตรวจ `eval_set.json` ให้วัดครบ: retrieval hit rate, citation accuracy, **permission leakage rate**, hallucination rate, faithfulness, latency
**ทำไม:** เป็น gate ก่อนตัดสิน hardware/Go-No-Go ตาม CLAUDE.md

---

## อยากให้ GPT ช่วยตัดสิน/เสนอ
1. **ลำดับ** — P1 (leakage) มาก่อน P2 (reranker) ถูกไหม ; หรือควรทำ P5 (eval harness) ก่อนเพื่อมี baseline วัด P1/P2
2. **P1 filter model** — enforce หลายมิติที่ Qdrant filter (role+confidentiality+group) พอไหม หรือต้องมี policy layer แยกก่อน retrieval ; fail-closed เมื่อ field tagging หายควรทำยังไง
3. **P2 reranker** — วาง rerank ใน API path (ต่อ request) รับได้ไหมสำหรับ PoC หรือกังวลเรื่อง latency/GPU
4. มี gap อื่นที่ผมมองข้าม (เช่น chunk quality, parent-child retrieval, Thai normalization) ที่ควรมาก่อนไหม

## Out of scope (ยัง gated)
user auth จริง (Keycloak OIDC) · deploy · real corpus (PII/trade secret) · cloud egress ของข้อมูลจริง · production monitoring/rate-limit/TLS = รอ DPO/Legal + มติผู้บริหาร
