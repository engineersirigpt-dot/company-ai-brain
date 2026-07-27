-- ============================================================================
-- test/030_service_tests.sql — RFQ service layer v2 (005) functional tests
-- ⚠️ รันบน DB fresh หลัง 001+002+003+005 (ข้าม 004 — 005 supersede)
-- ขอบเขต: ความถูกต้องเชิงตรรกะ (single connection, รันเป็น superuser)
--   - security boundary (role) + concurrency (2 connection) อยู่ใน rfq_concurrency_tests.py
-- ครอบ: mark_ready happy/blocked/row_version(NULL+stale)/status/is_current,
--        create_rfq_revision happy + clone completeness + non-READY,
--        readiness-input mutators + freeze check
-- ============================================================================
SET search_path TO rfq;
DO $$
DECLARE
    pass int := 0; fail int := 0;
    a_rfq uuid; a_item uuid; b_rfq uuid; b_item uuid; b_comp uuid;
    c_rfq uuid; rv int; newid uuid; clar uuid; so uuid;
    n_item int; n_qty int; n_comp int;
BEGIN
    ------------------------------------------------------------------
    -- helper fixture: RFQ พร้อม mark_ready (READY_FOR_REVIEW + signoff)
    ------------------------------------------------------------------
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref, sales_owner_ref,
        status_code, created_by_ref, updated_by_ref)
    VALUES ('SVC-A','ENQ-SVC-A','EMAIL','CUST-X','AE-X','READY_FOR_REVIEW','P','P')
    RETURNING id INTO a_rfq;
    INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, finished_width_mm,
        finished_length_mm, finished_depth_mm, finishing_state, packing_state, artwork_state, sample_state)
    VALUES (a_rfq,1,'[synthetic] box','PT',80,120,50,'SPECIFIED','SPECIFIED','RECEIVED','AVAILABLE')
    RETURNING id INTO a_item;
    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, is_primary)
    VALUES (a_item,1,5000,'PCS',true);
    INSERT INTO rfq_component (rfq_item_id, component_no, component_name, box_template_ref,
        box_width_mm, box_length_mm, box_depth_mm)
    VALUES (a_item,1,'body','BOXTPL-2',80,120,50);
    INSERT INTO rfq_component_corrugated (rfq_component_id, flute_code_snapshot, layer_count_snapshot)
    SELECT id,'B',3 FROM rfq_component WHERE rfq_item_id=a_item;
    INSERT INTO rfq_signoff (rfq_id, signoff_role, decision_code, actor_ref)
    VALUES (a_rfq,'REVIEWER','CONFIRMED','REVIEWER-X');

    -- ST1: mark_ready happy path
    SELECT row_version INTO rv FROM rfq WHERE id=a_rfq;
    PERFORM mark_ready(a_rfq, rv, 'REVIEWER-X');
    IF (SELECT status_code FROM rfq WHERE id=a_rfq)='READY_FOR_ESTIMATE'
       AND (SELECT row_version FROM rfq WHERE id=a_rfq)=rv+1
       AND (SELECT ready_by_ref FROM rfq WHERE id=a_rfq)='REVIEWER-X'
    THEN RAISE NOTICE 'PASS st1: mark_ready → READY_FOR_ESTIMATE + row_version+1'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st1'; fail:=fail+1; END IF;

    IF EXISTS (SELECT 1 FROM rfq_readiness_run WHERE rfq_id=a_rfq AND passed)
       AND EXISTS (SELECT 1 FROM rfq_status_history WHERE rfq_id=a_rfq AND to_status_code='READY_FOR_ESTIMATE')
    THEN RAISE NOTICE 'PASS st1b: readiness_run + history recorded'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st1b'; fail:=fail+1; END IF;

    ------------------------------------------------------------------
    -- fixture B: จะใช้ทดสอบ blocked / version / status
    ------------------------------------------------------------------
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref, sales_owner_ref,
        status_code, created_by_ref, updated_by_ref)
    VALUES ('SVC-B','ENQ-SVC-B','LINE','CUST-Y','AE-Y','READY_FOR_REVIEW','P','P')
    RETURNING id INTO b_rfq;
    INSERT INTO rfq_item (rfq_id, line_no, product_type_ref, finished_width_mm, finished_length_mm,
        finished_depth_mm, finishing_state, packing_state, artwork_state)
    VALUES (b_rfq,1,'PT',60,60,100,'SPECIFIED','SPECIFIED','RECEIVED') RETURNING id INTO b_item;
    INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, is_primary)
    VALUES (b_item,1,3000,'PCS',true);
    INSERT INTO rfq_component (rfq_item_id, component_no, box_template_ref, box_width_mm, box_length_mm, box_depth_mm)
    VALUES (b_item,1,'BOXTPL-4',60,60,100) RETURNING id INTO b_comp;
    INSERT INTO rfq_signoff (rfq_id, signoff_role, decision_code, actor_ref)
    VALUES (b_rfq,'REVIEWER','CONFIRMED','REVIEWER-Y');

    -- ST2: mark_ready blocked (open blocking clarification via service)
    clar := add_clarification(b_rfq,'COMPONENT',b_comp,'ยังไม่ระบุกระดาษ',true,'AI','AI-BOT');
    SELECT row_version INTO rv FROM rfq WHERE id=b_rfq;
    BEGIN
        PERFORM mark_ready(b_rfq, rv, 'REVIEWER-Y');
        RAISE NOTICE 'FAIL st2: blocked RFQ passed mark_ready'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st2: mark_ready blocked by open clarification'; pass:=pass+1;
    END;

    -- ST3: stale row_version → 40001
    SELECT row_version INTO rv FROM rfq WHERE id=b_rfq;
    BEGIN
        PERFORM mark_ready(b_rfq, rv + 99, 'REVIEWER-Y');
        RAISE NOTICE 'FAIL st3: stale row_version accepted'; fail:=fail+1;
    EXCEPTION WHEN serialization_failure THEN
        RAISE NOTICE 'PASS st3: stale row_version rejected (40001)'; pass:=pass+1;
    END;

    -- ST3b (F4a): NULL row_version → 40001 (ห้ามข้าม check)
    BEGIN
        PERFORM mark_ready(b_rfq, NULL, 'REVIEWER-Y');
        RAISE NOTICE 'FAIL st3b: NULL row_version accepted (F4a)'; fail:=fail+1;
    EXCEPTION WHEN serialization_failure THEN
        RAISE NOTICE 'PASS st3b: NULL row_version rejected (F4a)'; pass:=pass+1;
    END;

    -- ST4: mark_ready บน RFQ ที่ไม่ใช่ READY_FOR_REVIEW (a_rfq = READY_FOR_ESTIMATE แล้ว)
    SELECT row_version INTO rv FROM rfq WHERE id=a_rfq;
    BEGIN
        PERFORM mark_ready(a_rfq, rv, 'X');
        RAISE NOTICE 'FAIL st4: mark_ready on non-review status passed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st4: mark_ready requires READY_FOR_REVIEW'; pass:=pass+1;
    END;

    -- ST4b (F4b): mark_ready บน non-current REVIEW row → reject
    INSERT INTO rfq (rfq_no, enquiry_ref, source_channel, customer_ref, sales_owner_ref,
        status_code, is_current, created_by_ref, updated_by_ref)
    VALUES ('SVC-C','ENQ-SVC-C','EMAIL','CUST-Z','AE-Z','READY_FOR_REVIEW',false,'P','P')
    RETURNING id INTO c_rfq;
    SELECT row_version INTO rv FROM rfq WHERE id=c_rfq;
    BEGIN
        PERFORM mark_ready(c_rfq, rv, 'X');
        RAISE NOTICE 'FAIL st4b: mark_ready on non-current row passed (F4b)'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st4b: mark_ready requires is_current (F4b)'; pass:=pass+1;
    END;

    ------------------------------------------------------------------
    -- ST5: create_rfq_revision happy + clone completeness (F6)
    ------------------------------------------------------------------
    -- ทำให้ a_item มี design_variant + process + packing + delivery เพื่อทดสอบ clone ครบ tree
    INSERT INTO rfq_design_variant (rfq_item_id, variant_no, design_code) VALUES (a_item,1,'D1');
    INSERT INTO rfq_process_requirement (rfq_item_id, rfq_component_id, sequence_no, process_ref)
    SELECT a_item, id, 1, 'PRC-1' FROM rfq_component WHERE rfq_item_id=a_item;
    INSERT INTO rfq_packing_requirement (rfq_item_id, sequence_no, packing_ref) VALUES (a_item,1,'PK-1');
    INSERT INTO rfq_delivery (rfq_item_id, quantity_option_id, delivery_no, destination_ref)
    SELECT a_item, id, 1, 'DEST-1' FROM rfq_quantity_option WHERE rfq_item_id=a_item;

    newid := create_rfq_revision(a_rfq, 'ลูกค้าเปลี่ยนสเปก', 'PREPARER-X');
    IF (SELECT status_code FROM rfq WHERE id=a_rfq)='SUPERSEDED'
       AND (SELECT status_code FROM rfq WHERE id=newid)='DRAFT'
       AND (SELECT is_current FROM rfq WHERE id=newid)=true
       AND (SELECT is_current FROM rfq WHERE id=a_rfq)=false
       AND (SELECT revision_no FROM rfq WHERE id=newid)=2
       AND (SELECT rfq_no FROM rfq WHERE id=newid)=(SELECT rfq_no FROM rfq WHERE id=a_rfq)
    THEN RAISE NOTICE 'PASS st5: revision creates DRAFT + supersedes old'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st5'; fail:=fail+1; END IF;

    -- clone completeness: ทุกตารางใน spec tree ต้องถูก copy เข้า revision ใหม่ครบ
    SELECT
        (SELECT count(*) FROM rfq_item WHERE rfq_id=newid),
        (SELECT count(*) FROM rfq_quantity_option q JOIN rfq_item i ON i.id=q.rfq_item_id WHERE i.rfq_id=newid),
        (SELECT count(*) FROM rfq_component c JOIN rfq_item i ON i.id=c.rfq_item_id WHERE i.rfq_id=newid)
    INTO n_item, n_qty, n_comp;
    IF n_item=1 AND n_qty=1 AND n_comp=1
       AND EXISTS (SELECT 1 FROM rfq_component_corrugated cc JOIN rfq_component c ON c.id=cc.rfq_component_id JOIN rfq_item i ON i.id=c.rfq_item_id WHERE i.rfq_id=newid)
       AND EXISTS (SELECT 1 FROM rfq_design_variant v JOIN rfq_item i ON i.id=v.rfq_item_id WHERE i.rfq_id=newid)
       AND EXISTS (SELECT 1 FROM rfq_process_requirement p JOIN rfq_item i ON i.id=p.rfq_item_id WHERE i.rfq_id=newid AND p.rfq_component_id IS NOT NULL)
       AND EXISTS (SELECT 1 FROM rfq_packing_requirement p JOIN rfq_item i ON i.id=p.rfq_item_id WHERE i.rfq_id=newid)
       AND EXISTS (SELECT 1 FROM rfq_delivery d JOIN rfq_item i ON i.id=d.rfq_item_id WHERE i.rfq_id=newid AND d.quantity_option_id IS NOT NULL)
    THEN RAISE NOTICE 'PASS st5b: full spec tree cloned (incl. corrugated/variant/process/packing/delivery w/ remapped FKs)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st5b: item=% qty=% comp=%', n_item, n_qty, n_comp; fail:=fail+1; END IF;

    -- history ทั้ง 2 ฝั่ง atomic (M1)
    IF (SELECT count(*) FROM rfq_status_history WHERE rfq_id=a_rfq AND to_status_code='SUPERSEDED')=1
       AND EXISTS (SELECT 1 FROM rfq_status_history WHERE rfq_id=newid AND from_status_code IS NULL AND to_status_code='DRAFT')
    THEN RAISE NOTICE 'PASS st5c: both transitions logged (M1)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st5c'; fail:=fail+1; END IF;

    -- clone ต้องเป็น UUID ใหม่ (ไม่แชร์ row เดิม) — id ของ item ต้องไม่เท่าเดิม
    IF NOT EXISTS (SELECT 1 FROM rfq_item WHERE rfq_id=newid AND id=a_item)
    THEN RAISE NOTICE 'PASS st5d: cloned item has fresh UUID (not shared with predecessor)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st5d'; fail:=fail+1; END IF;

    -- ST6: create_rfq_revision บน RFQ ที่ไม่ READY_FOR_ESTIMATE (newid = DRAFT) → reject
    BEGIN
        PERFORM create_rfq_revision(newid, 'x', 'X');
        RAISE NOTICE 'FAIL st6: revision from non-READY allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st6: revision requires READY_FOR_ESTIMATE predecessor'; pass:=pass+1;
    END;

    ------------------------------------------------------------------
    -- ST7: readiness-input mutators (functional; concurrency ใน harness)
    ------------------------------------------------------------------
    -- b_rfq ยัง REVIEW → add/resolve clarification + add/revoke signoff ได้
    PERFORM resolve_clarification(clar, 'ANSWERED', 'AE-Y', 'ใช้กระดาษ KA 185');
    so := add_signoff(b_rfq, 'PREPARER', 'CONFIRMED', 'PREP-Y');
    PERFORM revoke_signoff(so, 'PREP-Y');
    IF (SELECT status_code FROM rfq_clarification WHERE id=clar)='ANSWERED'
       AND NOT EXISTS (SELECT 1 FROM rfq_signoff WHERE id=so)
    THEN RAISE NOTICE 'PASS st7: resolve_clarification + add/revoke signoff work on REVIEW rfq'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL st7'; fail:=fail+1; END IF;

    -- ST8 (F3 freeze): แก้ readiness input ของ revision ที่ล็อกแล้ว (a_rfq=SUPERSEDED) → reject
    BEGIN
        PERFORM add_signoff(a_rfq, 'APPROVER', 'CONFIRMED', 'X');
        RAISE NOTICE 'FAIL st8: signoff added to locked revision'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS st8: readiness input frozen on locked revision (F3)'; pass:=pass+1;
    END;

    RAISE NOTICE '========= SERVICE v2 RESULT: % passed, % failed =========', pass, fail;
    IF fail>0 THEN RAISE EXCEPTION 'SERVICE TESTS FAILED: %', fail; END IF;
END $$;
