-- ============================================================================
-- test/020_run_tests.sql — acceptance tests (รันหลัง 001+002+003+test/010)
-- ⚠️ ต้องรันบน DB ทดสอบ fresh (ephemeral) — POS3 สร้าง revision 2 จริง (mutating);
--    รันซ้ำบน DB เดิมจะไม่ผ่าน (ตั้งใจ: 1 run = 1 fresh DB ผ่าน harness --rm)
-- แก้ตาม Codex: neg6 assert เจาะจง error, + neg8/9/10 สำหรับ fix รอบนี้
-- ============================================================================
SET search_path TO rfq;
DO $$
DECLARE
    pass int := 0; fail int := 0;
    a_rfq uuid; a_item uuid; a_comp uuid; a_run uuid;
    b_rfq uuid;
    c_rfq uuid; other_comp uuid; other_run uuid;
    hist_cnt int; tmp uuid;
BEGIN
    SELECT id INTO a_rfq FROM rfq WHERE rfq_no='RFQ-TEST-A';
    SELECT id INTO a_item FROM rfq_item WHERE rfq_id=a_rfq AND line_no=1;
    SELECT id INTO a_comp FROM rfq_component WHERE rfq_item_id=a_item AND component_no=1;
    SELECT id INTO a_run FROM rfq_readiness_run WHERE rfq_id=a_rfq LIMIT 1;
    SELECT id INTO b_rfq FROM rfq WHERE rfq_no='RFQ-TEST-B';
    SELECT id INTO c_rfq FROM rfq WHERE rfq_no='RFQ-TEST-C';
    SELECT c.id INTO other_comp FROM rfq_component c JOIN rfq_item i ON i.id=c.rfq_item_id WHERE i.rfq_id=c_rfq;

    -- POS 1
    IF (SELECT count(*) FROM rfq WHERE rfq_no IN ('RFQ-TEST-A','RFQ-TEST-B','RFQ-TEST-C'))=3
    THEN RAISE NOTICE 'PASS pos1: 3 fixtures seeded'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL pos1'; fail:=fail+1; END IF;

    -- POS 2 (delivery sum — descriptive; DLV-003 validator เป็น service-layer ยังไม่ทดสอบตรงนี้)
    IF (SELECT sum(quantity) FROM rfq_delivery d JOIN rfq_item i ON i.id=d.rfq_item_id WHERE i.rfq_id=a_rfq)=5000
    THEN RAISE NOTICE 'PASS pos2: split delivery sums (data-level)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL pos2'; fail:=fail+1; END IF;

    -- NEG 1: READY ไม่มี rfq_no/ready_at
    BEGIN
        INSERT INTO rfq (revision_no,status_code,created_by_ref,updated_by_ref)
        VALUES (1,'READY_FOR_ESTIMATE','X','X');
        RAISE NOTICE 'FAIL neg1'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg1: READY missing fields rejected'; pass:=pass+1; END;

    -- NEG 2: cross-RFQ subject (evidence ต้องมี extraction_run ด้วย แต่ควร fail ที่ membership ก่อน)
    BEGIN
        INSERT INTO rfq_field_evidence (rfq_id,subject_type,subject_id,field_name,source_type)
        VALUES (a_rfq,'COMPONENT',other_comp,'paper_ref','MANUAL');
        RAISE NOTICE 'FAIL neg2'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg2: cross-RFQ subject rejected'; pass:=pass+1; END;

    -- NEG 3: subject ไม่มีจริง
    BEGIN
        INSERT INTO rfq_clarification (rfq_id,subject_type,subject_id,question,raised_by_type)
        VALUES (a_rfq,'COMPONENT',gen_random_uuid(),'q','AI');
        RAISE NOTICE 'FAIL neg3'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg3: nonexistent subject rejected'; pass:=pass+1; END;

    -- NEG 4: field_policy subject_type ผิด (finding #2)
    BEGIN
        INSERT INTO rfq_field_policy VALUES ('ITEMS','*','INTERNAL','NONE','ALLOW','NONE','v1',NULL);
        RAISE NOTICE 'FAIL neg4'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg4: bad policy subject_type rejected'; pass:=pass+1; END;

    -- NEG 5: revision ข้าม rfq_no
    BEGIN
        INSERT INTO rfq (rfq_no,revision_no,supersedes_rfq_id,revision_reason,enquiry_ref,
            status_code,is_current,created_by_ref,updated_by_ref)
        VALUES ('RFQ-DIFF',2,a_rfq,'x','ENQ-A','DRAFT',true,'X','X');
        RAISE NOTICE 'FAIL neg5'; fail:=fail+1;
    EXCEPTION WHEN others THEN
        IF SQLERRM LIKE '%rfq_no%' THEN RAISE NOTICE 'PASS neg5: cross rfq_no rejected'; pass:=pass+1;
        ELSE RAISE NOTICE 'FAIL neg5 wrong reason: %', SQLERRM; fail:=fail+1; END IF;
    END;

    -- NEG 6 (tightened): revision ข้าม enquiry_ref — ต้อง fail ด้วยเหตุผล enquiry เจาะจง
    -- (เดิม false-positive ได้เพราะ unique index ก็ reject; คราวนี้ assert SQLERRM)
    BEGIN
        INSERT INTO rfq (rfq_no,revision_no,supersedes_rfq_id,revision_reason,enquiry_ref,
            status_code,is_current,created_by_ref,updated_by_ref)
        VALUES ('RFQ-TEST-A',2,a_rfq,'x','ENQ-DIFFERENT','DRAFT',true,'X','X');
        RAISE NOTICE 'FAIL neg6'; fail:=fail+1;
    EXCEPTION WHEN others THEN
        IF SQLERRM LIKE '%enquiry_ref%' THEN RAISE NOTICE 'PASS neg6: enquiry guard fired (asserted)'; pass:=pass+1;
        ELSE RAISE NOTICE 'FAIL neg6 wrong reason: %', SQLERRM; fail:=fail+1; END IF;
    END;

    -- NEG 7: initial revision_no<>1
    BEGIN
        INSERT INTO rfq (rfq_no,revision_no,enquiry_ref,created_by_ref,updated_by_ref)
        VALUES ('RFQ-TEST-Z',3,'ENQ-Z','X','X');
        RAISE NOTICE 'FAIL neg7'; fail:=fail+1;
    EXCEPTION WHEN others THEN RAISE NOTICE 'PASS neg7: bad initial revision_no rejected'; pass:=pass+1; END;

    -- NEG 8 (Blocker 1 DB-level): estimate_link บน RFQ ที่เป็น DRAFT (Case B) → reject
    BEGIN
        INSERT INTO rfq_estimate_link (rfq_id,estimate_system_code,external_estimate_id,handoff_by_ref,handoff_payload_sha256)
        VALUES (b_rfq,'EST','EXT-1','X',repeat('a',64));
        RAISE NOTICE 'FAIL neg8: estimate_link on DRAFT allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg8: estimate_link on non-READY rejected'; pass:=pass+1; END;

    -- NEG 9 (F5/R5): AI-derived evidence (derivation_type) โดยไม่มี extraction_run_id → reject
    BEGIN
        INSERT INTO rfq_field_evidence (rfq_id,subject_type,subject_id,field_name,source_type,derivation_type)
        VALUES (a_rfq,'COMPONENT',a_comp,'paper_ref','PDF','AI_EXTRACTED');
        RAISE NOTICE 'FAIL neg9: AI evidence without extraction_run allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg9: AI-derived evidence requires extraction_run'; pass:=pass+1; END;

    -- NEG 10 (M6): status_history อ้าง readiness_run ของ RFQ อื่น → reject
    BEGIN
        INSERT INTO rfq_status_history (rfq_id,to_status_code,changed_by_ref,readiness_run_id)
        VALUES (c_rfq,'READY_FOR_REVIEW','X',a_run);   -- a_run เป็นของ RFQ A ไม่ใช่ C
        RAISE NOTICE 'FAIL neg10: cross-RFQ readiness_run allowed'; fail:=fail+1;
    EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'PASS neg10: cross-RFQ readiness_run rejected'; pass:=pass+1; END;

    -- POS 3 (finding #1): revision ถูกต้องผ่าน service function → supersede + log ทั้ง 2 ฝั่ง
    tmp := create_rfq_revision(a_rfq, 'ลูกค้าเปลี่ยนขนาด', 'PREPARER-MOCK');
    IF (SELECT status_code FROM rfq WHERE id=a_rfq)='SUPERSEDED'
       AND (SELECT is_current FROM rfq WHERE id=a_rfq)=false
    THEN RAISE NOTICE 'PASS pos3a: old revision superseded'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL pos3a'; fail:=fail+1; END IF;
    SELECT count(*) INTO hist_cnt FROM rfq_status_history WHERE rfq_id=a_rfq AND to_status_code='SUPERSEDED';
    IF hist_cnt=1 THEN RAISE NOTICE 'PASS pos3b: supersede logged (finding #1)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL pos3b: got % rows', hist_cnt; fail:=fail+1; END IF;
    -- pos3c: revision ใหม่มี history NULL→DRAFT (M1 ทั้ง 2 ฝั่ง atomic)
    IF EXISTS (SELECT 1 FROM rfq_status_history WHERE rfq_id=tmp AND from_status_code IS NULL AND to_status_code='DRAFT')
    THEN RAISE NOTICE 'PASS pos3c: new revision DRAFT logged (M1)'; pass:=pass+1;
    ELSE RAISE NOTICE 'FAIL pos3c'; fail:=fail+1; END IF;

    -- POS 4 (B2 happy path): AI evidence ที่มี extraction_run ที่ถูก → insert ได้
    INSERT INTO rfq_ai_extraction_run (id, rfq_id, source_attachment_id, execution_target,
        provider_name, model_name, egress_policy_version, egress_decision_code,
        redaction_applied, redaction_manifest, status_code)
    VALUES (gen_random_uuid(), c_rfq, NULL, 'CLOUD','anthropic','claude','rfq-egress-v1',
        'REDACTED_ALLOW', true, '{"dropped":["contact_phone"]}'::jsonb, 'SUCCEEDED')
    RETURNING id INTO other_run;
    INSERT INTO rfq_field_evidence (rfq_id,subject_type,subject_id,field_name,source_type,derivation_type,extraction_run_id)
    VALUES (c_rfq,'COMPONENT',other_comp,'paper_ref','PDF','AI_EXTRACTED',other_run);
    RAISE NOTICE 'PASS pos4: AI evidence (derivation) with valid extraction_run inserted'; pass:=pass+1;

    -- NEG 11 (egress edge): REDACTED_ALLOW แต่ manifest ว่าง → reject
    BEGIN
        INSERT INTO rfq_ai_extraction_run (rfq_id,execution_target,provider_name,model_name,
            egress_policy_version,egress_decision_code,redaction_applied,status_code)
        VALUES (c_rfq,'CLOUD','anthropic','claude','v1','REDACTED_ALLOW',true,'SUCCEEDED');
        RAISE NOTICE 'FAIL neg11: empty manifest allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS neg11: REDACTED_ALLOW empty manifest rejected'; pass:=pass+1; END;

    -- POS 5 (egress edge): CLOUD + BLOCKED บันทึกได้ (audit ของ attempt ที่ถูกห้าม)
    INSERT INTO rfq_ai_extraction_run (rfq_id,execution_target,provider_name,model_name,
        egress_policy_version,egress_decision_code,status_code)
    VALUES (c_rfq,'CLOUD','anthropic','claude','v1','BLOCKED','BLOCKED');
    RAISE NOTICE 'PASS pos5: blocked CLOUD attempt recorded'; pass:=pass+1;

    RAISE NOTICE '================ RESULT: % passed, % failed ================', pass, fail;
    IF fail>0 THEN RAISE EXCEPTION 'TESTS FAILED: %', fail; END IF;
END $$;
