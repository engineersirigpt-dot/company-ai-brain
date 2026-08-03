-- ============================================================================
-- 012_rfq_draft_edit.sql — draft-edit endpoint (consumer ตัวแรกของ V3 pattern)
-- ============================================================================
-- เปิด write workflow ของ RFQ: อ่าน Draft → แก้ข้อมูล → ตรวจ expected_row_version
--   → lock/freeze → บันทึก → reconcile evidence → bump version (optimistic concurrency)
--
-- upsert_rfq_draft(rfq, expected_row_version, actor, patch):
--   - lock parent (FOR UPDATE) + สถานะต้อง DRAFT + is_current
--   - F4a optimistic: expected_row_version ต้องตรง + ไม่ NULL → mismatch = 40001
--   - patch = { header:{fields:{...}}, items:[{line_no, fields:{...}}] }
--   - **update-only** (Codex first-cut): header + item scalar fields ของ item ที่มีอยู่ (match line_no)
--     insert item ใหม่ = slice ถัดไป (ต้องมาพร้อม delete/tree lifecycle จึงจะสมมาตร)
--   - **null semantics (M1)**: omit key = ไม่เปลี่ยน ; key:null = clear (nullable) / reject (NOT NULL) — ใช้ `f ? key` แยก presence ไม่ใช่ COALESCE
--   - **evidence reconciliation (B1)**: field ที่ค่าจริงเปลี่ยน → supersede AI evidence เดิม (verification_status=CORRECTED)
--     + append MANUAL/HUMAN_EXTRACTED evidence ของค่าใหม่ (value_snapshot จาก actual column) → provenance ไม่อ้าง source ผิด
--   - **limits (M2)**: patch ≤1MB, items ≤100, reject empty header.fields/items/item.fields + duplicate line_no
--   - bump row_version หนึ่งครั้ง ผ่าน _bump_rfq_version (V3 pattern)
--
-- SCOPE (flag Codex): item update-only ; ยังไม่ทำ insert/delete item, quantity/component/tree,
--   READY_FOR_REVIEW edit, submit_for_review transition
-- ขอบเขต: local + synthetic prototype ยังไม่ deploy ; owner=rfq_owner DEFINER, pinned path, REVOKE PUBLIC, GRANT rfq_app
-- current-evidence contract: latest by created_at ที่ verification_status NOT IN ('CORRECTED','REJECTED')
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ----------------------------------------------------------------------------
-- helper: reconcile evidence เมื่อ human แก้ค่า field (supersede AI เดิม + append MANUAL)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _reconcile_field_evidence(
    p_rfq_id uuid, p_subject_type text, p_subject_id uuid, p_field text, p_new jsonb, p_actor text
) RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
BEGIN
    -- (a) supersede evidence เดิมที่ยัง active (ไม่ CORRECTED/REJECTED) → เก็บหลักฐานไว้ ไม่ลบ
    UPDATE rfq_field_evidence
        SET verification_status='CORRECTED', verified_by_ref=p_actor, verified_at=clock_timestamp(),
            correction_note='superseded by manual draft edit'
        WHERE rfq_id=p_rfq_id AND subject_type=p_subject_type AND subject_id=p_subject_id AND field_name=p_field
          AND verification_status NOT IN ('CORRECTED','REJECTED');
    -- (b) append MANUAL/HUMAN_EXTRACTED evidence ของค่าใหม่จริง (value_snapshot = actual column ที่ cast แล้ว)
    INSERT INTO rfq_field_evidence (rfq_id, subject_type, subject_id, field_name, value_snapshot,
        source_type, derivation_type, verification_status, verified_by_ref, verified_at)
    VALUES (p_rfq_id, p_subject_type, p_subject_id, p_field, p_new,
        'MANUAL', 'HUMAN_EXTRACTED', 'VERIFIED', p_actor, clock_timestamp());
END;
$$;
ALTER FUNCTION _reconcile_field_evidence(uuid, text, uuid, text, jsonb, text) OWNER TO rfq_owner;
REVOKE ALL ON FUNCTION _reconcile_field_evidence(uuid, text, uuid, text, jsonb, text) FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- upsert_rfq_draft (update-only first cut)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION upsert_rfq_draft(
    p_rfq_id uuid, p_expected_row_version int, p_actor text, p_patch jsonb
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    r rfq%ROWTYPE; hdr jsonb; f jsonb; it jsonb; v_ln smallint; v_item_id uuid;
    v_before jsonb; v_after jsonb; v_key text; v_seen smallint[] := '{}';
    v_upd int := 0; v_edited_header boolean := false;
    c_max_bytes constant int := 1000000; c_max_items constant int := 100;
    c_top   constant text[] := ARRAY['header','items'];
    c_hdr   constant text[] := ARRAY['customer_ref','customer_code_snapshot','customer_name_snapshot','customer_name_raw',
        'is_new_customer','contact_name','contact_phone','contact_email','sales_owner_ref','sales_owner_code_snapshot',
        'sales_owner_name_snapshot','customer_notes','quote_due_at','priority_code'];
    c_itemf constant text[] := ARRAY['job_name','product_type_ref','product_type_code_snapshot','product_type_name_snapshot',
        'product_type_raw','description','intended_use','finished_width_mm','finished_length_mm','finished_depth_mm',
        'is_reprint','previous_job_ref','use_previous_plate','is_multiple_design','finishing_state','packing_state',
        'artwork_state','sample_state','sample_description','notes'];
BEGIN
    -- ---- input validation + limits (ก่อนถือ lock) ----
    IF p_actor IS NULL OR p_actor !~ '[^[:space:]]' OR length(p_actor) > 200 OR p_actor ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'upsert_rfq_draft actor invalid (blank/whitespace/too long/control char)' USING ERRCODE = '23514';
    END IF;
    IF p_patch IS NULL OR jsonb_typeof(p_patch) <> 'object' THEN
        RAISE EXCEPTION 'patch ต้องเป็น object' USING ERRCODE = '23514';
    END IF;
    IF octet_length(p_patch::text) > c_max_bytes THEN RAISE EXCEPTION 'patch เกินขนาด' USING ERRCODE = '54000'; END IF;
    PERFORM _reject_unknown_keys(p_patch, c_top, 'patch');
    IF NOT (p_patch ? 'header' OR p_patch ? 'items') THEN
        RAISE EXCEPTION 'patch ต้องมี header หรือ items อย่างน้อยหนึ่ง' USING ERRCODE = '23514';
    END IF;

    -- ---- lock parent + gate (DRAFT + is_current + optimistic version) ----
    SELECT * INTO r FROM rfq WHERE id = p_rfq_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'RFQ % not found', p_rfq_id USING ERRCODE = '23503'; END IF;
    IF r.status_code <> 'DRAFT' THEN
        RAISE EXCEPTION 'upsert_rfq_draft ต้องเป็น DRAFT (got %)', r.status_code USING ERRCODE = '23514';
    END IF;
    IF r.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'only the current revision can be edited (F4b)' USING ERRCODE = '23514';
    END IF;
    IF p_expected_row_version IS NULL OR r.row_version IS DISTINCT FROM p_expected_row_version THEN
        RAISE EXCEPTION 'row_version mismatch (expected %, got %)', p_expected_row_version, r.row_version USING ERRCODE = '40001';
    END IF;

    -- ---- header.fields → UPDATE rfq (presence-based ; null clears nullable) ----
    IF p_patch ? 'header' THEN
        hdr := p_patch->'header'; PERFORM _reject_unknown_keys(hdr, ARRAY['fields'], 'header');
        f := COALESCE(hdr->'fields', '{}'::jsonb); PERFORM _reject_unknown_keys(f, c_hdr, 'header.fields');
        IF f = '{}'::jsonb THEN RAISE EXCEPTION 'header.fields ว่าง (no-op)' USING ERRCODE = '23514'; END IF;
        SELECT to_jsonb(x.*) INTO v_before FROM rfq x WHERE x.id = p_rfq_id;
        UPDATE rfq SET
            customer_ref            = CASE WHEN f ? 'customer_ref' THEN f->>'customer_ref' ELSE customer_ref END,
            customer_code_snapshot  = CASE WHEN f ? 'customer_code_snapshot' THEN f->>'customer_code_snapshot' ELSE customer_code_snapshot END,
            customer_name_snapshot  = CASE WHEN f ? 'customer_name_snapshot' THEN f->>'customer_name_snapshot' ELSE customer_name_snapshot END,
            customer_name_raw       = CASE WHEN f ? 'customer_name_raw' THEN f->>'customer_name_raw' ELSE customer_name_raw END,
            is_new_customer         = CASE WHEN f ? 'is_new_customer' THEN (f->>'is_new_customer')::boolean ELSE is_new_customer END,
            contact_name            = CASE WHEN f ? 'contact_name' THEN f->>'contact_name' ELSE contact_name END,
            contact_phone           = CASE WHEN f ? 'contact_phone' THEN f->>'contact_phone' ELSE contact_phone END,
            contact_email           = CASE WHEN f ? 'contact_email' THEN f->>'contact_email' ELSE contact_email END,
            sales_owner_ref         = CASE WHEN f ? 'sales_owner_ref' THEN f->>'sales_owner_ref' ELSE sales_owner_ref END,
            sales_owner_code_snapshot = CASE WHEN f ? 'sales_owner_code_snapshot' THEN f->>'sales_owner_code_snapshot' ELSE sales_owner_code_snapshot END,
            sales_owner_name_snapshot = CASE WHEN f ? 'sales_owner_name_snapshot' THEN f->>'sales_owner_name_snapshot' ELSE sales_owner_name_snapshot END,
            customer_notes          = CASE WHEN f ? 'customer_notes' THEN f->>'customer_notes' ELSE customer_notes END,
            quote_due_at            = CASE WHEN f ? 'quote_due_at' THEN (f->>'quote_due_at')::timestamptz ELSE quote_due_at END,
            priority_code           = CASE WHEN f ? 'priority_code' THEN f->>'priority_code' ELSE priority_code END
        WHERE id = p_rfq_id;
        SELECT to_jsonb(x.*) INTO v_after FROM rfq x WHERE x.id = p_rfq_id;
        FOR v_key IN SELECT jsonb_object_keys(f) LOOP
            IF v_before->v_key IS DISTINCT FROM v_after->v_key THEN                       -- เฉพาะค่าจริงเปลี่ยน → ไม่ churn
                PERFORM _reconcile_field_evidence(p_rfq_id, 'RFQ', p_rfq_id, v_key, v_after->v_key, p_actor);
            END IF;
        END LOOP;
        v_edited_header := true;
    END IF;

    -- ---- items[] → UPDATE existing by (rfq_id, line_no) เท่านั้น (update-only) ----
    IF p_patch ? 'items' THEN
        IF jsonb_typeof(p_patch->'items') <> 'array' THEN RAISE EXCEPTION 'items ต้องเป็น array' USING ERRCODE='23514'; END IF;
        IF jsonb_array_length(p_patch->'items') < 1 THEN RAISE EXCEPTION 'items ว่าง (no-op)' USING ERRCODE='23514'; END IF;
        IF jsonb_array_length(p_patch->'items') > c_max_items THEN RAISE EXCEPTION 'items เกิน limit (%)', c_max_items USING ERRCODE='54000'; END IF;
        FOR it IN SELECT * FROM jsonb_array_elements(p_patch->'items') LOOP
            PERFORM _reject_unknown_keys(it, ARRAY['line_no','fields'], 'item');
            IF NOT (it ? 'line_no') OR jsonb_typeof(it->'line_no') <> 'number' THEN
                RAISE EXCEPTION 'item ต้องมี line_no (number)' USING ERRCODE='23514'; END IF;
            v_ln := (it->>'line_no')::smallint;
            IF v_ln < 1 THEN RAISE EXCEPTION 'line_no ต้อง > 0 (got %)', v_ln USING ERRCODE='23514'; END IF;
            IF v_ln = ANY(v_seen) THEN RAISE EXCEPTION 'duplicate line_no % ใน patch', v_ln USING ERRCODE='23514'; END IF;
            v_seen := v_seen || v_ln;
            f := COALESCE(it->'fields', '{}'::jsonb); PERFORM _reject_unknown_keys(f, c_itemf, 'item.fields');
            IF f = '{}'::jsonb THEN RAISE EXCEPTION 'item.fields ว่าง (line_no %, no-op)', v_ln USING ERRCODE='23514'; END IF;
            SELECT id INTO v_item_id FROM rfq_item WHERE rfq_id=p_rfq_id AND line_no=v_ln FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'item line_no % ไม่พบ (update-only ; insert item = slice ถัดไป)', v_ln USING ERRCODE='23503';
            END IF;
            SELECT to_jsonb(x.*) INTO v_before FROM rfq_item x WHERE x.id = v_item_id;
            UPDATE rfq_item SET
                job_name           = CASE WHEN f ? 'job_name' THEN f->>'job_name' ELSE job_name END,
                product_type_ref   = CASE WHEN f ? 'product_type_ref' THEN f->>'product_type_ref' ELSE product_type_ref END,
                product_type_code_snapshot = CASE WHEN f ? 'product_type_code_snapshot' THEN f->>'product_type_code_snapshot' ELSE product_type_code_snapshot END,
                product_type_name_snapshot = CASE WHEN f ? 'product_type_name_snapshot' THEN f->>'product_type_name_snapshot' ELSE product_type_name_snapshot END,
                product_type_raw   = CASE WHEN f ? 'product_type_raw' THEN f->>'product_type_raw' ELSE product_type_raw END,
                description        = CASE WHEN f ? 'description' THEN f->>'description' ELSE description END,
                intended_use      = CASE WHEN f ? 'intended_use' THEN f->>'intended_use' ELSE intended_use END,
                finished_width_mm  = CASE WHEN f ? 'finished_width_mm' THEN (f->>'finished_width_mm')::numeric ELSE finished_width_mm END,
                finished_length_mm = CASE WHEN f ? 'finished_length_mm' THEN (f->>'finished_length_mm')::numeric ELSE finished_length_mm END,
                finished_depth_mm  = CASE WHEN f ? 'finished_depth_mm' THEN (f->>'finished_depth_mm')::numeric ELSE finished_depth_mm END,
                is_reprint         = CASE WHEN f ? 'is_reprint' THEN (f->>'is_reprint')::boolean ELSE is_reprint END,
                previous_job_ref   = CASE WHEN f ? 'previous_job_ref' THEN f->>'previous_job_ref' ELSE previous_job_ref END,
                use_previous_plate = CASE WHEN f ? 'use_previous_plate' THEN (f->>'use_previous_plate')::boolean ELSE use_previous_plate END,
                is_multiple_design = CASE WHEN f ? 'is_multiple_design' THEN (f->>'is_multiple_design')::boolean ELSE is_multiple_design END,
                finishing_state    = CASE WHEN f ? 'finishing_state' THEN f->>'finishing_state' ELSE finishing_state END,
                packing_state      = CASE WHEN f ? 'packing_state' THEN f->>'packing_state' ELSE packing_state END,
                artwork_state      = CASE WHEN f ? 'artwork_state' THEN f->>'artwork_state' ELSE artwork_state END,
                sample_state       = CASE WHEN f ? 'sample_state' THEN f->>'sample_state' ELSE sample_state END,
                sample_description = CASE WHEN f ? 'sample_description' THEN f->>'sample_description' ELSE sample_description END,
                notes              = CASE WHEN f ? 'notes' THEN f->>'notes' ELSE notes END,
                updated_at         = clock_timestamp()
            WHERE id = v_item_id;
            SELECT to_jsonb(x.*) INTO v_after FROM rfq_item x WHERE x.id = v_item_id;
            FOR v_key IN SELECT jsonb_object_keys(f) LOOP
                IF v_before->v_key IS DISTINCT FROM v_after->v_key THEN
                    PERFORM _reconcile_field_evidence(p_rfq_id, 'ITEM', v_item_id, v_key, v_after->v_key, p_actor);
                END IF;
            END LOOP;
            v_upd := v_upd + 1;
        END LOOP;
    END IF;

    -- ---- bump parent version + audit (V3 pattern) ----
    PERFORM _bump_rfq_version(p_rfq_id, p_actor);

    RETURN jsonb_build_object('rfq_id', p_rfq_id, 'row_version', r.row_version + 1,
        'edited_header', v_edited_header, 'items_updated', v_upd);
END;
$$;

ALTER FUNCTION upsert_rfq_draft(uuid, int, text, jsonb) OWNER TO rfq_owner;
REVOKE ALL ON FUNCTION upsert_rfq_draft(uuid, int, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION upsert_rfq_draft(uuid, int, text, jsonb) TO rfq_app;   -- editor surface = rfq_app (ไม่ให้ rfq_ingest)

COMMIT;
-- Rollback (manual): DROP FUNCTION upsert_rfq_draft(uuid,int,text,jsonb); DROP FUNCTION _reconcile_field_evidence(uuid,text,uuid,text,jsonb,text);
