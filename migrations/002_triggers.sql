-- ============================================================================
-- 002_triggers.sql — revision chain + subject membership guards
-- ต้นทาง: RFQ_SCHEMA_V0_2.md ข้อ 6.2 + 6.18
-- FINDING #1 (accepted): revision chain trigger ต้อง insert rfq_status_history
--   ของ transition READY_FOR_ESTIMATE → SUPERSEDED ให้ atomic ในตัว (เดิมไม่ log)
-- Rollback: DROP TRIGGER ... ; DROP FUNCTION ...
-- ============================================================================

BEGIN;
SET search_path TO rfq;

-- ---- revision chain (บังคับ rfq_no/enquiry_ref เดิม + supersede atomic + log) ----
CREATE OR REPLACE FUNCTION enforce_rfq_revision_chain()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous_row rfq%ROWTYPE;
BEGIN
    IF NEW.supersedes_rfq_id IS NULL THEN
        IF NEW.revision_no <> 1 THEN
            RAISE EXCEPTION 'Initial RFQ must use revision_no = 1';
        END IF;
        RETURN NEW;
    END IF;

    SELECT * INTO previous_row FROM rfq WHERE id = NEW.supersedes_rfq_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Superseded RFQ does not exist';
    END IF;
    IF previous_row.rfq_no IS NULL THEN
        RAISE EXCEPTION 'Cannot create a revision before RFQ number is assigned';
    END IF;
    IF NEW.rfq_no IS DISTINCT FROM previous_row.rfq_no THEN
        RAISE EXCEPTION 'Revision must keep the same rfq_no';
    END IF;
    IF NEW.enquiry_ref IS DISTINCT FROM previous_row.enquiry_ref THEN
        RAISE EXCEPTION 'Revision must keep the same enquiry_ref';
    END IF;
    IF NEW.revision_no <> previous_row.revision_no + 1 THEN
        RAISE EXCEPTION 'Revision number must increment by exactly one';
    END IF;
    IF previous_row.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'Only the current revision can be superseded';
    END IF;
    IF previous_row.status_code <> 'READY_FOR_ESTIMATE' THEN
        RAISE EXCEPTION 'Create a revision only after READY_FOR_ESTIMATE; edit Draft in place';
    END IF;
    IF NEW.status_code <> 'DRAFT' OR NEW.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'A new revision must start as current DRAFT';
    END IF;

    UPDATE rfq
       SET is_current = false, status_code = 'SUPERSEDED', updated_at = now()
     WHERE id = previous_row.id;

    -- FINDING #1: log transition ของ revision เก่าให้ atomic (อยู่ใน INSERT txn เดียวกัน)
    -- changed_by_ref ใช้ผู้สร้าง revision ใหม่ (คนที่ทำให้เกิดการ supersede)
    INSERT INTO rfq_status_history
        (rfq_id, from_status_code, to_status_code, changed_by_ref, reason, idempotency_key)
    VALUES
        (previous_row.id, previous_row.status_code, 'SUPERSEDED', NEW.created_by_ref,
         'superseded by revision ' || NEW.revision_no,
         'supersede:' || previous_row.id || ':' || NEW.revision_no);
    -- หมายเหตุ: history ของ NEW (creation → DRAFT) ให้ service/AFTER-trigger บันทึกหลัง INSERT
    -- (BEFORE INSERT อ้าง NEW.id ใน FK ไม่ได้เพราะ row ยังไม่ถูก insert)

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_enforce_rfq_revision_chain
BEFORE INSERT ON rfq
FOR EACH ROW EXECUTE FUNCTION enforce_rfq_revision_chain();

-- ---- subject membership guard (กัน subject_id ข้าม rfq_id) ----
CREATE OR REPLACE FUNCTION rfq_subject_belongs_to(
    p_rfq_id uuid, p_subject_type text, p_subject_id uuid
) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
BEGIN
    CASE p_subject_type
        WHEN 'RFQ' THEN
            RETURN p_subject_id = p_rfq_id
               AND EXISTS (SELECT 1 FROM rfq r WHERE r.id = p_rfq_id);
        WHEN 'ITEM' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_item i
                WHERE i.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'QUANTITY' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_quantity_option q
                JOIN rfq_item i ON i.id = q.rfq_item_id
                WHERE q.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'DESIGN_VARIANT' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_design_variant d
                JOIN rfq_item i ON i.id = d.rfq_item_id
                WHERE d.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'COMPONENT' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_component c
                JOIN rfq_item i ON i.id = c.rfq_item_id
                WHERE c.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'CORRUGATED' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_component_corrugated cc
                JOIN rfq_component c ON c.id = cc.rfq_component_id
                JOIN rfq_item i ON i.id = c.rfq_item_id
                WHERE cc.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'PROCESS' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_process_requirement p
                JOIN rfq_item i ON i.id = p.rfq_item_id
                WHERE p.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'PACKING' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_packing_requirement p
                JOIN rfq_item i ON i.id = p.rfq_item_id
                WHERE p.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'DELIVERY' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_delivery d
                JOIN rfq_item i ON i.id = d.rfq_item_id
                WHERE d.id = p_subject_id AND i.rfq_id = p_rfq_id);
        WHEN 'ATTACHMENT' THEN
            RETURN EXISTS (SELECT 1 FROM rfq_attachment a
                WHERE a.id = p_subject_id AND a.rfq_id = p_rfq_id);
        ELSE
            RETURN false;   -- unknown subject_type → reject (ปิด typo ในตัว)
    END CASE;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_rfq_subject_membership()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT rfq_subject_belongs_to(NEW.rfq_id, NEW.subject_type, NEW.subject_id) THEN
        RAISE EXCEPTION 'subject %:% does not belong to RFQ %',
            NEW.subject_type, NEW.subject_id, NEW.rfq_id USING ERRCODE = '23514';
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

-- ---- estimate link guard (Blocker 1, DB-level defense) ----
-- สร้าง handoff link ได้เฉพาะ RFQ ที่ READY_FOR_ESTIMATE + เป็น revision ปัจจุบัน
-- (การบังคับ readiness rules เต็มชุด = หน้าที่ mark_ready() service — ดู STATUS decision)
CREATE OR REPLACE FUNCTION enforce_estimate_link_ready()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r rfq%ROWTYPE;
BEGIN
    SELECT * INTO r FROM rfq WHERE id = NEW.rfq_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RFQ % not found', NEW.rfq_id;
    END IF;
    IF r.status_code <> 'READY_FOR_ESTIMATE' OR r.is_current IS NOT TRUE THEN
        RAISE EXCEPTION
            'estimate_link ต้องการ RFQ ที่ READY_FOR_ESTIMATE + current (พบ status=%, is_current=%)',
            r.status_code, r.is_current USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_estimate_link_ready
BEFORE INSERT ON rfq_estimate_link
FOR EACH ROW EXECUTE FUNCTION enforce_estimate_link_ready();

COMMIT;
