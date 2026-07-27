# RFQ PostgreSQL Schema v0.2 - Packaging-first

**วันที่:** 2026-07-27  
**สถานะ:** Design สำหรับ cross-check ก่อนเขียน migration  
**ต้นทาง:** `RFQ_SCHEMA_DRAFT.md` v0.1 + Cross-check Review ข้อ 13 + `STATUS.md` หัวข้อ "คำตอบ Business รอบแรก"

## 1. Decisions ที่ใช้ใน v0.2

| เรื่อง | Decision |
|---|---|
| Product profile แรก | Packaging/กล่องเท่านั้น |
| RFQ database | เก็บ RFQ, workflow, evidence, audit และ external references |
| Business Master Data | Company DB เป็น source of truth และเข้าผ่าน API เท่านั้น |
| Local master copy | ห้ามสร้างสำเนา Paper, Corrugated, Coating, Machine, Waste, Rate หรือ Master อื่นใน RFQ DB |
| `*_ref` | เป็น opaque reference ที่ Company DB API คืนให้ ห้ามตีความจาก label |
| Snapshot | เก็บได้เฉพาะ code/label ของค่าที่เลือกเพื่อ audit ไม่ใช่ master replica และไม่ใช้แทน API validation |
| เลข RFQ | ใช้ `job_id`/เลขที่ระบบ RFQ Estimate เดิมออกให้ ไม่สร้าง format ใหม่ใน v0.2 |
| Ready approver | ยังเปิดอยู่ ให้ reuse identity/JWT/status flow เดิมผ่าน policy adapter ห้าม hard-code role ใน schema |
| Claude กับ corpus ปัจจุบัน | ใช้ได้ตาม scope ปัจจุบันของ SOP/WI |
| Claude กับ RFQ จริง | Tripwire: ต้อง redact, route Local หรือมี approved exception ก่อนส่งข้อมูลออก |
| Costing | ยังเป็น deterministic engine เดิม ไม่ย้ายสูตรเข้า LLM หรือ schema นี้ |

### 1.1 สิ่งที่เปลี่ยนจาก v0.1

1. ตัด page count และ binding ออกจาก Packaging readiness profile
2. เพิ่ม corrugated specification สำหรับงานกล่อง
3. กำหนด Company Master Gateway และ validation contract
4. เพิ่ม field/attachment classification กับ outbound policy
5. เพิ่ม audit ของ AI extraction และ redaction
6. เปลี่ยน `field_path` แบบตำแหน่งเป็น `subject_type + subject_id + field_name`
7. เพิ่ม revision trigger ที่บังคับ `rfq_no`, `enquiry_ref` และลำดับ revision
8. เพิ่ม `revision_reason`, attachment retention และ index ที่ reviewer แนะนำ

## 2. System boundary

```text
Company DB / Estimate API
  - Customer
  - Employee
  - Paper
  - Corrugated
  - Coating / Foil / Special Ink
  - Box Template
  - Process / Price / Waste / Machine / Rate
                ^
                | read/validate by API
                | no local master replication
                |
RFQ Service + RFQ PostgreSQL
  - Enquiry/RFQ input
  - Selected external refs + audit snapshots
  - Clarification / Evidence
  - Readiness / Approval / Revision / Audit
                |
                | handoff only when READY_FOR_ESTIMATE
                v
Existing RFQ_Estimate / Deterministic Costing
```

ข้อห้าม:

- ห้ามทำ foreign key ข้าม database
- ห้ามดึง `tb_master_*` มาสร้าง local master table
- ห้ามใช้ snapshot label/code เป็นหลักฐานว่า master ยัง active
- ห้ามให้ frontend เรียก Company DB โดยตรง ต้องผ่าน RFQ/Estimate service
- ห้ามส่งราคา, rate, waste หรือ machine master เข้า RFQ payload โดยไม่จำเป็น

## 3. Company Master Gateway Contract

### 3.1 Endpoint ที่ระบบเดิมใช้อยู่

```text
GET /estimate/master_data?type=paper_info&estimate_type=packaging
GET /estimate/master_data?type=coating_info&estimate_type=packaging
GET /estimate/master_data?type=corrugated_info&estimate_type=packaging
GET /estimate/master_data?type=foilstamp_info
GET /estimate/master_data?type=boxtemplate_info
GET /estimate/master_data?type=specialink_info
GET /estimate/master_data?type=process_type
GET /estimate/master_data?type=price_type

GET /estimate/autocomplete?type=customer
GET /estimate/autocomplete?type=employee
GET /estimate/autocomplete?type=delivery
GET /estimate/customer
GET /estimate/emp_status
```

ประเภทที่ Costing Engine ใช้แต่ RFQ DB ห้าม copy:

```text
price_info
min_price_info
waste_info
blockdiecut_info
jetpress_waste_info
jetpress_info
marking_price_info
machine_std_paper_info
delivery_rate_info
konica_waste_info
exchange_rate
```

### 3.2 Minimum response contract

Company Master Gateway ต้อง normalize response จาก API เดิมให้แต่ละ record มีอย่างน้อย:

```json
{
  "source_system": "COMPANY_ESTIMATE_API",
  "master_type": "PAPER",
  "ref": "opaque-provider-reference",
  "code": "AC",
  "label": "Art Card",
  "active": true,
  "version": "optional-provider-version",
  "effective_at": "2026-07-27T00:00:00+07:00"
}
```

กติกา:

1. `ref` ต้อง stable และไม่เปลี่ยนเมื่อ label เปลี่ยน
2. ถ้า API ปัจจุบันยังไม่คืน stable ID ต้องให้ API/Data Owner ระบุ canonical key ก่อน migration
3. ห้าม RFQ service สร้าง hash จาก label แล้วเรียกว่า Master ID โดยพลการ
4. Draft บันทึก `*_raw` ได้เมื่อ resolve ไม่สำเร็จ
5. `READY_FOR_ESTIMATE` ต้อง revalidate critical `*_ref` กับ API
6. Company API ล่ม: บันทึก Draft ได้ แต่ Ready transition ต้อง fail closed พร้อม error ที่ retry ได้
7. Cache แบบ read-through ใน memory/Redis ใช้ได้เมื่อมี TTL และ ETag/version แต่ห้าม persist เป็น master table

### 3.3 External reference pattern

ทุก domain field ที่อ้าง Company Master ใช้รูปแบบ:

```text
*_ref               opaque ID จาก gateway
*_code_snapshot     code ตอนที่ผู้ใช้เลือก
*_name_snapshot     label ตอนที่ผู้ใช้เลือก
```

Snapshot มีไว้:

- แสดงประวัติ RFQ เดิมแม้ Master เปลี่ยนชื่อ
- อธิบายว่า Estimate revision นั้นใช้ตัวเลือกอะไร
- ตรวจ diff ระหว่าง revision

Snapshot ไม่มีสิทธิ์:

- ยืนยันว่า record ยัง active
- ให้ราคาปัจจุบัน
- แทนการเรียก Company API ตอน Ready/Estimate

## 4. RFQ workflow

```text
DRAFT
  -> NEEDS_CLARIFICATION
  -> READY_FOR_REVIEW
  -> READY_FOR_ESTIMATE
  -> handoff ไป Estimate

DRAFT/NEEDS_CLARIFICATION/READY_FOR_REVIEW
  -> CANCELLED

READY_FOR_ESTIMATE
  -> สร้าง revision ใหม่เท่านั้นเมื่อสเปกเปลี่ยน

revision เก่า
  -> SUPERSEDED
```

สถานะ `ESTIMATING`, `NEEDS_APPROVAL`, `APPROVED`, `REJECTED` และ `QUOTATION_CREATED` เป็น downstream lifecycle ไม่อยู่ใน `rfq.status_code`

## 5. Logical model

```text
rfq
├── rfq_item
│   ├── rfq_quantity_option
│   ├── rfq_design_variant
│   ├── rfq_component
│   │   ├── rfq_component_corrugated
│   │   └── rfq_process_requirement
│   ├── rfq_packing_requirement
│   └── rfq_delivery
├── rfq_attachment
├── rfq_external_ref_resolution
├── rfq_clarification
├── rfq_field_evidence
├── rfq_ai_extraction_run
├── rfq_readiness_run
│   └── rfq_readiness_check
├── rfq_signoff
├── rfq_status_history
└── rfq_estimate_link

rfq_field_policy
  - local policy configuration
  - ไม่ใช่ Company Business Master
```

## 6. PostgreSQL DDL v0.2

> DDL นี้เป็น design target ยังไม่ใช่ migration ที่อนุมัติแล้ว

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE rfq_status (
    code              text PRIMARY KEY,
    display_name_th   text NOT NULL,
    sort_order        integer NOT NULL UNIQUE,
    is_active         boolean NOT NULL DEFAULT true
);

INSERT INTO rfq_status (code, display_name_th, sort_order) VALUES
    ('DRAFT',               'แบบร่าง',                    10),
    ('NEEDS_CLARIFICATION', 'รอข้อมูลเพิ่มเติม',           20),
    ('READY_FOR_REVIEW',    'พร้อมให้ตรวจสอบ',             30),
    ('READY_FOR_ESTIMATE',  'พร้อมส่งเข้าประเมินราคา',      40),
    ('CANCELLED',           'ยกเลิก',                      90),
    ('SUPERSEDED',          'ถูกแทนที่ด้วย revision ใหม่',  99);
```

### 6.1 RFQ header และ revision identity

```sql
CREATE TABLE rfq (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- มาจากระบบ RFQ Estimate เดิม ห้าม generate format เองใน v0.2
    rfq_no                     text,
    rfq_number_source          text NOT NULL DEFAULT 'RFQ_ESTIMATE_API',

    revision_no                integer NOT NULL DEFAULT 1
                               CHECK (revision_no > 0),
    supersedes_rfq_id          uuid REFERENCES rfq(id),
    revision_reason            text,
    is_current                 boolean NOT NULL DEFAULT true,

    status_code                text NOT NULL DEFAULT 'DRAFT'
                               REFERENCES rfq_status(code),

    enquiry_ref                text,
    source_channel             text
                               CHECK (source_channel IS NULL OR source_channel IN
                               ('EMAIL', 'LINE', 'PHONE', 'MEETING', 'WEB_FORM',
                                'UPLOAD', 'OTHER')),
    source_channel_other       text,
    received_at                timestamptz NOT NULL DEFAULT now(),
    quote_due_at               timestamptz,
    priority_code              text NOT NULL DEFAULT 'NORMAL'
                               CHECK (priority_code IN
                               ('NORMAL', 'URGENT', 'KEY_ACCOUNT')),

    customer_ref               text,
    customer_code_snapshot     text,
    customer_name_snapshot     text,
    customer_name_raw          text,
    is_new_customer            boolean NOT NULL DEFAULT false,

    contact_name               text,
    contact_phone              text,
    contact_email              text,

    sales_owner_ref            text,
    sales_owner_code_snapshot  text,
    sales_owner_name_snapshot  text,
    customer_notes             text,

    created_by_ref             text NOT NULL,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_by_ref             text NOT NULL,
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    row_version                integer NOT NULL DEFAULT 1
                               CHECK (row_version > 0),

    ready_at                   timestamptz,
    ready_by_ref               text,
    cancelled_at               timestamptz,
    cancelled_reason           text,

    CHECK (quote_due_at IS NULL OR quote_due_at >= received_at),
    CHECK (
        (revision_no = 1 AND supersedes_rfq_id IS NULL)
        OR
        (revision_no > 1
         AND supersedes_rfq_id IS NOT NULL
         AND NULLIF(btrim(revision_reason), '') IS NOT NULL)
    ),
    CHECK (
        (status_code = 'READY_FOR_ESTIMATE'
         AND rfq_no IS NOT NULL
         AND ready_at IS NOT NULL
         AND ready_by_ref IS NOT NULL)
        OR status_code <> 'READY_FOR_ESTIMATE'
    ),
    UNIQUE (rfq_no, revision_no),
    UNIQUE (supersedes_rfq_id)
);

CREATE UNIQUE INDEX uq_rfq_current_revision
    ON rfq (rfq_no)
    WHERE is_current AND rfq_no IS NOT NULL;

CREATE INDEX ix_rfq_status_received
    ON rfq (status_code, received_at DESC);

CREATE INDEX ix_rfq_customer
    ON rfq (customer_ref);

CREATE INDEX ix_rfq_sales_owner
    ON rfq (sales_owner_ref, status_code);
```

### 6.2 Revision chain trigger

Trigger นี้ปิด blocker ที่ v0.1 ปล่อยให้ `supersedes_rfq_id` ชี้ข้าม RFQ:

```sql
CREATE OR REPLACE FUNCTION enforce_rfq_revision_chain()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    previous_row rfq%ROWTYPE;
BEGIN
    IF NEW.supersedes_rfq_id IS NULL THEN
        IF NEW.revision_no <> 1 THEN
            RAISE EXCEPTION
                'Initial RFQ must use revision_no = 1';
        END IF;
        RETURN NEW;
    END IF;

    SELECT *
      INTO previous_row
      FROM rfq
     WHERE id = NEW.supersedes_rfq_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Superseded RFQ does not exist';
    END IF;

    IF previous_row.rfq_no IS NULL THEN
        RAISE EXCEPTION
            'Cannot create a revision before RFQ number is assigned';
    END IF;

    IF NEW.rfq_no IS DISTINCT FROM previous_row.rfq_no THEN
        RAISE EXCEPTION
            'Revision must keep the same rfq_no';
    END IF;

    IF NEW.enquiry_ref IS DISTINCT FROM previous_row.enquiry_ref THEN
        RAISE EXCEPTION
            'Revision must keep the same enquiry_ref';
    END IF;

    IF NEW.revision_no <> previous_row.revision_no + 1 THEN
        RAISE EXCEPTION
            'Revision number must increment by exactly one';
    END IF;

    IF previous_row.is_current IS NOT TRUE THEN
        RAISE EXCEPTION
            'Only the current revision can be superseded';
    END IF;

    IF previous_row.status_code <> 'READY_FOR_ESTIMATE' THEN
        RAISE EXCEPTION
            'Create a revision only after READY_FOR_ESTIMATE; edit Draft in place';
    END IF;

    IF NEW.status_code <> 'DRAFT' OR NEW.is_current IS NOT TRUE THEN
        RAISE EXCEPTION
            'A new revision must start as current DRAFT';
    END IF;

    UPDATE rfq
       SET is_current = false,
           status_code = 'SUPERSEDED',
           updated_at = now()
     WHERE id = previous_row.id;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_enforce_rfq_revision_chain
BEFORE INSERT ON rfq
FOR EACH ROW
EXECUTE FUNCTION enforce_rfq_revision_chain();
```

ข้อกำหนดเพิ่มเติมของ service:

- การ assign `rfq_no` ครั้งแรกต้องรับค่าจาก RFQ Estimate API เท่านั้น
- หลัง assign แล้ว `rfq_no`, `rfq_number_source` และ `enquiry_ref` เป็น immutable identity
- Child rows ของ `READY_FOR_ESTIMATE` ห้าม update/delete ต้องสร้าง revision ใหม่
- การ clone revision ต้องทำใน transaction เดียวกับ status history

### 6.3 Packaging item

```sql
CREATE TABLE rfq_item (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    line_no                    smallint NOT NULL CHECK (line_no > 0),

    product_family_code        text NOT NULL DEFAULT 'PACKAGING'
                               CHECK (product_family_code = 'PACKAGING'),
    job_name                   text,
    product_type_ref           text,
    product_type_code_snapshot text,
    product_type_name_snapshot text,
    product_type_raw           text,
    description                text,
    intended_use               text,

    finished_width_mm          numeric(12,3),
    finished_length_mm         numeric(12,3),
    finished_depth_mm          numeric(12,3),

    is_reprint                 boolean NOT NULL DEFAULT false,
    previous_job_ref           text,
    use_previous_plate         boolean NOT NULL DEFAULT false,
    is_multiple_design         boolean NOT NULL DEFAULT false,

    finishing_state            text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (finishing_state IN
                               ('UNKNOWN', 'NONE', 'SPECIFIED')),
    packing_state              text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (packing_state IN
                               ('UNKNOWN', 'NONE', 'SPECIFIED')),
    artwork_state              text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (artwork_state IN
                               ('UNKNOWN', 'RECEIVED', 'NOT_RECEIVED',
                                'NOT_REQUIRED')),
    sample_state               text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (sample_state IN
                               ('UNKNOWN', 'AVAILABLE', 'NOT_AVAILABLE',
                                'NOT_REQUIRED')),
    sample_description         text,
    notes                      text,

    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now(),

    CHECK (finished_width_mm  IS NULL OR finished_width_mm  > 0),
    CHECK (finished_length_mm IS NULL OR finished_length_mm > 0),
    CHECK (finished_depth_mm  IS NULL OR finished_depth_mm  > 0),
    CHECK (NOT use_previous_plate OR is_reprint),

    UNIQUE (rfq_id, line_no),
    UNIQUE (id, rfq_id)
);

CREATE INDEX ix_rfq_item_rfq
    ON rfq_item (rfq_id, line_no);
```

Page count, cover/content และ binding จาก PDF ไม่ถูกลบทิ้งจาก roadmap แต่ไม่อยู่ใน Packaging v0.2 readiness ให้เพิ่มเป็น product profile ใหม่ภายหลังโดยไม่ทำให้ Packaging field กลายเป็น nullable จำนวนมาก

### 6.4 Quantity และหลาย design/F

```sql
CREATE TABLE rfq_quantity_option (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id           uuid NOT NULL REFERENCES rfq_item(id),
    option_no             smallint NOT NULL CHECK (option_no > 0),
    quantity              numeric(14,3) NOT NULL CHECK (quantity > 0),
    unit_ref              text,
    unit_code_snapshot    text,
    unit_name_snapshot    text,
    unit_raw              text,
    is_primary            boolean NOT NULL DEFAULT false,
    notes                 text,
    UNIQUE (rfq_item_id, option_no),
    UNIQUE (id, rfq_item_id)
);

CREATE UNIQUE INDEX uq_rfq_quantity_primary
    ON rfq_quantity_option (rfq_item_id)
    WHERE is_primary;

CREATE TABLE rfq_design_variant (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id           uuid NOT NULL REFERENCES rfq_item(id),
    variant_no            smallint NOT NULL CHECK (variant_no > 0),
    design_code           text NOT NULL,
    quantity              numeric(14,3),
    unit_ref              text,
    unit_code_snapshot    text,
    notes                 text,
    CHECK (quantity IS NULL OR quantity > 0),
    UNIQUE (rfq_item_id, variant_no),
    UNIQUE (rfq_item_id, design_code)
);
```

### 6.5 Packaging component

```sql
CREATE TABLE rfq_component (
    id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id                  uuid NOT NULL REFERENCES rfq_item(id),
    component_no                 smallint NOT NULL CHECK (component_no > 0),

    component_name               text,
    component_type_ref           text,
    component_type_code_snapshot text,
    component_type_name_snapshot text,
    component_type_raw           text,

    paper_ref                    text,
    paper_code_snapshot          text,
    paper_name_snapshot          text,
    paper_gsm_snapshot           numeric(8,2),
    paper_source_code            text
                                 CHECK (paper_source_code IS NULL OR
                                 paper_source_code IN
                                 ('DOMESTIC', 'IMPORTED',
                                  'CUSTOMER_SUPPLIED', 'UNKNOWN')),

    print_sides_code             text
                                 CHECK (print_sides_code IS NULL OR
                                 print_sides_code IN
                                 ('NONE', 'ONE_SIDE', 'TWO_SIDES')),
    color_outside_count          smallint,
    color_inside_count           smallint,
    ink_type_ref                 text,
    ink_type_code_snapshot       text,

    box_template_ref             text,
    box_template_code_snapshot   text,
    box_template_name_snapshot   text,
    box_width_mm                 numeric(12,3),
    box_length_mm                numeric(12,3),
    box_depth_mm                 numeric(12,3),
    flap_mm                      numeric(12,3),
    glue_mm                      numeric(12,3),
    tuck_mm                      numeric(12,3),

    notes                        text,

    CHECK (paper_gsm_snapshot  IS NULL OR paper_gsm_snapshot > 0),
    CHECK (color_outside_count IS NULL OR color_outside_count >= 0),
    CHECK (color_inside_count  IS NULL OR color_inside_count >= 0),
    CHECK (box_width_mm        IS NULL OR box_width_mm > 0),
    CHECK (box_length_mm       IS NULL OR box_length_mm > 0),
    CHECK (box_depth_mm        IS NULL OR box_depth_mm > 0),
    CHECK (flap_mm             IS NULL OR flap_mm >= 0),
    CHECK (glue_mm             IS NULL OR glue_mm >= 0),
    CHECK (tuck_mm             IS NULL OR tuck_mm >= 0),

    UNIQUE (rfq_item_id, component_no),
    UNIQUE (id, rfq_item_id)
);

CREATE INDEX ix_rfq_component_item
    ON rfq_component (rfq_item_id, component_no);
```

`paper_gsm_snapshot` ใช้แสดง/audit เท่านั้น Ready validator ต้องตรวจ `paper_ref` กับ Company API และยืนยันว่า GSM ที่ API คืนตรงกับ snapshot

### 6.6 Corrugated specification

```sql
CREATE TABLE rfq_component_corrugated (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_component_id           uuid NOT NULL UNIQUE
                               REFERENCES rfq_component(id),
    corrugated_board_ref       text,
    corrugated_code_snapshot   text,
    corrugated_name_snapshot   text,
    layer_count_snapshot       smallint,
    flute_code_snapshot        text,
    grade_spec_snapshot        jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes                      text,
    CHECK (layer_count_snapshot IS NULL OR layer_count_snapshot > 0)
);
```

ถ้า Company API ยังไม่มี stable `corrugated_board_ref` และคืนเพียง combination ของ layer/flute/type/gram ต้องให้ API owner กำหนด canonical reference ก่อน migration ห้าม RFQ service invent ID เอง

### 6.7 Finishing และงานพิเศษ

```sql
CREATE TABLE rfq_process_requirement (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id                uuid NOT NULL REFERENCES rfq_item(id),
    rfq_component_id           uuid,
    sequence_no                smallint NOT NULL CHECK (sequence_no > 0),

    process_ref                text,
    process_code_snapshot      text,
    process_name_snapshot      text,
    process_name_raw           text,

    option_ref                 text,
    option_code_snapshot       text,
    option_name_snapshot       text,
    option_name_raw            text,

    side_code                  text
                               CHECK (side_code IS NULL OR side_code IN
                               ('OUTSIDE', 'INSIDE', 'BOTH',
                                'NOT_APPLICABLE')),
    width_mm                   numeric(12,3),
    height_mm                  numeric(12,3),
    depth_mm                   numeric(12,3),

    color_ref                  text,
    color_code_snapshot        text,
    color_name_snapshot        text,

    specification_extra        jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes                      text,

    CHECK (width_mm  IS NULL OR width_mm  > 0),
    CHECK (height_mm IS NULL OR height_mm > 0),
    CHECK (depth_mm  IS NULL OR depth_mm  >= 0),
    CHECK (
        process_ref IS NOT NULL
        OR NULLIF(btrim(process_name_raw), '') IS NOT NULL
    ),

    UNIQUE (rfq_item_id, sequence_no),
    UNIQUE (id, rfq_item_id),
    FOREIGN KEY (rfq_component_id, rfq_item_id)
        REFERENCES rfq_component(id, rfq_item_id)
);
```

### 6.8 Packing และ delivery

```sql
CREATE TABLE rfq_packing_requirement (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id                uuid NOT NULL REFERENCES rfq_item(id),
    sequence_no                smallint NOT NULL CHECK (sequence_no > 0),
    packing_ref                text,
    packing_code_snapshot      text,
    packing_name_snapshot      text,
    packing_name_raw           text,
    quantity_per_pack          numeric(14,3),
    unit_ref                   text,
    unit_code_snapshot         text,
    specification             text,
    CHECK (quantity_per_pack IS NULL OR quantity_per_pack > 0),
    CHECK (
        packing_ref IS NOT NULL
        OR NULLIF(btrim(packing_name_raw), '') IS NOT NULL
    ),
    UNIQUE (rfq_item_id, sequence_no),
    UNIQUE (id, rfq_item_id)
);

CREATE TABLE rfq_delivery (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id                uuid NOT NULL REFERENCES rfq_item(id),
    quantity_option_id         uuid,
    delivery_no                smallint NOT NULL CHECK (delivery_no > 0),

    destination_ref            text,
    destination_code_snapshot  text,
    destination_name_snapshot  text,
    destination_raw            text,

    requested_date             date,
    quantity                   numeric(14,3),
    unit_ref                   text,
    unit_code_snapshot         text,
    is_split_delivery          boolean NOT NULL DEFAULT false,
    notes                      text,

    CHECK (quantity IS NULL OR quantity > 0),
    CHECK (
        destination_ref IS NOT NULL
        OR NULLIF(btrim(destination_raw), '') IS NOT NULL
    ),
    UNIQUE (rfq_item_id, delivery_no),
    UNIQUE (id, rfq_item_id),
    FOREIGN KEY (quantity_option_id, rfq_item_id)
        REFERENCES rfq_quantity_option(id, rfq_item_id)
);
```

### 6.9 Attachment classification และ retention

```sql
CREATE TABLE rfq_attachment (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    rfq_item_id                uuid,

    purpose_code               text NOT NULL
                               CHECK (purpose_code IN
                               ('ENQUIRY', 'SPEC', 'ARTWORK', 'DIELINE',
                                'SAMPLE_IMAGE', 'PREVIOUS_JOB', 'OTHER')),
    original_filename          text NOT NULL,
    object_store_key           text NOT NULL,
    mime_type                  text,
    size_bytes                 bigint
                               CHECK (size_bytes IS NULL OR size_bytes >= 0),
    sha256                     char(64),

    classification_code        text NOT NULL DEFAULT 'UNCLASSIFIED'
                               CHECK (classification_code IN
                               ('UNCLASSIFIED', 'INTERNAL',
                                'CONFIDENTIAL', 'RESTRICTED')),
    contains_personal_data     boolean,
    contains_trade_secret      boolean,
    cloud_action_code          text NOT NULL DEFAULT 'BLOCK'
                               CHECK (cloud_action_code IN
                               ('ALLOW', 'REDACT', 'LOCAL_ONLY', 'BLOCK')),
    classification_status      text NOT NULL DEFAULT 'PENDING'
                               CHECK (classification_status IN
                               ('PENDING', 'CONFIRMED', 'REJECTED')),
    classified_by_ref          text,
    classified_at              timestamptz,

    malware_scan_status        text NOT NULL DEFAULT 'PENDING'
                               CHECK (malware_scan_status IN
                               ('PENDING', 'CLEAN', 'BLOCKED', 'ERROR')),

    retention_policy_code      text,
    delete_after               timestamptz,
    legal_hold                 boolean NOT NULL DEFAULT false,
    deleted_at                 timestamptz,

    uploaded_by_ref            text NOT NULL,
    uploaded_at                timestamptz NOT NULL DEFAULT now(),
    notes                      text,

    CHECK (
        classification_status <> 'CONFIRMED'
        OR (classified_by_ref IS NOT NULL AND classified_at IS NOT NULL)
    ),
    CHECK (
        deleted_at IS NULL
        OR legal_hold IS FALSE
    ),

    UNIQUE (id, rfq_id),
    FOREIGN KEY (rfq_item_id, rfq_id)
        REFERENCES rfq_item(id, rfq_id)
);

CREATE INDEX ix_rfq_attachment_rfq
    ON rfq_attachment (rfq_id, purpose_code);

CREATE INDEX ix_rfq_attachment_sha256
    ON rfq_attachment (sha256)
    WHERE sha256 IS NOT NULL;
```

ไฟล์จริงอยู่ MinIO/NAS การลบต้องเป็น lifecycle operation ที่บันทึก audit ไม่ใช้ hard delete row

### 6.10 Stable field subject

v0.1 ใช้ `items[1].components[1]` ซึ่งเปลี่ยนความหมายเมื่อเรียง/ลบแถว v0.2 ใช้:

```text
subject_type = COMPONENT
subject_id   = 3d59... (rfq_component.id)
field_name   = paper_ref
```

ค่าที่รองรับ:

```text
RFQ
ITEM
QUANTITY
DESIGN_VARIANT
COMPONENT
CORRUGATED
PROCESS
PACKING
DELIVERY
ATTACHMENT
```

Service ต้องตรวจว่า `subject_id` อยู่ใต้ `rfq_id` เดียวกันทุกครั้ง ตาราง evidence/clarification/readiness ห้ามรับ positional path จาก client

### 6.11 External reference resolution audit

ตารางนี้เก็บผล resolve ของ reference ที่ถูกเลือกใน RFQ เท่านั้น ไม่ใช่ master copy:

```sql
CREATE TABLE rfq_external_ref_resolution (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    subject_type               text NOT NULL,
    subject_id                 uuid NOT NULL,
    field_name                 text NOT NULL,

    source_system              text NOT NULL DEFAULT 'COMPANY_ESTIMATE_API',
    master_type                text NOT NULL,
    external_ref               text NOT NULL,
    code_snapshot              text,
    name_snapshot              text,

    provider_version           text,
    response_etag              text,
    active_at_resolve          boolean NOT NULL,
    resolved_at                timestamptz NOT NULL DEFAULT now(),
    expires_at                 timestamptz,

    UNIQUE (rfq_id, subject_type, subject_id, field_name)
);

CREATE INDEX ix_rfq_external_ref_expiry
    ON rfq_external_ref_resolution (rfq_id, expires_at);
```

Ready validator ต้องยืนยัน:

- subject เป็นของ RFQ เดียวกัน
- `external_ref` ตรงกับค่าบน domain row
- `active_at_resolve = true`
- validation ยังไม่หมดอายุ
- critical refs ถูก revalidate ใน readiness run ปัจจุบัน

### 6.12 Clarification แบบ stable ID

```sql
CREATE TABLE rfq_clarification (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    subject_type               text NOT NULL,
    subject_id                 uuid NOT NULL,
    field_name                 text,

    question                   text NOT NULL,
    reason                     text,
    is_blocking                boolean NOT NULL DEFAULT true,
    status_code                text NOT NULL DEFAULT 'OPEN'
                               CHECK (status_code IN
                               ('OPEN', 'ANSWERED', 'WAIVED', 'CANCELLED')),
    raised_by_type             text NOT NULL
                               CHECK (raised_by_type IN
                               ('HUMAN', 'AI', 'VALIDATOR')),
    raised_by_ref              text,
    raised_at                  timestamptz NOT NULL DEFAULT now(),
    answer                     text,
    answered_by_ref            text,
    answered_at                timestamptz,
    waiver_reason              text,

    CHECK (
        status_code <> 'ANSWERED'
        OR (answer IS NOT NULL AND answered_at IS NOT NULL)
    ),
    CHECK (
        status_code <> 'WAIVED'
        OR NULLIF(btrim(waiver_reason), '') IS NOT NULL
    )
);

CREATE INDEX ix_rfq_clarification_open
    ON rfq_clarification (rfq_id, is_blocking)
    WHERE status_code = 'OPEN';

CREATE INDEX ix_rfq_clarification_subject
    ON rfq_clarification (subject_type, subject_id);
```

### 6.13 Field policy

Policy นี้เป็น security configuration ของ RFQ service ไม่ใช่ Business Master:

```sql
CREATE TABLE rfq_field_policy (
    subject_type          text NOT NULL,
    field_name            text NOT NULL,
    classification_code   text NOT NULL
                          CHECK (classification_code IN
                          ('INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')),
    data_category_code    text NOT NULL
                          CHECK (data_category_code IN
                          ('NONE', 'PERSONAL', 'TRADE_SECRET', 'MIXED')),
    cloud_action_code     text NOT NULL
                          CHECK (cloud_action_code IN
                          ('ALLOW', 'REDACT', 'LOCAL_ONLY', 'BLOCK')),
    redaction_method      text NOT NULL DEFAULT 'NONE'
                          CHECK (redaction_method IN
                          ('NONE', 'DROP', 'MASK', 'TOKENIZE')),
    policy_version        text NOT NULL,
    notes                 text,
    PRIMARY KEY (subject_type, field_name)
);

-- Default deny สำหรับ field ที่ยังไม่ได้จำแนก
INSERT INTO rfq_field_policy VALUES
('ANY', '*', 'RESTRICTED', 'MIXED', 'BLOCK', 'NONE', 'rfq-egress-v1',
 'Unregistered RFQ field is blocked from Cloud by default');

INSERT INTO rfq_field_policy VALUES
('RFQ', 'customer_ref',
 'CONFIDENTIAL', 'TRADE_SECRET', 'REDACT', 'TOKENIZE', 'rfq-egress-v1',
 'Use a request-scoped token outside the company'),
('RFQ', 'customer_name_raw',
 'CONFIDENTIAL', 'MIXED', 'REDACT', 'TOKENIZE', 'rfq-egress-v1',
 'Company/contact text may contain personal data'),
('RFQ', 'contact_name',
 'CONFIDENTIAL', 'PERSONAL', 'REDACT', 'TOKENIZE', 'rfq-egress-v1', NULL),
('RFQ', 'contact_phone',
 'CONFIDENTIAL', 'PERSONAL', 'REDACT', 'MASK', 'rfq-egress-v1', NULL),
('RFQ', 'contact_email',
 'CONFIDENTIAL', 'PERSONAL', 'REDACT', 'MASK', 'rfq-egress-v1', NULL),
('RFQ', 'customer_notes',
 'RESTRICTED', 'MIXED', 'LOCAL_ONLY', 'NONE', 'rfq-egress-v1',
 'Unstructured text cannot be assumed safe'),
('ITEM', '*',
 'CONFIDENTIAL', 'TRADE_SECRET', 'LOCAL_ONLY', 'NONE', 'rfq-egress-v1',
 'Job specification is confidential by default'),
('COMPONENT', '*',
 'CONFIDENTIAL', 'TRADE_SECRET', 'LOCAL_ONLY', 'NONE', 'rfq-egress-v1',
 'Dimensions/material/colors are confidential by default'),
('ATTACHMENT', '*',
 'RESTRICTED', 'MIXED', 'LOCAL_ONLY', 'NONE', 'rfq-egress-v1',
 'Raw RFQ files never go to Cloud without an approved exception');
```

Policy resolution priority:

```text
exact subject_type + field_name
  -> subject_type + '*'
  -> ANY + '*'
```

### 6.14 Field evidence แบบ stable ID

```sql
CREATE TABLE rfq_field_evidence (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    subject_type               text NOT NULL,
    subject_id                 uuid NOT NULL,
    field_name                 text NOT NULL,
    value_snapshot             jsonb,

    source_type                text NOT NULL
                               CHECK (source_type IN
                               ('MANUAL', 'EMAIL', 'LINE', 'PDF', 'DOCX',
                                'XLSX', 'IMAGE', 'MASTER_DATA',
                                'PREVIOUS_JOB', 'AI_INFERENCE')),
    source_attachment_id       uuid,
    source_page                integer,
    source_excerpt             text,

    extractor_name             text,
    extractor_version          text,
    confidence                 numeric(5,4),
    verification_status        text NOT NULL DEFAULT 'UNVERIFIED'
                               CHECK (verification_status IN
                               ('UNVERIFIED', 'VERIFIED',
                                'REJECTED', 'CORRECTED')),
    verified_by_ref            text,
    verified_at                timestamptz,
    correction_note            text,
    created_at                 timestamptz NOT NULL DEFAULT now(),

    CHECK (source_page IS NULL OR source_page > 0),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CHECK (
        verification_status = 'UNVERIFIED'
        OR (verified_by_ref IS NOT NULL AND verified_at IS NOT NULL)
    ),
    FOREIGN KEY (source_attachment_id, rfq_id)
        REFERENCES rfq_attachment(id, rfq_id)
);

CREATE INDEX ix_rfq_field_evidence_subject
    ON rfq_field_evidence
       (rfq_id, subject_type, subject_id, field_name, created_at DESC);
```

ตัวอย่าง:

```text
subject_type = COMPONENT
subject_id   = <rfq_component.id>
field_name   = paper_ref
```

ไม่มี `items[1]` หรือ index ตามลำดับอีกต่อไป

### 6.15 AI extraction และ Data Egress audit

```sql
CREATE TABLE rfq_ai_extraction_run (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    source_attachment_id       uuid,
    input_sha256               char(64),

    execution_target           text NOT NULL
                               CHECK (execution_target IN
                               ('LOCAL', 'CLOUD')),
    provider_name              text NOT NULL,
    model_name                 text NOT NULL,

    egress_policy_version      text NOT NULL,
    egress_decision_code       text NOT NULL
                               CHECK (egress_decision_code IN
                               ('LOCAL_ONLY', 'REDACTED_ALLOW',
                                'APPROVED_EXCEPTION', 'BLOCKED')),
    redaction_applied          boolean NOT NULL DEFAULT false,
    redaction_manifest         jsonb NOT NULL DEFAULT '{}'::jsonb,

    exception_approved_by_ref  text,
    exception_reason           text,

    status_code                text NOT NULL
                               CHECK (status_code IN
                               ('PENDING', 'SUCCEEDED', 'FAILED', 'BLOCKED')),
    started_at                 timestamptz NOT NULL DEFAULT now(),
    completed_at               timestamptz,
    error_code                 text,

    CHECK (
        execution_target <> 'CLOUD'
        OR egress_decision_code IN
           ('REDACTED_ALLOW', 'APPROVED_EXCEPTION')
    ),
    CHECK (
        egress_decision_code <> 'REDACTED_ALLOW'
        OR redaction_applied IS TRUE
    ),
    CHECK (
        egress_decision_code <> 'APPROVED_EXCEPTION'
        OR (exception_approved_by_ref IS NOT NULL
            AND NULLIF(btrim(exception_reason), '') IS NOT NULL)
    ),
    FOREIGN KEY (source_attachment_id, rfq_id)
        REFERENCES rfq_attachment(id, rfq_id)
);

CREATE INDEX ix_rfq_ai_extraction_run
    ON rfq_ai_extraction_run (rfq_id, started_at DESC);
```

Routing rule สำหรับ RFQ จริง:

```text
raw attachment / unstructured notes
  -> LOCAL_ONLY เป็นค่าเริ่มต้น

Cloud Claude
  -> ส่งได้เฉพาะ payload ที่ policy อนุญาต
  -> ต้อง redact ก่อน
  -> ต้องบันทึก manifest ว่าตัด/แทนค่าอะไร

Approved exception
  -> ต้องมี identity ผู้อนุมัติและเหตุผล
```

การอนุญาต Claude กับ SOP/WI ปัจจุบันไม่ใช่การอนุญาต RFQ attachment โดยอัตโนมัติ

### 6.16 Readiness, sign-off และ status history

```sql
CREATE TABLE rfq_readiness_run (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    validator_version          text NOT NULL,
    master_policy_version      text NOT NULL,
    egress_policy_version      text NOT NULL,
    executed_by_ref            text NOT NULL,
    executed_at                timestamptz NOT NULL DEFAULT now(),
    passed                     boolean NOT NULL,
    blocking_count             integer NOT NULL DEFAULT 0
                               CHECK (blocking_count >= 0),
    warning_count              integer NOT NULL DEFAULT 0
                               CHECK (warning_count >= 0),
    UNIQUE (id, rfq_id)
);

CREATE TABLE rfq_readiness_check (
    id                         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    readiness_run_id           uuid NOT NULL,
    rfq_id                     uuid NOT NULL,
    subject_type               text NOT NULL,
    subject_id                 uuid NOT NULL,
    field_name                 text,
    check_code                 text NOT NULL,
    severity                   text NOT NULL
                               CHECK (severity IN
                               ('BLOCKER', 'WARNING', 'INFO')),
    result_code                text NOT NULL
                               CHECK (result_code IN
                               ('PASS', 'FAIL', 'WAIVED')),
    detail                     text,
    waived_by_ref              text,
    waiver_reason              text,
    CHECK (
        result_code <> 'WAIVED'
        OR (waived_by_ref IS NOT NULL
            AND NULLIF(btrim(waiver_reason), '') IS NOT NULL)
    ),
    FOREIGN KEY (readiness_run_id, rfq_id)
        REFERENCES rfq_readiness_run(id, rfq_id)
);

CREATE TABLE rfq_signoff (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    signoff_role               text NOT NULL
                               CHECK (signoff_role IN
                               ('PREPARER', 'REVIEWER', 'APPROVER')),
    decision_code              text NOT NULL
                               CHECK (decision_code IN
                               ('CONFIRMED', 'RETURNED', 'REJECTED')),
    actor_ref                  text NOT NULL,
    actor_name_snapshot        text,
    auth_source                text NOT NULL DEFAULT 'EXISTING_JWT',
    signed_at                  timestamptz NOT NULL DEFAULT now(),
    comment                    text
);

CREATE TABLE rfq_status_history (
    id                         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    from_status_code           text REFERENCES rfq_status(code),
    to_status_code             text NOT NULL REFERENCES rfq_status(code),
    changed_by_ref             text NOT NULL,
    changed_at                 timestamptz NOT NULL DEFAULT now(),
    reason                     text,
    readiness_run_id           uuid REFERENCES rfq_readiness_run(id),
    idempotency_key            text,
    UNIQUE (rfq_id, idempotency_key)
);
```

Ready approver ยังเป็น open business decision ดังนั้น schema เก็บ sign-off แบบทั่วไป ส่วน policy adapter เป็นผู้ map JWT role เดิมว่าใครทำ `REVIEWER`/`APPROVER` ได้

### 6.17 Handoff ไป Estimate เดิม

```sql
CREATE TABLE rfq_estimate_link (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    estimate_system_code       text NOT NULL,
    external_estimate_id       text NOT NULL,
    scenario_code              text,
    is_primary                 boolean NOT NULL DEFAULT true,
    handoff_at                 timestamptz NOT NULL DEFAULT now(),
    handoff_by_ref             text NOT NULL,
    handoff_payload_sha256      char(64) NOT NULL,
    UNIQUE (estimate_system_code, external_estimate_id)
);

CREATE UNIQUE INDEX uq_rfq_primary_estimate
    ON rfq_estimate_link (rfq_id, estimate_system_code)
    WHERE is_primary;
```

สร้าง link ได้เฉพาะ revision ที่:

- `status_code = READY_FOR_ESTIMATE`
- `is_current = true`
- readiness run ล่าสุดผ่าน
- critical external refs revalidate แล้ว

### 6.18 Subject membership guard

`subject_type + subject_id` แก้ปัญหา path เปลี่ยนเมื่อ reorder/delete แล้ว แต่ยังต้องกัน
UUID ของ RFQ อื่นหลุดเข้ามาใน evidence/clarification/readiness ด้วย จึง enforce ซ้ำที่
PostgreSQL ไม่พึ่ง service validation อย่างเดียว:

```sql
CREATE OR REPLACE FUNCTION rfq_subject_belongs_to(
    p_rfq_id uuid,
    p_subject_type text,
    p_subject_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    CASE p_subject_type
        WHEN 'RFQ' THEN
            RETURN p_subject_id = p_rfq_id
               AND EXISTS (
                   SELECT 1 FROM rfq r
                   WHERE r.id = p_rfq_id
               );

        WHEN 'ITEM' THEN
            RETURN EXISTS (
                SELECT 1 FROM rfq_item i
                WHERE i.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'QUANTITY' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_quantity_option q
                JOIN rfq_item i ON i.id = q.rfq_item_id
                WHERE q.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'DESIGN_VARIANT' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_design_variant d
                JOIN rfq_item i ON i.id = d.rfq_item_id
                WHERE d.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'COMPONENT' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_component c
                JOIN rfq_item i ON i.id = c.rfq_item_id
                WHERE c.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'CORRUGATED' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_component_corrugated cc
                JOIN rfq_component c ON c.id = cc.rfq_component_id
                JOIN rfq_item i ON i.id = c.rfq_item_id
                WHERE cc.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'PROCESS' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_process_requirement p
                JOIN rfq_item i ON i.id = p.rfq_item_id
                WHERE p.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'PACKING' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_packing_requirement p
                JOIN rfq_item i ON i.id = p.rfq_item_id
                WHERE p.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'DELIVERY' THEN
            RETURN EXISTS (
                SELECT 1
                FROM rfq_delivery d
                JOIN rfq_item i ON i.id = d.rfq_item_id
                WHERE d.id = p_subject_id
                  AND i.rfq_id = p_rfq_id
            );

        WHEN 'ATTACHMENT' THEN
            RETURN EXISTS (
                SELECT 1 FROM rfq_attachment a
                WHERE a.id = p_subject_id
                  AND a.rfq_id = p_rfq_id
            );

        ELSE
            RETURN false;
    END CASE;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_rfq_subject_membership()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT rfq_subject_belongs_to(
        NEW.rfq_id,
        NEW.subject_type,
        NEW.subject_id
    ) THEN
        RAISE EXCEPTION
            'subject %:% does not belong to RFQ %',
            NEW.subject_type, NEW.subject_id, NEW.rfq_id
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_external_ref_subject_membership
BEFORE INSERT OR UPDATE OF rfq_id, subject_type, subject_id
ON rfq_external_ref_resolution
FOR EACH ROW EXECUTE FUNCTION enforce_rfq_subject_membership();

CREATE TRIGGER trg_clarification_subject_membership
BEFORE INSERT OR UPDATE OF rfq_id, subject_type, subject_id
ON rfq_clarification
FOR EACH ROW EXECUTE FUNCTION enforce_rfq_subject_membership();

CREATE TRIGGER trg_field_evidence_subject_membership
BEFORE INSERT OR UPDATE OF rfq_id, subject_type, subject_id
ON rfq_field_evidence
FOR EACH ROW EXECUTE FUNCTION enforce_rfq_subject_membership();

CREATE TRIGGER trg_readiness_check_subject_membership
BEFORE INSERT OR UPDATE OF rfq_id, subject_type, subject_id
ON rfq_readiness_check
FOR EACH ROW EXECUTE FUNCTION enforce_rfq_subject_membership();
```

Migration test ต้องลองทั้ง subject ที่ถูกต้อง, UUID ที่ไม่มีจริง และ UUID ที่อยู่คนละ
`rfq_id` สำหรับทั้ง 4 ตาราง

## 7. Packaging Ready-for-Estimate rules

### 7.1 Header

| Code | Severity | Rule |
|---|---|---|
| `RFQ-001` | Blocker | revision ปัจจุบันและ status เดิมเป็น `READY_FOR_REVIEW` |
| `RFQ-002` | Blocker | มี `rfq_no` จากระบบ RFQ Estimate เดิม |
| `RFQ-003` | Blocker | customer resolve แล้ว หรือผ่าน workflow ลูกค้าใหม่ |
| `RFQ-004` | Blocker | มีผู้ติดต่อและโทรศัพท์หรืออีเมลอย่างน้อยหนึ่งช่องทาง |
| `RFQ-005` | Blocker | มี Sales/AE owner ที่ Company API ยืนยันว่า active |
| `RFQ-006` | Blocker | ไม่มี blocking clarification ที่เปิดอยู่ |
| `RFQ-007` | Blocker | reviewer/approver ตาม policy ปัจจุบัน sign-off แล้ว |

### 7.2 Packaging item

| Code | Severity | Rule |
|---|---|---|
| `PKG-001` | Blocker | `product_family_code = PACKAGING` |
| `PKG-002` | Blocker | มีชื่องานและประเภทสินค้า |
| `PKG-003` | Blocker | มี quantity option อย่างน้อยหนึ่งแถวพร้อม unit ref |
| `PKG-004` | Blocker | มี finished W/L/D มากกว่า 0 |
| `PKG-005` | Blocker | มี component อย่างน้อยหนึ่งแถว |
| `PKG-006` | Blocker | Reprint ต้องมี previous job ref |
| `PKG-007` | Blocker | งานหลาย F/design ต้องมี variant และจำนวนครบ |
| `PKG-008` | Blocker | finishing/packing/artwork state ห้ามเป็น `UNKNOWN` |

### 7.3 Component/material

| Code | Severity | Rule |
|---|---|---|
| `COMP-001` | Blocker | มี component type ref ที่ Company API ยืนยัน |
| `COMP-002` | Blocker | มี paper ref และ GSM ที่ตรงกับ Company API |
| `COMP-003` | Blocker | ระบุหน้าเดียว/สองหน้าและจำนวนสีแต่ละด้าน |
| `COMP-004` | Blocker | มี box template ref หรือ Custom พร้อม Dieline |
| `COMP-005` | Blocker | มีขนาดกล่อง W/L/D |
| `COMP-006` | Blocker | Component ประกบ/ลูกฟูกต้องมี corrugated ref |
| `COMP-007` | Warning | สี/ขนาดเกิน profile ปกติ ต้องให้ Production Engineering ตรวจ |

### 7.4 Finishing, packing, delivery

| Code | Severity | Rule |
|---|---|---|
| `PROC-001` | Blocker | ถ้า `finishing_state = SPECIFIED` ต้องมี process |
| `PROC-002` | Blocker | process/option/color ref ที่ใช้ต้อง active |
| `PACK-001` | Blocker | ถ้า `packing_state = SPECIFIED` ต้องมี packing detail |
| `DLV-001` | Blocker | มี destination และ requested date หรือ waiver |
| `DLV-002` | Blocker | แบ่งส่งต้องมีจำนวนทุกเที่ยว |
| `DLV-003` | Blocker | ผลรวมแบ่งส่งตรงกับ quantity option |

### 7.5 Master/API

| Code | Severity | Rule |
|---|---|---|
| `MST-001` | Blocker | critical `*_ref` ทุกตัวมี resolution audit |
| `MST-002` | Blocker | resolution ยังไม่หมดอายุและ record active |
| `MST-003` | Blocker | snapshot ตรงกับ response ล่าสุด หรือมี accepted change |
| `MST-004` | Blocker | Company API unavailable ตอน Ready ให้ fail closed |
| `MST-005` | Warning | non-critical label เปลี่ยนแต่ ref เดิม ให้ refresh snapshot |

### 7.6 Privacy/Egress

| Code | Severity | Rule |
|---|---|---|
| `SEC-001` | Blocker | attachment ทุกไฟล์ classify และ malware scan ผ่าน |
| `SEC-002` | Blocker | field ที่ไม่มี policy ใช้ default `BLOCK` |
| `SEC-003` | Blocker | Cloud extraction ต้องเป็น redacted allow หรือ approved exception |
| `SEC-004` | Blocker | AI evidence จาก Cloud ต้องอ้าง extraction run ที่ถูก policy |
| `SEC-005` | Warning | retention policy/delete date ยังไม่ถูกกำหนด |

## 8. Ready transition transaction

```text
1. Verify caller identity/JWT ผ่าน existing auth adapter
2. SELECT rfq FOR UPDATE
3. ตรวจ row_version และ status = READY_FOR_REVIEW
4. ตรวจ revision/current identity
5. ตรวจ Packaging rules
6. Revalidate critical refs ผ่าน Company Master Gateway
7. บันทึก rfq_external_ref_resolution ชุดล่าสุด
8. ตรวจ field/attachment classification และ AI extraction audit
9. ตรวจ clarification และ sign-off
10. บันทึก readiness run/check
11. อัปเดต READY_FOR_ESTIMATE + ready_by/ready_at
12. บันทึก status history ด้วย idempotency key
13. Commit
```

ถ้า Company API timeout:

- rollback Ready transition
- คืน error แบบ retryable
- ห้ามใช้ stale local snapshot ผ่าน gate

## 9. Mapping ไป RFQ_Estimate เดิม

| v0.2 | Existing |
|---|---|
| `rfq.rfq_no` | `job.job_id` จากระบบเดิม |
| `customer_ref` | `customer.customer_id` |
| `sales_owner_ref` | `ae.ae_id` |
| `rfq_item.job_name` | `job.job_name` |
| `rfq_quantity_option[]` | `qty.main[]` |
| `rfq_design_variant[]` | `f_codes[]` / `f_detail.f_list[]` |
| `rfq_component[]` | `component1[]` |
| `paper_ref` + snapshots | `component1[].paper` |
| `box_template_ref` | `components[].box_template_id` |
| `rfq_component_corrugated` | `components[].corrugated` |
| `rfq_process_requirement[]` | coating/foil/emboss/deboss/process |
| `rfq_packing_requirement[]` | paperband/kraftwrap/carton/pallet |
| `rfq_delivery[]` | `delivery[]` |
| `rfq_attachment[]` | `fileUpload[]` |

Adapter ต้องส่ง ref ให้ระบบเดิม resolve กับ Company API ห้ามส่ง snapshot price/rate ไปแทน master

## 10. Database placement

v0.2 กำหนด logical boundary แต่ยังไม่ lock physical host:

```text
RFQ PostgreSQL
  - schema นี้
  - ไม่มี Company Master tables

Company DB
  - อยู่ระบบจริงของบริษัท
  - เข้าผ่าน Estimate API/Gateway
```

ก่อน migration ต้องตัดสินใจอย่างใดอย่างหนึ่ง:

1. database ใหม่สำหรับ RFQ service บน PostgreSQL instance ที่จัด resource แยก
2. schema แยกใน RFQ PostgreSQL เดิม พร้อม connection pool/resource limit

ห้ามวาง schema นี้ในฐานข้อมูล clinic หรือพึ่ง cross-database query ไป Company DB

## 11. Open decisions ที่ยัง block migration

1. Company API/Data Owner คือทีม/บุคคลใด
2. API คืน stable `ref` ของ Paper/Corrugated/Coating/Box Template จริงหรือไม่
3. ถ้าไม่มี stable ref ใครอนุมัติ canonical key contract
4. `job_id` ถูก reserve ตอนสร้าง Draft หรือออกตอน save/handoff
5. Ready approver คือ role ใด และต้องแยกจากผู้จัดทำหรือไม่
6. Retention ของ Enquiry, Artwork, Dieline และข้อมูลผู้ติดต่อกี่วัน/ปี
7. Exception ใดอนุญาต Cloud extraction ของ RFQ จริง และใครอนุมัติ
8. Physical PostgreSQL placement และ owner ของ backup/restore

## 12. Cross-check checklist สำหรับ v0.2

### ปิด 3 blocker

- [x] มี field/attachment classification และ Data Egress routing
- [x] `field_path` เปลี่ยนเป็น stable UUID subject
- [x] Revision chain บังคับ `rfq_no`, `enquiry_ref`, revision order และ current predecessor

### Packaging-first

- [x] ไม่มี page/binding เป็น blocker
- [x] มี box W/L/D, box template และ Custom Dieline path
- [x] มี paper, corrugated, colors, finishing, packing และ split delivery
- [x] รองรับ multi-quantity และ multi-F

### Master boundary

- [x] ไม่มี local Paper/Machine/Material/Rate master table
- [x] `*_ref` เป็น opaque Company API reference
- [x] Snapshot ใช้ audit เท่านั้น
- [x] Ready revalidate ผ่าน API และ fail closed
- [x] Costing-only master ไม่ไหลเข้า RFQ DB

### Tests ที่ migration ต้องมีภายหลัง

- [ ] revision ข้าม `rfq_no` ต้อง fail
- [ ] revision ที่ `enquiry_ref` ต่างต้อง fail
- [ ] reorder/delete component แล้ว evidence ยังชี้ UUID เดิม
- [ ] evidence/clarification/readiness ที่ชี้ UUID ของ RFQ อื่นต้อง fail
- [ ] unknown field policy ต้อง Cloud block
- [ ] raw RFQ attachment ส่ง Claude โดยไม่มี exception ต้อง fail
- [ ] redacted Cloud run ต้องมี manifest
- [ ] inactive Paper/Corrugated ref ต้อง Ready fail
- [ ] Company API timeout ต้อง Draft save ได้แต่ Ready fail
- [ ] historical RFQ ยังแสดง snapshot ได้เมื่อ Master เปลี่ยน label
- [ ] handoff payload ใช้ ref และไม่ส่ง master price/rate snapshot

---

## 13. Cross-check Review v0.2 (Claude, 2026-07-27)

**Verdict: ✅ ผ่าน — อนุมัติให้เริ่มเตรียม migration ได้**

### ยืนยันการปิด 3 blocker จาก review v0.1

1. **Data Egress/PDPA** — ปิดแล้ว ดีกว่าที่ขอ: `rfq_field_policy` (default-deny `ANY/*` = BLOCK),
   attachment classification + retention/legal_hold, `rfq_ai_extraction_run` มี CHECK บังคับว่า
   CLOUD ต้อง REDACTED_ALLOW (พร้อม manifest) หรือ APPROVED_EXCEPTION (พร้อมผู้อนุมัติ+เหตุผล)
   — เป็น schema-enforced ไม่ใช่แค่ policy บนกระดาษ และสอดคล้อง tripwire ใน STATUS.md ถูกต้อง
2. **Stable subject reference** — ปิดแล้ว: `subject_type + subject_id + field_name` แทน positional
   path ครบ 4 ตาราง + membership guard function/trigger กัน UUID ข้าม RFQ ที่ระดับ DB
   (unknown subject_type → false → reject = ปิด typo ไปในตัว)
3. **Revision chain** — ปิดแล้ว: trigger บังคับ rfq_no/enquiry_ref เดิม, revision +1,
   predecessor ต้อง current+READY, concurrent insert กันด้วย FOR UPDATE + UNIQUE(supersedes_rfq_id)
   — ตรวจ concurrency แล้วถูกหลัก (READ COMMITTED re-check หลัง lock ทำงานถูก)

### จุดต้องเก็บตอนเขียน migration (ไม่ block design)

1. **Trigger supersede ข้าม status_history** — `enforce_rfq_revision_chain` UPDATE previous row
   เป็น SUPERSEDED ตรงๆ โดยไม่ insert `rfq_status_history` → ประวัติ transition หาย 1 จังหวะ
   แก้: insert history ใน trigger ด้วย หรือระบุใน service contract ว่า clone-revision transaction
   ต้อง log ทั้ง (READY→SUPERSEDED ของตัวเก่า) และ (สร้าง DRAFT ตัวใหม่) เสมอ
2. **`rfq_field_policy.subject_type/field_name` เป็น free text** — policy ที่พิมพ์ผิด
   (เช่น `ITEMS/*`) จะไม่ match เงียบๆ แล้วตกไป default BLOCK (ปลอดภัยแต่ debug ยาก)
   แก้: เพิ่ม CHECK subject_type IN (รายการ + 'ANY') และ validation ตอน load policy
3. **Child tables ไม่มี `updated_by_ref`** — เจตนาพึ่ง shared audit subsystem (ยอมรับได้)
   แต่ต้องเขียนเป็น acceptance test ของ migration ว่า audit log ระดับ field ครอบคลุม child tables จริง

### ข้อสังเกตเชิงบวก

- ตัดสินใจถูกที่ให้ RFQ จริง strict (LOCAL_ONLY default) แม้ policy ปัจจุบันของ corpus SOP ผ่อน —
  ตรงกับ tripwire ไม่ใช่ตีความนโยบาย "เต็มที่ไปก่อน" เกินขอบเขต
- Fail-closed เมื่อ Company API ล่ม ณ Ready gate + ห้าม stale snapshot ผ่าน — ถูกหลัก
- Open decisions ข้อ 11 (8 ข้อ) คือ blocker list จริงของ migration — ใช้เป็นวาระตามงานได้เลย
