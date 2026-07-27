# RFQ PostgreSQL Schema Draft

**Version:** 0.1  
**วันที่:** 2026-07-27  
**สถานะ:** Design draft - ยังไม่ใช่ migration และยังไม่แก้ระบบที่รันอยู่

## 1. ข้อสรุปเชิงออกแบบ

RFQ ต้องเป็น Data Contract ระหว่าง Enquiry กับ Estimate:

```text
Enquiry
  -> จัดข้อมูลลง RFQ
  -> ตรวจข้อมูลขาด/ขัดแย้ง
  -> ผู้รับผิดชอบตรวจและยืนยัน
  -> Ready for Estimate
  -> Estimate/Costing เริ่มทำงาน
```

ข้อกำหนดหลักของ draft นี้:

1. RFQ เก็บข้อเท็จจริงและความต้องการลูกค้า ไม่เก็บผลคำนวณต้นทุนหรือ Margin
2. `READY_FOR_ESTIMATE` เป็น gate ที่ผ่าน validation และ human review แล้ว ไม่ใช่เพียงค่าที่ผู้ใช้เลือกเอง
3. รองรับทั้งงานหนังสือ/สิ่งพิมพ์ทั่วไปตาม PDF และงานกล่องตามระบบ `RFQ_Estimate` เดิม
4. จำนวนเสนอราคาหลายระดับ, หลายชิ้นส่วน, หลายจุดส่ง และหลายไฟล์ ต้องเป็นแถวลูก ไม่เก็บเป็น comma-separated text หรือ PostgreSQL array
5. ค่า AI ทุกค่าต้องมี source, confidence และสถานะการตรวจโดยมนุษย์
6. ค่า Master Data ใช้รหัสอ้างอิงและเก็บ label snapshot ประกอบ ไม่พึ่งชื่อ free text อย่างเดียว
7. รายการที่ปรับเปลี่ยนบ่อย เช่นชนิดกระดาษ วิธีเข้าเล่ม และกระบวนการหลังพิมพ์ ไม่ใช้ PostgreSQL native enum
8. เมื่อ RFQ ผ่าน `READY_FOR_ESTIMATE` แล้ว ห้ามแก้ข้อมูลเดิมแบบเงียบ ๆ การแก้ต้องสร้าง revision ใหม่และเก็บ audit trail

## 2. แหล่งข้อมูลที่ใช้ร่าง

### เอกสารผู้บริหาร

`Ai Agent และ EstimateV1 Update19072026.pdf`

- หน้า 13: ลูกค้า, ชื่องาน, ประเภทสินค้า, จำนวนพิมพ์, ขนาดสำเร็จ, จำนวนหน้า, จำนวนสี, หน้าเดียว/สองหน้า, กระดาษ, เข้าเล่ม, หลังพิมพ์, เคลือบ, ปั๊ม, งานพิเศษ, บรรจุ, สถานที่และวันที่ส่ง, ไฟล์ตัวอย่าง
- หน้า 14: ตรวจข้อมูลครบ, ข้อมูลขัดแย้ง, คำถามที่ต้องถามลูกค้า, สมมติฐาน และความเสี่ยง
- หน้า 17: สร้าง RFQ, AI แยกสเปก, ตรวจสอบ, ค้นงานเก่า, ส่งเข้า Estimate และบันทึกการแก้ไข
- หน้า 28-29: ฟอร์ม 5 ส่วน, checklist, ผู้จัดทำ/ผู้ตรวจสอบ/ผู้อนุมัติ และหลักการว่าต้องผ่าน `Ready for Estimate` ก่อนเริ่ม Estimate

### เอกสารทบทวน

`AI_AGENT_ESTIMATE_REVIEW.md`

- PostgreSQL เป็น source of truth ของ RFQ, Master Data, Workflow และ Audit
- LLM ใช้อ่าน Enquiry, เติมข้อมูล และแจ้งสิ่งที่ขาด แต่ไม่เป็นเจ้าของสูตรต้นทุน
- RFQ ต้องมาก่อน Estimate และต้องมี Ready-for-Estimate Gate

### ระบบเดิมที่ใช้ cross-check แบบ read-only

`../RFQ_Estimate`

- `est.mainData` รองรับ multi-quantity, multi-F, component, กระดาษ, สี, box template, finishing, packing และ delivery
- ระบบเดิมมี `tb_rfq_*` จำนวนมากซึ่งผสม input, costing และ derived result
- Schema ใหม่นี้จึงเป็น upstream RFQ contract ไม่แทนตารางคำนวณเดิมในรอบแรก

## 3. ขอบเขตและสิ่งที่ยังไม่ทำ

อยู่ในขอบเขต:

- RFQ header และ customer enquiry
- รายการสินค้า/งาน
- จำนวนที่ต้องการเสนอราคา
- ชิ้นส่วนและสเปกการพิมพ์
- งานหลังพิมพ์ บรรจุ และจัดส่ง
- ไฟล์แนบและที่มาของข้อมูล
- คำถามที่ต้องกลับไปถามลูกค้า
- Workflow, readiness validation, sign-off, revision และ audit
- จุดเชื่อมไปยังระบบ Estimate เดิม

อยู่นอกขอบเขต:

- Machine selection
- Imposition และจำนวนตัด
- Waste ที่ระบบคำนวณ
- Machine hourly rate
- Costing, Margin, Pricing และ Approval ราคาขาย
- Actual cost
- Quotation schema

รายการนอกขอบเขตต้องอยู่ใน Estimate/Costing schema แยกต่างหากและอ้าง `rfq.id` กับ revision ที่ใช้คำนวณ

## 4. Workflow

### 4.1 RFQ lifecycle

```text
DRAFT
  | \
  |  -> CANCELLED
  v
NEEDS_CLARIFICATION
  |  ^
  v  |
READY_FOR_REVIEW
  |
  | readiness ผ่าน + reviewer ยืนยัน
  v
READY_FOR_ESTIMATE
  |
  | ถ้าต้องแก้สเปก ให้สร้าง revision ใหม่
  v
SUPERSEDED
```

| Status | ความหมาย | ผู้มีสิทธิ์ดำเนินการหลัก |
|---|---|---|
| `DRAFT` | กำลังรวบรวมข้อมูลจาก Enquiry | Sales/AE หรือ AI ภายใต้การตรวจของคน |
| `NEEDS_CLARIFICATION` | มีข้อมูลสำคัญขาดหรือขัดแย้ง ต้องถามลูกค้า | Sales/AE |
| `READY_FOR_REVIEW` | กรอกครบเบื้องต้น รอผู้ตรวจสอบ | Pre-estimate reviewer |
| `READY_FOR_ESTIMATE` | ผ่านกฎและผู้ตรวจสอบยืนยันแล้ว ส่งต่อ Estimate ได้ | Reviewer/Approver ตาม policy |
| `CANCELLED` | ลูกค้ายกเลิกหรือไม่ดำเนินการต่อ | Owner/Manager |
| `SUPERSEDED` | มี revision ใหม่แทนที่ | System |

### 4.2 Downstream lifecycle

สถานะต่อไปนี้เป็นของ Estimate/Quotation ในอนาคต ไม่ควรยัดลง `rfq.status_code`:

```text
ESTIMATING
  -> NEEDS_APPROVAL
  -> APPROVED | REJECTED
  -> QUOTATION_CREATED
```

การแยกนี้ทำให้ RFQ คงความหมายว่าเป็นข้อมูลลูกค้าที่ตรวจแล้ว ส่วน Estimate เป็นผลคำนวณซึ่งอาจมีหลาย scenario หรือหลาย version ต่อ RFQ เดียว

### 4.3 Transition ที่อนุญาต

| From | To |
|---|---|
| `DRAFT` | `NEEDS_CLARIFICATION`, `READY_FOR_REVIEW`, `CANCELLED` |
| `NEEDS_CLARIFICATION` | `DRAFT`, `READY_FOR_REVIEW`, `CANCELLED` |
| `READY_FOR_REVIEW` | `NEEDS_CLARIFICATION`, `READY_FOR_ESTIMATE`, `CANCELLED` |
| `READY_FOR_ESTIMATE` | สร้าง revision ใหม่ หรือ `CANCELLED` โดยผู้มีอำนาจ |
| revision เก่า | `SUPERSEDED` เมื่อ revision ใหม่ได้รับการสร้าง |

## 5. Logical data model

```text
rfq
├── rfq_item
│   ├── rfq_quantity_option
│   ├── rfq_design_variant
│   ├── rfq_component
│   │   └── rfq_process_requirement
│   ├── rfq_packing_requirement
│   └── rfq_delivery
├── rfq_attachment
├── rfq_clarification
├── rfq_field_evidence
├── rfq_readiness_run
│   └── rfq_readiness_check
├── rfq_signoff
├── rfq_status_history
└── rfq_estimate_link
```

### 5.1 Coverage ของฟอร์มผู้บริหาร

| ส่วนในฟอร์ม PDF | Field หลัก | ตาราง |
|---|---|---|
| ข้อมูลลูกค้าและ Enquiry | วันที่รับ, ช่องทาง, บริษัท, ผู้ติดต่อ, โทรศัพท์, อีเมล, Sales owner, ระดับความเร่งด่วน, กำหนดตอบราคา | `rfq` |
| ข้อมูลผลิตภัณฑ์หลัก | ชื่องาน, ประเภทสินค้า, จำนวนและหน่วย, ขนาดสำเร็จ, จำนวนหน้า, ภาษา, ตัวอย่างเดิม, วัตถุประสงค์/หมายเหตุ | `rfq_item`, `rfq_quantity_option` |
| รายละเอียดชิ้นส่วนและสเปกพิมพ์ | ปก/เนื้อใน/แทรก/ชิ้นส่วนกล่อง, ขนาดกาง, จำนวนหน้า, กระดาษ/วัสดุ, แกรม, สีแต่ละด้าน, หน้าเดียว/สองหน้า | `rfq_component` |
| งานหลังพิมพ์/งานพิเศษ | เข้าเล่ม, พับ, เคลือบ, ปั๊มฟอยล์, ปั๊มนูน/จม, ไดคัท, ติดกาว และงานพิเศษ | `rfq_item`, `rfq_process_requirement` |
| บรรจุและจัดส่ง | วิธีบรรจุ, จำนวนต่อแพ็ก, สถานที่, วันที่, จำนวนและการแบ่งส่ง | `rfq_packing_requirement`, `rfq_delivery` |
| เอกสารแนบ | Enquiry, Spec, Artwork, Dieline, รูปหรือตัวอย่างงานเดิม | `rfq_attachment` |
| Checklist และข้อสงสัย | ข้อมูลขาด/ขัดแย้ง, คำถามลูกค้า, ผลตรวจแต่ละข้อ | `rfq_clarification`, `rfq_readiness_run`, `rfq_readiness_check` |
| ผู้จัดทำ/ตรวจสอบ/อนุมัติ | ผู้ปฏิบัติ, ผู้ตรวจ, ผู้อนุมัติ, วันเวลาและความเห็น | `rfq_signoff`, `rfq_status_history` |
| ข้อมูลที่ AI เติม | ค่า, แหล่งที่มา, confidence, ผู้ตรวจและการแก้ไข | `rfq_field_evidence` |

## 6. PostgreSQL DDL Draft

> ใช้ `gen_random_uuid()` จาก `pgcrypto` ในตัวอย่าง ถ้าองค์กรไม่อนุญาต extension ให้ application สร้าง UUID แล้วส่งเข้า DB แทน

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

### 6.1 RFQ header

```sql
CREATE TABLE rfq (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_no                     varchar(32) NOT NULL,
    revision_no                integer NOT NULL DEFAULT 1
                               CHECK (revision_no > 0),
    supersedes_rfq_id          uuid REFERENCES rfq(id),
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
    customer_name_raw          text,
    is_new_customer            boolean NOT NULL DEFAULT false,
    contact_name               text,
    contact_phone              text,
    contact_email              text,

    sales_owner_ref            text,
    sales_owner_name_snapshot  text,
    customer_notes             text,

    requested_currency_code    char(3) NOT NULL DEFAULT 'THB',

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
        (status_code = 'READY_FOR_ESTIMATE'
         AND ready_at IS NOT NULL
         AND ready_by_ref IS NOT NULL)
        OR status_code <> 'READY_FOR_ESTIMATE'
    ),
    UNIQUE (rfq_no, revision_no),
    UNIQUE (supersedes_rfq_id)
);

CREATE UNIQUE INDEX uq_rfq_current_revision
    ON rfq (rfq_no)
    WHERE is_current;

CREATE INDEX ix_rfq_status_received
    ON rfq (status_code, received_at DESC);

CREATE INDEX ix_rfq_customer
    ON rfq (customer_ref);

CREATE INDEX ix_rfq_sales_owner
    ON rfq (sales_owner_ref, status_code);
```

เหตุผลที่เก็บทั้ง `customer_ref` และ `customer_name_raw`:

- ถ้าเป็นลูกค้าเดิม ต้อง resolve เข้ากับ Customer Master
- ถ้าเป็นลูกค้าใหม่ Draft ยังเก็บชื่อที่อ่านจาก Enquiry ได้
- ก่อน `READY_FOR_ESTIMATE` ต้องมี `customer_ref` หรือผ่านขั้นตอนสร้าง/ยืนยันลูกค้าใหม่ตาม policy

### 6.2 รายการสินค้า/งาน

```sql
CREATE TABLE rfq_item (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                     uuid NOT NULL REFERENCES rfq(id),
    line_no                    smallint NOT NULL CHECK (line_no > 0),

    job_name                   text,
    product_type_ref           text,
    product_type_raw           text,
    description                text,
    intended_use               text,
    language_code              text,

    finished_width_mm          numeric(12,3),
    finished_length_mm         numeric(12,3),
    finished_height_mm         numeric(12,3),

    total_page_count           integer,
    cover_page_count           integer,
    content_page_count         integer,

    is_reprint                 boolean NOT NULL DEFAULT false,
    previous_job_ref           text,
    use_previous_plate         boolean NOT NULL DEFAULT false,
    is_multiple_design         boolean NOT NULL DEFAULT false,

    binding_state              text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (binding_state IN
                               ('UNKNOWN', 'NONE', 'SPECIFIED', 'NOT_APPLICABLE')),
    binding_method_ref         text,
    binding_method_raw         text,

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
    CHECK (finished_height_mm IS NULL OR finished_height_mm > 0),
    CHECK (total_page_count   IS NULL OR total_page_count   > 0),
    CHECK (cover_page_count   IS NULL OR cover_page_count   >= 0),
    CHECK (content_page_count IS NULL OR content_page_count >= 0),
    CHECK (
        NOT use_previous_plate
        OR is_reprint
    ),
    UNIQUE (rfq_id, line_no),
    UNIQUE (id, rfq_id)
);

CREATE INDEX ix_rfq_item_rfq
    ON rfq_item (rfq_id, line_no);
```

`UNKNOWN` ต้องต่างจาก `NONE` เสมอ:

- `UNKNOWN` = ยังไม่ได้ถามหรือ AI หาไม่พบ จึงยังไม่ผ่าน readiness
- `NONE` = ลูกค้ายืนยันแล้วว่าไม่ต้องการ
- `SPECIFIED` = มีรายละเอียดในตารางลูก
- `NOT_APPLICABLE` = ใช้เฉพาะกลุ่มที่ไม่เกี่ยวกับประเภทสินค้านั้น

### 6.3 จำนวนที่ต้องการเสนอราคา

```sql
CREATE TABLE rfq_quantity_option (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id        uuid NOT NULL REFERENCES rfq_item(id) ON DELETE CASCADE,
    option_no          smallint NOT NULL CHECK (option_no > 0),
    quantity           numeric(14,3) NOT NULL CHECK (quantity > 0),
    unit_ref           text,
    unit_raw           text,
    is_primary         boolean NOT NULL DEFAULT false,
    notes              text,
    UNIQUE (rfq_item_id, option_no),
    UNIQUE (id, rfq_item_id)
);

CREATE UNIQUE INDEX uq_rfq_quantity_primary
    ON rfq_quantity_option (rfq_item_id)
    WHERE is_primary;
```

จำนวน 5,000 / 10,000 / 20,000 ต้องเป็นสามแถว ไม่ใช่ string `"5000,10000,20000"`

### 6.4 หลาย design code หรือหลาย F

```sql
CREATE TABLE rfq_design_variant (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id        uuid NOT NULL REFERENCES rfq_item(id) ON DELETE CASCADE,
    variant_no         smallint NOT NULL CHECK (variant_no > 0),
    design_code        text NOT NULL,
    quantity           numeric(14,3),
    unit_ref           text,
    notes              text,
    CHECK (quantity IS NULL OR quantity > 0),
    UNIQUE (rfq_item_id, variant_no),
    UNIQUE (rfq_item_id, design_code)
);
```

### 6.5 ชิ้นส่วนและสเปกการพิมพ์

```sql
CREATE TABLE rfq_component (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id                uuid NOT NULL REFERENCES rfq_item(id)
                               ON DELETE CASCADE,
    component_no               smallint NOT NULL CHECK (component_no > 0),

    component_role_code        text
                               CHECK (component_role_code IS NULL OR
                               component_role_code IN
                               ('COVER', 'CONTENT', 'INSERT', 'BOX_PART',
                                'LABEL', 'OTHER')),
    component_name             text,
    component_type_code        text
                               CHECK (component_type_code IS NULL OR
                               component_type_code IN
                               ('PLAIN', 'LAMINATED_CORRUGATED',
                                'CORRUGATED_ONLY', 'OTHER')),

    page_count                 integer,
    flat_width_mm              numeric(12,3),
    flat_length_mm             numeric(12,3),

    material_ref               text,
    material_name_raw          text,
    grammage_gsm               numeric(8,2),
    material_source_code       text
                               CHECK (material_source_code IS NULL OR
                               material_source_code IN
                               ('DOMESTIC', 'IMPORTED', 'CUSTOMER_SUPPLIED',
                                'UNKNOWN')),

    print_sides_code           text
                               CHECK (print_sides_code IS NULL OR
                               print_sides_code IN
                               ('NONE', 'ONE_SIDE', 'TWO_SIDES')),
    color_outside_count        smallint,
    color_inside_count         smallint,
    ink_type_code              text,
    requested_print_process    text,

    box_template_ref           text,
    box_width_mm               numeric(12,3),
    box_length_mm              numeric(12,3),
    box_depth_mm               numeric(12,3),
    flap_mm                    numeric(12,3),
    glue_mm                    numeric(12,3),
    tuck_mm                    numeric(12,3),

    notes                      text,

    CHECK (page_count          IS NULL OR page_count > 0),
    CHECK (flat_width_mm       IS NULL OR flat_width_mm > 0),
    CHECK (flat_length_mm      IS NULL OR flat_length_mm > 0),
    CHECK (grammage_gsm        IS NULL OR grammage_gsm > 0),
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

จำนวนสีมากกว่าความสามารถเครื่องไม่ควรเป็น DB constraint เพราะเป็นเรื่อง Machine Master และ Production Engineering ให้ readiness validator แจ้ง warning/block ตามประเภทเครื่องภายหลัง

### 6.6 งานหลังพิมพ์และงานพิเศษ

```sql
CREATE TABLE rfq_process_requirement (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id                uuid NOT NULL REFERENCES rfq_item(id)
                               ON DELETE CASCADE,
    rfq_component_id           uuid,
    sequence_no                smallint NOT NULL CHECK (sequence_no > 0),

    process_ref                text,
    process_code_snapshot      text,
    process_name_raw           text,
    option_ref                 text,
    option_name_raw            text,
    side_code                  text
                               CHECK (side_code IS NULL OR side_code IN
                               ('OUTSIDE', 'INSIDE', 'BOTH', 'NOT_APPLICABLE')),
    width_mm                   numeric(12,3),
    height_mm                  numeric(12,3),
    depth_mm                   numeric(12,3),
    color_ref                  text,
    color_name_raw             text,
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
    FOREIGN KEY (rfq_component_id, rfq_item_id)
        REFERENCES rfq_component(id, rfq_item_id)
        ON DELETE CASCADE
);

CREATE INDEX ix_rfq_process_component
    ON rfq_process_requirement (rfq_component_id);
```

ตัวอย่าง `process_code_snapshot`:

```text
FOLDING
BINDING
COATING
FOIL_STAMP
EMBOSS
DEBOSS
DIE_CUT
GLUING
HANDWORK
OTHER
```

`specification_extra` ใช้เฉพาะรายละเอียดที่ต่างกันตาม process และยังไม่คุ้มสร้าง column เช่นตำแหน่ง Spot UV หรือรหัสบล็อก ห้ามใช้ JSONB แทน field หลักทั้งหมด

### 6.7 การบรรจุ

```sql
CREATE TABLE rfq_packing_requirement (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id        uuid NOT NULL REFERENCES rfq_item(id) ON DELETE CASCADE,
    sequence_no        smallint NOT NULL CHECK (sequence_no > 0),
    packing_ref        text,
    packing_code_snapshot text,
    packing_name_raw   text,
    quantity_per_pack  numeric(14,3),
    unit_ref           text,
    specification      text,
    CHECK (quantity_per_pack IS NULL OR quantity_per_pack > 0),
    CHECK (
        packing_ref IS NOT NULL
        OR NULLIF(btrim(packing_name_raw), '') IS NOT NULL
    ),
    UNIQUE (rfq_item_id, sequence_no)
);
```

ตัวอย่าง packing: มัดกระดาษ, ห่อฟิล์ม, ห่อ Kraft, ใส่กล่อง, วางพาเลต

### 6.8 การจัดส่ง

```sql
CREATE TABLE rfq_delivery (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id            uuid NOT NULL REFERENCES rfq_item(id)
                           ON DELETE CASCADE,
    quantity_option_id     uuid,
    delivery_no            smallint NOT NULL CHECK (delivery_no > 0),
    destination_ref        text,
    destination_raw        text,
    requested_date         date,
    quantity               numeric(14,3),
    unit_ref               text,
    is_split_delivery      boolean NOT NULL DEFAULT false,
    notes                  text,
    CHECK (quantity IS NULL OR quantity > 0),
    CHECK (
        destination_ref IS NOT NULL
        OR NULLIF(btrim(destination_raw), '') IS NOT NULL
    ),
    UNIQUE (rfq_item_id, delivery_no),
    FOREIGN KEY (quantity_option_id, rfq_item_id)
        REFERENCES rfq_quantity_option(id, rfq_item_id)
);
```

ถ้าเป็นการแบ่งส่ง:

- ทุกแถวต้องระบุวันที่และสถานที่
- ผลรวม `rfq_delivery.quantity` ของ quantity option เดียวกันต้องเท่ากับจำนวนที่ลูกค้าต้องการ หรือมี waiver ที่บันทึกเหตุผล

### 6.9 ไฟล์แนบ

```sql
CREATE TABLE rfq_attachment (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id) ON DELETE CASCADE,
    rfq_item_id            uuid,
    purpose_code           text NOT NULL
                           CHECK (purpose_code IN
                           ('ENQUIRY', 'SPEC', 'ARTWORK', 'DIELINE',
                            'SAMPLE_IMAGE', 'PREVIOUS_JOB', 'OTHER')),
    original_filename      text NOT NULL,
    object_store_key       text NOT NULL,
    mime_type              text,
    size_bytes             bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    sha256                 char(64),
    malware_scan_status    text NOT NULL DEFAULT 'PENDING'
                           CHECK (malware_scan_status IN
                           ('PENDING', 'CLEAN', 'BLOCKED', 'ERROR')),
    uploaded_by_ref        text NOT NULL,
    uploaded_at            timestamptz NOT NULL DEFAULT now(),
    notes                  text,
    UNIQUE (id, rfq_id),
    FOREIGN KEY (rfq_item_id, rfq_id)
        REFERENCES rfq_item(id, rfq_id)
        ON DELETE CASCADE
);

CREATE INDEX ix_rfq_attachment_rfq
    ON rfq_attachment (rfq_id, purpose_code);
```

เก็บไฟล์จริงใน MinIO/NAS และเก็บเฉพาะ metadata/object key ใน PostgreSQL

### 6.10 คำถามและข้อมูลที่ต้องขอเพิ่ม

```sql
CREATE TABLE rfq_clarification (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id) ON DELETE CASCADE,
    rfq_item_id            uuid,
    field_path             text,
    question               text NOT NULL,
    reason                 text,
    is_blocking            boolean NOT NULL DEFAULT true,
    status_code            text NOT NULL DEFAULT 'OPEN'
                           CHECK (status_code IN
                           ('OPEN', 'ANSWERED', 'WAIVED', 'CANCELLED')),
    raised_by_type         text NOT NULL
                           CHECK (raised_by_type IN
                           ('HUMAN', 'AI', 'VALIDATOR')),
    raised_by_ref          text,
    raised_at              timestamptz NOT NULL DEFAULT now(),
    answer                 text,
    answered_by_ref        text,
    answered_at            timestamptz,
    waiver_reason          text,
    CHECK (
        status_code <> 'ANSWERED'
        OR (answer IS NOT NULL AND answered_at IS NOT NULL)
    ),
    CHECK (
        status_code <> 'WAIVED'
        OR waiver_reason IS NOT NULL
    ),
    FOREIGN KEY (rfq_item_id, rfq_id)
        REFERENCES rfq_item(id, rfq_id)
        ON DELETE CASCADE
);

CREATE INDEX ix_rfq_clarification_open
    ON rfq_clarification (rfq_id, is_blocking)
    WHERE status_code = 'OPEN';
```

### 6.11 ที่มาของค่าที่ AI สกัด

```sql
CREATE TABLE rfq_field_evidence (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id) ON DELETE CASCADE,
    field_path             text NOT NULL,
    value_snapshot         jsonb,

    source_type            text NOT NULL
                           CHECK (source_type IN
                           ('MANUAL', 'EMAIL', 'LINE', 'PDF', 'DOCX', 'XLSX',
                            'IMAGE', 'MASTER_DATA', 'PREVIOUS_JOB',
                            'AI_INFERENCE')),
    source_attachment_id   uuid,
    source_page            integer,
    source_excerpt         text,

    extractor_name         text,
    extractor_version      text,
    confidence             numeric(5,4),
    verification_status    text NOT NULL DEFAULT 'UNVERIFIED'
                           CHECK (verification_status IN
                           ('UNVERIFIED', 'VERIFIED', 'REJECTED',
                            'CORRECTED')),
    verified_by_ref        text,
    verified_at            timestamptz,
    correction_note        text,

    created_at             timestamptz NOT NULL DEFAULT now(),

    CHECK (source_page IS NULL OR source_page > 0),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CHECK (
        verification_status = 'UNVERIFIED'
        OR (verified_by_ref IS NOT NULL AND verified_at IS NOT NULL)
    ),
    FOREIGN KEY (source_attachment_id, rfq_id)
        REFERENCES rfq_attachment(id, rfq_id)
);

CREATE INDEX ix_rfq_field_evidence_path
    ON rfq_field_evidence (rfq_id, field_path, created_at DESC);
```

ตัวอย่าง `field_path`:

```text
customer_name_raw
items[1].quantity_options[2].quantity
items[1].components[1].grammage_gsm
items[1].deliveries[1].requested_date
```

AI ต้องไม่เติมข้อมูลที่ไม่มีหลักฐานเป็นค่าจริง หากจำเป็นต้องตั้งสมมติฐานให้ใช้ `source_type = 'AI_INFERENCE'` และสร้าง clarification แบบ blocking

### 6.12 Readiness run และ checklist

```sql
CREATE TABLE rfq_readiness_run (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id) ON DELETE CASCADE,
    validator_version      text NOT NULL,
    executed_by_ref        text NOT NULL,
    executed_at            timestamptz NOT NULL DEFAULT now(),
    passed                 boolean NOT NULL,
    blocking_count         integer NOT NULL DEFAULT 0 CHECK (blocking_count >= 0),
    warning_count          integer NOT NULL DEFAULT 0 CHECK (warning_count >= 0)
);

CREATE TABLE rfq_readiness_check (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    readiness_run_id       uuid NOT NULL REFERENCES rfq_readiness_run(id)
                           ON DELETE CASCADE,
    check_code             text NOT NULL,
    field_path             text,
    severity               text NOT NULL
                           CHECK (severity IN ('BLOCKER', 'WARNING', 'INFO')),
    result_code            text NOT NULL
                           CHECK (result_code IN ('PASS', 'FAIL', 'WAIVED')),
    detail                 text,
    waived_by_ref          text,
    waiver_reason          text,
    CHECK (
        result_code <> 'WAIVED'
        OR (waived_by_ref IS NOT NULL AND waiver_reason IS NOT NULL)
    )
);

CREATE INDEX ix_rfq_readiness_latest
    ON rfq_readiness_run (rfq_id, executed_at DESC);
```

### 6.13 ผู้จัดทำ ผู้ตรวจสอบ และผู้อนุมัติ

```sql
CREATE TABLE rfq_signoff (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id) ON DELETE CASCADE,
    signoff_role           text NOT NULL
                           CHECK (signoff_role IN
                           ('PREPARER', 'REVIEWER', 'APPROVER')),
    decision_code          text NOT NULL
                           CHECK (decision_code IN
                           ('CONFIRMED', 'RETURNED', 'REJECTED')),
    actor_ref              text NOT NULL,
    actor_name_snapshot    text,
    signed_at              timestamptz NOT NULL DEFAULT now(),
    comment                text
);

CREATE INDEX ix_rfq_signoff_rfq
    ON rfq_signoff (rfq_id, signoff_role, signed_at DESC);
```

Policy ฝั่ง service ต้องบังคับว่า:

- `REVIEWER` ห้ามเป็นคนเดียวกับ `PREPARER` หากบริษัทกำหนด four-eyes principle
- ผู้ sign-off ต้องได้ identity จาก token/service authentication ไม่รับชื่อจาก request body เป็นหลักฐาน

### 6.14 Status history และ audit

```sql
CREATE TABLE rfq_status_history (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rfq_id                 uuid NOT NULL REFERENCES rfq(id) ON DELETE CASCADE,
    from_status_code       text REFERENCES rfq_status(code),
    to_status_code         text NOT NULL REFERENCES rfq_status(code),
    changed_by_ref         text NOT NULL,
    changed_at             timestamptz NOT NULL DEFAULT now(),
    reason                 text,
    readiness_run_id       uuid REFERENCES rfq_readiness_run(id),
    idempotency_key        text,
    UNIQUE (rfq_id, idempotency_key)
);

CREATE INDEX ix_rfq_status_history
    ON rfq_status_history (rfq_id, changed_at);
```

นอกจาก status history ควรมี application audit log สำหรับ field-level change:

```text
actor
service
request_id
rfq_id
revision_no
field_path
before_value
after_value
reason
changed_at
```

Audit log อาจอยู่ใน shared audit subsystem ของ Company AI Brain ไม่จำเป็นต้องสร้างซ้ำใน schema นี้

### 6.15 จุดเชื่อมไป Estimate เดิม

```sql
CREATE TABLE rfq_estimate_link (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id),
    estimate_system_code   text NOT NULL,
    external_estimate_id   text NOT NULL,
    scenario_code          text,
    is_primary             boolean NOT NULL DEFAULT true,
    handoff_at             timestamptz NOT NULL DEFAULT now(),
    handoff_by_ref         text NOT NULL,
    UNIQUE (estimate_system_code, external_estimate_id)
);

CREATE UNIQUE INDEX uq_rfq_primary_estimate
    ON rfq_estimate_link (rfq_id, estimate_system_code)
    WHERE is_primary;
```

สร้าง link ได้เฉพาะ RFQ revision ที่มีสถานะ `READY_FOR_ESTIMATE`

## 7. Ready-for-Estimate validation rules

### 7.1 กฎทั่วไป

| Code | Severity | กฎ |
|---|---|---|
| `RFQ-001` | Blocker | ต้องเป็น revision ปัจจุบัน (`is_current = true`) |
| `RFQ-002` | Blocker | ต้องอยู่สถานะ `READY_FOR_REVIEW` ก่อนขอเปลี่ยนเป็น `READY_FOR_ESTIMATE` |
| `RFQ-003` | Blocker | ต้องมี customer ที่ resolve แล้ว หรือมี workflow ลูกค้าใหม่ที่ผู้รับผิดชอบยืนยัน |
| `RFQ-004` | Blocker | ต้องมีชื่อผู้ติดต่อและอย่างน้อยหนึ่งช่องทาง: โทรศัพท์หรืออีเมล |
| `RFQ-005` | Blocker | ต้องมี Sales/AE owner |
| `RFQ-006` | Blocker | ต้องมีอย่างน้อยหนึ่ง `rfq_item` |
| `RFQ-007` | Blocker | ห้ามมี blocking clarification ที่ยัง `OPEN` |
| `RFQ-008` | Blocker | ต้องมี reviewer sign-off ล่าสุดเป็น `CONFIRMED` |
| `RFQ-009` | Blocker | readiness run ล่าสุดต้องใช้ validator version ปัจจุบันและไม่มี blocker |
| `RFQ-010` | Warning | `quote_due_at` ใกล้เกิน SLA ที่บริษัทกำหนด |

### 7.2 กฎต่อรายการสินค้า

| Code | Severity | กฎ |
|---|---|---|
| `ITEM-001` | Blocker | ต้องมีชื่องาน |
| `ITEM-002` | Blocker | ต้อง resolve ประเภทสินค้า หรือระบุ `product_type_raw` พร้อม clarification |
| `ITEM-003` | Blocker | ต้องมี quantity option อย่างน้อยหนึ่งแถว และทุกค่ามากกว่า 0 |
| `ITEM-004` | Blocker | ต้องมีหน่วยของทุก quantity option |
| `ITEM-005` | Blocker | ต้องมีขนาดสำเร็จตาม profile ของประเภทสินค้า |
| `ITEM-006` | Blocker | ต้องมี component อย่างน้อยหนึ่งแถว |
| `ITEM-007` | Blocker | `binding_state`, `finishing_state`, `packing_state` ห้ามเป็น `UNKNOWN` |
| `ITEM-008` | Blocker | ถ้าเป็น Reprint ต้องมี previous job reference หรือ waiver |
| `ITEM-009` | Blocker | ถ้า `is_multiple_design = true` ต้องมี design variant และจำนวนของแต่ละแบบ |
| `ITEM-010` | Warning | ไม่มีไฟล์ Artwork/Dieline/Sample และไม่ได้ระบุว่าไม่จำเป็น |

### 7.3 กฎต่อชิ้นส่วน

| Code | Severity | กฎ |
|---|---|---|
| `COMP-001` | Blocker | ต้องมีชื่อ/บทบาทของ component เช่น ปก เนื้อใน หรือชิ้นส่วนกล่อง |
| `COMP-002` | Blocker | ชิ้นส่วนที่เป็นกระดาษต้องมีชนิดกระดาษและแกรม |
| `COMP-003` | Blocker | งานพิมพ์ต้องระบุหน้าเดียว/สองหน้าและจำนวนสีแต่ละด้าน |
| `COMP-004` | Blocker | จำนวนสีต้องไม่ติดลบ และต้องสอดคล้องกับ `print_sides_code` |
| `COMP-005` | Blocker | งานหนังสือต้องมีจำนวนหน้าของปก/เนื้อในตาม profile |
| `COMP-006` | Blocker | งานกล่องต้องมี W/L/D และ box template หรือแนบ custom dieline |
| `COMP-007` | Warning | สเปกเกินขีดจำกัดเครื่องมาตรฐาน ต้องให้ Production Engineering ตรวจ |

### 7.4 กฎเข้าเล่ม งานหลังพิมพ์ และบรรจุ

| Code | Severity | กฎ |
|---|---|---|
| `PROC-001` | Blocker | ถ้า `finishing_state = SPECIFIED` ต้องมี process อย่างน้อยหนึ่งแถว |
| `PROC-002` | Blocker | Process ที่ต้องใช้ขนาด/สี/ด้าน ต้องกรอกรายละเอียดให้ครบ |
| `BIND-001` | Blocker | ถ้า `binding_state = SPECIFIED` ต้องมี binding method |
| `BIND-002` | Blocker | งานเย็บมุงหลังคาต้องมีจำนวนหน้าที่เข้าเงื่อนไข signature; ค่าไม่ลงตัวต้องให้ผู้ตรวจสอบยืนยัน exception |
| `PACK-001` | Blocker | ถ้า `packing_state = SPECIFIED` ต้องมี packing requirement |
| `PACK-002` | Blocker | ถ้าระบุจำนวนต่อแพ็ก ต้องมากกว่า 0 และมีหน่วย |

หมายเหตุ: ตัวอย่างใน PDF ระบุว่า 66 หน้าอาจไม่สอดคล้องกับเย็บมุงหลังคา กฎจริงต้องให้ Production/Estimator ยืนยันเรื่องการนับปก แทรก และหน้าว่างก่อน lock validator

### 7.5 กฎการจัดส่ง

| Code | Severity | กฎ |
|---|---|---|
| `DLV-001` | Blocker | ต้องมีสถานที่และวันที่ต้องการอย่างน้อยหนึ่งรายการ หรือมี waiver ว่ายังไม่ใช้ราคา delivery |
| `DLV-002` | Blocker | วันที่ส่งต้องไม่ก่อนวันที่รับ Enquiry |
| `DLV-003` | Blocker | ถ้าแบ่งส่ง ต้องมีจำนวนของแต่ละรอบ |
| `DLV-004` | Blocker | ผลรวมจำนวนแบ่งส่งต้องเท่ากับ quantity option ที่อ้าง |
| `DLV-005` | Warning | Lead time ต่ำกว่าเกณฑ์ของ product profile ต้องตรวจ capacity |

### 7.6 กฎ AI และหลักฐาน

Critical fields ต่อไปนี้ต้องมีหลักฐานและ `VERIFIED` ก่อน Ready:

```text
customer
job_name
product_type
quantity + unit
finished dimensions
page count
paper/material + gsm
print sides + colors
binding/finishing
packing
delivery destination + date
```

กฎเพิ่มเติม:

- ค่าที่ผู้รับผิดชอบกรอกเองผ่าน session ที่ยืนยันตัวตนแล้ว อาจบันทึกเป็น `MANUAL + VERIFIED` ได้ตาม policy
- ค่า `AI_INFERENCE` ห้ามทำให้ critical field ผ่านอัตโนมัติ
- ถ้าเอกสารสองแหล่งให้ค่าต่างกัน ต้องสร้าง blocking clarification
- ค่าที่มนุษย์แก้ต้องเก็บ evidence ใหม่เป็น `CORRECTED` พร้อม note
- confidence ใช้จัดลำดับให้คนตรวจ ไม่ใช้แทน business validation

## 8. วิธี enforce Ready-for-Estimate

ไม่ควรใช้ `UPDATE rfq SET status_code = 'READY_FOR_ESTIMATE'` โดยตรง

ให้มี service operation เดียว เช่น:

```text
POST /rfqs/{id}/transitions/ready-for-estimate
```

ลำดับใน transaction:

```sql
BEGIN;

SELECT *
FROM rfq
WHERE id = :rfq_id
FOR UPDATE;

-- 1. ตรวจ optimistic row_version
-- 2. รัน validator ที่ระบุ version
-- 3. บันทึก rfq_readiness_run + rfq_readiness_check
-- 4. ตรวจ blocking clarification
-- 5. ตรวจ critical field evidence
-- 6. ตรวจ reviewer sign-off
-- 7. ถ้าผ่าน อัปเดต status/ready_at/ready_by/row_version
-- 8. บันทึก rfq_status_history ด้วย idempotency_key

COMMIT;
```

Database `CHECK` เหมาะกับกฎภายในแถว เช่นจำนวนต้องเป็นบวก ส่วนกฎข้ามตารางและกฎตามประเภทสินค้าให้ validator service เป็นผู้ตรวจและบันทึกผลที่อธิบายได้

## 9. Mapping ไป `RFQ_Estimate` เดิม

| Schema ใหม่ | `est.mainData` เดิม |
|---|---|
| `rfq.rfq_no` / `rfq.id` | `job.job_id` หรือ upstream reference ใหม่ |
| `rfq.customer_ref`, `customer_name_raw` | `customer.customer_id`, `customer.customer_name` |
| `rfq.sales_owner_ref` | `ae.ae_id` |
| `rfq_item.job_name` | `job.job_name` |
| `rfq_item.is_reprint` | `job.is_reprinted` |
| `rfq_quantity_option[]` | `qty.main[]` |
| `rfq_design_variant[]` | `f_codes[]` / `f_detail.f_list[]` |
| `rfq_component[]` | `component1[]` |
| material/gram/colors | `component1[].paper`, `component1[].color` |
| box dimensions/template | `components[].dimensions_mm`, `box_template_id` |
| `rfq_process_requirement[]` | coating/foil/emboss/deboss/process/material |
| `rfq_delivery[]` | `delivery[]` |
| `rfq_attachment[]` | `fileUpload[]` |

ข้อเสนอ integration:

1. ระบบใหม่เป็นเจ้าของ Enquiry/RFQ และ Ready gate
2. เมื่อ Ready ให้ adapter แปลง snapshot ไปเป็น input ของ `RFQ_Estimate`
3. บันทึก external estimate ID ใน `rfq_estimate_link`
4. สูตรและ `tb_rfq_*` เดิมยังคำนวณเหมือนเดิมในช่วงเปลี่ยนผ่าน
5. Field ที่ระบบเดิมคำนวณได้เอง เช่น paper cost, waste, layout, machine และราคา ไม่ควรบังคับ Sales กรอกใน RFQ upstream

## 10. Enum, Master Data, Free text และ JSONB

| ประเภทข้อมูล | วิธีเก็บที่แนะนำ | ตัวอย่าง |
|---|---|---|
| สถานะระบบที่มี transition ชัด | Lookup table + FK | `rfq_status` |
| รายการธุรกิจที่เพิ่ม/ปิดใช้งานได้ | Master table + FK/ref | paper, product type, binding, process, packing |
| ข้อมูลที่ยัง resolve ไม่ได้ใน Draft | `*_raw` คู่กับ `*_ref` | `customer_name_raw`, `material_name_raw` |
| คำอธิบาย/หมายเหตุ | Free text | customer notes, clarification answer |
| ตัวแปรเฉพาะ process ที่ไม่เสถียร | JSONB แบบจำกัดขอบเขต | `specification_extra` |
| ตัวเลขสำคัญที่ใช้ filter/report | Typed column | quantity, gsm, size, colors, date |

ไม่แนะนำ PostgreSQL native enum สำหรับชนิดกระดาษ/งานหลังพิมพ์ เพราะการเพิ่มค่าต้อง deploy schema และค่าบางรายการอาจถูกปิดใช้งานแต่ยังต้องอ่านประวัติเก่า

## 11. ประเด็นที่ต้องให้ Business/Data Owner ตัดสินใจก่อน migration

1. V1 จะเริ่มจากงาน 3-5 ประเภทใด ระหว่างหนังสือ สิ่งพิมพ์ทั่วไป และกล่อง
2. นิยาม page count ของแต่ละประเภท รวมปก/ไม่รวมปกอย่างไร
3. กฎเย็บมุงหลังคาและวิธีเข้าเล่มอื่นที่เป็น blocker จริง
4. Customer Master และ Employee Master ใช้ ID ชนิดใดและใครเป็น owner
5. รายการ Product, Component Role, Paper, Binding, Process, Packing และ Unit ที่อนุมัติแล้ว
6. ไฟล์ใดบังคับก่อน Ready เช่น Artwork, Dieline, ตัวอย่างจริง หรือ Spec
7. กรณีไม่คิดค่าจัดส่ง อนุญาตให้ Ready โดยไม่มี delivery detail ได้หรือไม่
8. ใครเป็น Reviewer/Approver และต้องแยกคนจากผู้จัดทำหรือไม่
9. SLA ของ Urgent/Key Account และระดับใดต้องตรวจ Capacity
10. หลัง Ready แล้ว หากลูกค้าเปลี่ยนสเปก จะสร้าง revision ใหม่ทุกครั้งหรือมีช่วงแก้ไขที่อนุญาต
11. RFQ number format เช่น `RFQ-2026-000125` และผู้รับผิดชอบการออกเลข
12. Existing `tb_rfq_*` ใดเป็น source of truth ชั่วคราวระหว่าง migration

## 12. Acceptance criteria ของ schema draft

ก่อนนำไปทำ migration ต้องพิสูจน์ด้วยตัวอย่างอย่างน้อย:

- งานหนังสือ 1 งานที่มีปก/เนื้อใน/เข้าเล่มและหลายจำนวน
- งานกล่อง 1 งานที่มีหลาย component, box template และงานหลังพิมพ์
- งานหลาย F/design 1 งาน
- งานแบ่งส่งอย่างน้อย 2 รอบ
- Enquiry ที่ข้อมูลขาดและต้องกลับไปถามลูกค้า
- ค่า AI ผิด 1 field แล้วมนุษย์แก้พร้อม evidence
- RFQ ที่ผ่าน Ready แล้วมีการแก้สเปกและสร้าง revision ใหม่
- Handoff เข้า `RFQ_Estimate` เดิมโดยไม่เปลี่ยนสูตรต้นทุน

ผลที่ต้องได้:

1. ไม่มี critical field ที่ไม่ทราบค่าแต่ถูกตีความเป็น `NONE`
2. ไม่สามารถส่ง RFQ ที่มี blocker เข้า Estimate
3. อธิบายได้ว่าทุก critical field มาจากไหนและใครตรวจ
4. ดึง snapshot เดิมที่ใช้ Estimate ย้อนหลังได้
5. เพิ่ม product/process master ใหม่ได้โดยไม่แก้ PostgreSQL enum

---

## 13. Cross-check Review (Claude, 2026-07-27)

**Verdict: อนุมัติทิศทาง — design ระดับใช้งานได้จริง มี 3 จุดต้องแก้ก่อนเขียน migration**

### จุดแข็งที่ยืนยันว่าถูกต้อง

- `UNKNOWN` ≠ `NONE` ≠ `NOT_APPLICABLE` — กันปัญหา "ไม่ได้ถาม" ถูกตีความเป็น "ไม่ต้องการ" ได้จริง
- Multi-quantity / multi-component / multi-delivery เป็นแถวลูกทั้งหมด — ตรง domain งานพิมพ์
- `*_ref` + `*_raw` + snapshot — รองรับ Draft ที่ master ยัง resolve ไม่ได้ โดยไม่เสีย integrity
- `rfq_field_evidence` (source + confidence + verification) — implement หลักการ "AI เสนอ มนุษย์ตรวจ" ระดับ field
- Composite FK `(id, rfq_id)` กัน child ข้าม parent — ถูกหลัก
- แยก RFQ lifecycle ออกจาก Estimate lifecycle — ถูกต้อง กัน scope creep ใน status enum
- Ready gate เป็น service operation เดียว + `FOR UPDATE` + idempotency — ถูกหลัก concurrency

### ต้องแก้ก่อน migration (3 จุด)

1. **PDPA / Data Egress ยังไม่ถูก mark ระดับ field** — `contact_name/phone/email` และไฟล์แนบลูกค้า
   เป็นข้อมูลส่วนบุคคล+ความลับการค้า ตาม decision ใน STATUS.md (Claude ใช้กับข้อมูล
   สังเคราะห์/redacted เท่านั้น) ขั้น AI extraction ที่อ่าน Enquiry ดิบจะเจอข้อมูลเหล่านี้เสมอ
   → ต้องเพิ่ม: ตาราง/annotation ระบุ field ไหนเป็น `PERSONAL / TRADE_SECRET / INTERNAL`
   และ extraction pipeline ของจริงต้องเป็น Local LLM หรือ redact ก่อนส่ง cloud จนกว่าบริษัทอนุมัติ
2. **`field_path` ใช้ index ตามตำแหน่ง** (`items[1].components[1]`) — ถ้าลบ/สลับแถว path
   จะชี้ผิดเงียบๆ → เปลี่ยนเป็นอ้างด้วย id (`items[<uuid>]`) หรือเพิ่มคอลัมน์
   `rfq_item_id`/`rfq_component_id` ใน `rfq_field_evidence` แล้วให้ `field_path` เป็นชื่อ field เฉยๆ
3. **Revision chain ไม่บังคับ `rfq_no` เดียวกัน** — `supersedes_rfq_id` ชี้ข้าม rfq_no ได้
   → เพิ่ม trigger หรือ service check ว่า revision ใหม่ต้องมี `rfq_no` เดิม + copy `enquiry_ref`

### ควรเพิ่ม (ไม่ block)

- `revision_reason` บนตาราง `rfq` (ตอนนี้เหตุผลอยู่แค่ใน status_history)
- Retention/deletion policy ของ `rfq_attachment` (PDPA มาตรา 37 — ไฟล์ลูกค้าเก็บนานแค่ไหน)
- Index `rfq_clarification (rfq_item_id)` และ `rfq_attachment (sha256)` สำหรับ dedupe
- ระบุตั้งแต่แรกว่า schema นี้อยู่ database ไหน (แนะนำ: PostgreSQL instance ใหม่แยกจาก
  ระบบ siriwattana/clinic บน server เดิม — กัน resource ชนกับ pgvector ที่มีอยู่)

### ขั้นถัดไปที่แนะนำ

ข้อ 11 (คำถาม 12 ข้อ) คือ**วาระประชุมกับเฮียโดยตรง** — โดยเฉพาะข้อ 1 (เลือกงาน 3 ประเภท),
ข้อ 4-5 (Master Data owner) และข้อ 11 (เลข RFQ) เพราะทุกอย่างที่เหลือ block อยู่บนคำตอบพวกนี้
