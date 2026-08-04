# P5a — Measurement-contract repair → handoff ให้ Codex/GPT review

> **บริบท:** `company-ai-brain` = Knowledge Brain (Module 12) เท่านั้น · local + synthetic · ยังไม่ deploy/ยังไม่รับข้อมูลจริง
> **สืบเนื่องจาก:** `KB_NEXT_STEPS_CODEX_REVIEW.md` verdict **REORDER-THEN-GO** → เริ่มที่ **P5a: ซ่อม eval harness ก่อน** (ต้องมี measuring stick ที่เชื่อได้ ก่อนจะไปวัด P1/P2)
> **บทบาท GPT/Codex:** reviewer — ยืนยันว่า contract ถูก, ชี้ช่องโหว่ที่ยังเหลือ, ไฟเขียวไป P1

## ปัญหาที่ปิด (จาก Codex B3 + M2)
- **B3 — เขียวผิดเหตุผล:** harness เดิม `call_ask()` ไม่ส่ง `X-API-Key`; พอ error (เช่น 401) → `except` เซ็ต `resp={"citations":[]}` → leak check เห็น citations ว่าง → **นับว่า "ไม่รั่ว"**. แปลว่า auth ที่ปฏิเสธเรากลายเป็นหลักฐานปลอมว่า filter ทำงาน
- **B3 — leak เช็คหลวม:** เดิมดูแค่ keyword ใน `source` string ของ citation ไม่ได้ assert ว่า doc ที่ดึงมาอยู่ในขอบเขตสิทธิ์ของ role จริง
- **M2 — citation 92% ไม่ใช่ citation accuracy:** เลขนั้นคือ retrieval-source-hit (expected_source โผล่ใน citations) ไม่ได้พิสูจน์ว่า answer อ้าง `[n]` ถูกช่วง

## สิ่งที่ทำ (verifiable offline — ไม่แตะ Qdrant/stack จริง ตาม NO-GO)
| ไฟล์ | บทบาท |
|---|---|
| **`eval_contract.py`** (ใหม่) | pure logic — `classify_outcome`, `parse_citation_refs`, `citation_integrity`, `leak_verdict` ; ไม่พึ่ง network/model → unit-test ได้ทันที |
| **`test_eval_contract.py`** (ใหม่) | 18 unit test — **รันแล้ว 18/18 ผ่าน** โดยไม่ต้องมี stack |
| **`ask_eval.py`** (refactor) | ส่ง `X-API-Key`, แยกผลผ่าน `eval_contract`, leak = citation.collection ⊄ allow-set(role), exit non-zero ถ้า LEAK/ERROR |
| **`app/main.py`** (additive) | เพิ่ม `point_id` (+`collection`,`level` ใน AskCitation) — additive ล้วน ไม่แตะความหมาย field เดิม (ตรง M3) |

### contract ที่บังคับใช้
1. **4-way outcome** ทุกการเรียก /ask|/search → `OK / NO_RESULT / DENIED(401,403) / ERROR` ; **DENIED/NO_RESULT/ERROR ห้าม collapse เป็น CLEAN** — error/deny ไม่ใช่หลักฐานว่า filter ทำงาน
2. **leak check ผูกกับ ACL จริง** — invert `rbac_config.COLLECTIONS` เป็น `role → allowed_collections` ; probe LEAK เมื่อ citation ใดมี `collection` นอก allow-set ของ role (หรือมี banned canary ใน answer)
3. **citation แยกสองแกน** — retrieval-source-hit (พบ expected_source) แยกจาก reference-validity (`[n]` ที่ answer อ้างอยู่ในช่วง `[1, n_citations]`, จับ dangling ref)
4. **fail-closed ที่ระดับ suite** — มี LEAK หรือ ERROR ใดๆ → `exit(1)` ให้ CI จับ ไม่เขียวลวง

### probe allow-sets (จาก ACL จริง — ยืนยันแล้วว่าแต่ละ probe role ไม่มีสิทธิ์ collection เป้าหมาย)
```
logistics  → {IT_SYSTEMS, LOGISTICS, PACKAGING}          (ถาม recall → RECALL นอก set = LEAK)
production → {ENGINEERING, IT_SYSTEMS, PACKAGING, PRODUCTION, QUALITY}  (ถาม recall/HR → นอก set)
qc         → {IT_SYSTEMS, PACKAGING, PRODUCTION, QUALITY, RECALL}       (ถาม SALES → นอก set)
prepress   → {IT_SYSTEMS, PRODUCTION}                    (ถาม PURCHASING → นอก set)
```

## ยังไม่ได้ทำ (จงใจ — gated)
- **end-to-end run จริง** ของ `ask_eval.py` — ต้องมี API รัน + Qdrant + **synthetic canary corpus** ที่ tag collection/level ครบก่อน ; Codex NO-GO ห้ามแตะ Qdrant prod ยังบังคับ → deferred เป็น **P5b**
- ยังไม่ยืนยันว่า payload จริงมี `collection_group`/`confidentiality_level` ครบทุก point (P1 ข้อ 1 จะไปเช็ค)

## อยากให้ GPT ช่วยยืนยัน/ชี้
1. **contract ครบไหม** — 4-way outcome + ACL-bound leak + citation 2 แกน พอจะเป็น measuring stick ที่เชื่อได้สำหรับวัด P1 (leakage) หรือมีสถานะ/ช่องที่ยังหลุด (เช่น 429, redirect, partial body)?
2. **allow-set semantics** — ใช้ `collection ⊄ allowed_collections(role)` เป็นนิยาม leak ถูกไหม หรือควรผูกกับ `confidentiality_level` ด้วย (ตอนนี้ `level` ดึงมาแล้วแต่ leak_verdict ยังไม่ใช้ — ตั้งใจเผื่อ P1)?
3. **ควรเพิ่ม negative-control ไหม** — เช่น probe role ที่ *มีสิทธิ์* จริง เพื่อยืนยันว่าไม่ได้ over-deny (กัน false CLEAN จากการ deny ทุกอย่าง)?
4. ไฟเขียวไป **P1 (permission-leakage hardening: filter หลายมิติ + automated leak test)** ได้เลยไหม หรือมีอะไรต้องปิดใน P5a ก่อน?
