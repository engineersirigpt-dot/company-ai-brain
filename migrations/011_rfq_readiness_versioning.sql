-- ============================================================================
-- 011_rfq_readiness_versioning.sql — V3 (HIGH, ก่อน draft-edit): readiness mutation
--                                    bump parent row_version (optimistic concurrency)
-- ============================================================================
-- ปิด backlog V3 (ส่วนที่ทำได้ตอนนี้ — draft-edit endpoint ยังไม่มี = future consumer ของ pattern นี้):
--   เดิม readiness mutation (add/resolve_clarification, add/revoke_signoff) lock parent + reject terminal
--   (freeze ผ่าน `_lock_rfq_for_input`) แล้ว **แต่ยังไม่ bump `rfq.row_version`**
--   → client ที่ถือ row_version เก่า ยัง mark_ready ผ่านได้ทั้งที่ readiness input เปลี่ยนไป (stale optimistic token)
--   ตอนนี้ทุก mutation bump row_version → mark_ready ด้วย version เก่า = 40001 → client ต้อง re-read
--
-- ขอบเขต: local + synthetic prototype ยังไม่ deploy ; reject-terminal มีอยู่แล้ว (freeze check) — slice นี้เพิ่ม bump
-- pattern: SECURITY DEFINER owner=rfq_owner, pinned search_path, REVOKE PUBLIC, GRANT rfq_app ; ทั้งไฟล์ = 1 transaction
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ----------------------------------------------------------------------------
-- helper: bump parent row_version (เรียกหลัง _lock_rfq_for_input = parent ถูก lock แล้ว)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _bump_rfq_version(p_rfq_id uuid, p_actor text)
RETURNS void LANGUAGE sql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
    UPDATE rfq SET row_version = row_version + 1, updated_at = now(), updated_by_ref = p_actor
    WHERE id = p_rfq_id;
$$;
ALTER FUNCTION _bump_rfq_version(uuid, text) OWNER TO rfq_owner;
REVOKE ALL ON FUNCTION _bump_rfq_version(uuid, text) FROM PUBLIC;   -- internal helper — ไม่ grant app/ingest

-- ----------------------------------------------------------------------------
-- re-CREATE readiness mutations ให้ bump row_version (เปลี่ยนเฉพาะเพิ่ม _bump_rfq_version)
-- ----------------------------------------------------------------------------
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
    PERFORM _bump_rfq_version(p_rfq_id, p_raised_by_ref);   -- V3: readiness input เปลี่ยน → bump
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
    PERFORM _bump_rfq_version(c.rfq_id, p_actor);   -- V3: readiness input เปลี่ยน → bump
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
    PERFORM _bump_rfq_version(p_rfq_id, p_actor);   -- V3: readiness input เปลี่ยน → bump
    RETURN v_id;
END;
$$;

-- revoke_signoff — reproduce จาก 010 (audit-preserving soft revoke) + เพิ่ม bump
CREATE OR REPLACE FUNCTION revoke_signoff(p_signoff_id uuid, p_actor text, p_reason text)
RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE v_rfq uuid; s rfq_signoff%ROWTYPE;
BEGIN
    IF p_actor IS NULL OR p_actor !~ '[^[:space:]]' OR length(p_actor) > 200 OR p_actor ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'revoke actor invalid (blank/whitespace/too long/control char)' USING ERRCODE = '23514';
    END IF;
    IF p_reason IS NULL OR p_reason !~ '[^[:space:]]' OR length(p_reason) > 2000 THEN
        RAISE EXCEPTION 'revoke reason required (non-blank, <=2000)' USING ERRCODE = '23514';
    END IF;
    SELECT rfq_id INTO v_rfq FROM rfq_signoff WHERE id = p_signoff_id;   -- MVCC read หา parent (ยังไม่ lock child)
    IF NOT FOUND THEN RAISE EXCEPTION 'signoff % not found', p_signoff_id USING ERRCODE = '23503'; END IF;
    PERFORM _lock_rfq_for_input(v_rfq);   -- lock parent + freeze check (F3) ก่อน
    SELECT * INTO s FROM rfq_signoff WHERE id = p_signoff_id FOR UPDATE;   -- แล้วค่อย lock child
    IF NOT FOUND THEN RAISE EXCEPTION 'signoff % not found', p_signoff_id USING ERRCODE = '23503'; END IF;
    IF s.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'signoff % already revoked', p_signoff_id USING ERRCODE = '23514';
    END IF;
    UPDATE rfq_signoff
        SET revoked_at = clock_timestamp(),
            revoked_by_ref = btrim(p_actor, E' \t\n\r\f\v'),
            revoke_reason  = btrim(p_reason, E' \t\n\r\f\v')
        WHERE id = p_signoff_id;
    PERFORM _bump_rfq_version(v_rfq, btrim(p_actor, E' \t\n\r\f\v'));   -- V3: readiness input เปลี่ยน → bump
END;
$$;

-- ----------------------------------------------------------------------------
-- ownership + grant (CREATE OR REPLACE รักษา ACL เดิม — re-affirm ให้ชัด)
-- ----------------------------------------------------------------------------
ALTER FUNCTION add_clarification(uuid, text, uuid, text, boolean, text, text) OWNER TO rfq_owner;
ALTER FUNCTION resolve_clarification(uuid, text, text, text, text)   OWNER TO rfq_owner;
ALTER FUNCTION add_signoff(uuid, text, text, text, text)             OWNER TO rfq_owner;
ALTER FUNCTION revoke_signoff(uuid, text, text)                      OWNER TO rfq_owner;

REVOKE ALL ON FUNCTION add_clarification(uuid, text, uuid, text, boolean, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION resolve_clarification(uuid, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION add_signoff(uuid, text, text, text, text)     FROM PUBLIC;
REVOKE ALL ON FUNCTION revoke_signoff(uuid, text, text)              FROM PUBLIC;

GRANT EXECUTE ON FUNCTION add_clarification(uuid, text, uuid, text, boolean, text, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION resolve_clarification(uuid, text, text, text, text) TO rfq_app;
GRANT EXECUTE ON FUNCTION add_signoff(uuid, text, text, text, text)  TO rfq_app;
GRANT EXECUTE ON FUNCTION revoke_signoff(uuid, text, text)           TO rfq_app;

COMMIT;
-- Rollback (manual): คืน 4 ฟังก์ชันเป็นเวอร์ชันก่อน bump (005 + 010) แล้ว DROP _bump_rfq_version
