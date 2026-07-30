-- ============================================================================
-- 006_enq_ingest.sql — ENQ→initial DRAFT write path (create_rfq_draft v1, hardened)
-- ต้นทาง: Codex go/no-go 612eb2f + ENQ_INGEST_006_REVIEW_28CFD79 (F2-F9 harden)
--
-- create_rfq_draft(payload jsonb, actor, service, request_id):
--   สร้าง RFQ DRAFT (header + spec tree) atomic ผ่าน single SECURITY DEFINER function
--   grant EXECUTE เฉพาะ rfq_ingest (create-only worker)
--
-- DB บังคับ:
--   #1 schema_version + reject unknown key ทุก object node (allowlist strict)
--   #2 lifecycle server-controlled: revision_no=1/is_current/status=DRAFT/row_version=1 (ไม่รับจาก payload)
--   #3 ห้าม payload ตั้ง status/ready/sign-off/policy/actor/identity (allowlist strict)
--   #4/#10 atomic → fail กลาง = rollback ครบ ไม่มี partial draft
--   #7 size/item/child-array limit ทุก array + ปฏิเสธ opaque JSON (ตัด grade_spec_snapshot/specification_extra ใน v1)
--   #9 idempotency: advisory-xact-lock claim (service,request_id) ก่อนสร้าง tree → concurrent = คืน id เดิม (ไม่ใช่ loser 23505);
--      เทียบ payload hash + actor → ต่าง = conflict
--   F2 internal ref ที่ระบุแล้ว resolve ไม่ได้ (process.component_no/delivery.option_no) = reject (ไม่เงียบเป็น NULL)
--   F5 corrugated ผิดชนิด = reject; F8 arg length/charset limit + revoke PUBLIC execute จาก legacy fn (ดู 005)
--
-- v1 fail-closed: ปฏิเสธ extraction_runs/field_evidence (unknown-key) → v1 = manual/synthetic DRAFT เท่านั้น
--   **ไม่มี AI provenance** — เส้นทาง AI ENQ จริงต้องทำ v1.1 (#6 extraction run + evidence atomic) ก่อน (Codex F1)
-- หมายเหตุ: 006 ไม่มี dependency ต่อ pgcrypto (ใช้ sha256() built-in pg_catalog)
-- Rollback: DROP FUNCTION create_rfq_draft, _reject_unknown_keys, _child_array; DROP TABLE rfq_ingest_request
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ---- idempotency + provenance log (#9) ----
CREATE TABLE IF NOT EXISTS rfq_ingest_request (
    service        text NOT NULL,
    request_id     text NOT NULL,
    payload_sha256 char(64) NOT NULL,
    rfq_id         uuid NOT NULL REFERENCES rfq(id),
    actor_ref      text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (service, request_id)
);
ALTER TABLE rfq_ingest_request OWNER TO rfq_owner;

-- ---- helper: reject unknown key (allowlist strict — #1/#3) ----
-- SECURITY INVOKER: ถูกเรียกจาก create_rfq_draft (definer) จึงรันใน owner context อยู่แล้ว; external ถูก revoke
CREATE OR REPLACE FUNCTION _reject_unknown_keys(obj jsonb, allowed text[], ctx text)
RETURNS void LANGUAGE plpgsql
SECURITY INVOKER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE k text;
BEGIN
    IF obj IS NULL THEN RETURN; END IF;
    IF jsonb_typeof(obj) <> 'object' THEN
        RAISE EXCEPTION '% ต้องเป็น JSON object (พบ %)', ctx, jsonb_typeof(obj) USING ERRCODE = '22023';
    END IF;
    FOR k IN SELECT jsonb_object_keys(obj) LOOP
        IF NOT (k = ANY(allowed)) THEN
            RAISE EXCEPTION 'unknown/forbidden key "%" in % (allowlist strict)', k, ctx USING ERRCODE = '22023';
        END IF;
    END LOOP;
END;
$$;
ALTER FUNCTION _reject_unknown_keys(jsonb, text[], text) OWNER TO rfq_owner;

-- ---- helper: validate + fetch child array (type + count ทุกชนิด — F3) ----
CREATE OR REPLACE FUNCTION _child_array(parent jsonb, key text, max_n int, ctx text)
RETURNS jsonb LANGUAGE plpgsql
SECURITY INVOKER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE arr jsonb;
BEGIN
    arr := parent->key;
    IF arr IS NULL OR jsonb_typeof(arr) = 'null' THEN RETURN '[]'::jsonb; END IF;
    IF jsonb_typeof(arr) <> 'array' THEN
        RAISE EXCEPTION '% ต้องเป็น array (พบ %)', ctx, jsonb_typeof(arr) USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(arr) > max_n THEN
        RAISE EXCEPTION '% เกิน limit (% > %)', ctx, jsonb_array_length(arr), max_n USING ERRCODE = '54000';
    END IF;
    RETURN arr;
END;
$$;
ALTER FUNCTION _child_array(jsonb, text, int, text) OWNER TO rfq_owner;

-- ---- create_rfq_draft: header + spec tree atomic ----
CREATE OR REPLACE FUNCTION create_rfq_draft(
    p_payload jsonb, p_actor text, p_service text, p_request_id text
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    c_max_bytes  constant int := 1000000;   -- 1MB payload (หลัง parse; raw-body/depth limit = หน้าที่ FastAPI)
    c_max_items  constant int := 100;
    c_max_child  constant int := 200;        -- ต่อ array ในแต่ละ item
    c_max_arg    constant int := 200;        -- length ของ actor/request_id
    c_top   constant text[] := ARRAY['schema_version','header','items'];
    c_hdr   constant text[] := ARRAY['enquiry_ref','source_channel','source_channel_other','quote_due_at',
        'priority_code','customer_ref','customer_code_snapshot','customer_name_snapshot','customer_name_raw',
        'is_new_customer','contact_name','contact_phone','contact_email','sales_owner_ref',
        'sales_owner_code_snapshot','sales_owner_name_snapshot','customer_notes'];
    c_item  constant text[] := ARRAY['line_no','job_name','product_type_ref','product_type_code_snapshot',
        'product_type_name_snapshot','product_type_raw','description','intended_use','finished_width_mm',
        'finished_length_mm','finished_depth_mm','is_reprint','previous_job_ref','use_previous_plate',
        'is_multiple_design','finishing_state','packing_state','artwork_state','sample_state',
        'sample_description','notes','quantity_options','design_variants','components','processes',
        'packings','deliveries'];
    c_qty   constant text[] := ARRAY['option_no','quantity','unit_ref','unit_code_snapshot','unit_name_snapshot','unit_raw','is_primary','notes'];
    c_var   constant text[] := ARRAY['variant_no','design_code','quantity','unit_ref','unit_code_snapshot','notes'];
    -- corrugated: ตัด grade_spec_snapshot (opaque JSON) ออกจาก v1 (Codex F5B/F7)
    c_comp  constant text[] := ARRAY['component_no','component_name','component_type_ref','component_type_code_snapshot',
        'component_type_name_snapshot','component_type_raw','paper_ref','paper_code_snapshot','paper_name_snapshot',
        'paper_gsm_snapshot','paper_source_code','print_sides_code','color_outside_count','color_inside_count',
        'ink_type_ref','ink_type_code_snapshot','box_template_ref','box_template_code_snapshot','box_template_name_snapshot',
        'box_width_mm','box_length_mm','box_depth_mm','flap_mm','glue_mm','tuck_mm','notes','corrugated'];
    c_corr  constant text[] := ARRAY['corrugated_board_ref','corrugated_code_snapshot','corrugated_name_snapshot',
        'layer_count_snapshot','flute_code_snapshot','notes'];
    -- process: ตัด specification_extra (opaque JSON) ออกจาก v1
    c_proc  constant text[] := ARRAY['sequence_no','component_no','process_ref','process_code_snapshot','process_name_snapshot',
        'process_name_raw','option_ref','option_code_snapshot','option_name_snapshot','option_name_raw','side_code',
        'width_mm','height_mm','depth_mm','color_ref','color_code_snapshot','color_name_snapshot','notes'];
    c_pack  constant text[] := ARRAY['sequence_no','packing_ref','packing_code_snapshot','packing_name_snapshot',
        'packing_name_raw','quantity_per_pack','unit_ref','unit_code_snapshot','specification'];
    c_dlv   constant text[] := ARRAY['delivery_no','option_no','destination_ref','destination_code_snapshot',
        'destination_name_snapshot','destination_raw','requested_date','quantity','unit_ref','unit_code_snapshot','is_split_delivery','notes'];
    v_hash char(64);
    v_existing_id uuid; v_existing_hash char(64); v_existing_actor text;
    v_hdr jsonb; v_items jsonb; v_new uuid;
    it jsonb; ch jsonb; comp jsonb; corr jsonb;
    v_item uuid; v_comp uuid; v_comp_ref uuid; v_qty_ref uuid;
BEGIN
    -- ---- trusted args: normalize + length/charset limit (F8) ----
    IF p_actor IS NULL OR p_service IS NULL OR p_request_id IS NULL THEN
        RAISE EXCEPTION 'actor/service/request_id ต้องมาจาก trusted server context (ห้าม NULL)' USING ERRCODE = '22023';
    END IF;
    p_actor      := btrim(p_actor,      E' \t\n\r\f\v');
    p_service    := btrim(p_service,    E' \t\n\r\f\v');
    p_request_id := btrim(p_request_id, E' \t\n\r\f\v');
    IF p_actor = '' OR p_service = '' OR p_request_id = '' THEN
        RAISE EXCEPTION 'actor/service/request_id ห้ามว่าง (หลัง trim)' USING ERRCODE = '22023';
    END IF;
    IF length(p_actor) > c_max_arg OR length(p_service) > 100 OR length(p_request_id) > c_max_arg THEN
        RAISE EXCEPTION 'actor/service/request_id ยาวเกิน limit' USING ERRCODE = '22023';
    END IF;
    IF p_actor ~ '[[:cntrl:]]' OR p_service ~ '[[:cntrl:]]' OR p_request_id ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'actor/service/request_id มี control character' USING ERRCODE = '22023';
    END IF;

    -- ---- validate envelope (#1) ----
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'payload ต้องเป็น JSON object' USING ERRCODE = '22023';
    END IF;
    IF octet_length(p_payload::text) > c_max_bytes THEN
        RAISE EXCEPTION 'payload เกินขนาด (% bytes > %)', octet_length(p_payload::text), c_max_bytes USING ERRCODE = '54000';
    END IF;
    IF p_payload->>'schema_version' <> 'draft-v1' THEN
        RAISE EXCEPTION 'schema_version ต้องเป็น draft-v1 (พบ %)', p_payload->>'schema_version' USING ERRCODE = '22023';
    END IF;
    PERFORM _reject_unknown_keys(p_payload, c_top, 'payload');

    -- ---- idempotency (#9): claim key ด้วย advisory-xact lock ก่อนสร้าง tree ----
    -- concurrent same key → serialize; ตัวที่สองหลัง commit จะเห็น record แล้วคืน id เดิม (ไม่ใช่ loser 23505)
    v_hash := encode(sha256(p_payload::text::bytea), 'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(p_service || ':' || p_request_id, 0));
    SELECT rfq_id, payload_sha256, actor_ref INTO v_existing_id, v_existing_hash, v_existing_actor
        FROM rfq_ingest_request WHERE service = p_service AND request_id = p_request_id;
    IF FOUND THEN
        IF v_existing_hash = v_hash AND v_existing_actor = p_actor THEN
            RETURN v_existing_id;                       -- replay เดิม (payload+actor ตรง) → id เดิม
        ELSE
            RAISE EXCEPTION 'request_id "%" ถูกใช้ซ้ำด้วย payload/actor ต่าง (conflict)', p_request_id USING ERRCODE = 'RFI01';
        END IF;
    END IF;

    v_hdr := COALESCE(p_payload->'header', '{}'::jsonb);
    PERFORM _reject_unknown_keys(v_hdr, c_hdr, 'header');
    v_items := _child_array(p_payload, 'items', c_max_items, 'items');
    IF jsonb_array_length(v_items) < 1 THEN
        RAISE EXCEPTION 'ต้องมี item อย่างน้อย 1' USING ERRCODE = '22023';
    END IF;

    -- ---- header insert (#2: lifecycle hard-coded) ----
    INSERT INTO rfq (
        rfq_number_source, revision_no, is_current, status_code, row_version,
        enquiry_ref, source_channel, source_channel_other, quote_due_at, priority_code,
        customer_ref, customer_code_snapshot, customer_name_snapshot, customer_name_raw, is_new_customer,
        contact_name, contact_phone, contact_email, sales_owner_ref, sales_owner_code_snapshot,
        sales_owner_name_snapshot, customer_notes, created_by_ref, updated_by_ref)
    VALUES (
        'RFQ_ESTIMATE_API', 1, true, 'DRAFT', 1,
        v_hdr->>'enquiry_ref', v_hdr->>'source_channel', v_hdr->>'source_channel_other',
        (v_hdr->>'quote_due_at')::timestamptz, COALESCE(v_hdr->>'priority_code','NORMAL'),
        v_hdr->>'customer_ref', v_hdr->>'customer_code_snapshot', v_hdr->>'customer_name_snapshot',
        v_hdr->>'customer_name_raw', COALESCE((v_hdr->>'is_new_customer')::boolean, false),
        v_hdr->>'contact_name', v_hdr->>'contact_phone', v_hdr->>'contact_email', v_hdr->>'sales_owner_ref',
        v_hdr->>'sales_owner_code_snapshot', v_hdr->>'sales_owner_name_snapshot', v_hdr->>'customer_notes',
        p_actor, p_actor)
    RETURNING id INTO v_new;

    -- ---- items + spec tree ----
    FOR it IN SELECT * FROM jsonb_array_elements(v_items) LOOP
        PERFORM _reject_unknown_keys(it, c_item, 'item');
        INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, product_type_code_snapshot,
            product_type_name_snapshot, product_type_raw, description, intended_use, finished_width_mm,
            finished_length_mm, finished_depth_mm, is_reprint, previous_job_ref, use_previous_plate,
            is_multiple_design, finishing_state, packing_state, artwork_state, sample_state,
            sample_description, notes)
        VALUES (v_new, (it->>'line_no')::smallint, it->>'job_name', it->>'product_type_ref',
            it->>'product_type_code_snapshot', it->>'product_type_name_snapshot', it->>'product_type_raw',
            it->>'description', it->>'intended_use', (it->>'finished_width_mm')::numeric,
            (it->>'finished_length_mm')::numeric, (it->>'finished_depth_mm')::numeric,
            COALESCE((it->>'is_reprint')::boolean, false), it->>'previous_job_ref',
            COALESCE((it->>'use_previous_plate')::boolean, false), COALESCE((it->>'is_multiple_design')::boolean, false),
            COALESCE(it->>'finishing_state','UNKNOWN'), COALESCE(it->>'packing_state','UNKNOWN'),
            COALESCE(it->>'artwork_state','UNKNOWN'), COALESCE(it->>'sample_state','UNKNOWN'),
            it->>'sample_description', it->>'notes')
        RETURNING id INTO v_item;

        -- quantity_options
        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'quantity_options',c_max_child,'quantity_options')) LOOP
            PERFORM _reject_unknown_keys(ch, c_qty, 'quantity_option');
            INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, unit_code_snapshot,
                unit_name_snapshot, unit_raw, is_primary, notes)
            VALUES (v_item, (ch->>'option_no')::smallint, (ch->>'quantity')::numeric, ch->>'unit_ref',
                ch->>'unit_code_snapshot', ch->>'unit_name_snapshot', ch->>'unit_raw',
                COALESCE((ch->>'is_primary')::boolean, false), ch->>'notes');
        END LOOP;

        -- design_variants
        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'design_variants',c_max_child,'design_variants')) LOOP
            PERFORM _reject_unknown_keys(ch, c_var, 'design_variant');
            INSERT INTO rfq_design_variant (rfq_item_id, variant_no, design_code, quantity, unit_ref, unit_code_snapshot, notes)
            VALUES (v_item, (ch->>'variant_no')::smallint, ch->>'design_code', (ch->>'quantity')::numeric,
                ch->>'unit_ref', ch->>'unit_code_snapshot', ch->>'notes');
        END LOOP;

        -- components (+ nested corrugated)
        FOR comp IN SELECT * FROM jsonb_array_elements(_child_array(it,'components',c_max_child,'components')) LOOP
            PERFORM _reject_unknown_keys(comp, c_comp, 'component');
            INSERT INTO rfq_component (rfq_item_id, component_no, component_name, component_type_ref,
                component_type_code_snapshot, component_type_name_snapshot, component_type_raw, paper_ref,
                paper_code_snapshot, paper_name_snapshot, paper_gsm_snapshot, paper_source_code, print_sides_code,
                color_outside_count, color_inside_count, ink_type_ref, ink_type_code_snapshot, box_template_ref,
                box_template_code_snapshot, box_template_name_snapshot, box_width_mm, box_length_mm, box_depth_mm,
                flap_mm, glue_mm, tuck_mm, notes)
            VALUES (v_item, (comp->>'component_no')::smallint, comp->>'component_name', comp->>'component_type_ref',
                comp->>'component_type_code_snapshot', comp->>'component_type_name_snapshot', comp->>'component_type_raw',
                comp->>'paper_ref', comp->>'paper_code_snapshot', comp->>'paper_name_snapshot',
                (comp->>'paper_gsm_snapshot')::numeric, comp->>'paper_source_code', comp->>'print_sides_code',
                (comp->>'color_outside_count')::smallint, (comp->>'color_inside_count')::smallint, comp->>'ink_type_ref',
                comp->>'ink_type_code_snapshot', comp->>'box_template_ref', comp->>'box_template_code_snapshot',
                comp->>'box_template_name_snapshot', (comp->>'box_width_mm')::numeric, (comp->>'box_length_mm')::numeric,
                (comp->>'box_depth_mm')::numeric, (comp->>'flap_mm')::numeric, (comp->>'glue_mm')::numeric,
                (comp->>'tuck_mm')::numeric, comp->>'notes')
            RETURNING id INTO v_comp;
            corr := comp->'corrugated';
            IF corr IS NOT NULL AND jsonb_typeof(corr) <> 'null' THEN   -- F5: ผิดชนิด = reject ไม่ข้ามเงียบ
                IF jsonb_typeof(corr) <> 'object' THEN
                    RAISE EXCEPTION 'corrugated ต้องเป็น object (พบ %)', jsonb_typeof(corr) USING ERRCODE = '22023';
                END IF;
                PERFORM _reject_unknown_keys(corr, c_corr, 'corrugated');
                INSERT INTO rfq_component_corrugated (rfq_component_id, corrugated_board_ref, corrugated_code_snapshot,
                    corrugated_name_snapshot, layer_count_snapshot, flute_code_snapshot, notes)
                VALUES (v_comp, corr->>'corrugated_board_ref', corr->>'corrugated_code_snapshot',
                    corr->>'corrugated_name_snapshot', (corr->>'layer_count_snapshot')::smallint, corr->>'flute_code_snapshot', corr->>'notes');
            END IF;
        END LOOP;

        -- processes (component_no → resolve; ระบุแล้วหาไม่เจอ = reject — F2)
        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'processes',c_max_child,'processes')) LOOP
            PERFORM _reject_unknown_keys(ch, c_proc, 'process');
            v_comp_ref := NULL;
            IF ch ? 'component_no' AND jsonb_typeof(ch->'component_no') <> 'null' AND ch->>'component_no' IS NOT NULL THEN
                SELECT id INTO v_comp_ref FROM rfq_component WHERE rfq_item_id = v_item AND component_no = (ch->>'component_no')::smallint;
                IF v_comp_ref IS NULL THEN
                    RAISE EXCEPTION 'process.component_no % ไม่พบ component ใน item เดียวกัน', ch->>'component_no' USING ERRCODE = '23503';
                END IF;
            END IF;
            INSERT INTO rfq_process_requirement (rfq_item_id, rfq_component_id, sequence_no, process_ref,
                process_code_snapshot, process_name_snapshot, process_name_raw, option_ref, option_code_snapshot,
                option_name_snapshot, option_name_raw, side_code, width_mm, height_mm, depth_mm, color_ref,
                color_code_snapshot, color_name_snapshot, notes)
            VALUES (v_item, v_comp_ref, (ch->>'sequence_no')::smallint, ch->>'process_ref', ch->>'process_code_snapshot',
                ch->>'process_name_snapshot', ch->>'process_name_raw', ch->>'option_ref', ch->>'option_code_snapshot',
                ch->>'option_name_snapshot', ch->>'option_name_raw', ch->>'side_code', (ch->>'width_mm')::numeric,
                (ch->>'height_mm')::numeric, (ch->>'depth_mm')::numeric, ch->>'color_ref', ch->>'color_code_snapshot',
                ch->>'color_name_snapshot', ch->>'notes');
        END LOOP;

        -- packings
        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'packings',c_max_child,'packings')) LOOP
            PERFORM _reject_unknown_keys(ch, c_pack, 'packing');
            INSERT INTO rfq_packing_requirement (rfq_item_id, sequence_no, packing_ref, packing_code_snapshot,
                packing_name_snapshot, packing_name_raw, quantity_per_pack, unit_ref, unit_code_snapshot, specification)
            VALUES (v_item, (ch->>'sequence_no')::smallint, ch->>'packing_ref', ch->>'packing_code_snapshot',
                ch->>'packing_name_snapshot', ch->>'packing_name_raw', (ch->>'quantity_per_pack')::numeric,
                ch->>'unit_ref', ch->>'unit_code_snapshot', ch->>'specification');
        END LOOP;

        -- deliveries (option_no → resolve; ระบุแล้วหาไม่เจอ = reject — F2)
        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'deliveries',c_max_child,'deliveries')) LOOP
            PERFORM _reject_unknown_keys(ch, c_dlv, 'delivery');
            v_qty_ref := NULL;
            IF ch ? 'option_no' AND jsonb_typeof(ch->'option_no') <> 'null' AND ch->>'option_no' IS NOT NULL THEN
                SELECT id INTO v_qty_ref FROM rfq_quantity_option WHERE rfq_item_id = v_item AND option_no = (ch->>'option_no')::smallint;
                IF v_qty_ref IS NULL THEN
                    RAISE EXCEPTION 'delivery.option_no % ไม่พบ quantity_option ใน item เดียวกัน', ch->>'option_no' USING ERRCODE = '23503';
                END IF;
            END IF;
            INSERT INTO rfq_delivery (rfq_item_id, quantity_option_id, delivery_no, destination_ref,
                destination_code_snapshot, destination_name_snapshot, destination_raw, requested_date, quantity,
                unit_ref, unit_code_snapshot, is_split_delivery, notes)
            VALUES (v_item, v_qty_ref, (ch->>'delivery_no')::smallint, ch->>'destination_ref', ch->>'destination_code_snapshot',
                ch->>'destination_name_snapshot', ch->>'destination_raw', (ch->>'requested_date')::date,
                (ch->>'quantity')::numeric, ch->>'unit_ref', ch->>'unit_code_snapshot',
                COALESCE((ch->>'is_split_delivery')::boolean, false), ch->>'notes');
        END LOOP;
    END LOOP;

    -- ---- initial DRAFT history + idempotency record (atomic) ----
    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref, reason)
    VALUES (v_new, NULL, 'DRAFT', p_actor, 'created via ENQ (' || p_service || '/' || p_request_id || ')');
    INSERT INTO rfq_ingest_request (service, request_id, payload_sha256, rfq_id, actor_ref)
    VALUES (p_service, p_request_id, v_hash, v_new, p_actor);

    RETURN v_new;
END;
$$;
ALTER FUNCTION create_rfq_draft(jsonb, text, text, text) OWNER TO rfq_owner;

-- ---- grant: EXECUTE เฉพาะ rfq_ingest (Codex go/no-go #2) ----
REVOKE ALL ON FUNCTION _reject_unknown_keys(jsonb, text[], text) FROM PUBLIC;
REVOKE ALL ON FUNCTION _child_array(jsonb, text, int, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION create_rfq_draft(jsonb, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_rfq_draft(jsonb, text, text, text) TO rfq_ingest;
-- rfq_app (reviewer/FastAPI) ไม่ได้ grant create_rfq_draft — ถ้าต้องการ human draft-entry ค่อย grant แยก

COMMIT;
