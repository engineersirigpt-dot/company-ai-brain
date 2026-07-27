-- ============================================================================
-- run_tests.sql — acceptance tests (รันหลัง 001+002+003)
-- เน้น negative test: guard ต้อง "ปฏิเสธ" ของผิดจริง (สำคัญกว่า happy path)
-- แต่ละ test: ทำใน savepoint, คาดว่า error, ถ้าไม่ error = FAIL
-- output: NOTICE 'PASS ...'/'FAIL ...'; ปิดท้ายสรุปผ่าน/ไม่ผ่าน
-- ============================================================================
DO $$
DECLARE
    pass int := 0; fail int := 0;
    a_rfq uuid; a_item uuid; a_comp uuid;
    c_rfq uuid;
    other_comp uuid;
    hist_cnt int;
    tmp uuid;
BEGIN
    SELECT id INTO a_rfq FROM rfq WHERE rfq_no = 'RFQ-TEST-A';
    SELECT id INTO a_item FROM rfq_item WHERE rfq_id = a_rfq AND line_no = 1;
    SELECT id INTO a_comp FROM rfq_component WHERE rfq_item_id = a_item AND component_no = 1;
    SELECT id INTO c_rfq FROM rfq WHERE rfq_no = 'RFQ-TEST-C';
    SELECT c.id INTO other_comp FROM rfq_component c
        JOIN rfq_item i ON i.id = c.rfq_item_id WHERE i.rfq_id = c_rfq;

    -- POS 1: 3 เคส seed สำเร็จ
    IF (SELECT count(*) FROM rfq WHERE rfq_no IN ('RFQ-TEST-A','RFQ-TEST-B','RFQ-TEST-C')) = 3 THEN
        RAISE NOTICE 'PASS pos1: 3 synthetic cases seeded'; pass := pass + 1;
    ELSE RAISE NOTICE 'FAIL pos1'; fail := fail + 1; END IF;

    -- POS 2: multi-delivery รวม = 5000 (qty option 1 ของ A)
    IF (SELECT sum(quantity) FROM rfq_delivery d JOIN rfq_item i ON i.id=d.rfq_item_id
        WHERE i.rfq_id=a_rfq) = 5000 THEN
        RAISE NOTICE 'PASS pos2: split delivery sums correctly'; pass := pass + 1;
    ELSE RAISE NOTICE 'FAIL pos2'; fail := fail + 1; END IF;

    -- NEG 1: READY_FOR_ESTIMATE โดยไม่มี rfq_no/ready_at → CHECK ต้อง fail
    BEGIN
        INSERT INTO rfq (revision_no, status_code, created_by_ref, updated_by_ref)
        VALUES (1, 'READY_FOR_ESTIMATE', 'X', 'X');
        RAISE NOTICE 'FAIL neg1: READY without rfq_no/ready_at was allowed'; fail := fail + 1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS neg1: READY without rfq_no/ready_at rejected'; pass := pass + 1;
    END;

    -- NEG 2: subject membership — evidence ชี้ component ของ RFQ อื่น (other_comp ∈ C, แต่ rfq_id = A)
    BEGIN
        INSERT INTO rfq_field_evidence (rfq_id, subject_type, subject_id, field_name, source_type)
        VALUES (a_rfq, 'COMPONENT', other_comp, 'paper_ref', 'AI_INFERENCE');
        RAISE NOTICE 'FAIL neg2: cross-RFQ subject was allowed'; fail := fail + 1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS neg2: cross-RFQ subject rejected'; pass := pass + 1;
    END;

    -- NEG 3: subject_id ที่ไม่มีจริง → reject
    BEGIN
        INSERT INTO rfq_clarification (rfq_id, subject_type, subject_id, question, raised_by_type)
        VALUES (a_rfq, 'COMPONENT', gen_random_uuid(), 'q', 'AI');
        RAISE NOTICE 'FAIL neg3: nonexistent subject allowed'; fail := fail + 1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS neg3: nonexistent subject rejected'; pass := pass + 1;
    END;

    -- NEG 4: field_policy subject_type ผิด (finding #2) → CHECK fail
    BEGIN
        INSERT INTO rfq_field_policy VALUES
        ('ITEMS','*','INTERNAL','NONE','ALLOW','NONE','v1',NULL);  -- 'ITEMS' พิมพ์ผิด
        RAISE NOTICE 'FAIL neg4: bad field_policy subject_type allowed'; fail := fail + 1;
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS neg4: bad field_policy subject_type rejected'; pass := pass + 1;
    END;

    -- NEG 5: revision ข้าม rfq_no → trigger fail
    BEGIN
        INSERT INTO rfq (rfq_no, revision_no, supersedes_rfq_id, revision_reason,
            enquiry_ref, status_code, is_current, created_by_ref, updated_by_ref)
        VALUES ('RFQ-TEST-DIFFERENT', 2, a_rfq, 'change', 'ENQ-A', 'DRAFT', true, 'X', 'X');
        RAISE NOTICE 'FAIL neg5: revision crossing rfq_no allowed'; fail := fail + 1;
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'PASS neg5: revision crossing rfq_no rejected (%)', SQLERRM; pass := pass + 1;
    END;

    -- NEG 6: revision ข้าม enquiry_ref → trigger fail
    BEGIN
        INSERT INTO rfq (rfq_no, revision_no, supersedes_rfq_id, revision_reason,
            enquiry_ref, status_code, is_current, created_by_ref, updated_by_ref)
        VALUES ('RFQ-TEST-A', 2, a_rfq, 'change', 'ENQ-DIFFERENT', 'DRAFT', true, 'X', 'X');
        RAISE NOTICE 'FAIL neg6: revision crossing enquiry_ref allowed'; fail := fail + 1;
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'PASS neg6: revision crossing enquiry_ref rejected'; pass := pass + 1;
    END;

    -- POS 3 (finding #1): revision ที่ถูกต้อง → supersede + log status_history atomic
    INSERT INTO rfq (rfq_no, revision_no, supersedes_rfq_id, revision_reason,
        enquiry_ref, status_code, is_current, created_by_ref, updated_by_ref)
    VALUES ('RFQ-TEST-A', 2, a_rfq, 'ลูกค้าเปลี่ยนขนาด', 'ENQ-A', 'DRAFT', true, 'PREPARER-MOCK', 'PREPARER-MOCK')
    RETURNING id INTO tmp;
    -- ตัวเก่าต้องเป็น SUPERSEDED + is_current=false
    IF (SELECT status_code FROM rfq WHERE id=a_rfq) = 'SUPERSEDED'
       AND (SELECT is_current FROM rfq WHERE id=a_rfq) = false THEN
        RAISE NOTICE 'PASS pos3a: old revision superseded'; pass := pass + 1;
    ELSE RAISE NOTICE 'FAIL pos3a'; fail := fail + 1; END IF;
    -- ต้องมี history row ของ transition → SUPERSEDED (finding #1)
    SELECT count(*) INTO hist_cnt FROM rfq_status_history
        WHERE rfq_id=a_rfq AND to_status_code='SUPERSEDED';
    IF hist_cnt = 1 THEN
        RAISE NOTICE 'PASS pos3b: supersede logged in status_history (finding #1)'; pass := pass + 1;
    ELSE RAISE NOTICE 'FAIL pos3b: supersede not logged (got % rows)', hist_cnt; fail := fail + 1; END IF;

    -- NEG 7: initial RFQ ที่ revision_no<>1 โดยไม่มี supersedes → fail
    BEGIN
        INSERT INTO rfq (rfq_no, revision_no, enquiry_ref, created_by_ref, updated_by_ref)
        VALUES ('RFQ-TEST-Z', 3, 'ENQ-Z', 'X', 'X');
        RAISE NOTICE 'FAIL neg7: revision_no<>1 without supersedes allowed'; fail := fail + 1;
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'PASS neg7: bad initial revision_no rejected'; pass := pass + 1;
    END;

    RAISE NOTICE '================ RESULT: % passed, % failed ================', pass, fail;
    IF fail > 0 THEN RAISE EXCEPTION 'TESTS FAILED: %', fail; END IF;
END $$;
