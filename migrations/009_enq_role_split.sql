-- ============================================================================
-- 009_enq_role_split.sql — M1/M2 pre-deploy role hardening (Codex orchestration review)
-- ----------------------------------------------------------------------------
-- M2: แยก inbound (public API) ออกจาก worker — credential เดียวถูกเจาะแล้วเรียก worker mutation ไม่ได้
--   rfq_ingest  = inbound เท่านั้น : create_rfq_draft + begin_rfq_extraction
--   rfq_worker  (ใหม่)            : list_claimable_extractions + claim/apply/fail_rfq_extraction
-- M1: read role ของ public API แบบ allowlist (แทน SELECT ON ALL TABLES ของ rfq_app)
--   rfq_read_api (ใหม่) : SELECT เฉพาะ business tree tables + EXECUTE get_extraction_status
--     → อ่าน rfq_ai_extraction_run / rfq_attachment / trusted tables ตรง ๆ ไม่ได้
--       (ปิด leak provider_input_ref / input_sha256 / object-store key ที่ safe projection ซ่อนไว้)
--   rfq_app (reviewer) คงเดิม — ไม่ใช่ credential ของ public API แล้ว
--
-- B2: ทั้งไฟล์ = transaction เดียว. local + synthetic เท่านั้น — ยังไม่ deploy ; prod ใช้ credential แยก + secret manager
-- Rollback: DROP ROLE rfq_worker, rfq_read_api (หลัง reassign/ revoke) — ดูหมายเหตุท้ายไฟล์
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ---- M2: worker role ----
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_worker') THEN CREATE ROLE rfq_worker NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA rfq TO rfq_worker;
-- ย้าย worker functions ออกจาก rfq_ingest (inbound เหลือ create_rfq_draft + begin)
REVOKE EXECUTE ON FUNCTION claim_rfq_extraction(uuid,text,text,text)            FROM rfq_ingest;
REVOKE EXECUTE ON FUNCTION apply_rfq_extraction(uuid,uuid,jsonb,text,text,text) FROM rfq_ingest;
REVOKE EXECUTE ON FUNCTION fail_rfq_extraction(uuid,uuid,text,text,text,text)   FROM rfq_ingest;
REVOKE EXECUTE ON FUNCTION list_claimable_extractions(int)                      FROM rfq_ingest;
GRANT  EXECUTE ON FUNCTION claim_rfq_extraction(uuid,text,text,text)            TO rfq_worker;
GRANT  EXECUTE ON FUNCTION apply_rfq_extraction(uuid,uuid,jsonb,text,text,text) TO rfq_worker;
GRANT  EXECUTE ON FUNCTION fail_rfq_extraction(uuid,uuid,text,text,text,text)   TO rfq_worker;
GRANT  EXECUTE ON FUNCTION list_claimable_extractions(int)                      TO rfq_worker;

-- ---- M1: minimal public read role — **column-level** SELECT ตรง query ของ GET /enq/rfq เท่านั้น ----
-- (B1: table-level SELECT เปิด PII/notes/spec ทุก column ที่ API ไม่คืน — ใช้ column grant แทน)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_read_api') THEN CREATE ROLE rfq_read_api NOLOGIN; END IF;
END $$;
-- ถ้า role เดิมมี table-level SELECT ค้าง (เวอร์ชันก่อน) → revoke ให้เหลือ column grant ล้วน
REVOKE SELECT ON rfq, rfq_item, rfq_quantity_option, rfq_component,
                 rfq_process_requirement, rfq_packing_requirement, rfq_delivery FROM rfq_read_api;
GRANT USAGE ON SCHEMA rfq TO rfq_read_api;
-- column list = ตรงกับ SELECT + FK/filter/order columns ใน enq_api/main.py GET /enq/rfq
GRANT SELECT (id, rfq_no, status_code, revision_no, enquiry_ref, source_channel, customer_name_raw, priority_code)
    ON rfq TO rfq_read_api;
GRANT SELECT (id, rfq_id, line_no, job_name, is_reprint, previous_job_ref, finished_width_mm, finished_length_mm,
              finished_depth_mm, finishing_state, packing_state, artwork_state)
    ON rfq_item TO rfq_read_api;
GRANT SELECT (rfq_item_id, option_no, quantity, unit_ref, is_primary)                          ON rfq_quantity_option TO rfq_read_api;
GRANT SELECT (rfq_item_id, component_no, component_name, paper_name_snapshot, paper_gsm_snapshot,
              print_sides_code, color_outside_count, color_inside_count)                        ON rfq_component TO rfq_read_api;
GRANT SELECT (rfq_item_id, sequence_no, process_ref, process_name_raw, side_code)               ON rfq_process_requirement TO rfq_read_api;
GRANT SELECT (rfq_item_id, sequence_no, packing_name_raw, quantity_per_pack, unit_ref)          ON rfq_packing_requirement TO rfq_read_api;
GRANT SELECT (rfq_item_id, delivery_no, destination_raw, requested_date)                        ON rfq_delivery TO rfq_read_api;
GRANT EXECUTE ON FUNCTION get_extraction_status(uuid) TO rfq_read_api;

-- assert M1 (F2): **exact (table,column) allowlist** ของ rfq_read_api = ตรง GET /enq/rfq เป๊ะ
--   (ไม่ใช่ sentinel blacklist — จับได้ทั้ง extra column, missing column, table-level, และ DML)
DO $assert$
DECLARE v_extra text; v_missing text; v_tab text;
BEGIN
  CREATE TEMP TABLE _rd_exp(t text, c text) ON COMMIT DROP;
  INSERT INTO _rd_exp(t,c) VALUES
    ('rfq','id'),('rfq','rfq_no'),('rfq','status_code'),('rfq','revision_no'),('rfq','enquiry_ref'),
    ('rfq','source_channel'),('rfq','customer_name_raw'),('rfq','priority_code'),
    ('rfq_item','id'),('rfq_item','rfq_id'),('rfq_item','line_no'),('rfq_item','job_name'),('rfq_item','is_reprint'),
    ('rfq_item','previous_job_ref'),('rfq_item','finished_width_mm'),('rfq_item','finished_length_mm'),
    ('rfq_item','finished_depth_mm'),('rfq_item','finishing_state'),('rfq_item','packing_state'),('rfq_item','artwork_state'),
    ('rfq_quantity_option','rfq_item_id'),('rfq_quantity_option','option_no'),('rfq_quantity_option','quantity'),
    ('rfq_quantity_option','unit_ref'),('rfq_quantity_option','is_primary'),
    ('rfq_component','rfq_item_id'),('rfq_component','component_no'),('rfq_component','component_name'),
    ('rfq_component','paper_name_snapshot'),('rfq_component','paper_gsm_snapshot'),('rfq_component','print_sides_code'),
    ('rfq_component','color_outside_count'),('rfq_component','color_inside_count'),
    ('rfq_process_requirement','rfq_item_id'),('rfq_process_requirement','sequence_no'),('rfq_process_requirement','process_ref'),
    ('rfq_process_requirement','process_name_raw'),('rfq_process_requirement','side_code'),
    ('rfq_packing_requirement','rfq_item_id'),('rfq_packing_requirement','sequence_no'),('rfq_packing_requirement','packing_name_raw'),
    ('rfq_packing_requirement','quantity_per_pack'),('rfq_packing_requirement','unit_ref'),
    ('rfq_delivery','rfq_item_id'),('rfq_delivery','delivery_no'),('rfq_delivery','destination_raw'),('rfq_delivery','requested_date');
  -- extra: column SELECT ที่ grant แต่ไม่อยู่ใน allowlist (จับ over-grant ทุกชนิด รวม PII/ledger/spec)
  SELECT string_agg(t||'.'||c, ', ' ORDER BY t,c) INTO v_extra FROM (
    SELECT table_name t, column_name c FROM information_schema.role_column_grants
      WHERE grantee='rfq_read_api' AND table_schema='rfq' AND privilege_type='SELECT'
    EXCEPT SELECT t,c FROM _rd_exp) x;
  IF v_extra IS NOT NULL THEN RAISE EXCEPTION 'M1 exact-allowlist: rfq_read_api มี column นอก allowlist: %', v_extra; END IF;
  -- missing: allowlist column ที่ endpoint ใช้แต่ไม่ถูก grant
  SELECT string_agg(t||'.'||c, ', ' ORDER BY t,c) INTO v_missing FROM (
    SELECT t,c FROM _rd_exp
    EXCEPT SELECT table_name, column_name FROM information_schema.role_column_grants
      WHERE grantee='rfq_read_api' AND table_schema='rfq' AND privilege_type='SELECT') x;
  IF v_missing IS NOT NULL THEN RAISE EXCEPTION 'M1 exact-allowlist: rfq_read_api ขาด column ที่ endpoint ใช้: %', v_missing; END IF;
  -- ต้องไม่มี table-level privilege ใด ๆ (SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER) บน rfq schema
  SELECT string_agg(table_name||':'||privilege_type, ', ') INTO v_tab FROM information_schema.role_table_grants
    WHERE grantee='rfq_read_api' AND table_schema='rfq';
  IF v_tab IS NOT NULL THEN RAISE EXCEPTION 'M1: rfq_read_api มี table-level privilege (ต้อง column SELECT เท่านั้น): %', v_tab; END IF;
  -- get_extraction_status ต้องเรียกได้ (positive)
  IF NOT has_function_privilege('rfq_read_api','rfq.get_extraction_status(uuid)','EXECUTE') THEN
    RAISE EXCEPTION 'M1: rfq_read_api เรียก get_extraction_status ไม่ได้';
  END IF;
END $assert$;

COMMIT;
-- Rollback (manual): REVOKE ... ; REASSIGN/DROP OWNED; DROP ROLE rfq_worker, rfq_read_api;
--   และคืน GRANT claim/apply/fail/list_claimable ให้ rfq_ingest ถ้าต้องย้อน M2
