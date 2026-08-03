-- ============================================================================
-- 010_rfq_signoff_v2.sql — V2 (HIGH, ก่อน Ready จริง): sign-off latest/active-decision
--                          rule + revoke_signoff append-only audit
-- ============================================================================
-- ปิด backlog V2 (documented ใน STATUS.md):
--   1) mark_ready RFQ-007 เดิม = `EXISTS CONFIRMED` → reviewer CONFIRMED แล้ว REJECTED
--      ทีหลัง **ยังผ่าน** (stale decision) ; ต้องใช้ decision **ล่าสุดที่ยัง active** (non-revoked)
--   2) revoke_signoff เดิม = `DELETE` → audit หาย (ไม่รู้ใคร revoke/เมื่อไหร่/ทำไม) ;
--      ต้อง **append-only** (revoked_at/by/reason) + gate มองข้าม row ที่ revoke แล้ว
--
-- ขอบเขต: local + synthetic เท่านั้น ยังไม่ deploy ; ไม่แตะ app/main.py, .env, Qdrant, ข้อมูลจริง
-- pattern เดิม: SECURITY DEFINER owner=rfq_owner, pinned search_path, REVOKE PUBLIC, GRANT rfq_app
-- ============================================================================
SET search_path TO rfq;

-- ----------------------------------------------------------------------------
-- 1) schema: append-only revoke columns + all-or-nothing CHECK
-- ----------------------------------------------------------------------------
ALTER TABLE rfq_signoff
    ADD COLUMN IF NOT EXISTS revoked_at     timestamptz,
    ADD COLUMN IF NOT EXISTS revoked_by_ref text,
    ADD COLUMN IF NOT EXISTS revoke_reason  text;

-- revoke = all-or-nothing ; ถ้า revoke แล้วต้องมี actor + reason (non-blank) เพื่อ audit ครบ
ALTER TABLE rfq_signoff DROP CONSTRAINT IF EXISTS ck_rfq_signoff_revoke;
ALTER TABLE rfq_signoff ADD CONSTRAINT ck_rfq_signoff_revoke CHECK (
    (revoked_at IS NULL     AND revoked_by_ref IS NULL AND revoke_reason IS NULL)
 OR (revoked_at IS NOT NULL AND revoked_by_ref IS NOT NULL AND NULLIF(btrim(revoke_reason), '') IS NOT NULL)
);

-- partial index: หา latest active (non-revoked) decision ต่อ (rfq, role) เร็ว
CREATE INDEX IF NOT EXISTS ix_rfq_signoff_active
    ON rfq_signoff (rfq_id, signoff_role, signed_at DESC, id DESC) WHERE revoked_at IS NULL;

-- ----------------------------------------------------------------------------
-- 2) helper: latest active (non-revoked) REVIEWER decision = CONFIRMED ?
--    NULL (ไม่มี active reviewer sign-off) → false ; latest = REJECTED/RETURNED → false
--    (deterministic tie-break signed_at DESC, id DESC)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _reviewer_latest_confirmed(p_rfq_id uuid)
RETURNS boolean LANGUAGE sql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
    SELECT COALESCE((
        SELECT decision_code = 'CONFIRMED'
        FROM rfq_signoff
        WHERE rfq_id = p_rfq_id AND signoff_role = 'REVIEWER' AND revoked_at IS NULL
        ORDER BY signed_at DESC, id DESC
        LIMIT 1
    ), false);
$$;
ALTER FUNCTION _reviewer_latest_confirmed(uuid) OWNER TO rfq_owner;
REVOKE ALL ON FUNCTION _reviewer_latest_confirmed(uuid) FROM PUBLIC;   -- internal helper — ไม่ grant app/ingest

-- ----------------------------------------------------------------------------
-- 3) mark_ready — RFQ-007 ใช้ latest-decision rule (re-CREATE เต็ม body ; เปลี่ยนเฉพาะ block RFQ-007)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mark_ready(
    p_rfq_id uuid, p_expected_row_version int, p_actor text
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    r rfq%ROWTYPE; v_run uuid; v_block int := 0; it RECORD;
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

    -- RFQ-007 (V2): reviewer sign-off — decision **ล่าสุดที่ยัง active** (non-revoked) ต้อง CONFIRMED
    --   เดิม EXISTS CONFIRMED → CONFIRMED แล้ว REJECTED/revoke ทีหลังยังผ่าน (stale) ; ปิดด้วย latest-decision rule
    IF NOT _reviewer_latest_confirmed(p_rfq_id) THEN
        INSERT INTO rfq_readiness_check (readiness_run_id, rfq_id, subject_type, subject_id,
            check_code, severity, result_code, detail)
        VALUES (v_run, p_rfq_id, 'RFQ', p_rfq_id, 'RFQ-007', 'BLOCKER', 'FAIL',
            'reviewer sign-off ไม่ผ่าน: latest active decision ต้อง CONFIRMED (missing/rejected/revoked)');
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

-- ----------------------------------------------------------------------------
-- 4) revoke_signoff — append-only (3-arg: +reason). DROP old 2-arg ก่อน (signature เปลี่ยน)
--    เดิม DELETE (audit หาย) → ตอนนี้ set revoked_at/by/reason ; gate มองข้าม revoked row
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS revoke_signoff(uuid, text);
CREATE OR REPLACE FUNCTION revoke_signoff(p_signoff_id uuid, p_actor text, p_reason text)
RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE s rfq_signoff%ROWTYPE;
BEGIN
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'revoke reason required (append-only audit)' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO s FROM rfq_signoff WHERE id = p_signoff_id FOR UPDATE;   -- lock row → serialize double-revoke
    IF NOT FOUND THEN RAISE EXCEPTION 'signoff % not found', p_signoff_id USING ERRCODE = '23503'; END IF;
    PERFORM _lock_rfq_for_input(s.rfq_id);   -- parent-lock + freeze check (F3: READY/SUPERSEDED/CANCELLED → reject)
    IF s.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'signoff % already revoked', p_signoff_id USING ERRCODE = '23514';   -- append-only: no double-revoke
    END IF;
    UPDATE rfq_signoff
        SET revoked_at = now(), revoked_by_ref = p_actor, revoke_reason = p_reason
        WHERE id = p_signoff_id;
END;
$$;

-- ----------------------------------------------------------------------------
-- 5) ownership + grant (F1: EXECUTE เท่านั้น ; revoke จาก PUBLIC ; reviewer capability = rfq_app)
-- ----------------------------------------------------------------------------
ALTER FUNCTION mark_ready(uuid, int, text)          OWNER TO rfq_owner;
ALTER FUNCTION revoke_signoff(uuid, text, text)     OWNER TO rfq_owner;

REVOKE ALL ON FUNCTION mark_ready(uuid, int, text)      FROM PUBLIC;
REVOKE ALL ON FUNCTION revoke_signoff(uuid, text, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION mark_ready(uuid, int, text)      TO rfq_app;
GRANT EXECUTE ON FUNCTION revoke_signoff(uuid, text, text) TO rfq_app;
-- rfq_ingest **ไม่ได้** grant → ปลอม Ready/revoke sign-off ไม่ได้ (V1)
