-- ============================================================================
-- 001_rfq_core.sql — RFQ service schema (Packaging v1) — tables + indexes
-- ต้นทาง: RFQ_SCHEMA_V0_2.md ข้อ 6 | ใส่ finding #2 (field_policy subject_type CHECK)
-- Triggers/functions อยู่ 002 | seed อยู่ 003
-- รันบน PostgreSQL เปล่า (instance/DB แยก — ห้ามรวมกับ clinic/siriwattana)
-- Rollback: DROP SCHEMA rfq CASCADE;  (หรือ drop ตามลำดับย้อน)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---- lookup: RFQ status ----
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

-- ---- RFQ header + revision identity ----
CREATE TABLE rfq (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_no                     text,
    rfq_number_source          text NOT NULL DEFAULT 'RFQ_ESTIMATE_API',
    revision_no                integer NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    supersedes_rfq_id          uuid REFERENCES rfq(id),
    revision_reason            text,
    is_current                 boolean NOT NULL DEFAULT true,
    status_code                text NOT NULL DEFAULT 'DRAFT' REFERENCES rfq_status(code),

    enquiry_ref                text,
    source_channel             text CHECK (source_channel IS NULL OR source_channel IN
                               ('EMAIL','LINE','PHONE','MEETING','WEB_FORM','UPLOAD','OTHER')),
    source_channel_other       text,
    received_at                timestamptz NOT NULL DEFAULT now(),
    quote_due_at               timestamptz,
    priority_code              text NOT NULL DEFAULT 'NORMAL'
                               CHECK (priority_code IN ('NORMAL','URGENT','KEY_ACCOUNT')),

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
    row_version                integer NOT NULL DEFAULT 1 CHECK (row_version > 0),

    ready_at                   timestamptz,
    ready_by_ref               text,
    cancelled_at               timestamptz,
    cancelled_reason           text,

    CHECK (quote_due_at IS NULL OR quote_due_at >= received_at),
    CHECK ((revision_no = 1 AND supersedes_rfq_id IS NULL)
           OR (revision_no > 1 AND supersedes_rfq_id IS NOT NULL
               AND NULLIF(btrim(revision_reason), '') IS NOT NULL)),
    CHECK ((status_code = 'READY_FOR_ESTIMATE'
            AND rfq_no IS NOT NULL AND ready_at IS NOT NULL AND ready_by_ref IS NOT NULL)
           OR status_code <> 'READY_FOR_ESTIMATE'),
    UNIQUE (rfq_no, revision_no),
    UNIQUE (supersedes_rfq_id)
);

CREATE UNIQUE INDEX uq_rfq_current_revision ON rfq (rfq_no) WHERE is_current AND rfq_no IS NOT NULL;
CREATE INDEX ix_rfq_status_received ON rfq (status_code, received_at DESC);
CREATE INDEX ix_rfq_customer ON rfq (customer_ref);
CREATE INDEX ix_rfq_sales_owner ON rfq (sales_owner_ref, status_code);

-- ---- item (Packaging) ----
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
                               CHECK (finishing_state IN ('UNKNOWN','NONE','SPECIFIED')),
    packing_state              text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (packing_state IN ('UNKNOWN','NONE','SPECIFIED')),
    artwork_state              text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (artwork_state IN ('UNKNOWN','RECEIVED','NOT_RECEIVED','NOT_REQUIRED')),
    sample_state               text NOT NULL DEFAULT 'UNKNOWN'
                               CHECK (sample_state IN ('UNKNOWN','AVAILABLE','NOT_AVAILABLE','NOT_REQUIRED')),
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
CREATE INDEX ix_rfq_item_rfq ON rfq_item (rfq_id, line_no);

-- ---- quantity options + design variants ----
CREATE TABLE rfq_quantity_option (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id        uuid NOT NULL REFERENCES rfq_item(id),
    option_no          smallint NOT NULL CHECK (option_no > 0),
    quantity           numeric(14,3) NOT NULL CHECK (quantity > 0),
    unit_ref           text,
    unit_code_snapshot text,
    unit_name_snapshot text,
    unit_raw           text,
    is_primary         boolean NOT NULL DEFAULT false,
    notes              text,
    UNIQUE (rfq_item_id, option_no),
    UNIQUE (id, rfq_item_id)
);
CREATE UNIQUE INDEX uq_rfq_quantity_primary ON rfq_quantity_option (rfq_item_id) WHERE is_primary;

CREATE TABLE rfq_design_variant (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id        uuid NOT NULL REFERENCES rfq_item(id),
    variant_no         smallint NOT NULL CHECK (variant_no > 0),
    design_code        text NOT NULL,
    quantity           numeric(14,3),
    unit_ref           text,
    unit_code_snapshot text,
    notes              text,
    CHECK (quantity IS NULL OR quantity > 0),
    UNIQUE (rfq_item_id, variant_no),
    UNIQUE (rfq_item_id, design_code)
);

-- ---- component + corrugated ----
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
    paper_source_code            text CHECK (paper_source_code IS NULL OR paper_source_code IN
                                 ('DOMESTIC','IMPORTED','CUSTOMER_SUPPLIED','UNKNOWN')),
    print_sides_code             text CHECK (print_sides_code IS NULL OR print_sides_code IN
                                 ('NONE','ONE_SIDE','TWO_SIDES')),
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
CREATE INDEX ix_rfq_component_item ON rfq_component (rfq_item_id, component_no);

CREATE TABLE rfq_component_corrugated (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_component_id         uuid NOT NULL UNIQUE REFERENCES rfq_component(id),
    corrugated_board_ref     text,
    corrugated_code_snapshot text,
    corrugated_name_snapshot text,
    layer_count_snapshot     smallint,
    flute_code_snapshot      text,
    grade_spec_snapshot      jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes                    text,
    CHECK (layer_count_snapshot IS NULL OR layer_count_snapshot > 0)
);

-- ---- process / packing / delivery ----
CREATE TABLE rfq_process_requirement (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id           uuid NOT NULL REFERENCES rfq_item(id),
    rfq_component_id      uuid,
    sequence_no           smallint NOT NULL CHECK (sequence_no > 0),
    process_ref           text,
    process_code_snapshot text,
    process_name_snapshot text,
    process_name_raw      text,
    option_ref            text,
    option_code_snapshot  text,
    option_name_snapshot  text,
    option_name_raw       text,
    side_code             text CHECK (side_code IS NULL OR side_code IN
                          ('OUTSIDE','INSIDE','BOTH','NOT_APPLICABLE')),
    width_mm              numeric(12,3),
    height_mm             numeric(12,3),
    depth_mm              numeric(12,3),
    color_ref             text,
    color_code_snapshot   text,
    color_name_snapshot   text,
    specification_extra   jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes                 text,
    CHECK (width_mm  IS NULL OR width_mm  > 0),
    CHECK (height_mm IS NULL OR height_mm > 0),
    CHECK (depth_mm  IS NULL OR depth_mm  >= 0),
    CHECK (process_ref IS NOT NULL OR NULLIF(btrim(process_name_raw), '') IS NOT NULL),
    UNIQUE (rfq_item_id, sequence_no),
    UNIQUE (id, rfq_item_id),
    FOREIGN KEY (rfq_component_id, rfq_item_id) REFERENCES rfq_component(id, rfq_item_id)
);

CREATE TABLE rfq_packing_requirement (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id           uuid NOT NULL REFERENCES rfq_item(id),
    sequence_no           smallint NOT NULL CHECK (sequence_no > 0),
    packing_ref           text,
    packing_code_snapshot text,
    packing_name_snapshot text,
    packing_name_raw      text,
    quantity_per_pack     numeric(14,3),
    unit_ref              text,
    unit_code_snapshot    text,
    specification         text,
    CHECK (quantity_per_pack IS NULL OR quantity_per_pack > 0),
    CHECK (packing_ref IS NOT NULL OR NULLIF(btrim(packing_name_raw), '') IS NOT NULL),
    UNIQUE (rfq_item_id, sequence_no),
    UNIQUE (id, rfq_item_id)
);

CREATE TABLE rfq_delivery (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_item_id               uuid NOT NULL REFERENCES rfq_item(id),
    quantity_option_id        uuid,
    delivery_no               smallint NOT NULL CHECK (delivery_no > 0),
    destination_ref           text,
    destination_code_snapshot text,
    destination_name_snapshot text,
    destination_raw           text,
    requested_date            date,
    quantity                  numeric(14,3),
    unit_ref                  text,
    unit_code_snapshot        text,
    is_split_delivery         boolean NOT NULL DEFAULT false,
    notes                     text,
    CHECK (quantity IS NULL OR quantity > 0),
    CHECK (destination_ref IS NOT NULL OR NULLIF(btrim(destination_raw), '') IS NOT NULL),
    UNIQUE (rfq_item_id, delivery_no),
    UNIQUE (id, rfq_item_id),
    FOREIGN KEY (quantity_option_id, rfq_item_id)
        REFERENCES rfq_quantity_option(id, rfq_item_id)
);

-- ---- attachment (classification + retention) ----
CREATE TABLE rfq_attachment (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id),
    rfq_item_id            uuid,
    purpose_code           text NOT NULL CHECK (purpose_code IN
                           ('ENQUIRY','SPEC','ARTWORK','DIELINE','SAMPLE_IMAGE','PREVIOUS_JOB','OTHER')),
    original_filename      text NOT NULL,
    object_store_key       text NOT NULL,
    mime_type              text,
    size_bytes             bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    sha256                 char(64),
    classification_code    text NOT NULL DEFAULT 'UNCLASSIFIED'
                           CHECK (classification_code IN
                           ('UNCLASSIFIED','INTERNAL','CONFIDENTIAL','RESTRICTED')),
    contains_personal_data boolean,
    contains_trade_secret  boolean,
    cloud_action_code      text NOT NULL DEFAULT 'BLOCK'
                           CHECK (cloud_action_code IN ('ALLOW','REDACT','LOCAL_ONLY','BLOCK')),
    classification_status  text NOT NULL DEFAULT 'PENDING'
                           CHECK (classification_status IN ('PENDING','CONFIRMED','REJECTED')),
    classified_by_ref      text,
    classified_at          timestamptz,
    malware_scan_status    text NOT NULL DEFAULT 'PENDING'
                           CHECK (malware_scan_status IN ('PENDING','CLEAN','BLOCKED','ERROR')),
    retention_policy_code  text,
    delete_after           timestamptz,
    legal_hold             boolean NOT NULL DEFAULT false,
    deleted_at             timestamptz,
    uploaded_by_ref        text NOT NULL,
    uploaded_at            timestamptz NOT NULL DEFAULT now(),
    notes                  text,
    CHECK (classification_status <> 'CONFIRMED'
           OR (classified_by_ref IS NOT NULL AND classified_at IS NOT NULL)),
    CHECK (deleted_at IS NULL OR legal_hold IS FALSE),
    UNIQUE (id, rfq_id),
    FOREIGN KEY (rfq_item_id, rfq_id) REFERENCES rfq_item(id, rfq_id)
);
CREATE INDEX ix_rfq_attachment_rfq ON rfq_attachment (rfq_id, purpose_code);
CREATE INDEX ix_rfq_attachment_sha256 ON rfq_attachment (sha256) WHERE sha256 IS NOT NULL;

-- ---- external ref resolution audit (ไม่ใช่ master copy) ----
CREATE TABLE rfq_external_ref_resolution (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id            uuid NOT NULL REFERENCES rfq(id),
    subject_type      text NOT NULL,
    subject_id        uuid NOT NULL,
    field_name        text NOT NULL,
    source_system     text NOT NULL DEFAULT 'COMPANY_ESTIMATE_API',
    master_type       text NOT NULL,
    external_ref      text NOT NULL,
    code_snapshot     text,
    name_snapshot     text,
    provider_version  text,
    response_etag     text,
    active_at_resolve boolean NOT NULL,
    resolved_at       timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz,
    UNIQUE (rfq_id, subject_type, subject_id, field_name)
);
CREATE INDEX ix_rfq_external_ref_expiry ON rfq_external_ref_resolution (rfq_id, expires_at);

-- ---- clarification ----
CREATE TABLE rfq_clarification (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id          uuid NOT NULL REFERENCES rfq(id),
    subject_type    text NOT NULL,
    subject_id      uuid NOT NULL,
    field_name      text,
    question        text NOT NULL,
    reason          text,
    is_blocking     boolean NOT NULL DEFAULT true,
    status_code     text NOT NULL DEFAULT 'OPEN'
                    CHECK (status_code IN ('OPEN','ANSWERED','WAIVED','CANCELLED')),
    raised_by_type  text NOT NULL CHECK (raised_by_type IN ('HUMAN','AI','VALIDATOR')),
    raised_by_ref   text,
    raised_at       timestamptz NOT NULL DEFAULT now(),
    answer          text,
    answered_by_ref text,
    answered_at     timestamptz,
    waiver_reason   text,
    CHECK (status_code <> 'ANSWERED' OR (answer IS NOT NULL AND answered_at IS NOT NULL)),
    CHECK (status_code <> 'WAIVED'   OR NULLIF(btrim(waiver_reason), '') IS NOT NULL)
);
CREATE INDEX ix_rfq_clarification_open ON rfq_clarification (rfq_id, is_blocking) WHERE status_code = 'OPEN';
CREATE INDEX ix_rfq_clarification_subject ON rfq_clarification (subject_type, subject_id);

-- ---- field policy (security config; finding #2 = CHECK subject_type) ----
CREATE TABLE rfq_field_policy (
    subject_type        text NOT NULL
                        CHECK (subject_type IN
                        ('ANY','RFQ','ITEM','QUANTITY','DESIGN_VARIANT','COMPONENT',
                         'CORRUGATED','PROCESS','PACKING','DELIVERY','ATTACHMENT')),
    field_name          text NOT NULL,
    classification_code text NOT NULL
                        CHECK (classification_code IN ('INTERNAL','CONFIDENTIAL','RESTRICTED')),
    data_category_code  text NOT NULL
                        CHECK (data_category_code IN ('NONE','PERSONAL','TRADE_SECRET','MIXED')),
    cloud_action_code   text NOT NULL
                        CHECK (cloud_action_code IN ('ALLOW','REDACT','LOCAL_ONLY','BLOCK')),
    redaction_method    text NOT NULL DEFAULT 'NONE'
                        CHECK (redaction_method IN ('NONE','DROP','MASK','TOKENIZE')),
    policy_version      text NOT NULL,
    notes               text,
    PRIMARY KEY (subject_type, field_name)
);

-- ---- field evidence (stable subject) ----
CREATE TABLE rfq_field_evidence (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id               uuid NOT NULL REFERENCES rfq(id),
    subject_type         text NOT NULL,
    subject_id           uuid NOT NULL,
    field_name           text NOT NULL,
    value_snapshot       jsonb,
    source_type          text NOT NULL CHECK (source_type IN
                         ('MANUAL','EMAIL','LINE','PDF','DOCX','XLSX','IMAGE',
                          'MASTER_DATA','PREVIOUS_JOB','AI_INFERENCE')),
    source_attachment_id uuid,
    source_page          integer,
    source_excerpt       text,
    extractor_name       text,
    extractor_version    text,
    confidence           numeric(5,4),
    verification_status  text NOT NULL DEFAULT 'UNVERIFIED'
                         CHECK (verification_status IN ('UNVERIFIED','VERIFIED','REJECTED','CORRECTED')),
    verified_by_ref      text,
    verified_at          timestamptz,
    correction_note      text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (source_page IS NULL OR source_page > 0),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CHECK (verification_status = 'UNVERIFIED'
           OR (verified_by_ref IS NOT NULL AND verified_at IS NOT NULL)),
    FOREIGN KEY (source_attachment_id, rfq_id) REFERENCES rfq_attachment(id, rfq_id)
);
CREATE INDEX ix_rfq_field_evidence_subject
    ON rfq_field_evidence (rfq_id, subject_type, subject_id, field_name, created_at DESC);

-- ---- AI extraction + egress audit ----
CREATE TABLE rfq_ai_extraction_run (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                    uuid NOT NULL REFERENCES rfq(id),
    source_attachment_id      uuid,
    input_sha256              char(64),
    execution_target          text NOT NULL CHECK (execution_target IN ('LOCAL','CLOUD')),
    provider_name             text NOT NULL,
    model_name                text NOT NULL,
    egress_policy_version     text NOT NULL,
    egress_decision_code      text NOT NULL CHECK (egress_decision_code IN
                              ('LOCAL_ONLY','REDACTED_ALLOW','APPROVED_EXCEPTION','BLOCKED')),
    redaction_applied         boolean NOT NULL DEFAULT false,
    redaction_manifest        jsonb NOT NULL DEFAULT '{}'::jsonb,
    exception_approved_by_ref text,
    exception_reason          text,
    status_code               text NOT NULL CHECK (status_code IN
                              ('PENDING','SUCCEEDED','FAILED','BLOCKED')),
    started_at                timestamptz NOT NULL DEFAULT now(),
    completed_at              timestamptz,
    error_code                text,
    CHECK (execution_target <> 'CLOUD'
           OR egress_decision_code IN ('REDACTED_ALLOW','APPROVED_EXCEPTION')),
    CHECK (egress_decision_code <> 'REDACTED_ALLOW' OR redaction_applied IS TRUE),
    CHECK (egress_decision_code <> 'APPROVED_EXCEPTION'
           OR (exception_approved_by_ref IS NOT NULL
               AND NULLIF(btrim(exception_reason), '') IS NOT NULL)),
    FOREIGN KEY (source_attachment_id, rfq_id) REFERENCES rfq_attachment(id, rfq_id)
);
CREATE INDEX ix_rfq_ai_extraction_run ON rfq_ai_extraction_run (rfq_id, started_at DESC);

-- ---- readiness / signoff / status history / estimate link ----
CREATE TABLE rfq_readiness_run (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                uuid NOT NULL REFERENCES rfq(id),
    validator_version     text NOT NULL,
    master_policy_version text NOT NULL,
    egress_policy_version text NOT NULL,
    executed_by_ref       text NOT NULL,
    executed_at           timestamptz NOT NULL DEFAULT now(),
    passed                boolean NOT NULL,
    blocking_count        integer NOT NULL DEFAULT 0 CHECK (blocking_count >= 0),
    warning_count         integer NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    UNIQUE (id, rfq_id)
);

CREATE TABLE rfq_readiness_check (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    readiness_run_id uuid NOT NULL,
    rfq_id           uuid NOT NULL,
    subject_type     text NOT NULL,
    subject_id       uuid NOT NULL,
    field_name       text,
    check_code       text NOT NULL,
    severity         text NOT NULL CHECK (severity IN ('BLOCKER','WARNING','INFO')),
    result_code      text NOT NULL CHECK (result_code IN ('PASS','FAIL','WAIVED')),
    detail           text,
    waived_by_ref    text,
    waiver_reason    text,
    CHECK (result_code <> 'WAIVED'
           OR (waived_by_ref IS NOT NULL AND NULLIF(btrim(waiver_reason), '') IS NOT NULL)),
    FOREIGN KEY (readiness_run_id, rfq_id) REFERENCES rfq_readiness_run(id, rfq_id)
);

CREATE TABLE rfq_signoff (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id              uuid NOT NULL REFERENCES rfq(id),
    signoff_role        text NOT NULL CHECK (signoff_role IN ('PREPARER','REVIEWER','APPROVER')),
    decision_code       text NOT NULL CHECK (decision_code IN ('CONFIRMED','RETURNED','REJECTED')),
    actor_ref           text NOT NULL,
    actor_name_snapshot text,
    auth_source         text NOT NULL DEFAULT 'EXISTING_JWT',
    signed_at           timestamptz NOT NULL DEFAULT now(),
    comment             text
);
CREATE INDEX ix_rfq_signoff_rfq ON rfq_signoff (rfq_id, signoff_role, signed_at DESC);

CREATE TABLE rfq_status_history (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rfq_id           uuid NOT NULL REFERENCES rfq(id),
    from_status_code text REFERENCES rfq_status(code),
    to_status_code   text NOT NULL REFERENCES rfq_status(code),
    changed_by_ref   text NOT NULL,
    changed_at       timestamptz NOT NULL DEFAULT now(),
    reason           text,
    readiness_run_id uuid REFERENCES rfq_readiness_run(id),
    idempotency_key  text,
    UNIQUE (rfq_id, idempotency_key)
);
CREATE INDEX ix_rfq_status_history ON rfq_status_history (rfq_id, changed_at);

CREATE TABLE rfq_estimate_link (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id                 uuid NOT NULL REFERENCES rfq(id),
    estimate_system_code   text NOT NULL,
    external_estimate_id   text NOT NULL,
    scenario_code          text,
    is_primary             boolean NOT NULL DEFAULT true,
    handoff_at             timestamptz NOT NULL DEFAULT now(),
    handoff_by_ref         text NOT NULL,
    handoff_payload_sha256 char(64) NOT NULL,
    UNIQUE (estimate_system_code, external_estimate_id)
);
CREATE UNIQUE INDEX uq_rfq_primary_estimate
    ON rfq_estimate_link (rfq_id, estimate_system_code) WHERE is_primary;

-- NOTE (finding #3 — child-table audit): ตารางลูกยังไม่มี updated_by_ref/audit ในตัว
-- production ต้องมี durable audit/outbox ครอบ field-level change ของทุกตารางลูกก่อน go-live
-- (ดู STATUS.md → Hardening backlog) — v1 นี้พึ่ง rfq_status_history + external_ref_resolution ไปก่อน
