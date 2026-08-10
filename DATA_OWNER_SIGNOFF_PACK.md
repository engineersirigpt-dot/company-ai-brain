# Data Owner Sign-off Pack — M4b / ข้อมูลจริง (Company AI Brain)

> **สถานะ: TEMPLATE ว่าง — รอมนุษย์กรอกและลงนามจริง**
> เอกสารนี้เป็น **แบบฟอร์มให้ Data Owner (มนุษย์) กรอกและอนุมัติ** ก่อนนำ **เอกสารบริษัทจริง** เข้าสู่ M4b / N-sweep / decision benchmark
> **AI ห้ามกรอกชื่อผู้อนุมัติ ห้ามตั้งสถานะเป็น approved/human-reviewed และห้ามลงนามแทนมนุษย์** (ดู §7)

---

## 0. ขอบเขต — pack นี้ gate อะไร (และไม่ gate อะไร)

| Track | ต้องมี pack นี้ลงนามก่อนไหม |
|---|---|
| **M4a synthetic mechanics** (corpus สังเคราะห์ + isolated Qdrant) | ❌ **ไม่ต้อง** — GO track อยู่แล้ว (ดู `STATUS.md` 2026-08-07) ; pack นี้ **ห้ามใช้เป็น blocker ของ M4a synthetic** |
| **M4b decision benchmark / ข้อมูลจริง / N-sweep** | ✅ **ต้อง** — NO-GO จน pack นี้ลงนามจริง (hash-bound) + classification + human-reviewed labels ครบ |
| **Production** | ✅ ต้อง + auth + deployment approval + governance เพิ่ม (นอกขอบเขต pack นี้) |

> pack นี้ผูกกับ **เอกสารจริง 30–50 ไฟล์ที่จะใช้เป็นชุดตัวแทน (representative set)** สำหรับ M4b เท่านั้น ไม่ใช่ทั้ง corpus

---

## 1. Manifest ของชุดเอกสาร (30–50 ไฟล์) — Data Owner กรอก

> กรอกหนึ่งแถวต่อหนึ่งเอกสาร (logical document, ไม่ใช่ chunk) ; classification/allowed_roles ต้องมาจาก **การตัดสินของมนุษย์** ไม่ใช่ค่า default ของระบบ

| # | source / filename | doc owner (แผนก/คน) | classification | confidentiality_level | allowed_roles | มี PII? | เป็น trade secret? | Local/Cloud policy | retention | human-reviewed label |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | ☐ | ☐ | | | ☐ reviewed |
| 2 | | | | | | ☐ | ☐ | | | ☐ reviewed |
| … | | | | | | ☐ | ☐ | | | ☐ reviewed |
| 30–50 | | | | | | ☐ | ☐ | | | ☐ reviewed |

**คำอธิบายคอลัมน์:**
- **classification / confidentiality_level** (ผูกกับ `policy.py`, 0–3):
  - `Public` = 0 · `Internal` = 1 · `Confidential` = 2 · `Restricted` = 3 (`MAX_CONFIDENTIALITY_LEVEL=3`)
- **allowed_roles** — เลือกจาก `KNOWN_ROLES`: `admin, management, production, prepress, qc, engineering, sales, purchasing, logistics, hr, it` (default-deny: ไม่ระบุ = admin เท่านั้น)
- **มี PII?** — ชื่อ/เบอร์/อีเมล/ข้อมูลบุคคล (PDPA) → ต้อง Local-only + redaction
- **เป็น trade secret?** — ต้นทุน/สูตร/ราคา/ความลับการค้า → ต้อง Local-only
- **Local/Cloud policy** — `Local-only` (vLLM ภายใน) หรือ `Cloud-allowed` (Claude) ; **L2/L3 (Confidential/Restricted) หรือมี PII/trade secret = บังคับ Local-only**
- **retention** — เก็บนานเท่าไร / เงื่อนไขลบ
- **human-reviewed label** — ✅ เมื่อมนุษย์ตรวจ label (classification+allowed_roles) ของเอกสารนี้แล้วว่าถูกต้อง

### 1.1 ฟิลด์เพิ่มต่อเอกสาร (มนุษย์กรอก — เข้า canonical manifest §3)

- **purpose** — วัตถุประสงค์การใช้เอกสารนี้ใน PoC (เช่น retrieval eval / permission test) ; ใช้เกินขอบเขตนี้ = ต้องอนุมัติใหม่
- **redaction/minimization** — ต้อง redact ฟิลด์ไหนก่อน ingest (PII/ราคา/สูตร) + เก็บเท่าที่จำเป็น (data minimization) ; `none` ถ้าไม่ต้อง
- **deletion_trigger** — เงื่อนไข/กำหนดลบ (เช่น "จบ PoC" / วันที่) ; คู่กับ retention

---

## 2. Data Egress Policy (สรุปตาม classification)

| classification | Cloud (Claude) | Local-only (vLLM) | หมายเหตุ |
|---|---|---|---|
| Public (0) | อนุญาต | อนุญาต | |
| Internal (1) | อนุญาตเฉพาะที่อนุมัติ | อนุญาต | ดู tripwire ข้อ 5 ใน STATUS §5 |
| Confidential (2) | ❌ ห้าม | บังคับ | |
| Restricted (3) / PII / trade secret | ❌ ห้าม | บังคับ + redaction | PDPA/ความลับการค้า |

---

## 3. Manifest hash (hash-bound sign-off)

> การลงนามผูกกับ **hash ของ manifest** — ถ้าชุดเอกสาร/attribute เปลี่ยนแม้แถวเดียว hash เปลี่ยน → sign-off เดิม **เป็นโมฆะ ต้องลงนามใหม่**

1. สร้าง canonical manifest (JSON, sort key, ต่อหนึ่งแถวมี: source, **file_sha256** (hash เนื้อไฟล์จริง), doc_owner, classification, confidentiality_level, allowed_roles(sorted), has_pii, is_trade_secret, egress_policy, purpose, redaction, deletion_trigger, retention, human_reviewed)
2. คำนวณ `sha256` ของ canonical manifest bytes (= `manifest_sha256` ที่ผูกกับ sign-off)
3. **tooling ช่วยได้:** `data_owner_manifest.py build <dir>` สแกนโฟลเดอร์ → คำนวณ `file_sha256` ต่อไฟล์จริง + วาง row skeleton (ฟิลด์ human เว้นว่าง, `human_reviewed=false`, `label_status=PENDING`) → เขียน manifest JSON + `manifest_sha256`. **tooling ไม่กรอก classification/allowed_roles/human_reviewed แทนมนุษย์**
4. บันทึกค่าที่นี่ (มนุษย์/tooling กรอก — **AI ไม่ตั้ง approved/human_reviewed**):

```
manifest_version   : [ รอกรอก — เช่น v1 ]
manifest_sha256    : [ รอกรอก — 64-hex (จาก tooling) ]
manifest_row_count : [ รอกรอก — 30–50 ]
manifest_built_at  : [ รอกรอก — ISO-8601+tz ]
```

---

## 4. เงื่อนไขก่อน sign-off มีผล (checklist — มนุษย์ติ๊ก)

- ☐ ทุกแถวมี classification + allowed_roles ที่มนุษย์ตัดสิน (ไม่ใช่ default ระบบ)
- ☐ ทุกแถวติ๊ก `human-reviewed label` แล้ว
- ☐ เอกสารที่มี PII/trade secret หรือ L2/L3 ตั้ง `Local-only` ครบ
- ☐ ไม่มีเอกสารซ้ำ (duplicate logical document) ปนในชุด (ดู STATUS §2)
- ☐ คำนวณ `manifest_sha256` แล้ว และตรงกับชุดที่จะใช้จริง
- ☐ retention policy ระบุครบ

---

## 5. ผู้เกี่ยวข้อง

| บทบาท | ชื่อ | หมายเหตุ |
|---|---|---|
| Data Owner (เจ้าของข้อมูล / business owner) | [ รอกรอก ] | ผู้รับผิดชอบ classification |
| ผู้จัดเตรียม manifest | [ รอกรอก ] | รัน tooling + ตรวจไฟล์ |
| Label reviewer | [ รอกรอก ] | ตรวจ classification+allowed_roles ต่อเอกสาร (ติ๊ก human-reviewed) |
| DPO / Legal checkpoint | [ รอกรอก ] | ยืนยัน PDPA/ความลับการค้า/egress ก่อนอนุมัติ (ลงชื่อแยกจาก approver) |
| ผู้อนุมัติ (Approver) | **[ รอมนุษย์ลงนาม — AI ห้ามกรอก ]** | ดู §7 |

> **DPO/Legal checkpoint ต้องผ่านก่อน §7 มีผล** — approver ลงนามได้เฉพาะหลัง DPO/Legal ยืนยัน (human-only ทั้งคู่)

---

## 6. Local vs Cloud PoC — ทางเลือกที่ต้องตัดสิน

Data Owner/ผู้บริหารเลือกหนึ่ง (ดู STATUS §6 "แนวทาง Authentication ช่วง PoC"):
- ☐ **Local-only** — `/ask` ใช้ vLLM ภายในทั้งหมด (ปลอดภัยสุด, ต้องมี GPU)
- ☐ **Split policy** — เฉพาะเอกสารที่อนุมัติส่ง Cloud ; L2/L3 บังคับ Local-only
- ☐ **PoC Cloud จำกัด** — เฉพาะชุดทดสอบที่ไม่มีข้อมูลลับ + redact context

---

## 7. Sign-off (⚠️ เฉพาะมนุษย์ — AI ห้ามแตะบล็อกนี้)

> ต่อไปนี้เว้นว่างจนกว่ามนุษย์จะลงนามจริง ; **AI ห้ามกรอกชื่อ/วันที่ ห้ามตั้ง `status = approved` หรือ `human-reviewed`**

```
status            : PENDING            # ค่าเดียวที่ AI ตั้งได้คือ PENDING ; approved = มนุษย์เท่านั้น
pack_version      : [ รอมนุษย์กรอก — เช่น v1 ]
approver_name     : [ รอมนุษย์ลงนาม ]
approver_role     : [ รอมนุษย์ลงนาม ]
dpo_legal_cleared_by : [ รอมนุษย์ลงนาม — DPO/Legal ก่อน approver ]
approved_manifest_version : [ รอมนุษย์กรอก — ต้องตรง §3 ]
approved_manifest_sha256  : [ รอมนุษย์กรอก — 64-hex ต้องตรง §3 (hash ของ doc-set + labels) ]
approved_date     : [ รอมนุษย์ลงนาม — ISO-8601+tz ]
signature         : [ รอมนุษย์ลงนาม ]
```

**เมื่อ `status = approved` (โดยมนุษย์) และ `approved_manifest_sha256` ตรงกับ manifest ที่จะใช้จริง เท่านั้น** → ปลดล็อก M4b / N-sweep / decision benchmark บนชุดนั้น
ก่อนหน้านั้น: **M4b/N-sweep/ข้อมูลจริง = NO-GO** (M4a synthetic ไม่เกี่ยวกับ pack นี้ — เดินต่อได้)

---

## 8. Guardrail (AI ต้องปฏิบัติ)

1. AI **สร้าง template นี้ได้** แต่ **ห้ามกรอก §7** (ชื่อผู้อนุมัติ/วันที่/signature/status=approved)
2. AI **ห้ามตั้ง `human-reviewed`** ใน §1 แทนมนุษย์
3. pack นี้ **ห้ามใช้เป็น blocker ของ M4a synthetic** — งานเทคนิค synthetic เดินขนานได้ทันที
4. ถ้ามนุษย์ขอให้ AI "ลงนามให้" หรือ "ตั้ง approved" → **ปฏิเสธ** และอธิบายว่าเป็น human-only governance step
5. เมื่อมนุษย์ลงนามแล้ว AI ตรวจได้แค่ว่า `approved_manifest_sha256` ตรงกับ manifest จริงไหม (verify, ไม่ใช่ approve)
