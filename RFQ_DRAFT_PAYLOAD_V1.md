# RFQ Draft Payload Contract — `draft-v1`

Interface สำหรับ **ENQ extractor** และ **FastAPI** ที่จะเรียก `create_rfq_draft()` (migration `006`)
Source of truth ของ allowlist = ตัวฟังก์ชันใน [`migrations/006_enq_ingest.sql`](migrations/006_enq_ingest.sql) — ไฟล์นี้สรุปให้อ่านง่าย

## Signature

```sql
create_rfq_draft(p_payload jsonb, p_actor text, p_service text, p_request_id text) RETURNS uuid
```

- เรียกได้เฉพาะ DB role **`rfq_ingest`** (ENQ worker) — role อื่นโดน 42501
- `p_actor` / `p_service` / `p_request_id` = **trusted server context** (มาจาก FastAPI/worker) — **ห้ามอยู่ใน JSON**
  - ถ้ายังไม่มี user auth ให้ `p_actor` = service identity เช่น `enq-extractor` (อย่าปลอมเป็นมนุษย์)
- คืน `rfq_id` (uuid) ของ DRAFT ที่สร้าง; ถ้า `(service, request_id)` เคยสำเร็จด้วย payload เดิม → คืน id เดิม (idempotent)

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
      "quantity_options": [ {"option_no":1,"quantity":5000,"unit_ref":"PCS","is_primary":true} ],
      "design_variants":  [ {"variant_no":1,"design_code":"D1","quantity":2500} ],
      "components": [
        {
          "component_no": 1, "component_name": "body", "box_template_ref": "BT-2",
          "paper_ref":"...", "paper_gsm_snapshot":250, "print_sides_code":"TWO_SIDES",
          "box_width_mm":80,"box_length_mm":120,"box_depth_mm":50,
          // + color_outside_count/inside_count, ink_type_ref, flap_mm/glue_mm/tuck_mm, *_snapshot, notes
          "corrugated": { "flute_code_snapshot":"B", "layer_count_snapshot":3, "grade_spec_snapshot":{} }
        }
      ],
      "processes": [ {"sequence_no":1,"process_ref":"PRC-1","component_no":1,"side_code":"OUTSIDE"} ],
      "packings":  [ {"sequence_no":1,"packing_ref":"PK-1","quantity_per_pack":50} ],
      "deliveries":[ {"delivery_no":1,"destination_ref":"DEST-1","option_no":1,"requested_date":"2026-08-15"} ]
    }
  ]
}
```

## กติกาที่ DB บังคับ (fail-closed)

| กติกา | พฤติกรรม |
|---|---|
| unknown/forbidden key (ทุกชั้น) | reject `22023` — allowlist strict |
| `schema_version` ≠ `draft-v1` | reject `22023` |
| ตั้ง lifecycle/identity ใน payload (`status_code`, `revision_no`, `is_current`, `row_version`, `ready_*`, `rfq_no`, `created_by_ref`, …) | reject (ไม่อยู่ allowlist) — server hard-code เอง |
| `items` ว่าง / >100 / ไม่ใช่ array | reject |
| payload >1MB, array ต่อ item >200 | reject `54000` |
| `actor`/`service`/`request_id` ว่าง | reject `22023` |
| `(service, request_id)` ซ้ำ + payload เดิม | คืน id เดิม (idempotent replay) |
| `(service, request_id)` ซ้ำ + payload ต่าง | reject `23505` (conflict) |
| fail กลาง insert | rollback ครบ — ไม่มี partial draft |

**FK ภายใน item:** `processes[].component_no` → resolve เป็น component ที่มี `component_no` เดียวกันใน item นั้น;
`deliveries[].option_no` → resolve เป็น quantity_option; ถ้าไม่ระบุ = NULL

## v1 ยังไม่รองรับ (fail-closed — reject ถ้าใส่มา)

- **`extraction_runs` / `field_evidence`** ใน payload → v1.1 (พร้อม #6: validate run `SUCCEEDED`/ไม่ `BLOCKED`/policy allow
  ก่อน insert AI evidence + resolve subject-ref) — v1 จึง**ไม่มีทางสร้าง AI evidence โดยไม่มี provenance**
- attachment ใน payload → แยก endpoint (upload + classification) ภายหลัง

## หน้าที่ฝั่ง app (DB บังคับไม่ได้)

- authenticate + map principal → `p_actor` (ห้าม copy จาก request JSON)
- validate payload ซ้ำเพื่อ error message ที่อ่านง่าย (DB เป็น last line of defense)
- ถ้ามี HTTP/worker retry อัตโนมัติ: reuse `request_id` เดิม (F7 full-concurrency ยัง gate)
