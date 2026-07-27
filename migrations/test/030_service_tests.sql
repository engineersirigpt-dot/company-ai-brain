-- ============================================================================
-- test/030_service_tests.sql — ทดสอบ RFQ service layer (004)
-- ⚠️ รันบน DB ทดสอบ fresh หลัง 001+002+003+004 (ไม่ต้องพึ่ง 010/020)
-- ครอบ: mark_ready happy/blocked/row_version/status, create_rfq_revision,
--        guard (direct status update, child edit ของ READY revision)
-- ============================================================================
SET search_path TO rfq;
DO $$
DECLARE
    pass int := 0; fail int := 0;
    d_rfq uuid; d_item uuid; e_rfq uuid; e_item uuid; e_comp uuid; rv int; newid uuid;
BEGIN
    -- helper: สร้าง RFQ ครบพร้อม mark_ready (READY_FOR_REVIEW + reviewer signoff)
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref, sales_owner_ref,
        status_code, created_by_ref, updated_by_ref)
    VALUES ('SVC-A','ENQ-SVC-A','EMAIL','CUST-X','AE-X','READY_FOR_REVIEW','P','P')
    RETURNING id INTO d_rfq;
    INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, finished_width_mm,
        finished_length_mm, finished_depth_mm, finishing_state, packing_state, artwork_state, sample_state)
    VALUES (d_rfq,1,'[synthetic] box','PT',80,120,50,'SPECIFIED','SPECIFIED','RECEIVED','AVAILABLE')
    RETURNING id INTO d_item;
    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, is_primary)
    VALUES (d_item,1,5000,'PCS',true);
    INSERT INTO rfq_component (rfq_item_id, component_no, component_name, box_template_ref,
        box_width_mm, box_length_mm, box_depth_mm)
    VALUES (d_item,1,'body','BOXTPL-2',80,120,50);
    INSERT INTO rfq_signoff (rfq_id, signoff_role, decision_code, actor_ref)
    VALUES (d_rfq,'REVIEWER','CONFIRMED','REVIEWER-X');

    -- ST1: mark_ready happy path
    SELECT row_version INTO rv FROM rfq WHERE id=d_rfq;
    PERFORM mark_ready(d_rfq, rv, 'REVIEWER-X');
    IF (SELECT status_code FROM rfq WHERE id=d_rfq)='READY_FOR_ESTIMATE'
       AND (SELECT ready_by_ref FROM rfq WHERE id=d_rfq)='REVIEWER-X'
    THEN RAISE NOTICE 'PASS st1: mark_ready happy → READY_FOR_ESTIMATE'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st1'; fail:=fail+1; END IF;
    -- readiness_run passed + status_history logged
    IF EXISTS (SELECT 1 FROM rfq_readiness_run WHERE rfq_id=d_rfq AND passed)
       AND EXISTS (SELECT 1 FROM rfq_status_history WHERE rfq_id=d_rfq
                   AND to_status_code='READY_FOR_ESTIMATE')
    THEN RAISE NOTICE 'PASS st1b: readiness_run + history recorded'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st1b'; fail:=fail+1; END IF;

    -- ST2: mark_ready blocked (blocking clarification)
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref, sales_owner_ref,
        status_code, created_by_ref, updated_by_ref)
    VALUES ('SVC-B','ENQ-SVC-B','LINE','CUST-Y','AE-Y','READY_FOR_REVIEW','P','P')
    RETURNING id INTO e_rfq;
    INSERT INTO rfq_item (rfq_id, line_no, product_type_ref, finished_width_mm, finished_length_mm,
        finished_depth_mm, finishing_state, packing_state, artwork_state)
    VALUES (e_rfq,1,'PT',60,60,100,'SPECIFIED','SPECIFIED','RECEIVED') RETURNING id INTO e_item;
    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, is_primary)
    VALUES (e_item,1,3000,'PCS',true);
    INSERT INTO rfq_component (rfq_item_id, component_no, box_template_ref, box_width_mm, box_length_mm, box_depth_mm)
    VALUES (e_item,1,'BOXTPL-4',60,60,100) RETURNING id INTO e_comp;
    INSERT INTO rfq_signoff (rfq_id, signoff_role, decision_code, actor_ref)
    VALUES (e_rfq,'REVIEWER','CONFIRMED','REVIEWER-Y');
    INSERT INTO rfq_clarification (rfq_id, subject_type, subject_id, question, is_blocking, raised_by_type)
    VALUES (e_rfq,'COMPONENT',e_comp,'ยังไม่ระบุกระดาษ',true,'AI');
    SELECT row_version INTO rv FROM rfq WHERE id=e_rfq;
    BEGIN
        PERFORM mark_ready(e_rfq, rv, 'REVIEWER-Y');
        RAISE NOTICE 'FAIL st2: blocked RFQ passed mark_ready'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st2: mark_ready blocked by open clarification'; pass:=pass+1;
    END;

    -- ST3: wrong row_version → mismatch
    SELECT row_version INTO rv FROM rfq WHERE id=e_rfq;
    BEGIN
        PERFORM mark_ready(e_rfq, rv + 99, 'REVIEWER-Y');
        RAISE NOTICE 'FAIL st3: stale row_version accepted'; fail:=fail+1;
    EXCEPTION WHEN serialization_failure THEN
        RAISE NOTICE 'PASS st3: row_version mismatch rejected'; pass:=pass+1;
    END;

    -- ST4: mark_ready บน RFQ ที่ไม่ใช่ READY_FOR_REVIEW (d_rfq ตอนนี้ READY_FOR_ESTIMATE)
    SELECT row_version INTO rv FROM rfq WHERE id=d_rfq;
    BEGIN
        PERFORM mark_ready(d_rfq, rv, 'X');
        RAISE NOTICE 'FAIL st4: mark_ready on non-review status passed'; fail:=fail+1;
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'PASS st4: mark_ready requires READY_FOR_REVIEW'; pass:=pass+1;
    END;

    -- ST5 (Guard): direct UPDATE status นอก function → block
    BEGIN
        UPDATE rfq SET status_code='DRAFT' WHERE id=d_rfq;
        RAISE NOTICE 'FAIL st5: direct status update allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st5: direct status/identity update blocked by guard'; pass:=pass+1;
    END;

    -- ST6 (Guard/M2): แก้ child ของ READY revision → block (d_rfq = READY_FOR_ESTIMATE)
    BEGIN
        UPDATE rfq_item SET job_name='hack' WHERE id=d_item;
        RAISE NOTICE 'FAIL st6: child edit of READY revision allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st6: child edit of locked revision blocked'; pass:=pass+1;
    END;

    -- ST7: create_rfq_revision happy → new DRAFT + old SUPERSEDED + histories
    newid := create_rfq_revision(d_rfq, 'ลูกค้าเปลี่ยนสเปก', 'PREPARER-X');
    IF (SELECT status_code FROM rfq WHERE id=d_rfq)='SUPERSEDED'
       AND (SELECT status_code FROM rfq WHERE id=newid)='DRAFT'
       AND (SELECT is_current FROM rfq WHERE id=newid)=true
       AND (SELECT revision_no FROM rfq WHERE id=newid)=2
       AND (SELECT rfq_no FROM rfq WHERE id=newid)=(SELECT rfq_no FROM rfq WHERE id=d_rfq)
    THEN RAISE NOTICE 'PASS st7: create_rfq_revision creates DRAFT + supersedes old'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st7'; fail:=fail+1; END IF;
    IF (SELECT count(*) FROM rfq_status_history WHERE rfq_id=d_rfq AND to_status_code='SUPERSEDED')=1
       AND EXISTS (SELECT 1 FROM rfq_status_history WHERE rfq_id=newid AND from_status_code IS NULL AND to_status_code='DRAFT')
    THEN RAISE NOTICE 'PASS st7b: both transitions logged atomically (M1)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st7b'; fail:=fail+1; END IF;

    -- ST8: create_rfq_revision บน RFQ ที่ไม่ READY_FOR_ESTIMATE (newid = DRAFT) → block
    BEGIN
        PERFORM create_rfq_revision(newid, 'x', 'X');
        RAISE NOTICE 'FAIL st8: revision from non-READY allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'PASS st8: revision requires READY_FOR_ESTIMATE predecessor'; pass:=pass+1;
    END;

    RAISE NOTICE '========= SERVICE RESULT: % passed, % failed =========', pass, fail;
    IF fail>0 THEN RAISE EXCEPTION 'SERVICE TESTS FAILED: %', fail; END IF;
END $$;
