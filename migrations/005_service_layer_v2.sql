-- ============================================================================
-- 005_service_layer_v2.sql — service-only write model (ปิด Codex F1/F2/F3/F4/F6)
-- ต้นทาง: RFQ_SERVICE_CONCURRENCY_REVIEW_489C5F0.md
--
-- แนวคิดใหม่ (แทนที่ guard แบบ flag ใน 004 ซึ่ง Codex F1 พิสูจน์ว่า bypass ได้):
--   security boundary = ROLE + REVOKE DML + SECURITY DEFINER ไม่ใช่ session flag
--   - rfq_app  : read-only (SELECT) + EXECUTE service function เท่านั้น เขียนตารางตรง ๆ ไม่ได้
--   - rfq_owner: เจ้าของตาราง/ฟังก์ชัน; SECURITY DEFINER function รันด้วยสิทธิ์นี้
--   ทุกการเปลี่ยน lifecycle/spec ผ่าน function → app ปลอมสถานะ/ข้าม readiness ไม่ได้
--
-- ปิด finding:
--   F1  raw UPDATE/SET flag bypass        → REVOKE DML จาก app; flag ไม่ใช่ boundary อีก
--   F2  raw INSERT / child mutation / reparent → app INSERT/UPDATE/DELETE ไม่ได้เลย (permission)
--   F3  readiness TOCTOU                  → parent-lock protocol: ทุก writer ของ readiness input
--                                          (clarification/signoff) LOCK parent RFQ ก่อน
--   F4  row_version NULL bypass / is_current → NULL = reject, require is_current
--   F6  revision ไม่ clone spec tree       → create_rfq_revision clone ทั้ง tree atomic
--
-- ยังไม่ทำ (documented, gated ตาม review):
--   create_rfq_draft(jsonb) → phase ENQ (รูป input = extraction output)
--   F5  validator ครบ (ตอนนี้ pkg-minimal-v1, fail closed)  |  F7 idempotency request_id
--   F8  durable audit ของ readiness attempt ที่ fail
-- Rollback: DROP FUNCTION ... ; REVOKE/GRANT คืน; (prototype — ไม่มี live DB)
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ---------------------------------------------------------------------------
-- 1) roles (idempotent) + ownership transfer + read-only grant สำหรับ app
-- ---------------------------------------------------------------------------
-- 3 roles (separation of duties — Codex V1):
--   rfq_owner  = เจ้าของ table/function (SECURITY DEFINER รันด้วยสิทธิ์นี้)
--   rfq_app    = FastAPI service (reviewer/Ready/revision capability — คน sign-off/Ready ผ่าน API นี้)
--   rfq_ingest = ENQ AI worker — สร้าง initial DRAFT ได้อย่างเดียว (จะได้ EXECUTE create_rfq_draft ใน phase ENQ)
--                **ห้าม** mark_ready/add_signoff/revoke_signoff/create_rfq_revision → ปลอมอนุมัติเองไม่ได้
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfq_owner')  THEN CREATE ROLE rfq_owner  NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfq_app')    THEN CREATE ROLE rfq_app    NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfq_ingest') THEN CREATE ROLE rfq_ingest NOLOGIN; END IF;
END
$roles$;

-- V5: normalize role attributes แม้ role มีอยู่ก่อน migration (idempotent)
ALTER ROLE rfq_owner  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE rfq_app    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE rfq_ingest NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT USAGE ON SCHEMA rfq TO rfq_owner, rfq_app, rfq_ingest;

-- โอนสิทธิ์เจ้าของ table/sequence ให้ rfq_owner (SECURITY DEFINER function จะรันด้วยสิทธิ์นี้)
-- ต้องรันด้วย superuser หรือสมาชิก rfq_owner (prototype รันเป็น postgres)
DO $own$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'rfq' LOOP
        EXECUTE format('ALTER TABLE rfq.%I OWNER TO rfq_owner', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'rfq' LOOP
        EXECUTE format('ALTER SEQUENCE rfq.%I OWNER TO rfq_owner', r.sequencename);
    END LOOP;
END
$own$;

-- app/ingest: อ่านได้ทุกตาราง แต่เขียนตรงไม่ได้ (นี่คือ boundary จริงของ F1/F2)
GRANT SELECT ON ALL TABLES IN SCHEMA rfq TO rfq_app, rfq_ingest;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA rfq FROM rfq_app, rfq_ingest;
-- ตารางที่สร้างภายหลังก็ default read-only
ALTER DEFAULT PRIVILEGES IN SCHEMA rfq GRANT SELECT ON TABLES TO rfq_app, rfq_ingest;
-- ห้ามสร้าง object ใน schema (revoke จาก PUBLIC + app/ingest ตรง ๆ — V5)
REVOKE CREATE ON SCHEMA rfq FROM PUBLIC, rfq_app, rfq_ingest;

-- ---------------------------------------------------------------------------
-- 2) ทิ้ง guard แบบ flag ใน 004 (F1: bypass ได้ → ให้ความมั่นใจผิด ๆ)
--    เก็บ invariant trigger ใน 002 ไว้ (revision-chain validation, subject
--    membership, estimate-link ready) เพราะไม่พึ่ง flag และเป็น invariant จริง
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_rfq_guard_identity_status ON rfq;
DROP FUNCTION IF EXISTS rfq_guard_identity_status();
DROP TRIGGER IF EXISTS trg_rfq_block_locked_item ON rfq_item;
DROP FUNCTION IF EXISTS rfq_block_locked_item();

-- ทิ้งของเก่าใน 004 เพื่อ reset ownership/security context
DROP FUNCTION IF EXISTS mark_ready(uuid, integer, text, text, text, text);
DROP FUNCTION IF EXISTS create_rfq_revision(uuid, text, text);

-- ---------------------------------------------------------------------------
-- helper: บังคับ parent-lock + freeze สำหรับ readiness input (F3)
--   ล็อกแถว rfq (FOR UPDATE) แล้วคืน status; reject ถ้า revision ถูกล็อกแล้ว
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _lock_rfq_for_input(p_rfq_id uuid)
RETURNS text LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE v_status text;
BEGIN
    SELECT status_code INTO v_status FROM rfq WHERE id = p_rfq_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RFQ % not found', p_rfq_id USING ERRCODE = '23503';
    END IF;
    IF v_status IN ('READY_FOR_ESTIMATE', 'SUPERSEDED', 'CANCELLED') THEN
        RAISE EXCEPTION 'RFQ ถูกล็อก (status=%) — แก้ readiness input ไม่ได้ ต้องสร้าง revision ใหม่', v_status
            USING ERRCODE = '23514';
    END IF;
    RETURN v_status;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3) mark_ready — ปิด F4 (NULL row_version, require is_current) + F1 (DEFINER)
--    validator = pkg-minimal-v1 (F5: ยังไม่ครบ — ตั้งชื่อให้ตรง fail closed)
-- ---------------------------------------------------------------------------
-- signature = (rfq_id, expected_row_version, actor) เท่านั้น — V1: policy version **ไม่รับจาก caller**
CREATE OR REPLACE FUNCTION mark_ready(
    p_rfq_id uuid, p_expected_row_version int, p_actor text
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    r rfq%ROWTYPE; v_run uuid; v_block int := 0; it RECORD;
    -- policy version มาจาก trusted source (constant ใน prototype; production อ่านจาก config table/registry)
    -- caller spoof ไม่ได้ (Codex V1) — p_actor ยังต้องมาจาก authenticated server context (FastAPI ยืนยัน)
    c_validator constant text := 'pkg-minimal-v1';
    c_master    constant text := 'master-v1';
    c_egress    constant text := 'rfq-egress-v1';
BEGIN
    SELECT * INTO r FROM rfq WHERE id = p_rfq_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'RFQ % not found', p_rfq_id USING ERRCODE = '23503'; END IF;

    -- F4a: NULL expected version = ต้อง reject (ไม่ปล่อยข้าม check)
    IF p_expected_row_version IS NULL OR r.row_version IS DISTINCT FROM p_expected_row_version THEN
        RAISE EXCEPTION 'row_version mismatch (expected %, got %)', p_expected_row_version, r.row_version
            USING ERRCODE = '40001';
    END IF;
    -- F4b: ต้องเป็น revision ปัจจุบัน (กันชุบ non-current กลับมา)
    IF r.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'only the current revision can be marked ready' USING ERRCODE = '23514';
    END IF;
    IF r.status_code <> 'READY_FOR_REVIEW' THEN
        RAISE EXCEPTION 'RFQ must be READY_FOR_REVIEW before Ready (got %)', r.status_code
            USING ERRCODE = '23514';
    END IF;
    IF r.rfq_no IS NULL THEN
        RAISE EXCEPTION 'rfq_no required for READY_FOR_ESTIMATE' USING ERRCODE = '23514';
    END IF;

    v_run := gen_random_uuid();
    INSERT INTO rfq_readiness_run (id, rfq_id, validator_version, master_policy_version,
        egress_policy_version, executed_by_ref, passed)
    VALUES (v_run, p_rfq_id, c_validator, c_master, c_egress, p_actor, false);

    -- RFQ-006: ไม่มี blocking clarification ที่ยัง OPEN
    IF EXISTS (SELECT 1 FROM rfq_clarification
               WHERE rfq_id = p_rfq_id AND is_blocking AND status_code = 'OPEN') THEN
        INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
            check_code, severity, result_code, detail)
        VALUES (v_run, p_rfq_id, 'RFQ', p_rfq_id, 'RFQ-006', 'BLOCKER', 'FAIL',
            'open blocking clarification');
        v_block := v_block + 1;
    END IF;

    -- RFQ-007: reviewer sign-off CONFIRMED
    IF NOT EXISTS (SELECT 1 FROM rfq_signoff
                   WHERE rfq_id = p_rfq_id AND signoff_role = 'REVIEWER' AND decision_code = 'CONFIRMED') THEN
        INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
            check_code, severity, result_code, detail)
        VALUES (v_run, p_rfq_id, 'RFQ', p_rfq_id, 'RFQ-007', 'BLOCKER', 'FAIL',
            'missing reviewer sign-off');
        v_block := v_block + 1;
    END IF;

    -- PKG-005: มี item อย่างน้อย 1
    IF NOT EXISTS (SELECT 1 FROM rfq_item WHERE rfq_id = p_rfq_id) THEN
        INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
            check_code, severity, result_code, detail)
        VALUES (v_run, p_rfq_id, 'RFQ', p_rfq_id, 'PKG-005', 'BLOCKER', 'FAIL', 'no item');
        v_block := v_block + 1;
    END IF;

    -- ต่อ item: state ไม่ UNKNOWN + มี quantity + มี component
    FOR it IN SELECT * FROM rfq_item WHERE rfq_id = p_rfq_id LOOP
        IF it.finishing_state = 'UNKNOWN' OR it.packing_state = 'UNKNOWN'
           OR it.artwork_state = 'UNKNOWN' THEN
            INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
                check_code, severity, result_code, detail)
            VALUES (v_run, p_rfq_id, 'ITEM', it.id, 'PKG-008', 'BLOCKER', 'FAIL',
                'finishing/packing/artwork state = UNKNOWN');
            v_block := v_block + 1;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM rfq_quantity_option WHERE rfq_item_id = it.id) THEN
            INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
                check_code, severity, result_code, detail)
            VALUES (v_run, p_rfq_id, 'ITEM', it.id, 'PKG-003', 'BLOCKER', 'FAIL', 'no quantity option');
            v_block := v_block + 1;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM rfq_component WHERE rfq_item_id = it.id) THEN
            INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
                check_code, severity, result_code, detail)
            VALUES (v_run, p_rfq_id, 'ITEM', it.id, 'PKG-005', 'BLOCKER', 'FAIL', 'no component');
            v_block := v_block + 1;
        END IF;
    END LOOP;

    UPDATE rfq_readiness_run SET passed = (v_block = 0), blocking_count = v_block WHERE id = v_run;

    IF v_block > 0 THEN
        -- F8 (accepted): RAISE = rollback → readiness_run/check ของรอบ fail หาย (log แยกใน production)
        RAISE EXCEPTION 'READY_FOR_ESTIMATE blocked: % blocker(s)', v_block USING ERRCODE = '23514';
    END IF;

    UPDATE rfq SET status_code = 'READY_FOR_ESTIMATE',
        ready_at = now(), ready_by_ref = p_actor,
        row_version = row_version + 1, updated_at = now(), updated_by_ref = p_actor
    WHERE id = p_rfq_id;
    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref,
        reason, readiness_run_id, idempotency_key)
    VALUES (p_rfq_id, 'READY_FOR_REVIEW', 'READY_FOR_ESTIMATE', p_actor,
        'mark_ready', v_run, 'ready:' || p_rfq_id || ':' || r.row_version);

    RETURN p_rfq_id;
END;
$$;

-- ---------------------------------------------------------------------------
-- 4) create_rfq_revision — supersede + สร้าง DRAFT ใหม่ + CLONE spec tree (F6)
--    clone เฉพาะ physical spec tree (item→qty/variant/component→corrugated,
--    process, packing, delivery). ไม่ copy history/readiness/signoff/
--    clarification/evidence/attachment (business rule ต่างหาก — F6 note)
--    map old→new ด้วย natural key (line_no/option_no/component_no) ไม่ต้อง temp map
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_rfq_revision(
    p_prev uuid, p_reason text, p_actor text
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE prev rfq%ROWTYPE; v_new uuid;
BEGIN
    SELECT * INTO prev FROM rfq WHERE id = p_prev FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'predecessor % not found', p_prev USING ERRCODE = '23503'; END IF;
    IF prev.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'only the current revision can be superseded' USING ERRCODE = '23514';
    END IF;
    IF prev.status_code <> 'READY_FOR_ESTIMATE' THEN
        RAISE EXCEPTION 'create a revision only after READY_FOR_ESTIMATE (got %)', prev.status_code
            USING ERRCODE = '23514';
    END IF;
    IF NULLIF(btrim(p_reason), '') IS NULL THEN
        RAISE EXCEPTION 'revision_reason required' USING ERRCODE = '23514';
    END IF;

    -- 1) supersede predecessor ก่อน (ปลด unique index is_current) + log
    UPDATE rfq SET is_current = false, status_code = 'SUPERSEDED',
        updated_at = now(), updated_by_ref = p_actor
    WHERE id = prev.id;
    INSERT INTO rfq_status_history
        (rfq_id, from_status_code, to_status_code, changed_by_ref, reason, idempotency_key)
    VALUES (prev.id, 'READY_FOR_ESTIMATE', 'SUPERSEDED', p_actor,
        'superseded by revision ' || (prev.revision_no + 1),
        'supersede:' || prev.id || ':' || (prev.revision_no + 1));

    -- 2) สร้าง DRAFT revision ใหม่ (copy header)
    INSERT INTO rfq (rfq_no, rfq_number_source, revision_no, supersedes_rfq_id, revision_reason,
        is_current, status_code, enquiry_ref, source_channel, source_channel_other, received_at,
        quote_due_at, priority_code, customer_ref, customer_code_snapshot, customer_name_snapshot,
        customer_name_raw, is_new_customer, contact_name, contact_phone, contact_email,
        sales_owner_ref, sales_owner_code_snapshot, sales_owner_name_snapshot, customer_notes,
        created_by_ref, updated_by_ref)
    SELECT rfq_no, rfq_number_source, prev.revision_no + 1, prev.id, p_reason,
        true, 'DRAFT', enquiry_ref, source_channel, source_channel_other, received_at,
        quote_due_at, priority_code, customer_ref, customer_code_snapshot, customer_name_snapshot,
        customer_name_raw, is_new_customer, contact_name, contact_phone, contact_email,
        sales_owner_ref, sales_owner_code_snapshot, sales_owner_name_snapshot, customer_notes,
        p_actor, p_actor
    FROM rfq WHERE id = prev.id
    RETURNING id INTO v_new;

    -- 3) clone spec tree (map old→new ด้วย natural key)
    INSERT INTO rfq_item (rfq_id, line_no, product_family_code, job_name, product_type_ref,
        product_type_code_snapshot, product_type_name_snapshot, product_type_raw, description,
        intended_use, finished_width_mm, finished_length_mm, finished_depth_mm, is_reprint,
        previous_job_ref, use_previous_plate, is_multiple_design, finishing_state, packing_state,
        artwork_state, sample_state, sample_description, notes)
    SELECT v_new, line_no, product_family_code, job_name, product_type_ref,
        product_type_code_snapshot, product_type_name_snapshot, product_type_raw, description,
        intended_use, finished_width_mm, finished_length_mm, finished_depth_mm, is_reprint,
        previous_job_ref, use_previous_plate, is_multiple_design, finishing_state, packing_state,
        artwork_state, sample_state, sample_description, notes
    FROM rfq_item WHERE rfq_id = prev.id;

    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref,
        unit_code_snapshot, unit_name_snapshot, unit_raw, is_primary, notes)
    SELECT ni.id, oq.option_no, oq.quantity, oq.unit_ref,
        oq.unit_code_snapshot, oq.unit_name_snapshot, oq.unit_raw, oq.is_primary, oq.notes
    FROM rfq_quantity_option oq
    JOIN rfq_item oi ON oi.id = oq.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no;

    INSERT INTO rfq_design_variant (rfq_item_id, variant_no, design_code, quantity,
        unit_ref, unit_code_snapshot, notes)
    SELECT ni.id, ov.variant_no, ov.design_code, ov.quantity, ov.unit_ref, ov.unit_code_snapshot, ov.notes
    FROM rfq_design_variant ov
    JOIN rfq_item oi ON oi.id = ov.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no;

    INSERT INTO rfq_component (rfq_item_id, component_no, component_name, component_type_ref,
        component_type_code_snapshot, component_type_name_snapshot, component_type_raw, paper_ref,
        paper_code_snapshot, paper_name_snapshot, paper_gsm_snapshot, paper_source_code,
        print_sides_code, color_outside_count, color_inside_count, ink_type_ref,
        ink_type_code_snapshot, box_template_ref, box_template_code_snapshot,
        box_template_name_snapshot, box_width_mm, box_length_mm, box_depth_mm, flap_mm, glue_mm,
        tuck_mm, notes)
    SELECT ni.id, oc.component_no, oc.component_name, oc.component_type_ref,
        oc.component_type_code_snapshot, oc.component_type_name_snapshot, oc.component_type_raw, oc.paper_ref,
        oc.paper_code_snapshot, oc.paper_name_snapshot, oc.paper_gsm_snapshot, oc.paper_source_code,
        oc.print_sides_code, oc.color_outside_count, oc.color_inside_count, oc.ink_type_ref,
        oc.ink_type_code_snapshot, oc.box_template_ref, oc.box_template_code_snapshot,
        oc.box_template_name_snapshot, oc.box_width_mm, oc.box_length_mm, oc.box_depth_mm, oc.flap_mm, oc.glue_mm,
        oc.tuck_mm, oc.notes
    FROM rfq_component oc
    JOIN rfq_item oi ON oi.id = oc.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no;

    INSERT INTO rfq_component_corrugated (rfq_component_id, corrugated_board_ref,
        corrugated_code_snapshot, corrugated_name_snapshot, layer_count_snapshot,
        flute_code_snapshot, grade_spec_snapshot, notes)
    SELECT nc.id, occ.corrugated_board_ref,
        occ.corrugated_code_snapshot, occ.corrugated_name_snapshot, occ.layer_count_snapshot,
        occ.flute_code_snapshot, occ.grade_spec_snapshot, occ.notes
    FROM rfq_component_corrugated occ
    JOIN rfq_component oc ON oc.id = occ.rfq_component_id
    JOIN rfq_item oi ON oi.id = oc.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no
    JOIN rfq_component nc ON nc.rfq_item_id = ni.id AND nc.component_no = oc.component_no;

    INSERT INTO rfq_process_requirement (rfq_item_id, rfq_component_id, sequence_no, process_ref,
        process_code_snapshot, process_name_snapshot, process_name_raw, option_ref,
        option_code_snapshot, option_name_snapshot, option_name_raw, side_code, width_mm, height_mm,
        depth_mm, color_ref, color_code_snapshot, color_name_snapshot, specification_extra, notes)
    SELECT ni.id, nc.id, op.sequence_no, op.process_ref,
        op.process_code_snapshot, op.process_name_snapshot, op.process_name_raw, op.option_ref,
        op.option_code_snapshot, op.option_name_snapshot, op.option_name_raw, op.side_code, op.width_mm, op.height_mm,
        op.depth_mm, op.color_ref, op.color_code_snapshot, op.color_name_snapshot, op.specification_extra, op.notes
    FROM rfq_process_requirement op
    JOIN rfq_item oi ON oi.id = op.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no
    LEFT JOIN rfq_component oc ON oc.id = op.rfq_component_id
    LEFT JOIN rfq_component nc ON nc.rfq_item_id = ni.id AND nc.component_no = oc.component_no;

    INSERT INTO rfq_packing_requirement (rfq_item_id, sequence_no, packing_ref,
        packing_code_snapshot, packing_name_snapshot, packing_name_raw, quantity_per_pack,
        unit_ref, unit_code_snapshot, specification)
    SELECT ni.id, opk.sequence_no, opk.packing_ref,
        opk.packing_code_snapshot, opk.packing_name_snapshot, opk.packing_name_raw, opk.quantity_per_pack,
        opk.unit_ref, opk.unit_code_snapshot, opk.specification
    FROM rfq_packing_requirement opk
    JOIN rfq_item oi ON oi.id = opk.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no;

    INSERT INTO rfq_delivery (rfq_item_id, quantity_option_id, delivery_no, destination_ref,
        destination_code_snapshot, destination_name_snapshot, destination_raw, requested_date,
        quantity, unit_ref, unit_code_snapshot, is_split_delivery, notes)
    SELECT ni.id, nq.id, od.delivery_no, od.destination_ref,
        od.destination_code_snapshot, od.destination_name_snapshot, od.destination_raw, od.requested_date,
        od.quantity, od.unit_ref, od.unit_code_snapshot, od.is_split_delivery, od.notes
    FROM rfq_delivery od
    JOIN rfq_item oi ON oi.id = od.rfq_item_id AND oi.rfq_id = prev.id
    JOIN rfq_item ni ON ni.rfq_id = v_new AND ni.line_no = oi.line_no
    LEFT JOIN rfq_quantity_option oq ON oq.id = od.quantity_option_id
    LEFT JOIN rfq_quantity_option nq ON nq.rfq_item_id = ni.id AND nq.option_no = oq.option_no;

    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref, reason)
    VALUES (v_new, NULL, 'DRAFT', p_actor,
        'revision ' || (prev.revision_no + 1) || ' created from ' || prev.rfq_no);

    RETURN v_new;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5) readiness-input mutators — parent-lock protocol (F3)
--    ทุกตัว LOCK parent RFQ ก่อน → ระหว่าง mark_ready ถือ lock อยู่ จะ block
--    และ reject ถ้า revision ถูกล็อก (READY/SUPERSEDED/CANCELLED)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION add_clarification(
    p_rfq_id uuid, p_subject_type text, p_subject_id uuid, p_question text,
    p_is_blocking boolean, p_raised_by_type text, p_raised_by_ref text
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE v_id uuid;
BEGIN
    PERFORM _lock_rfq_for_input(p_rfq_id);
    INSERT INTO rfq_clarification (rfq_id, subject_type, subject_id, question,
        is_blocking, raised_by_type, raised_by_ref)
    VALUES (p_rfq_id, p_subject_type, p_subject_id, p_question,
        p_is_blocking, p_raised_by_type, p_raised_by_ref)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION resolve_clarification(
    p_clar_id uuid, p_new_status text, p_actor text,
    p_answer text DEFAULT NULL, p_waiver_reason text DEFAULT NULL
) RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE c rfq_clarification%ROWTYPE;
BEGIN
    SELECT * INTO c FROM rfq_clarification WHERE id = p_clar_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'clarification % not found', p_clar_id USING ERRCODE = '23503'; END IF;
    PERFORM _lock_rfq_for_input(c.rfq_id);   -- parent-lock + freeze check (F3)
    IF p_new_status NOT IN ('OPEN', 'ANSWERED', 'WAIVED', 'CANCELLED') THEN
        RAISE EXCEPTION 'bad clarification status %', p_new_status USING ERRCODE = '23514';
    END IF;
    UPDATE rfq_clarification SET
        status_code     = p_new_status,
        answer          = CASE WHEN p_new_status = 'ANSWERED' THEN p_answer ELSE answer END,
        answered_by_ref = CASE WHEN p_new_status = 'ANSWERED' THEN p_actor ELSE answered_by_ref END,
        answered_at     = CASE WHEN p_new_status = 'ANSWERED' THEN now() ELSE answered_at END,
        waiver_reason   = CASE WHEN p_new_status = 'WAIVED' THEN p_waiver_reason ELSE waiver_reason END
    WHERE id = p_clar_id;
END;
$$;

CREATE OR REPLACE FUNCTION add_signoff(
    p_rfq_id uuid, p_role text, p_decision text, p_actor text, p_comment text DEFAULT NULL
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE v_id uuid;
BEGIN
    PERFORM _lock_rfq_for_input(p_rfq_id);
    INSERT INTO rfq_signoff (rfq_id, signoff_role, decision_code, actor_ref, comment)
    VALUES (p_rfq_id, p_role, p_decision, p_actor, p_comment)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION revoke_signoff(p_signoff_id uuid, p_actor text)
RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE s rfq_signoff%ROWTYPE;
BEGIN
    SELECT * INTO s FROM rfq_signoff WHERE id = p_signoff_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'signoff % not found', p_signoff_id USING ERRCODE = '23503'; END IF;
    PERFORM _lock_rfq_for_input(s.rfq_id);   -- parent-lock + freeze check (F3)
    DELETE FROM rfq_signoff WHERE id = p_signoff_id;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6) ownership + execute grant (F1: EXECUTE เท่านั้น, revoke จาก PUBLIC)
-- ---------------------------------------------------------------------------
ALTER FUNCTION _lock_rfq_for_input(uuid)                              OWNER TO rfq_owner;
ALTER FUNCTION mark_ready(uuid, int, text)                           OWNER TO rfq_owner;
ALTER FUNCTION create_rfq_revision(uuid, text, text)                 OWNER TO rfq_owner;
ALTER FUNCTION add_clarification(uuid, text, uuid, text, boolean, text, text) OWNER TO rfq_owner;
ALTER FUNCTION resolve_clarification(uuid, text, text, text, text)   OWNER TO rfq_owner;
ALTER FUNCTION add_signoff(uuid, text, text, text, text)             OWNER TO rfq_owner;
ALTER FUNCTION revoke_signoff(uuid, text)                            OWNER TO rfq_owner;

-- _lock_rfq_for_input เป็น internal helper — ห้าม app เรียกตรง
REVOKE ALL ON FUNCTION _lock_rfq_for_input(uuid) FROM PUBLIC;

REVOKE ALL ON FUNCTION mark_ready(uuid, int, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION create_rfq_revision(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION add_clarification(uuid, text, uuid, text, boolean, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION resolve_clarification(uuid, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION add_signoff(uuid, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION revoke_signoff(uuid, text) FROM PUBLIC;

-- reviewer/Ready/revision capability = rfq_app เท่านั้น (ผ่าน FastAPI authorization)
-- rfq_ingest **ไม่ได้** grant อะไรในนี้ → ปลอม sign-off/Ready/revision ไม่ได้ (V1)
-- (create_rfq_draft ของ phase ENQ จะ grant EXECUTE ให้ rfq_ingest ตัวเดียว)
GRANT EXECUTE ON FUNCTION mark_ready(uuid, int, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION create_rfq_revision(uuid, text, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION add_clarification(uuid, text, uuid, text, boolean, text, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION resolve_clarification(uuid, text, text, text, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION add_signoff(uuid, text, text, text, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION revoke_signoff(uuid, text) TO rfq_app;

COMMIT;
