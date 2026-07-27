-- ============================================================================
-- 006_enq_ingest.sql — ENQ→initial DRAFT write path (create_rfq_draft v1)
-- ต้นทาง: Codex go/no-go 612eb2f (contract 10 ข้อ) + RFQ_SCHEMA_V0_2.md
--
-- create_rfq_draft(payload jsonb, actor, service, request_id):
--   สร้าง RFQ DRAFT (header + spec tree) แบบ atomic ผ่าน single SECURITY DEFINER function
--   grant EXECUTE ให้ rfq_ingest ตัวเดียว (create-only worker) — ปลอม sign-off/Ready ไม่ได้ (V1)
--
-- DB บังคับตาม contract:
--   #1 schema_version + reject unknown keys ทุกชั้น (top/header/item/child)
--   #2 lifecycle server-controlled: revision_no=1, is_current=true, status='DRAFT', row_version=1 (ไม่รับจาก payload)
--   #3 ห้าม payload ตั้ง status/ready/sign-off/policy/actor/identity → allowlist strict, ไม่มี generic mapper
--   #4/#10 header+spec tree+history atomic (1 transaction) → fail กลาง = rollback ครบ (ไม่มี partial draft)
--   #5 actor/service/request มาจาก trusted argument (ไม่ปนใน JSON) — human actor authenticity = FastAPI contract
--   #7 size/depth/item-count limit ก่อน expand JSON
--   #9 idempotency: (service, request_id) unique + payload hash (sequential replay → คืน id เดิม)
--
-- v1 fail-closed: **ปฏิเสธ extraction_runs/field_evidence ใน payload** (unknown-key) →
--   สร้าง AI evidence โดยไม่มี provenance ไม่ได้; #6 (validate run SUCCEEDED/ไม่ BLOCKED/policy) → v1.1 พร้อม extraction pipeline
-- Rollback: DROP FUNCTION create_rfq_draft, _reject_unknown_keys; DROP TABLE rfq_ingest_request
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
CREATE OR REPLACE FUNCTION _reject_unknown_keys(obj jsonb, allowed text[], ctx text)
RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
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

-- ---- create_rfq_draft: header + spec tree atomic ----
CREATE OR REPLACE FUNCTION create_rfq_draft(
    p_payload jsonb, p_actor text, p_service text, p_request_id text
) RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    -- limits (#7)
    c_max_bytes  constant int := 1000000;   -- 1MB payload
    c_max_items  constant int := 100;
    c_max_child  constant int := 200;        -- ต่อ array ในแต่ละ item
    -- allowlists (#1/#3) — ไม่มี id/rfq_no/revision/is_current/status/row_version/ready_*/policy/audit
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
    c_comp  constant text[] := ARRAY['component_no','component_name','component_type_ref','component_type_code_snapshot',
        'component_type_name_snapshot','component_type_raw','paper_ref','paper_code_snapshot','paper_name_snapshot',
        'paper_gsm_snapshot','paper_source_code','print_sides_code','color_outside_count','color_inside_count',
        'ink_type_ref','ink_type_code_snapshot','box_template_ref','box_template_code_snapshot','box_template_name_snapshot',
        'box_width_mm','box_length_mm','box_depth_mm','flap_mm','glue_mm','tuck_mm','notes','corrugated'];
    c_corr  constant text[] := ARRAY['corrugated_board_ref','corrugated_code_snapshot','corrugated_name_snapshot',
        'layer_count_snapshot','flute_code_snapshot','grade_spec_snapshot','notes'];
    c_proc  constant text[] := ARRAY['sequence_no','component_no','process_ref','process_code_snapshot','process_name_snapshot',
        'process_name_raw','option_ref','option_code_snapshot','option_name_snapshot','option_name_raw','side_code',
        'width_mm','height_mm','depth_mm','color_ref','color_code_snapshot','color_name_snapshot','specification_extra','notes'];
    c_pack  constant text[] := ARRAY['sequence_no','packing_ref','packing_code_snapshot','packing_name_snapshot',
        'packing_name_raw','quantity_per_pack','unit_ref','unit_code_snapshot','specification'];
    c_dlv   constant text[] := ARRAY['delivery_no','option_no','destination_ref','destination_code_snapshot',
        'destination_name_snapshot','destination_raw','requested_date','quantity','unit_ref','unit_code_snapshot','is_split_delivery','notes'];
    v_hash char(64);
    v_existing_id uuid; v_existing_hash char(64);
    v_hdr jsonb; v_items jsonb; v_new uuid;
    it jsonb; ch jsonb; comp jsonb; corr jsonb;
    v_item uuid; v_comp uuid;
BEGIN
    -- ---- validate envelope (#1) ----
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'payload ต้องเป็น JSON object' USING ERRCODE = '22023';
    END IF;
    IF octet_length(p_payload::text) > c_max_bytes THEN
        RAISE EXCEPTION 'payload เกินขนาด (% bytes > %)', octet_length(p_payload::text), c_max_bytes USING ERRCODE = '54000';
    END IF;
    IF NULLIF(btrim(p_actor), '') IS NULL OR NULLIF(btrim(p_service), '') IS NULL
       OR NULLIF(btrim(p_request_id), '') IS NULL THEN
        RAISE EXCEPTION 'actor/service/request_id ต้องมาจาก trusted server context (ห้ามว่าง)' USING ERRCODE = '22023';
    END IF;
    IF p_payload->>'schema_version' <> 'draft-v1' THEN
        RAISE EXCEPTION 'schema_version ต้องเป็น draft-v1 (พบ %)', p_payload->>'schema_version' USING ERRCODE = '22023';
    END IF;
    PERFORM _reject_unknown_keys(p_payload, c_top, 'payload');

    -- ---- idempotency (#9) — sequential replay ----
    v_hash := encode(sha256(p_payload::text::bytea), 'hex');   -- sha256 = built-in pg_catalog (เลี่ยง pgcrypto/search_path)
    SELECT rfq_id, payload_sha256 INTO v_existing_id, v_existing_hash
        FROM rfq_ingest_request WHERE service = p_service AND request_id = p_request_id;
    IF FOUND THEN
        IF v_existing_hash = v_hash THEN
            RETURN v_existing_id;   -- replay เดิม → คืน id เดิม (idempotent)
        ELSE
            RAISE EXCEPTION 'request_id "%" ถูกใช้ซ้ำด้วย payload ต่าง (conflict)', p_request_id USING ERRCODE = '23505';
        END IF;
    END IF;

    v_hdr := COALESCE(p_payload->'header', '{}'::jsonb);
    v_items := COALESCE(p_payload->'items', '[]'::jsonb);
    PERFORM _reject_unknown_keys(v_hdr, c_hdr, 'header');
    IF jsonb_typeof(v_items) <> 'array' THEN
        RAISE EXCEPTION 'items ต้องเป็น array' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(v_items) < 1 THEN
        RAISE EXCEPTION 'ต้องมี item อย่างน้อย 1' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(v_items) > c_max_items THEN
        RAISE EXCEPTION 'items เกิน limit (% > %)', jsonb_array_length(v_items), c_max_items USING ERRCODE = '54000';
    END IF;

    -- ---- header insert (#2: lifecycle hard-coded, ไม่รับจาก payload) ----
    INSERT INTO rfq (
        rfq_number_source, revision_no, is_current, status_code, row_version,   -- server-controlled
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
        IF jsonb_array_length(COALESCE(it->'quantity_options','[]'::jsonb)) > c_max_child THEN
            RAISE EXCEPTION 'quantity_options เกิน limit' USING ERRCODE = '54000'; END IF;
        FOR ch IN SELECT * FROM jsonb_array_elements(COALESCE(it->'quantity_options','[]'::jsonb)) LOOP
            PERFORM _reject_unknown_keys(ch, c_qty, 'quantity_option');
            INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, unit_code_snapshot,
                unit_name_snapshot, unit_raw, is_primary, notes)
            VALUES (v_item, (ch->>'option_no')::smallint, (ch->>'quantity')::numeric, ch->>'unit_ref',
                ch->>'unit_code_snapshot', ch->>'unit_name_snapshot', ch->>'unit_raw',
                COALESCE((ch->>'is_primary')::boolean, false), ch->>'notes');
        END LOOP;

        -- design_variants
        FOR ch IN SELECT * FROM jsonb_array_elements(COALESCE(it->'design_variants','[]'::jsonb)) LOOP
            PERFORM _reject_unknown_keys(ch, c_var, 'design_variant');
            INSERT INTO rfq_design_variant (rfq_item_id, variant_no, design_code, quantity, unit_ref, unit_code_snapshot, notes)
            VALUES (v_item, (ch->>'variant_no')::smallint, ch->>'design_code', (ch->>'quantity')::numeric,
                ch->>'unit_ref', ch->>'unit_code_snapshot', ch->>'notes');
        END LOOP;

        -- components (+ nested corrugated)
        IF jsonb_array_length(COALESCE(it->'components','[]'::jsonb)) > c_max_child THEN
            RAISE EXCEPTION 'components เกิน limit' USING ERRCODE = '54000'; END IF;
        FOR comp IN SELECT * FROM jsonb_array_elements(COALESCE(it->'components','[]'::jsonb)) LOOP
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
            IF corr IS NOT NULL AND jsonb_typeof(corr) = 'object' THEN
                PERFORM _reject_unknown_keys(corr, c_corr, 'corrugated');
                INSERT INTO rfq_component_corrugated (rfq_component_id, corrugated_board_ref, corrugated_code_snapshot,
                    corrugated_name_snapshot, layer_count_snapshot, flute_code_snapshot, grade_spec_snapshot, notes)
                VALUES (v_comp, corr->>'corrugated_board_ref', corr->>'corrugated_code_snapshot',
                    corr->>'corrugated_name_snapshot', (corr->>'layer_count_snapshot')::smallint, corr->>'flute_code_snapshot',
                    COALESCE(corr->'grade_spec_snapshot', '{}'::jsonb), corr->>'notes');
            END IF;
        END LOOP;

        -- processes (component_no → resolve เป็น component id ใน item เดียวกัน)
        FOR ch IN SELECT * FROM jsonb_array_elements(COALESCE(it->'processes','[]'::jsonb)) LOOP
            PERFORM _reject_unknown_keys(ch, c_proc, 'process');
            INSERT INTO rfq_process_requirement (rfq_item_id, rfq_component_id, sequence_no, process_ref,
                process_code_snapshot, process_name_snapshot, process_name_raw, option_ref, option_code_snapshot,
                option_name_snapshot, option_name_raw, side_code, width_mm, height_mm, depth_mm, color_ref,
                color_code_snapshot, color_name_snapshot, specification_extra, notes)
            VALUES (v_item,
                CASE WHEN ch ? 'component_no' AND ch->>'component_no' IS NOT NULL
                     THEN (SELECT id FROM rfq_component WHERE rfq_item_id=v_item AND component_no=(ch->>'component_no')::smallint)
                     ELSE NULL END,
                (ch->>'sequence_no')::smallint, ch->>'process_ref', ch->>'process_code_snapshot',
                ch->>'process_name_snapshot', ch->>'process_name_raw', ch->>'option_ref', ch->>'option_code_snapshot',
                ch->>'option_name_snapshot', ch->>'option_name_raw', ch->>'side_code', (ch->>'width_mm')::numeric,
                (ch->>'height_mm')::numeric, (ch->>'depth_mm')::numeric, ch->>'color_ref', ch->>'color_code_snapshot',
                ch->>'color_name_snapshot', COALESCE(ch->'specification_extra','{}'::jsonb), ch->>'notes');
        END LOOP;

        -- packings
        FOR ch IN SELECT * FROM jsonb_array_elements(COALESCE(it->'packings','[]'::jsonb)) LOOP
            PERFORM _reject_unknown_keys(ch, c_pack, 'packing');
            INSERT INTO rfq_packing_requirement (rfq_item_id, sequence_no, packing_ref, packing_code_snapshot,
                packing_name_snapshot, packing_name_raw, quantity_per_pack, unit_ref, unit_code_snapshot, specification)
            VALUES (v_item, (ch->>'sequence_no')::smallint, ch->>'packing_ref', ch->>'packing_code_snapshot',
                ch->>'packing_name_snapshot', ch->>'packing_name_raw', (ch->>'quantity_per_pack')::numeric,
                ch->>'unit_ref', ch->>'unit_code_snapshot', ch->>'specification');
        END LOOP;

        -- deliveries (option_no → resolve เป็น quantity_option id ใน item เดียวกัน)
        FOR ch IN SELECT * FROM jsonb_array_elements(COALESCE(it->'deliveries','[]'::jsonb)) LOOP
            PERFORM _reject_unknown_keys(ch, c_dlv, 'delivery');
            INSERT INTO rfq_delivery (rfq_item_id, quantity_option_id, delivery_no, destination_ref,
                destination_code_snapshot, destination_name_snapshot, destination_raw, requested_date, quantity,
                unit_ref, unit_code_snapshot, is_split_delivery, notes)
            VALUES (v_item,
                CASE WHEN ch ? 'option_no' AND ch->>'option_no' IS NOT NULL
                     THEN (SELECT id FROM rfq_quantity_option WHERE rfq_item_id=v_item AND option_no=(ch->>'option_no')::smallint)
                     ELSE NULL END,
                (ch->>'delivery_no')::smallint, ch->>'destination_ref', ch->>'destination_code_snapshot',
                ch->>'destination_name_snapshot', ch->>'destination_raw', (ch->>'requested_date')::date,
                (ch->>'quantity')::numeric, ch->>'unit_ref', ch->>'unit_code_snapshot',
                COALESCE((ch->>'is_split_delivery')::boolean, false), ch->>'notes');
        END LOOP;
    END LOOP;

    -- ---- initial DRAFT history + idempotency record (atomic กับข้างบน) ----
    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref, reason)
    VALUES (v_new, NULL, 'DRAFT', p_actor, 'created via ENQ (' || p_service || '/' || p_request_id || ')');

    -- PK (service, request_id) กัน duplicate commit; concurrent loser → txn abort (F7 full concurrency gated)
    INSERT INTO rfq_ingest_request (service, request_id, payload_sha256, rfq_id, actor_ref)
    VALUES (p_service, p_request_id, v_hash, v_new, p_actor);

    RETURN v_new;
END;
$$;
ALTER FUNCTION create_rfq_draft(jsonb, text, text, text) OWNER TO rfq_owner;

-- ---- grant: EXECUTE เฉพาะ rfq_ingest (Codex go/no-go #2) ----
REVOKE ALL ON FUNCTION _reject_unknown_keys(jsonb, text[], text) FROM PUBLIC;
REVOKE ALL ON FUNCTION create_rfq_draft(jsonb, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_rfq_draft(jsonb, text, text, text) TO rfq_ingest;
-- rfq_app (reviewer/FastAPI) ไม่ได้ grant create_rfq_draft — ถ้าต้องการ human draft-entry ค่อย grant แยกเมื่อมี requirement

COMMIT;
