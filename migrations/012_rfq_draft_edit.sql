-- ============================================================================
-- 012_rfq_draft_edit.sql — draft-edit/upsert endpoint (consumer ตัวแรกของ V3 pattern)
-- ============================================================================
-- เปิด write workflow ของ RFQ ให้ครบวงจร: อ่าน Draft → แก้ข้อมูล → ตรวจ expected_row_version
--   → lock/freeze → บันทึก → bump version (optimistic concurrency)
--
-- upsert_rfq_draft(rfq, expected_row_version, actor, patch):
--   - lock parent (FOR UPDATE) + สถานะต้อง DRAFT + is_current (first cut)
--   - F4a optimistic: expected_row_version ต้องตรง + ไม่ NULL → mismatch = 40001
--   - patch = { header:{fields:{...}}, items:[{line_no, fields:{...}}] } (shape เดียวกับ extract payload, ไม่มี evidence)
--   - header.fields → UPDATE rfq (allowlist c_hdr) ; items → **upsert by line_no** (มี=UPDATE / ไม่มี=INSERT ; allowlist c_itemf)
--   - bump row_version หนึ่งครั้ง ผ่าน _bump_rfq_version (V3 pattern)
--
-- SCOPE first cut (flag ให้ Codex): update header + upsert item fields เท่านั้น
--   ยังไม่ทำ: delete item, quantity/component/design/process/packing/delivery tree, evidence reconciliation
--   (human edit ทับ field ที่มี AI evidence → evidence เดิม stale — ต้องตัดสิน semantics), READY_FOR_REVIEW edit,
--   DRAFT→READY_FOR_REVIEW submit transition (ยังไม่มีในระบบ)
--
-- ขอบเขต: local + synthetic prototype ยังไม่ deploy ; ไม่แตะ app/main.py, .env, Qdrant, ข้อมูลจริง
-- pattern: SECURITY DEFINER owner=rfq_owner, pinned search_path, REVOKE PUBLIC, GRANT rfq_app ; ทั้งไฟล์ = 1 transaction
-- ============================================================================
BEGIN;
SET search_path TO rfq;

CREATE OR REPLACE FUNCTION upsert_rfq_draft(
    p_rfq_id uuid, p_expected_row_version int, p_actor text, p_patch jsonb
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    r rfq%ROWTYPE; hdr jsonb; f jsonb; it jsonb; v_ln smallint; v_item_id uuid;
    v_upd int := 0; v_ins int := 0; v_edited_header boolean := false;
    c_top   constant text[] := ARRAY['header','items'];
    c_hdr   constant text[] := ARRAY['customer_ref','customer_code_snapshot','customer_name_snapshot','customer_name_raw',
        'is_new_customer','contact_name','contact_phone','contact_email','sales_owner_ref','sales_owner_code_snapshot',
        'sales_owner_name_snapshot','customer_notes','quote_due_at','priority_code'];
    c_itemf constant text[] := ARRAY['job_name','product_type_ref','product_type_code_snapshot','product_type_name_snapshot',
        'product_type_raw','description','intended_use','finished_width_mm','finished_length_mm','finished_depth_mm',
        'is_reprint','previous_job_ref','use_previous_plate','is_multiple_design','finishing_state','packing_state',
        'artwork_state','sample_state','sample_description','notes'];
BEGIN
    -- ---- input validation (cheap, ก่อนถือ lock) ----
    IF p_actor IS NULL OR p_actor !~ '[^[:space:]]' OR length(p_actor) > 200 OR p_actor ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'upsert_rfq_draft actor invalid (blank/whitespace/too long/control char)' USING ERRCODE = '23514';
    END IF;
    IF p_patch IS NULL OR jsonb_typeof(p_patch) <> 'object' THEN
        RAISE EXCEPTION 'patch ต้องเป็น object' USING ERRCODE = '23514';
    END IF;
    PERFORM _reject_unknown_keys(p_patch, c_top, 'patch');
    IF NOT (p_patch ? 'header' OR p_patch ? 'items') THEN
        RAISE EXCEPTION 'patch ต้องมี header หรือ items อย่างน้อยหนึ่ง' USING ERRCODE = '23514';
    END IF;

    -- ---- lock parent + gate (status DRAFT + is_current + optimistic version) ----
    SELECT * INTO r FROM rfq WHERE id = p_rfq_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'RFQ % not found', p_rfq_id USING ERRCODE = '23503'; END IF;
    IF r.status_code <> 'DRAFT' THEN
        RAISE EXCEPTION 'upsert_rfq_draft ต้องเป็น DRAFT (got %)', r.status_code USING ERRCODE = '23514';   -- freeze non-DRAFT
    END IF;
    IF r.is_current IS NOT TRUE THEN
        RAISE EXCEPTION 'only the current revision can be edited (F4b)' USING ERRCODE = '23514';
    END IF;
    IF p_expected_row_version IS NULL OR r.row_version IS DISTINCT FROM p_expected_row_version THEN
        RAISE EXCEPTION 'row_version mismatch (expected %, got %)', p_expected_row_version, r.row_version USING ERRCODE = '40001';
    END IF;

    -- ---- header.fields → UPDATE rfq (allowlist) ----
    IF p_patch ? 'header' THEN
        hdr := p_patch->'header'; PERFORM _reject_unknown_keys(hdr, ARRAY['fields'], 'header');
        f := COALESCE(hdr->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_hdr, 'header.fields');
        UPDATE rfq SET
            customer_ref=COALESCE(f->>'customer_ref',customer_ref), customer_code_snapshot=COALESCE(f->>'customer_code_snapshot',customer_code_snapshot),
            customer_name_snapshot=COALESCE(f->>'customer_name_snapshot',customer_name_snapshot), customer_name_raw=COALESCE(f->>'customer_name_raw',customer_name_raw),
            is_new_customer=COALESCE((f->>'is_new_customer')::boolean,is_new_customer), contact_name=COALESCE(f->>'contact_name',contact_name),
            contact_phone=COALESCE(f->>'contact_phone',contact_phone), contact_email=COALESCE(f->>'contact_email',contact_email),
            sales_owner_ref=COALESCE(f->>'sales_owner_ref',sales_owner_ref), sales_owner_code_snapshot=COALESCE(f->>'sales_owner_code_snapshot',sales_owner_code_snapshot),
            sales_owner_name_snapshot=COALESCE(f->>'sales_owner_name_snapshot',sales_owner_name_snapshot), customer_notes=COALESCE(f->>'customer_notes',customer_notes),
            quote_due_at=COALESCE((f->>'quote_due_at')::timestamptz,quote_due_at), priority_code=COALESCE(f->>'priority_code',priority_code)
        WHERE id=p_rfq_id;
        v_edited_header := true;
    END IF;

    -- ---- items[] → upsert by (rfq_id, line_no) ----
    IF p_patch ? 'items' THEN
        IF jsonb_typeof(p_patch->'items') <> 'array' THEN RAISE EXCEPTION 'items ต้องเป็น array' USING ERRCODE='23514'; END IF;
        FOR it IN SELECT * FROM jsonb_array_elements(p_patch->'items') LOOP
            PERFORM _reject_unknown_keys(it, ARRAY['line_no','fields'], 'item');
            IF NOT (it ? 'line_no') THEN RAISE EXCEPTION 'item ต้องมี line_no' USING ERRCODE='23514'; END IF;
            v_ln := (it->>'line_no')::smallint;
            f := COALESCE(it->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_itemf, 'item.fields');
            SELECT id INTO v_item_id FROM rfq_item WHERE rfq_id=p_rfq_id AND line_no=v_ln FOR UPDATE;
            IF FOUND THEN
                UPDATE rfq_item SET
                    job_name=COALESCE(f->>'job_name',job_name), product_type_ref=COALESCE(f->>'product_type_ref',product_type_ref),
                    product_type_code_snapshot=COALESCE(f->>'product_type_code_snapshot',product_type_code_snapshot),
                    product_type_name_snapshot=COALESCE(f->>'product_type_name_snapshot',product_type_name_snapshot),
                    product_type_raw=COALESCE(f->>'product_type_raw',product_type_raw), description=COALESCE(f->>'description',description),
                    intended_use=COALESCE(f->>'intended_use',intended_use),
                    finished_width_mm=COALESCE((f->>'finished_width_mm')::numeric,finished_width_mm),
                    finished_length_mm=COALESCE((f->>'finished_length_mm')::numeric,finished_length_mm),
                    finished_depth_mm=COALESCE((f->>'finished_depth_mm')::numeric,finished_depth_mm),
                    is_reprint=COALESCE((f->>'is_reprint')::boolean,is_reprint), previous_job_ref=COALESCE(f->>'previous_job_ref',previous_job_ref),
                    use_previous_plate=COALESCE((f->>'use_previous_plate')::boolean,use_previous_plate),
                    is_multiple_design=COALESCE((f->>'is_multiple_design')::boolean,is_multiple_design),
                    finishing_state=COALESCE(f->>'finishing_state',finishing_state), packing_state=COALESCE(f->>'packing_state',packing_state),
                    artwork_state=COALESCE(f->>'artwork_state',artwork_state), sample_state=COALESCE(f->>'sample_state',sample_state),
                    sample_description=COALESCE(f->>'sample_description',sample_description), notes=COALESCE(f->>'notes',notes),
                    updated_at=clock_timestamp()
                WHERE id=v_item_id;
                v_upd := v_upd + 1;
            ELSE
                INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, product_type_code_snapshot,
                    product_type_name_snapshot, product_type_raw, description, intended_use, finished_width_mm, finished_length_mm,
                    finished_depth_mm, is_reprint, previous_job_ref, use_previous_plate, is_multiple_design,
                    finishing_state, packing_state, artwork_state, sample_state, sample_description, notes)
                VALUES (p_rfq_id, v_ln, f->>'job_name', f->>'product_type_ref', f->>'product_type_code_snapshot',
                    f->>'product_type_name_snapshot', f->>'product_type_raw', f->>'description', f->>'intended_use',
                    (f->>'finished_width_mm')::numeric, (f->>'finished_length_mm')::numeric, (f->>'finished_depth_mm')::numeric,
                    COALESCE((f->>'is_reprint')::boolean,false), f->>'previous_job_ref', COALESCE((f->>'use_previous_plate')::boolean,false),
                    COALESCE((f->>'is_multiple_design')::boolean,false), COALESCE(f->>'finishing_state','UNKNOWN'),
                    COALESCE(f->>'packing_state','UNKNOWN'), COALESCE(f->>'artwork_state','UNKNOWN'), COALESCE(f->>'sample_state','UNKNOWN'),
                    f->>'sample_description', f->>'notes');
                v_ins := v_ins + 1;
            END IF;
        END LOOP;
    END IF;

    -- ---- bump parent version + audit (V3 pattern) ----
    PERFORM _bump_rfq_version(p_rfq_id, p_actor);

    RETURN jsonb_build_object('rfq_id', p_rfq_id, 'row_version', r.row_version + 1,
        'edited_header', v_edited_header, 'items_updated', v_upd, 'items_inserted', v_ins);
END;
$$;

ALTER FUNCTION upsert_rfq_draft(uuid, int, text, jsonb) OWNER TO rfq_owner;
REVOKE ALL ON FUNCTION upsert_rfq_draft(uuid, int, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION upsert_rfq_draft(uuid, int, text, jsonb) TO rfq_app;   -- editor surface = rfq_app (ไม่ให้ rfq_ingest)

COMMIT;
-- Rollback (manual): DROP FUNCTION upsert_rfq_draft(uuid,int,text,jsonb);
