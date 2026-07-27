-- ============================================================================
-- 003_seed_synthetic.sql — ข้อมูลสังเคราะห์ล้วน (ห้ามมีข้อมูลลูกค้า/ราคาจริง)
-- 3 เคส Packaging: ฝาคู่ฝาตรง(type_id=2) / ออโต้ล็อคทากาว(4) / ฝาครอบ(5)
-- *_ref เป็นค่าจำลอง (mock reference ของ Company API) — ไม่ได้ query API จริง
-- ============================================================================

-- ---- field policy: default-deny (v0.2 ข้อ 6.13) ----
INSERT INTO rfq_field_policy VALUES
('ANY','*','RESTRICTED','MIXED','BLOCK','NONE','rfq-egress-v1',
 'Unregistered RFQ field is blocked from Cloud by default'),
('RFQ','customer_ref','CONFIDENTIAL','TRADE_SECRET','REDACT','TOKENIZE','rfq-egress-v1',NULL),
('RFQ','customer_name_raw','CONFIDENTIAL','MIXED','REDACT','TOKENIZE','rfq-egress-v1',NULL),
('RFQ','contact_name','CONFIDENTIAL','PERSONAL','REDACT','TOKENIZE','rfq-egress-v1',NULL),
('RFQ','contact_phone','CONFIDENTIAL','PERSONAL','REDACT','MASK','rfq-egress-v1',NULL),
('RFQ','contact_email','CONFIDENTIAL','PERSONAL','REDACT','MASK','rfq-egress-v1',NULL),
('RFQ','customer_notes','RESTRICTED','MIXED','LOCAL_ONLY','NONE','rfq-egress-v1',NULL),
('ITEM','*','CONFIDENTIAL','TRADE_SECRET','LOCAL_ONLY','NONE','rfq-egress-v1',NULL),
('COMPONENT','*','CONFIDENTIAL','TRADE_SECRET','LOCAL_ONLY','NONE','rfq-egress-v1',NULL),
('ATTACHMENT','*','RESTRICTED','MIXED','LOCAL_ONLY','NONE','rfq-egress-v1',NULL);

-- ============================================================================
-- CASE A — กล่องฝาคู่ฝาตรง | READY_FOR_ESTIMATE | multi-quantity + multi-delivery
-- ============================================================================
DO $$
DECLARE
    v_rfq uuid; v_item uuid; v_qty1 uuid; v_qty2 uuid; v_comp uuid;
BEGIN
    INSERT INTO rfq (rfq_no, revision_no, enquiry_ref, source_channel, priority_code,
        customer_ref, customer_name_snapshot, contact_name, contact_phone,
        sales_owner_ref, status_code, ready_at, ready_by_ref,
        created_by_ref, updated_by_ref)
    VALUES ('RFQ-TEST-A', 1, 'ENQ-A', 'EMAIL', 'NORMAL',
        'CUST-MOCK-1', '[synthetic] ลูกค้าทดสอบ A', '[synthetic] ผู้ติดต่อ A', '0000000000',
        'AE-MOCK-1', 'READY_FOR_ESTIMATE', now(), 'REVIEWER-MOCK',
        'PREPARER-MOCK', 'PREPARER-MOCK')
    RETURNING id INTO v_rfq;

    INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, product_type_name_snapshot,
        finished_width_mm, finished_length_mm, finished_depth_mm,
        finishing_state, packing_state, artwork_state, sample_state)
    VALUES (v_rfq, 1, '[synthetic] กล่องฝาคู่ฝาตรง', 'PT-MOCK-BOX', 'กล่องฝาคู่ แบบฝาตรง',
        80, 120, 50, 'SPECIFIED', 'SPECIFIED', 'RECEIVED', 'AVAILABLE')
    RETURNING id INTO v_item;

    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, unit_code_snapshot, is_primary)
    VALUES (v_item, 1, 5000, 'UNIT-PCS', 'PCS', true) RETURNING id INTO v_qty1;
    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, unit_code_snapshot, is_primary)
    VALUES (v_item, 2, 10000, 'UNIT-PCS', 'PCS', false) RETURNING id INTO v_qty2;

    INSERT INTO rfq_component (rfq_item_id, component_no, component_name,
        paper_ref, paper_code_snapshot, paper_gsm_snapshot, paper_source_code,
        print_sides_code, color_outside_count, color_inside_count,
        box_template_ref, box_template_code_snapshot, box_template_name_snapshot,
        box_width_mm, box_length_mm, box_depth_mm)
    VALUES (v_item, 1, 'ตัวกล่อง', 'PAPER-MOCK-57', 'AC-350', 350, 'DOMESTIC',
        'ONE_SIDE', 4, 0, 'BOXTPL-2', '2', 'กล่องฝาคู่ แบบฝาตรง', 80, 120, 50)
    RETURNING id INTO v_comp;

    INSERT INTO rfq_process_requirement (rfq_item_id, rfq_component_id, sequence_no,
        process_ref, process_code_snapshot, process_name_snapshot, side_code)
    VALUES (v_item, v_comp, 1, 'PROC-MOCK-COAT', 'COATING', 'เคลือบเงา', 'OUTSIDE');

    INSERT INTO rfq_packing_requirement (rfq_item_id, sequence_no,
        packing_ref, packing_code_snapshot, packing_name_snapshot, quantity_per_pack, unit_ref)
    VALUES (v_item, 1, 'PACK-MOCK-CARTON', 'CARTON', 'ใส่กล่อง', 100, 'UNIT-PCS');

    -- multi-delivery: 2 เที่ยว รวม = qty option 1 (5000)
    INSERT INTO rfq_delivery (rfq_item_id, quantity_option_id, delivery_no,
        destination_ref, destination_name_snapshot, requested_date, quantity, unit_ref, is_split_delivery)
    VALUES (v_item, v_qty1, 1, 'DEST-MOCK-BKK', '[synthetic] คลัง กทม.', current_date + 14, 3000, 'UNIT-PCS', true),
           (v_item, v_qty1, 2, 'DEST-MOCK-CNX', '[synthetic] คลัง ชม.', current_date + 21, 2000, 'UNIT-PCS', true);

    -- external ref resolution (ผ่าน membership guard)
    INSERT INTO rfq_external_ref_resolution (rfq_id, subject_type, subject_id, field_name,
        master_type, external_ref, code_snapshot, active_at_resolve)
    VALUES (v_rfq, 'COMPONENT', v_comp, 'paper_ref', 'PAPER', 'PAPER-MOCK-57', 'AC-350', true),
           (v_rfq, 'COMPONENT', v_comp, 'box_template_ref', 'BOX_TEMPLATE', 'BOXTPL-2', '2', true);

    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref, reason)
    VALUES (v_rfq, NULL, 'DRAFT', 'PREPARER-MOCK', 'seed'),
           (v_rfq, 'READY_FOR_REVIEW', 'READY_FOR_ESTIMATE', 'REVIEWER-MOCK', 'seed ready');

    RAISE NOTICE 'CASE A seeded rfq=%', v_rfq;
END $$;

-- ============================================================================
-- CASE B — ออโต้ล็อคทากาว | multi-design (หลาย F) | DRAFT
-- ============================================================================
DO $$
DECLARE v_rfq uuid; v_item uuid;
BEGIN
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref,
        sales_owner_ref, created_by_ref, updated_by_ref)
    VALUES ('RFQ-TEST-B', 'ENQ-B', 'LINE', 'CUST-MOCK-2',
        'AE-MOCK-2', 'PREPARER-MOCK', 'PREPARER-MOCK')
    RETURNING id INTO v_rfq;

    INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, product_type_name_snapshot,
        finished_width_mm, finished_length_mm, finished_depth_mm, is_multiple_design,
        finishing_state, packing_state, artwork_state, sample_state)
    VALUES (v_rfq, 1, '[synthetic] กล่องออโต้ล็อคทากาว', 'PT-MOCK-BOX', 'กล่องออโต้ล็อคแบบทากาว',
        60, 60, 100, true, 'NONE', 'SPECIFIED', 'NOT_RECEIVED', 'NOT_AVAILABLE')
    RETURNING id INTO v_item;

    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, is_primary)
    VALUES (v_item, 1, 3000, 'UNIT-PCS', true);

    INSERT INTO rfq_design_variant (rfq_item_id, variant_no, design_code, quantity, unit_ref)
    VALUES (v_item, 1, 'LAY-RED', 1500, 'UNIT-PCS'),
           (v_item, 2, 'LAY-BLUE', 1500, 'UNIT-PCS');

    INSERT INTO rfq_component (rfq_item_id, component_no, component_name,
        paper_ref, paper_gsm_snapshot, print_sides_code, color_outside_count,
        box_template_ref, box_template_code_snapshot, box_width_mm, box_length_mm, box_depth_mm)
    VALUES (v_item, 1, 'ตัวกล่อง', 'PAPER-MOCK-57', 350, 'ONE_SIDE', 4,
        'BOXTPL-4', '4', 60, 60, 100);

    RAISE NOTICE 'CASE B seeded rfq=%', v_rfq;
END $$;

-- ============================================================================
-- CASE C — กล่องฝาครอบ | มี blocking clarification (Ready gate ต้อง fail เชิง business)
-- ============================================================================
DO $$
DECLARE v_rfq uuid; v_item uuid; v_comp uuid;
BEGIN
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref, status_code,
        sales_owner_ref, created_by_ref, updated_by_ref)
    VALUES ('RFQ-TEST-C', 'ENQ-C', 'PHONE', 'CUST-MOCK-3', 'NEEDS_CLARIFICATION',
        'AE-MOCK-3', 'PREPARER-MOCK', 'PREPARER-MOCK')
    RETURNING id INTO v_rfq;

    INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, product_type_name_snapshot,
        finished_width_mm, finished_length_mm, finished_depth_mm,
        finishing_state, packing_state, artwork_state, sample_state)
    VALUES (v_rfq, 1, '[synthetic] กล่องฝาครอบ', 'PT-MOCK-BOX', 'กล่องฝาครอบ',
        100, 150, 40, 'UNKNOWN', 'UNKNOWN', 'NOT_RECEIVED', 'UNKNOWN')  -- UNKNOWN = ยังไม่พร้อม
    RETURNING id INTO v_item;

    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, is_primary)
    VALUES (v_item, 1, 2000, 'UNIT-PCS', true);

    INSERT INTO rfq_component (rfq_item_id, component_no, component_name, box_template_ref,
        box_template_code_snapshot, box_width_mm, box_length_mm, box_depth_mm)
    VALUES (v_item, 1, 'ตัวกล่อง', 'BOXTPL-5', '5', 100, 150, 40)
    RETURNING id INTO v_comp;

    -- blocking clarification ที่ยัง OPEN → readiness validator ต้อง block
    INSERT INTO rfq_clarification (rfq_id, subject_type, subject_id, field_name,
        question, is_blocking, raised_by_type, raised_by_ref)
    VALUES (v_rfq, 'COMPONENT', v_comp, 'paper_ref',
        'ยังไม่ระบุชนิดกระดาษ ต้องถามลูกค้า', true, 'AI', 'EXTRACTOR-MOCK');

    RAISE NOTICE 'CASE C seeded rfq=% (มี blocking clarification)', v_rfq;
END $$;
