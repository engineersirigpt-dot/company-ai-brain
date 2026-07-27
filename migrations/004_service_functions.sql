-- ============================================================================
-- 004_service_functions.sql — RFQ service layer (ปิด Blocker 1/3, M1, M2)
-- ต้นทาง: RFQ_SCHEMA_V0_2.md ข้อ 8 + Codex review + STATUS "RFQ service layer"
--
-- แนวคิด: status/revision mutation ทำได้เฉพาะผ่าน 2 function นี้ (atomic)
--   - mark_ready()          : บังคับ readiness rules ครบ + transition + log
--   - create_rfq_revision() : supersede+create revision + log ทั้ง 2 ฝั่ง atomic
-- Guard: trigger บน rfq/rfq_item บล็อกการแก้ identity/status และแก้ child ของ
--   revision ที่ล็อกแล้ว เว้นแต่ทำผ่าน function (set session flag rfq.privileged=on)
-- Rollback: DROP FUNCTION mark_ready, create_rfq_revision, rfq_guard_*, ... CASCADE
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ---- Guard 1: identity/status ของ rfq เปลี่ยนได้เฉพาะผ่าน function (Blocker 1 / M2) ----
CREATE OR REPLACE FUNCTION rfq_guard_identity_status()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('rfq.privileged', true) IS DISTINCT FROM 'on' THEN
        IF NEW.status_code       IS DISTINCT FROM OLD.status_code
        OR NEW.is_current        IS DISTINCT FROM OLD.is_current
        OR NEW.revision_no       IS DISTINCT FROM OLD.revision_no
        OR NEW.rfq_no            IS DISTINCT FROM OLD.rfq_no
        OR NEW.enquiry_ref       IS DISTINCT FROM OLD.enquiry_ref
        OR NEW.supersedes_rfq_id IS DISTINCT FROM OLD.supersedes_rfq_id THEN
            RAISE EXCEPTION
                'identity/status ของ rfq เปลี่ยนได้เฉพาะผ่าน service function (mark_ready/create_rfq_revision)'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_rfq_guard_identity_status
BEFORE UPDATE ON rfq
FOR EACH ROW EXECUTE FUNCTION rfq_guard_identity_status();

-- ---- Guard 2: ห้ามแก้ child (rfq_item) ของ revision ที่ล็อก (M2) ----
-- v1 ครอบ rfq_item (top-level child ที่มี rfq_id); ตารางลูกที่ลึกกว่าให้เพิ่ม guard
-- ทำนองเดียวกันภายหลัง (ดู STATUS: child-lock coverage remaining)
CREATE OR REPLACE FUNCTION rfq_block_locked_item()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
    IF current_setting('rfq.privileged', true) = 'on' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    SELECT status_code INTO v_status FROM rfq WHERE id = COALESCE(NEW.rfq_id, OLD.rfq_id);
    IF v_status IN ('READY_FOR_ESTIMATE', 'SUPERSEDED') THEN
        RAISE EXCEPTION
            'ห้ามแก้/ลบ item ของ revision ที่ % — สร้าง revision ใหม่ผ่าน create_rfq_revision()', v_status
            USING ERRCODE = '23514';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER trg_rfq_block_locked_item
BEFORE UPDATE OR DELETE ON rfq_item
FOR EACH ROW EXECUTE FUNCTION rfq_block_locked_item();

-- ---- create_rfq_revision(): supersede เก่า + สร้าง DRAFT ใหม่ + log ทั้งคู่ (M1/Blocker 3) ----
CREATE OR REPLACE FUNCTION create_rfq_revision(
    p_prev uuid, p_reason text, p_actor text
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE prev rfq%ROWTYPE; v_new uuid;
BEGIN
    PERFORM set_config('rfq.privileged', 'on', true);

    SELECT * INTO prev FROM rfq WHERE id = p_prev FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'predecessor % not found', p_prev; END IF;
    IF prev.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'only the current revision can be superseded';
    END IF;
    IF prev.status_code <> 'READY_FOR_ESTIMATE' THEN
        RAISE EXCEPTION 'create a revision only after READY_FOR_ESTIMATE (got %)', prev.status_code;
    END IF;
    IF NULLIF(btrim(p_reason), '') IS NULL THEN
        RAISE EXCEPTION 'revision_reason required';
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

    -- 2) สร้าง DRAFT revision ใหม่ (copy header; child clone = หน้าที่ service ภายหลัง)
    INSERT INTO rfq (rfq_no, rfq_number_source, revision_no, supersedes_rfq_id, revision_reason,
        is_current, status_code, enquiry_ref, source_channel, priority_code,
        customer_ref, customer_code_snapshot, customer_name_snapshot, customer_name_raw,
        contact_name, contact_phone, contact_email, sales_owner_ref, created_by_ref, updated_by_ref)
    SELECT rfq_no, rfq_number_source, prev.revision_no + 1, prev.id, p_reason,
        true, 'DRAFT', enquiry_ref, source_channel, priority_code,
        customer_ref, customer_code_snapshot, customer_name_snapshot, customer_name_raw,
        contact_name, contact_phone, contact_email, sales_owner_ref, p_actor, p_actor
    FROM rfq WHERE id = prev.id
    RETURNING id INTO v_new;
    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref, reason)
    VALUES (v_new, NULL, 'DRAFT', p_actor,
        'revision ' || (prev.revision_no + 1) || ' created from ' || prev.rfq_no);

    PERFORM set_config('rfq.privileged', 'off', true);
    RETURN v_new;
END;
$$;

-- ---- mark_ready(): บังคับ readiness rules ครบ + transition atomic (Blocker 1) ----
CREATE OR REPLACE FUNCTION mark_ready(
    p_rfq_id uuid, p_expected_row_version int, p_actor text,
    p_validator_version text DEFAULT 'pkg-v1',
    p_master_policy_version text DEFAULT 'master-v1',
    p_egress_policy_version text DEFAULT 'rfq-egress-v1'
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    r rfq%ROWTYPE; v_run uuid; v_block int := 0; it RECORD;
BEGIN
    PERFORM set_config('rfq.privileged', 'on', true);

    SELECT * INTO r FROM rfq WHERE id = p_rfq_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'RFQ % not found', p_rfq_id; END IF;
    IF r.row_version <> p_expected_row_version THEN
        RAISE EXCEPTION 'row_version mismatch (expected %, got %)', p_expected_row_version, r.row_version
            USING ERRCODE = '40001';
    END IF;
    IF r.status_code <> 'READY_FOR_REVIEW' THEN
        RAISE EXCEPTION 'RFQ must be READY_FOR_REVIEW before Ready (got %)', r.status_code;
    END IF;
    IF r.rfq_no IS NULL THEN RAISE EXCEPTION 'rfq_no required for READY_FOR_ESTIMATE'; END IF;

    v_run := gen_random_uuid();
    INSERT INTO rfq_readiness_run (id, rfq_id, validator_version, master_policy_version,
        egress_policy_version, executed_by_ref, passed)
    VALUES (v_run, p_rfq_id, p_validator_version, p_master_policy_version,
        p_egress_policy_version, p_actor, false);

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

    -- PKG-005/006: มี item อย่างน้อย 1
    IF NOT EXISTS (SELECT 1 FROM rfq_item WHERE rfq_id = p_rfq_id) THEN
        INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
            check_code, severity, result_code, detail)
        VALUES (v_run, p_rfq_id, 'RFQ', p_rfq_id, 'PKG-005', 'BLOCKER', 'FAIL', 'no item');
        v_block := v_block + 1;
    END IF;

    -- ต่อ item: state ไม่เป็น UNKNOWN + มี quantity + มี component
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
        -- NOTE: RAISE = rollback ทั้ง txn → readiness_run/check ของรอบที่ fail ไม่ถูกเก็บ
        -- (v1 ยอมรับ; production ให้ service log attempt แยก หรือใช้ autonomous logging)
        RAISE EXCEPTION 'READY_FOR_ESTIMATE blocked: % blocker(s)', v_block USING ERRCODE = '23514';
    END IF;

    UPDATE rfq SET status_code = 'READY_FOR_ESTIMATE', is_current = true,
        ready_at = now(), ready_by_ref = p_actor,
        row_version = row_version + 1, updated_at = now(), updated_by_ref = p_actor
    WHERE id = p_rfq_id;
    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref,
        reason, readiness_run_id, idempotency_key)
    VALUES (p_rfq_id, 'READY_FOR_REVIEW', 'READY_FOR_ESTIMATE', p_actor,
        'mark_ready', v_run, 'ready:' || p_rfq_id || ':' || r.row_version);

    PERFORM set_config('rfq.privileged', 'off', true);
    RETURN p_rfq_id;
END;
$$;

-- ---- Production hardening (documented; ยังไม่รันใน prototype) ----
-- ก่อน production ให้เพิ่ม role-based defense-in-depth ควบคู่ trigger guard:
--   CREATE ROLE rfq_app NOLOGIN;
--   GRANT USAGE ON SCHEMA rfq TO rfq_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA rfq TO rfq_app;
--   REVOKE UPDATE (status_code, is_current, revision_no, rfq_no, enquiry_ref, supersedes_rfq_id)
--       ON rfq FROM rfq_app;
--   -- ให้ mark_ready/create_rfq_revision เป็น SECURITY DEFINER owned by table owner
--   GRANT EXECUTE ON FUNCTION mark_ready, create_rfq_revision TO rfq_app;
-- (prototype นี้พึ่ง trigger guard ซึ่ง enforce ได้โดยไม่ขึ้นกับ role)

COMMIT;
