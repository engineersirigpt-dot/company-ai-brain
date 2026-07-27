-- ============================================================================
-- test/040_ingest_tests.sql — create_rfq_draft v1 (006) functional tests
-- ⚠️ รันบน DB fresh หลัง 001+002+003+005+006 (single connection, superuser)
--   role allowlist / atomicity / PUBLIC-execute อยู่ใน rfq_concurrency_tests.py (T11-T13)
-- ครอบ Codex acceptance: happy, unknown-key ทุกชั้น, lifecycle/policy override reject,
--   schema_version, idempotency (replay+conflict), size/item limits, empty items
-- ============================================================================
SET search_path TO rfq;
DO $$
DECLARE
    pass int := 0; fail int := 0;
    p_ok jsonb := '{"schema_version":"draft-v1","header":{"enquiry_ref":"ENQ-A","source_channel":"EMAIL","customer_name_raw":"co"},
      "items":[{"line_no":1,"job_name":"box","finishing_state":"SPECIFIED","packing_state":"SPECIFIED","artwork_state":"RECEIVED",
        "quantity_options":[{"option_no":1,"quantity":5000,"unit_ref":"PCS","is_primary":true}],
        "components":[{"component_no":1,"box_template_ref":"BT","corrugated":{"flute_code_snapshot":"B"}}],
        "processes":[{"sequence_no":1,"process_ref":"P","component_no":1}],
        "deliveries":[{"delivery_no":1,"destination_ref":"D","option_no":1}]}]}'::jsonb;
    v_rfq uuid; v_rfq2 uuid;
BEGIN
    -- ST1: happy path
    v_rfq := create_rfq_draft(p_ok, 'enq-extractor', 'enq', 'r1');
    IF (SELECT status_code FROM rfq WHERE id=v_rfq)='DRAFT'
       AND (SELECT revision_no FROM rfq WHERE id=v_rfq)=1
       AND (SELECT is_current FROM rfq WHERE id=v_rfq)=true
       AND (SELECT row_version FROM rfq WHERE id=v_rfq)=1
       AND (SELECT created_by_ref FROM rfq WHERE id=v_rfq)='enq-extractor'
       AND (SELECT count(*) FROM rfq_item WHERE rfq_id=v_rfq)=1
       AND EXISTS (SELECT 1 FROM rfq_component_corrugated cc JOIN rfq_component c ON c.id=cc.rfq_component_id JOIN rfq_item i ON i.id=c.rfq_item_id WHERE i.rfq_id=v_rfq)
       AND EXISTS (SELECT 1 FROM rfq_process_requirement p JOIN rfq_item i ON i.id=p.rfq_item_id WHERE i.rfq_id=v_rfq AND p.rfq_component_id IS NOT NULL)
       AND EXISTS (SELECT 1 FROM rfq_delivery d JOIN rfq_item i ON i.id=d.rfq_item_id WHERE i.rfq_id=v_rfq AND d.quantity_option_id IS NOT NULL)
       AND EXISTS (SELECT 1 FROM rfq_status_history WHERE rfq_id=v_rfq AND from_status_code IS NULL AND to_status_code='DRAFT')
       AND EXISTS (SELECT 1 FROM rfq_ingest_request WHERE rfq_id=v_rfq AND service='enq' AND request_id='r1')
    THEN RAISE NOTICE 'PASS is1: happy path — DRAFT + tree + FK resolve + history + request log'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL is1'; fail:=fail+1; END IF;

    -- ST2: unknown key (top / header / item / component)
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","EVIL":1,"items":[{"line_no":1}]}'::jsonb,'a','enq','r2a');
        RAISE NOTICE 'FAIL is2a: top unknown key'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is2a: top-level unknown key rejected'; pass:=pass+1; END;
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","header":{"EVIL":1},"items":[{"line_no":1}]}'::jsonb,'a','enq','r2b');
        RAISE NOTICE 'FAIL is2b'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is2b: header unknown key rejected'; pass:=pass+1; END;
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","items":[{"line_no":1,"EVIL":1}]}'::jsonb,'a','enq','r2c');
        RAISE NOTICE 'FAIL is2c'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is2c: item unknown key rejected'; pass:=pass+1; END;
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","items":[{"line_no":1,"components":[{"component_no":1,"EVIL":1}]}]}'::jsonb,'a','enq','r2d');
        RAISE NOTICE 'FAIL is2d'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is2d: component unknown key rejected'; pass:=pass+1; END;

    -- ST3: lifecycle/identity override ใน payload → reject (เป็น unknown key เพราะไม่อยู่ allowlist)
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","header":{"status_code":"READY_FOR_ESTIMATE"},"items":[{"line_no":1}]}'::jsonb,'a','enq','r3a');
        RAISE NOTICE 'FAIL is3a: status override'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is3a: header status override rejected'; pass:=pass+1; END;
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","header":{"row_version":99},"items":[{"line_no":1}]}'::jsonb,'a','enq','r3b');
        RAISE NOTICE 'FAIL is3b'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is3b: header row_version override rejected'; pass:=pass+1; END;
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","header":{"created_by_ref":"forged"},"items":[{"line_no":1}]}'::jsonb,'a','enq','r3c');
        RAISE NOTICE 'FAIL is3c'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is3c: header actor override rejected'; pass:=pass+1; END;

    -- ST4: schema_version ผิด → reject
    BEGIN PERFORM create_rfq_draft('{"schema_version":"v999","items":[{"line_no":1}]}'::jsonb,'a','enq','r4');
        RAISE NOTICE 'FAIL is4'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is4: bad schema_version rejected'; pass:=pass+1; END;

    -- ST5: idempotency — replay เดิม = id เดิม; request เดิม + payload ต่าง = conflict
    v_rfq2 := create_rfq_draft(p_ok, 'x', 'enq', 'r1');   -- request r1 มีแล้ว (payload เดิม)
    IF v_rfq2 = v_rfq THEN RAISE NOTICE 'PASS is5a: replay same request → same id'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL is5a'; fail:=fail+1; END IF;
    BEGIN
        PERFORM create_rfq_draft(p_ok || '{"header":{"enquiry_ref":"CHANGED"}}'::jsonb, 'x', 'enq', 'r1');
        RAISE NOTICE 'FAIL is5b: request reuse with different payload allowed'; fail:=fail+1;
    EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'PASS is5b: request_id reuse w/ different payload → conflict'; pass:=pass+1; END;

    -- ST6: item-count limit (>100) → reject ก่อน expand
    BEGIN
        PERFORM create_rfq_draft(jsonb_build_object('schema_version','draft-v1',
            'items', (SELECT jsonb_agg(jsonb_build_object('line_no',g)) FROM generate_series(1,101) g)), 'a','enq','r6');
        RAISE NOTICE 'FAIL is6: 101 items allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN
        IF SQLSTATE='54000' THEN RAISE NOTICE 'PASS is6: item-count limit enforced'; pass:=pass+1;
        ELSE RAISE NOTICE 'FAIL is6 wrong: %', SQLERRM; fail:=fail+1; END IF;
    END;

    -- ST7: payload เกินขนาด (>1MB) → reject
    BEGIN
        PERFORM create_rfq_draft(jsonb_build_object('schema_version','draft-v1',
            'header', jsonb_build_object('customer_notes', repeat('x',1100000)),
            'items', jsonb_build_array(jsonb_build_object('line_no',1))), 'a','enq','r7');
        RAISE NOTICE 'FAIL is7: oversize allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN
        IF SQLSTATE='54000' THEN RAISE NOTICE 'PASS is7: payload size limit enforced'; pass:=pass+1;
        ELSE RAISE NOTICE 'FAIL is7 wrong: %', SQLERRM; fail:=fail+1; END IF;
    END;

    -- ST8: items ว่าง → reject
    BEGIN PERFORM create_rfq_draft('{"schema_version":"draft-v1","items":[]}'::jsonb,'a','enq','r8');
        RAISE NOTICE 'FAIL is8'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is8: empty items rejected'; pass:=pass+1; END;

    -- ST9: actor/service/request_id ว่าง → reject (trusted context ห้ามว่าง)
    BEGIN PERFORM create_rfq_draft(p_ok,'','enq','r9');
        RAISE NOTICE 'FAIL is9'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS is9: empty actor rejected'; pass:=pass+1; END;

    RAISE NOTICE '========= INGEST RESULT: % passed, % failed =========', pass, fail;
    IF fail>0 THEN RAISE EXCEPTION 'INGEST TESTS FAILED: %', fail; END IF;
END $$;
