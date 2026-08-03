-- ============================================================================
-- 010_rfq_signoff_v2.sql — V2 (HIGH, ก่อน Ready จริง): sign-off latest/active-decision
--                          rule + revoke_signoff audit-preserving soft revoke
-- ============================================================================
-- ปิด backlog V2 + Codex review RFQ_SIGNOFF_V2_REVIEW_61A26F8:
--   1) mark_ready RFQ-007 เดิม `EXISTS CONFIRMED` → CONFIRMED แล้ว REJECTED/revoke ทีหลังยังผ่าน (stale)
--      → ใช้ decision **ล่าสุดที่ยัง active** (non-revoked)
--   B1) "ล่าสุด" ต้องยึด **monotonic decision_seq** (assign หลังได้ parent lock ผ่าน DEFAULT nextval)
--       ไม่ใช่ signed_at=now() (= txn-start time → เรียงสวน mutation order จริงได้)
--   2) revoke_signoff เดิม DELETE (audit หาย) → **audit-preserving soft revoke** (revoked_at/by/reason)
--      M1) lock parent → child (ตาม protocol เดิม) ; M2) actor non-blank (function + CHECK), เวลา = clock_timestamp()
--   B2) migration ทั้งไฟล์เป็น transaction เดียว (atomic)
--
-- ขอบเขต: local + synthetic prototype ยังไม่ deploy ; ไม่แตะ app/main.py, .env, Qdrant, ข้อมูลจริง
-- pattern: SECURITY DEFINER owner=rfq_owner, pinned search_path, REVOKE PUBLIC, GRANT rfq_app
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ----------------------------------------------------------------------------
-- 1) B1: authoritative monotonic decision order (assign หลัง parent lock)
--    add_signoff ล็อก parent ก่อน INSERT (005:431) → DEFAULT nextval ถูก eval หลัง lock
--    → decision_seq สะท้อน serialize order จริง (ไม่ใช่ txn-start ของ signed_at)
-- ----------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS rfq_signoff_decision_seq;
ALTER SEQUENCE rfq_signoff_decision_seq OWNER TO rfq_owner;
REVOKE ALL ON SEQUENCE rfq_signoff_decision_seq FROM PUBLIC;   -- caller ส่งลำดับเองไม่ได้ (DB-assigned)

ALTER TABLE rfq_signoff ADD COLUMN IF NOT EXISTS decision_seq bigint;
-- backfill row เดิมแบบ deterministic (signed_at, id) → ลำดับ sequence (prototype ปกติ 0 rows)
UPDATE rfq_signoff s SET decision_seq = sub.rn
    FROM (SELECT id, row_number() OVER (ORDER BY signed_at, id) AS rn
          FROM rfq_signoff WHERE decision_seq IS NULL) sub
    WHERE s.id = sub.id;
SELECT setval('rfq_signoff_decision_seq',
              COALESCE((SELECT max(decision_seq) FROM rfq_signoff), 0) + 1, false);   -- next nextval = max+1
ALTER TABLE rfq_signoff ALTER COLUMN decision_seq SET DEFAULT nextval('rfq_signoff_decision_seq');
ALTER TABLE rfq_signoff ALTER COLUMN decision_seq SET NOT NULL;

-- ----------------------------------------------------------------------------
-- 2) audit-preserving soft-revoke columns + all-or-nothing CHECK (M2: actor+reason non-blank)
-- ----------------------------------------------------------------------------
ALTER TABLE rfq_signoff
    ADD COLUMN IF NOT EXISTS revoked_at     timestamptz,
    ADD COLUMN IF NOT EXISTS revoked_by_ref text,
    ADD COLUMN IF NOT EXISTS revoke_reason  text;

ALTER TABLE rfq_signoff DROP CONSTRAINT IF EXISTS ck_rfq_signoff_revoke;
ALTER TABLE rfq_signoff ADD CONSTRAINT ck_rfq_signoff_revoke CHECK (
    (revoked_at IS NULL     AND revoked_by_ref IS NULL AND revoke_reason IS NULL)
 OR (revoked_at IS NOT NULL AND NULLIF(btrim(revoked_by_ref), '') IS NOT NULL     -- M2: actor ห้ามว่าง
                            AND NULLIF(btrim(revoke_reason),  '') IS NOT NULL)     -- reason ห้ามว่าง
);

-- partial index: หา latest active decision ต่อ (rfq, role) เร็ว (ยึด decision_seq)
DROP INDEX IF EXISTS ix_rfq_signoff_active;
CREATE INDEX ix_rfq_signoff_active
    ON rfq_signoff (rfq_id, signoff_role, decision_seq DESC) WHERE revoked_at IS NULL;

-- ----------------------------------------------------------------------------
-- 3) helper: latest active (non-revoked) REVIEWER decision = CONFIRMED ?  (ยึด decision_seq — B1)
--    NULL (ไม่มี active reviewer sign-off) → false ; latest = REJECTED/RETURNED → false
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _reviewer_latest_confirmed(p_rfq_id uuid)
RETURNS boolean LANGUAGE sql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
    SELECT COALESCE((
        SELECT decision_code = 'CONFIRMED'
        FROM rfq_signoff
        WHERE rfq_id = p_rfq_id AND signoff_role = 'REVIEWER' AND revoked_at IS NULL
        ORDER BY decision_seq DESC
        LIMIT 1
    ), false);
$$;
ALTER FUNCTION _reviewer_latest_confirmed(uuid) OWNER TO rfq_owner;
REVOKE ALL ON FUNCTION _reviewer_latest_confirmed(uuid) FROM PUBLIC;   -- internal helper — ไม่ grant app/ingest

-- ----------------------------------------------------------------------------
-- 4) mark_ready — RFQ-007 ใช้ latest-decision rule (re-CREATE เต็ม body ; เปลี่ยนเฉพาะ block RFQ-007)
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

    -- RFQ-007 (V2): reviewer sign-off — decision **ล่าสุดที่ยัง active** (non-revoked, ยึด decision_seq) ต้อง CONFIRMED
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
-- 5) revoke_signoff — audit-preserving soft revoke (M1 parent→child lock, M2 actor invariant)
--    "soft revoke" = ห้าม DELETE, รักษาหลักฐานเดิม, mark revoked_at/by/reason (ไม่ใช่ immutable event ledger)
--    semantics = **correction** (invalidate บันทึกที่ผิด) ; revoke decision ล่าสุด → fallback ไป active decision เก่ากว่า
--    (business "ถอน approval" ต้อง add decision row ใหม่ ไม่ใช่ revoke) — ดู CODEX handoff Q4
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS revoke_signoff(uuid, text);
CREATE OR REPLACE FUNCTION revoke_signoff(p_signoff_id uuid, p_actor text, p_reason text)
RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE v_rfq uuid; s rfq_signoff%ROWTYPE;
BEGIN
    -- input validation ก่อน (cheap, ไม่แตะ state ; invalid มี precedence เหนือ not-found/frozen — fail cheap ก่อนถือ lock)
    IF p_actor IS NULL OR btrim(p_actor) = '' OR length(p_actor) > 200 OR p_actor ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'revoke actor invalid (blank/too long/control char)' USING ERRCODE = '23514';   -- M2
    END IF;
    IF p_reason IS NULL OR btrim(p_reason) = '' OR length(p_reason) > 2000 THEN
        RAISE EXCEPTION 'revoke reason required (non-blank, <=2000)' USING ERRCODE = '23514';
    END IF;
    -- M1: parent ก่อน → child (ตาม lock protocol เดิม parent RFQ → child ; กัน deadlock surface)
    SELECT rfq_id INTO v_rfq FROM rfq_signoff WHERE id = p_signoff_id;   -- MVCC read หา parent (ยังไม่ lock child)
    IF NOT FOUND THEN RAISE EXCEPTION 'signoff % not found', p_signoff_id USING ERRCODE = '23503'; END IF;
    PERFORM _lock_rfq_for_input(v_rfq);   -- lock parent + freeze check (F3: READY/SUPERSEDED/CANCELLED → reject) ก่อน
    SELECT * INTO s FROM rfq_signoff WHERE id = p_signoff_id FOR UPDATE;   -- แล้วค่อย lock child (serialize double-revoke)
    IF NOT FOUND THEN RAISE EXCEPTION 'signoff % not found', p_signoff_id USING ERRCODE = '23503'; END IF;
    IF s.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'signoff % already revoked', p_signoff_id USING ERRCODE = '23514';   -- soft revoke: no double-revoke
    END IF;
    UPDATE rfq_signoff
        SET revoked_at = clock_timestamp(), revoked_by_ref = btrim(p_actor), revoke_reason = p_reason
        WHERE id = p_signoff_id;   -- clock_timestamp() = เวลา revoke จริงหลัง lock (ไม่ใช่ txn-start)
END;
$$;

-- ----------------------------------------------------------------------------
-- 6) ownership + grant (F1: EXECUTE เท่านั้น ; revoke จาก PUBLIC ; reviewer capability = rfq_app)
-- ----------------------------------------------------------------------------
ALTER FUNCTION mark_ready(uuid, int, text)          OWNER TO rfq_owner;
ALTER FUNCTION revoke_signoff(uuid, text, text)     OWNER TO rfq_owner;

REVOKE ALL ON FUNCTION mark_ready(uuid, int, text)      FROM PUBLIC;
REVOKE ALL ON FUNCTION revoke_signoff(uuid, text, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION mark_ready(uuid, int, text)      TO rfq_app;
GRANT EXECUTE ON FUNCTION revoke_signoff(uuid, text, text) TO rfq_app;
-- rfq_ingest **ไม่ได้** grant → ปลอม Ready/revoke sign-off ไม่ได้ (V1)

COMMIT;
-- Rollback (manual): ต้องอยู่ใน transaction เดียว — DROP revoke_signoff(uuid,text,text) + คืน 2-arg,
--   คืน mark_ready RFQ-007 เป็น EXISTS CONFIRMED, DROP _reviewer_latest_confirmed, DROP decision_seq/sequence/revoke cols/index
