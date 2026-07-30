-- ============================================================================
-- test/050_extraction_tests.sql — ENQ extraction (007) functional tests
-- ⚠️ รันบน DB fresh หลัง 001+002+003+005+006+007 (single connection, superuser)
--   concurrency (2-worker claim / lease reclaim / apply race) อยู่ใน rfq_concurrency_tests.py
-- seed trusted tables ด้วย superuser (PoC synthetic — ไม่ใช่หลักฐาน scan/redaction production)
-- ============================================================================
SET search_path TO rfq;
DO $$
DECLARE
    pass int := 0; fail int := 0;
    h64 constant char(64) := repeat('a',64); rh64 constant char(64) := repeat('b',64);
    s_ok uuid; s_cloud_r uuid; s_cloud_a uuid; s_bad_mal uuid; s_unclass uuid; s_revoked uuid;
    att uuid; att_badp uuid; apr uuid; v_run uuid; v_lease uuid; v_res jsonb; v_dec text; v_shell uuid;
    p_ok jsonb;
BEGIN
    INSERT INTO rfq_ai_provider (provider_code, model_code, execution_target, policy_version) VALUES
        ('typhoon','v1','LOCAL','pol-v1'), ('claude','v1','CLOUD','pol-v1');
    -- sources
    INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref)
        VALUES ('s3://ok', h64,'CLEAN','CONFIRMED','INTERNAL',true,false,'LOCAL_ONLY','pol-v1','sc') RETURNING id INTO s_ok;
    INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref)
        VALUES ('s3://cr', h64,'CLEAN','CONFIRMED','CONFIDENTIAL',true,true,'REDACT','pol-v1','sc') RETURNING id INTO s_cloud_r;
    INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref)
        VALUES ('s3://ca', h64,'CLEAN','CONFIRMED','CONFIDENTIAL',true,true,'ALLOW','pol-v1','sc') RETURNING id INTO s_cloud_a;
    INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref)
        VALUES ('s3://mal', h64,'BLOCKED','CONFIRMED','INTERNAL',true,false,'LOCAL_ONLY','pol-v1','sc') RETURNING id INTO s_bad_mal;
    INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref)
        VALUES ('s3://unc', h64,'CLEAN','CONFIRMED','UNCLASSIFIED',true,false,'LOCAL_ONLY','pol-v1','sc') RETURNING id INTO s_unclass;
    INSERT INTO rfq_source_ingest (object_store_key,source_sha256,malware_scan_status,classification_status,classification_code,contains_personal_data,contains_trade_secret,cloud_action_code,policy_version,registered_by_ref,is_active,revoked_at)
        VALUES ('s3://rev', h64,'CLEAN','CONFIRMED','INTERNAL',true,false,'LOCAL_ONLY','pol-v1','sc',false, now()) RETURNING id INTO s_revoked;
    INSERT INTO rfq_redaction_attestation (source_ingest_id,purpose_code,redactor_ref,redactor_version,source_sha256,redacted_sha256,redacted_object_store_key,redaction_manifest,expires_at)
        VALUES (s_cloud_r,'enq','redgw','v1',h64,rh64,'s3://cr.redacted','{"dropped":["contact_phone"]}'::jsonb, now()+interval '1 day') RETURNING id INTO att;
    INSERT INTO rfq_egress_approval (source_ingest_id,provider_code,model_code,purpose_code,approved_by_ref,reason,expires_at)
        VALUES (s_cloud_a,'claude','v1','enq','dpo','pilot approved', now()+interval '1 day') RETURNING id INTO apr;
    INSERT INTO rfq_redaction_attestation (source_ingest_id,purpose_code,redactor_ref,redactor_version,source_sha256,redacted_sha256,redacted_object_store_key,redaction_manifest,expires_at)
        VALUES (s_cloud_r,'other','redgw','v1',h64,rh64,'s3://cr.redacted','{"x":1}'::jsonb, now()+interval '1 day') RETURNING id INTO att_badp;

    -- ES1: begin LOCAL → LOCAL_ONLY/PENDING, input=source hash
    v_res := begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{"enquiry_ref":"E1"}'::jsonb,'w','enq','b1');
    IF v_res->>'egress_decision_code'='LOCAL_ONLY' AND v_res->>'status'='PENDING'
       AND (SELECT input_sha256 FROM rfq_ai_extraction_run WHERE id=(v_res->>'run_id')::uuid)=h64
    THEN RAISE NOTICE 'PASS es1: begin LOCAL → LOCAL_ONLY/PENDING'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es1'; fail:=fail+1; END IF;

    -- ES2: CLOUD+REDACT + attestation → REDACTED_ALLOW, input=redacted hash
    v_res := begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',att,NULL,'{}'::jsonb,'w','enq','b2');
    IF v_res->>'egress_decision_code'='REDACTED_ALLOW'
       AND (SELECT input_sha256 FROM rfq_ai_extraction_run WHERE id=(v_res->>'run_id')::uuid)=rh64
       AND (SELECT provider_input_ref FROM rfq_ai_extraction_run WHERE id=(v_res->>'run_id')::uuid)='s3://cr.redacted'
    THEN RAISE NOTICE 'PASS es2: CLOUD+REDACT → REDACTED_ALLOW + redacted input'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es2 %', v_res; fail:=fail+1; END IF;

    -- ES3: CLOUD+REDACT ไม่มี attestation → BLOCKED
    v_res := begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b3');
    IF v_res->>'status'='BLOCKED' THEN RAISE NOTICE 'PASS es3: REDACT no-attestation → BLOCKED'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es3'; fail:=fail+1; END IF;

    -- ES4: CLOUD+ALLOW + approval → APPROVED_EXCEPTION (input=raw)
    v_res := begin_rfq_extraction(s_cloud_a,'CLOUD','claude','v1','enq',NULL,apr,'{}'::jsonb,'w','enq','b4');
    IF v_res->>'egress_decision_code'='APPROVED_EXCEPTION'
       AND (SELECT input_sha256 FROM rfq_ai_extraction_run WHERE id=(v_res->>'run_id')::uuid)=h64
    THEN RAISE NOTICE 'PASS es4: CLOUD+ALLOW → APPROVED_EXCEPTION + raw input'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es4'; fail:=fail+1; END IF;

    -- ES5: precondition fail (malware / unclassified) → BLOCKED
    IF begin_rfq_extraction(s_bad_mal,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b5a')->>'status'='BLOCKED'
       AND begin_rfq_extraction(s_unclass,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b5b')->>'status'='BLOCKED'
    THEN RAISE NOTICE 'PASS es5: malware/UNCLASSIFIED → BLOCKED'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es5'; fail:=fail+1; END IF;

    -- ES6 (C1): unknown source → 23503, ไม่มี RFQ/run สร้าง
    BEGIN PERFORM begin_rfq_extraction(gen_random_uuid(),'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b6');
        RAISE NOTICE 'FAIL es6: unknown source allowed'; fail:=fail+1;
    EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'PASS es6: unknown source → 23503'; pass:=pass+1; END;

    -- ES7 (C1): existing-but-revoked source → BLOCKED durable (มี run)
    v_res := begin_rfq_extraction(s_revoked,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b7');
    IF v_res->>'status'='BLOCKED' AND EXISTS (SELECT 1 FROM rfq_ai_extraction_run WHERE id=(v_res->>'run_id')::uuid AND status_code='BLOCKED')
    THEN RAISE NOTICE 'PASS es7: revoked source → BLOCKED durable'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es7'; fail:=fail+1; END IF;

    -- ES8: claim happy → RUNNING; claim ซ้ำ (active lease) → should_execute false
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b8')->>'run_id')::uuid;
    v_res := claim_rfq_extraction(v_run,'w1','enq','c8');
    v_lease := (v_res->>'lease_token')::uuid;
    IF v_res->>'should_execute'='true' AND (SELECT status_code FROM rfq_ai_extraction_run WHERE id=v_run)='RUNNING'
       AND (claim_rfq_extraction(v_run,'w2','enq','c8b')->>'should_execute')='false'
    THEN RAISE NOTICE 'PASS es8: claim → RUNNING; 2nd claim active-lease → no exec'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es8'; fail:=fail+1; END IF;

    -- ES9 (R1): revoke attestation หลัง begin → claim BLOCKED
    v_run := (begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',att,NULL,'{}'::jsonb,'w','enq','b9')->>'run_id')::uuid;
    UPDATE rfq_redaction_attestation SET is_active=false, revoked_at=now() WHERE id=att;
    v_res := claim_rfq_extraction(v_run,'w1','enq','c9');
    IF v_res->>'should_execute'='false' AND v_res->>'status'='BLOCKED'
       AND (SELECT status_code FROM rfq_ai_extraction_run WHERE id=v_run)='BLOCKED'
    THEN RAISE NOTICE 'PASS es9: claim revalidate revoked-attestation → BLOCKED'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es9 %',v_res; fail:=fail+1; END IF;
    UPDATE rfq_redaction_attestation SET is_active=true, revoked_at=NULL WHERE id=att;   -- restore

    -- ES10: apply happy → SUCCEEDED + evidence
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b10')->>'run_id')::uuid;
    v_lease := (claim_rfq_extraction(v_run,'w1','enq','c10')->>'lease_token')::uuid;
    p_ok := ('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'",
      "items":[{"line_no":1,"fields":{"job_name":"box"},
        "deliveries":[{"delivery_no":1,"option_no":1,"fields":{"destination_ref":"D"}}],
        "quantity_options":[{"option_no":1,"fields":{"quantity":5000}}]}],
      "evidence":[
        {"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF","confidence":0.9},
        {"subject_type":"QUANTITY","ref":{"line_no":1,"option_no":1},"field_name":"quantity","source_type":"PDF","confidence":0.9},
        {"subject_type":"DELIVERY","ref":{"line_no":1,"delivery_no":1},"field_name":"destination_ref","source_type":"PDF","confidence":0.8},
        {"subject_type":"DELIVERY","ref":{"line_no":1,"delivery_no":1},"field_name":"option_no","source_type":"PDF","confidence":0.8}
      ]}')::jsonb;
    v_res := apply_rfq_extraction(v_run,v_lease,p_ok,'w1','enq','a10');
    IF v_res->>'status'='SUCCEEDED'
       AND (SELECT count(*) FROM rfq_field_evidence WHERE extraction_run_id=v_run)=4
       AND (SELECT value_snapshot FROM rfq_field_evidence WHERE extraction_run_id=v_run AND subject_type='DELIVERY' AND field_name='option_no')='1'::jsonb
    THEN RAISE NOTICE 'PASS es10: apply happy + relationship evidence (option_no natural key)'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es10'; fail:=fail+1; END IF;

    -- ES11: apply replay (same request) → คืน SUCCEEDED เดิม (idempotent)
    IF apply_rfq_extraction(v_run,v_lease,p_ok,'w1','enq','a10')->>'status'='SUCCEEDED'
    THEN RAISE NOTICE 'PASS es11: apply exact replay idempotent'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es11'; fail:=fail+1; END IF;

    -- ES12: missing evidence → reject
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b12')->>'run_id')::uuid;
    v_lease := (claim_rfq_extraction(v_run,'w1','enq','c12')->>'lease_token')::uuid;
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":"x","product_type_ref":"PT"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF"}]}')::jsonb,'w1','enq','a12');
        RAISE NOTICE 'FAIL es12: missing evidence allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFR01' THEN RAISE NOTICE 'PASS es12: AI field missing evidence → RFR01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es12 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES13: evidence เกิน (field ไม่ได้เขียน) → reject
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF"},{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"notes","source_type":"PDF"}]}')::jsonb,'w1','enq','a13');
        RAISE NOTICE 'FAIL es13: extra evidence allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFR01' THEN RAISE NOTICE 'PASS es13: evidence for unwritten field → RFR01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es13 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES14: input_sha256 mismatch → reject
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||rh64||'","items":[{"line_no":1,"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF"}]}')::jsonb,'w1','enq','a14');
        RAISE NOTICE 'FAIL es14: input hash mismatch allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='22023' THEN RAISE NOTICE 'PASS es14: input_sha256 mismatch → reject'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es14 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES15 (C4): apply เมื่อ lease หมดอายุ → reject
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b15')->>'run_id')::uuid;
    v_lease := (claim_rfq_extraction(v_run,'w1','enq','c15')->>'lease_token')::uuid;
    UPDATE rfq_ai_extraction_run SET lease_expires_at = now()-interval '1 min' WHERE id=v_run;
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF"}]}')::jsonb,'w1','enq','a15');
        RAISE NOTICE 'FAIL es15: expired-lease apply allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFS01' THEN RAISE NOTICE 'PASS es15: apply with expired lease → RFS01 (C4)'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es15 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES16 (R6): fail บน PENDING (ยังไม่ claim) → reject
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b16')->>'run_id')::uuid;
    BEGIN PERFORM fail_rfq_extraction(v_run, gen_random_uuid(), 'x','w1','enq','f16');
        RAISE NOTICE 'FAIL es16: fail on PENDING allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFS01' THEN RAISE NOTICE 'PASS es16: fail on PENDING (no lease) → RFS01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es16 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES17: unknown business key ใน fields → reject
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b17')->>'run_id')::uuid;
    v_lease := (claim_rfq_extraction(v_run,'w1','enq','c17')->>'lease_token')::uuid;
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"EVIL":1}}],"evidence":[]}')::jsonb,'w1','enq','a17');
        RAISE NOTICE 'FAIL es17: unknown fields key allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='22023' THEN RAISE NOTICE 'PASS es17: unknown fields key → reject'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es17 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES18 (B1): direct UPDATE BLOCKED run → SUCCEEDED ถูก invariant เดิม (001) reject
    v_run := (begin_rfq_extraction(s_bad_mal,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b18')->>'run_id')::uuid;
    BEGIN UPDATE rfq_ai_extraction_run SET status_code='SUCCEEDED' WHERE id=v_run;
        RAISE NOTICE 'FAIL es18: BLOCKED→SUCCEEDED allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS es18: BLOCKED→SUCCEEDED blocked (B1 invariant คงอยู่)'; pass:=pass+1; END;

    -- ES19 (B3): attestation purpose ไม่ตรง → BLOCKED
    IF begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',att_badp,NULL,'{}'::jsonb,'w','enq','b19')->>'status'='BLOCKED'
    THEN RAISE NOTICE 'PASS es19: attestation purpose mismatch → BLOCKED'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es19'; fail:=fail+1; END IF;

    -- ES20 (B3): mutate attestation hash หลัง begin → claim BLOCKED (re-bind snapshot)
    v_run := (begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',att,NULL,'{}'::jsonb,'w','enq','b20')->>'run_id')::uuid;
    UPDATE rfq_redaction_attestation SET redacted_sha256=repeat('c',64) WHERE id=att;
    v_res := claim_rfq_extraction(v_run,'w1','enq','c20');
    IF v_res->>'should_execute'='false' AND v_res->>'status'='BLOCKED'
    THEN RAISE NOTICE 'PASS es20: attestation mutated หลัง begin → claim BLOCKED'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es20 %',v_res; fail:=fail+1; END IF;
    UPDATE rfq_redaction_attestation SET redacted_sha256=rh64 WHERE id=att;

    -- ES21 (B4): evidence ไม่ระบุ source_type → reject (ห้าม default)
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b21')->>'run_id')::uuid;
    v_lease := (claim_rfq_extraction(v_run,'w1','enq','c21')->>'lease_token')::uuid;
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name"}]}')::jsonb,'w1','enq','a21');
        RAISE NOTICE 'FAIL es21: missing source_type allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='22023' THEN RAISE NOTICE 'PASS es21: evidence missing source_type → reject'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es21 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES22 (B4): evidence derivation HUMAN_EXTRACTED ใน apply → reject (ต้องเป็น AI)
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF","derivation_type":"HUMAN_EXTRACTED"}]}')::jsonb,'w1','enq','a22');
        RAISE NOTICE 'FAIL es22: HUMAN derivation allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFR01' THEN RAISE NOTICE 'PASS es22: HUMAN/SYSTEM derivation ใน apply → RFR01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es22 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES23 (B4): business field JSON null → reject
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":null}}],"evidence":[]}')::jsonb,'w1','enq','a23');
        RAISE NOTICE 'FAIL es23: JSON null field allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='22023' THEN RAISE NOTICE 'PASS es23: JSON null business field → reject'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es23 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES24 (B4): AI_INFERENCE evidence โดยไม่มี matching blocking clarification → reject
    BEGIN PERFORM apply_rfq_extraction(v_run,v_lease,('{"schema_version":"extract-v1.1","input_sha256":"'||h64||'","items":[{"line_no":1,"fields":{"job_name":"x"}}],"evidence":[{"subject_type":"ITEM","ref":{"line_no":1},"field_name":"job_name","source_type":"PDF","derivation_type":"AI_INFERENCE"}]}')::jsonb,'w1','enq','a24');
        RAISE NOTICE 'FAIL es24: AI_INFERENCE without clarification allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFR01' THEN RAISE NOTICE 'PASS es24: AI_INFERENCE ต้องมี blocking clarification → RFR01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es24 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES25 (H1): blocked shell (มี extraction run ไม่ SUCCEEDED) → เข้า READY_FOR_ESTIMATE ไม่ได้
    v_run := (begin_rfq_extraction(s_bad_mal,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b25')->>'run_id')::uuid;
    SELECT rfq_id INTO v_shell FROM rfq_ai_extraction_run WHERE id=v_run;
    BEGIN UPDATE rfq SET status_code='READY_FOR_ESTIMATE', rfq_no='H1-X', ready_at=now(), ready_by_ref='x' WHERE id=v_shell;
        RAISE NOTICE 'FAIL es25: blocked shell → Ready allowed'; fail:=fail+1;
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'PASS es25: blocked shell → Ready blocked (H1)'; pass:=pass+1; END;

    -- ES26 (B3.1): purpose NULL / '' / whitespace → reject (กัน bypass purpose binding)
    BEGIN PERFORM begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1',NULL,att,NULL,'{}'::jsonb,'w','enq','b26a');
        RAISE NOTICE 'FAIL es26a: NULL purpose allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='22023' THEN RAISE NOTICE 'PASS es26a: NULL purpose → reject'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es26a %',SQLERRM; fail:=fail+1; END IF; END;
    BEGIN PERFORM begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','   ',att,NULL,'{}'::jsonb,'w','enq','b26b');
        RAISE NOTICE 'FAIL es26b: blank purpose allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='22023' THEN RAISE NOTICE 'PASS es26b: blank purpose → reject'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es26b %',SQLERRM; fail:=fail+1; END IF; END;
    -- purpose ที่ตรงกับ attestation (att.purpose='enq') ต้องผ่านปกติ (ยืนยันไม่ over-block)
    IF begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',att,NULL,'{}'::jsonb,'w','enq','b26c')->>'egress_decision_code'='REDACTED_ALLOW'
    THEN RAISE NOTICE 'PASS es26c: valid purpose ยังผ่าน REDACTED_ALLOW'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es26c'; fail:=fail+1; END IF;

    -- ES27 (B3.2): cloud_action drift REDACT→BLOCK หลัง begin → claim ต้อง BLOCKED (re-evaluate)
    v_run := (begin_rfq_extraction(s_cloud_r,'CLOUD','claude','v1','enq',att,NULL,'{}'::jsonb,'w','enq','b27')->>'run_id')::uuid;
    UPDATE rfq_source_ingest SET cloud_action_code='BLOCK' WHERE id=s_cloud_r;
    v_res := claim_rfq_extraction(v_run,'w1','enq','c27');
    IF v_res->>'should_execute'='false' AND v_res->>'status'='BLOCKED'
    THEN RAISE NOTICE 'PASS es27: cloud_action REDACT→BLOCK หลัง begin → claim BLOCKED (B3.2)'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es27 %',v_res; fail:=fail+1; END IF;
    UPDATE rfq_source_ingest SET cloud_action_code='REDACT' WHERE id=s_cloud_r;

    -- ES28 (B3.2): provider inactive หลัง begin → claim BLOCKED
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b28')->>'run_id')::uuid;
    UPDATE rfq_ai_provider SET is_active=false WHERE provider_code='typhoon';
    v_res := claim_rfq_extraction(v_run,'w1','enq','c28');
    IF v_res->>'should_execute'='false' AND v_res->>'status'='BLOCKED'
    THEN RAISE NOTICE 'PASS es28: provider disabled หลัง begin → claim BLOCKED'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es28 %',v_res; fail:=fail+1; END IF;
    UPDATE rfq_ai_provider SET is_active=true WHERE provider_code='typhoon';

    -- ES29 (F1): claim run ที่ไม่มี → RFN01 (run_not_found ≠ state conflict ≠ invalid result)
    BEGIN PERFORM claim_rfq_extraction(gen_random_uuid(),'w1','enq','c29');
        RAISE NOTICE 'FAIL es29: claim unknown run allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFN01' THEN RAISE NOTICE 'PASS es29: claim unknown run → RFN01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es29 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES30 (F1): apply run ที่ไม่มี → RFN01
    BEGIN PERFORM apply_rfq_extraction(gen_random_uuid(), gen_random_uuid(), p_ok,'w1','enq','a30');
        RAISE NOTICE 'FAIL es30: apply unknown run allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFN01' THEN RAISE NOTICE 'PASS es30: apply unknown run → RFN01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es30 %',SQLERRM; fail:=fail+1; END IF; END;

    -- ES31 (F1): apply ด้วย lease token ผิด (ยังไม่หมดอายุ) → RFS01 (state/lease conflict, แยกจาก invalid result RFR01)
    v_run := (begin_rfq_extraction(s_ok,'LOCAL','typhoon','v1','enq',NULL,NULL,'{}'::jsonb,'w','enq','b31')->>'run_id')::uuid;
    PERFORM claim_rfq_extraction(v_run,'w1','enq','c31');
    BEGIN PERFORM apply_rfq_extraction(v_run, gen_random_uuid(), p_ok,'w1','enq','a31');
        RAISE NOTICE 'FAIL es31: apply wrong-lease allowed'; fail:=fail+1;
    EXCEPTION WHEN others THEN IF SQLSTATE='RFS01' THEN RAISE NOTICE 'PASS es31: apply wrong lease → RFS01'; pass:=pass+1; ELSE RAISE NOTICE 'FAIL es31 %',SQLERRM; fail:=fail+1; END IF; END;

    RAISE NOTICE '========= EXTRACTION RESULT: % passed, % failed =========', pass, fail;
    IF fail>0 THEN RAISE EXCEPTION 'EXTRACTION TESTS FAILED: %', fail; END IF;
END $$;
