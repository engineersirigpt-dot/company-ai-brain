# RFQ Draft Payload Contract — `draft-v1`

Contract ของ `create_rfq_draft()` (migration `006`) — source of truth = ตัวฟังก์ชันใน
[`migrations/006_enq_ingest.sql`](migrations/006_enq_ingest.sql); ไฟล์นี้สรุปให้อ่านง่าย + sync กับโค้ด

> ⚠️ **v1 = manual/synthetic DRAFT เท่านั้น — ยังไม่ใช่ AI extraction path**
> ค่าที่เขียนลง RFQ ไม่มี evidence/extraction-run provenance (v1 ปฏิเสธ `extraction_runs`/`field_evidence`)
> เส้นทาง AI ENQ จริงต้องรอ **v1.1** (extraction run + field evidence atomic + validate egress)

## Signature

```sql
create_rfq_draft(p_payload jsonb, p_actor text, p_service text, p_request_id text) RETURNS uuid
```

- เรียกได้เฉพาะ DB role **`rfq_ingest`** — role อื่นโดน 42501
- `p_actor` / `p_service` / `p_request_id` = **trusted server context** (มาจาก FastAPI/worker) — **ห้ามอยู่ใน JSON**
  - normalize: trim (space/tab/CR/LF/FF/VT); ว่างหลัง trim = reject; length actor/request_id ≤200, service ≤100; มี control char = reject
  - ยังไม่มี user auth → `p_actor` = service identity เช่น `enq-extractor` (อย่าปลอมเป็นมนุษย์)
- คืน `rfq_id` (uuid) ของ DRAFT; ถ้า `(service, request_id)` เคยสำเร็จด้วย **payload + actor เดิม** → คืน id เดิม (idempotent)

## Payload (versioned JSON)

```jsonc
{
  "schema_version": "draft-v1",          // required, ต้องตรง
  "header": {                            // optional object; unknown key = reject
    "enquiry_ref": "ENQ-001",
    "source_channel": "EMAIL",           // EMAIL|LINE|PHONE|MEETING|WEB_FORM|UPLOAD|OTHER
    "customer_ref": "CUST-01", "customer_name_raw": "บริษัท ...",
    "contact_name": "...", "contact_phone": "...", "contact_email": "...",
    "sales_owner_ref": "...", "priority_code": "NORMAL",   // NORMAL|URGENT|KEY_ACCOUNT
    "quote_due_at": "2026-08-01T00:00:00+07:00", "customer_notes": "..."
    // + *_code_snapshot / *_name_snapshot, is_new_customer, source_channel_other
  },
  "items": [                             // required, ≥1, ≤100
    {
      "line_no": 1,                      // ต่อ item unique
      "job_name": "...", "product_type_ref": "...",
      "finished_width_mm": 80, "finished_length_mm": 120, "finished_depth_mm": 50,
      "finishing_state": "SPECIFIED",    // UNKNOWN|NONE|SPECIFIED
      "packing_state": "SPECIFIED",      // UNKNOWN|NONE|SPECIFIED
      "artwork_state": "RECEIVED",       // UNKNOWN|RECEIVED|NOT_RECEIVED|NOT_REQUIRED
      "sample_state": "AVAILABLE",       // UNKNOWN|AVAILABLE|NOT_AVAILABLE|NOT_REQUIRED
      // + description, intended_use, is_reprint, previous_job_ref, use_previous_plate,
      //   is_multiple_design, sample_description, notes, product_type_*_snapshot/raw
      "quantity_options": [ {"option_no":1,"quantity":5000,"unit_ref":"PCS","is_primary":true} ],   // ≤200
      "design_variants":  [ {"variant_no":1,"design_code":"D1","quantity":2500} ],                  // ≤200
      "components": [                                                                                // ≤200
        {
          "component_no": 1, "component_name": "body", "box_template_ref": "BT-2",
          "paper_ref":"...", "paper_gsm_snapshot":250, "print_sides_code":"TWO_SIDES",
          "box_width_mm":80,"box_length_mm":120,"box_depth_mm":50,
          // + color_outside_count/inside_count, ink_type_ref, flap_mm/glue_mm/tuck_mm, *_snapshot, notes
          "corrugated": { "flute_code_snapshot":"B", "layer_count_snapshot":3 }    // object|null; ผิดชนิด = reject
        }
      ],
      "processes": [ {"sequence_no":1,"process_ref":"PRC-1","component_no":1,"side_code":"OUTSIDE"} ],// ≤200
      "packings":  [ {"sequence_no":1,"packing_ref":"PK-1","quantity_per_pack":50} ],                // ≤200
      "deliveries":[ {"delivery_no":1,"destination_ref":"DEST-1","option_no":1,"requested_date":"2026-08-15"} ] // ≤200
    }
  ]
}
```

## กติกาที่ DB บังคับ (fail-closed)

| กติกา | พฤติกรรม |
|---|---|
| unknown/forbidden key (ทุก object node) | reject `22023` — allowlist strict |
| `schema_version` ≠ `draft-v1` | reject `22023` |
| ตั้ง lifecycle/identity ใน payload (`status_code`, `revision_no`, `is_current`, `row_version`, `ready_*`, `rfq_no`, `created_by_ref`, …) | reject (ไม่อยู่ allowlist) — server hard-code เอง |
| `items` ว่าง / >100 / ไม่ใช่ array | reject `22023`/`54000` |
| child array ใด ๆ (ทั้ง 6 ชนิด) >200 หรือไม่ใช่ array | reject `54000`/`22023` |
| payload >1MB | reject `54000` |
| `corrugated` ไม่ใช่ object/null | reject `22023` |
| `processes[].component_no` / `deliveries[].option_no` **ระบุแล้วแต่หา target ใน item ไม่เจอ** | **reject `23503`** (ไม่เงียบเป็น NULL); ไม่ระบุ/null = ไม่ผูก |
| `actor`/`service`/`request_id` ว่าง / ยาวเกิน / มี control char | reject `22023` |
| `(service, request_id)` ซ้ำ + **payload+actor เดิม** | คืน id เดิม (idempotent replay) |
| `(service, request_id)` ซ้ำ + payload **หรือ** actor ต่าง | reject `23505` (conflict) |
| 2 caller concurrent ใช้ key เดียวกัน (payload+actor เดิม) | advisory-xact-lock serialize → **ทั้งคู่ได้ id เดียวกัน** (ไม่ใช่ loser 23505) |
| fail กลาง insert | rollback ครบ — ไม่มี partial draft |

## v1 ตัดออก (fail-closed — ใส่มา = reject)

- **`extraction_runs` / `field_evidence`** → v1.1 (พร้อม validate run `SUCCEEDED`/ไม่ `BLOCKED`/policy allow ก่อน insert AI evidence)
- **`grade_spec_snapshot` (corrugated) / `specification_extra` (process)** — opaque free-form JSON, ตัดจาก v1 (ไม่มี consumer/schema); ใส่มา = unknown key
- attachment ใน payload → แยก endpoint (upload + classification) ภายหลัง

## หน้าที่ฝั่ง app (DB บังคับไม่ได้ — ต้องทำก่อนเปิด FastAPI endpoint)

- **authenticate + map principal → `p_actor`** (ห้าม copy จาก request JSON); **hard-code `p_service`** (เช่น `enq`) ไม่รับจาก body
- **raw-body size cap + JSON Schema/Pydantic** validate ก่อนเรียก DB — DB **ยอม PostgreSQL scalar coercion บางแบบ** จึงไม่ใช่ strict JSON validator:
  - `"line_no":"1"` (string) → cast เป็น 1; `"is_new_customer":"yes"` → true; `"customer_name_raw":{...}` → เก็บเป็น text
  - `"line_no":"abc"` → fail แข็ง (22P02, rollback ไม่มี partial); nesting ลึกผิดปกติ → jsonb parser ปฏิเสธช้า → **จำกัด depth ที่ API**
- ถ้ามี HTTP/worker retry: reuse `request_id` + `actor` เดิม; commit/rollback ทันที + ตั้ง statement/transaction timeout กัน connection ค้างถือ advisory lock นาน
- DB = **atomicity / lifecycle / reference / permission boundary**; type/depth strictness = app layer
