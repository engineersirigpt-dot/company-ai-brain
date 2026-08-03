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

-- assert M1 (fail-closed ถ้า grant drift):
DO $assert$ BEGIN
  -- positive: endpoint columns + status function อ่านได้
  IF NOT (has_column_privilege('rfq_read_api','rfq.rfq','customer_name_raw','SELECT')
          AND has_column_privilege('rfq_read_api','rfq.rfq_item','job_name','SELECT')
          AND has_function_privilege('rfq_read_api','rfq.get_extraction_status(uuid)','EXECUTE')) THEN
    RAISE EXCEPTION 'M1 regression: rfq_read_api อ่าน endpoint column/status ไม่ได้';
  END IF;
  -- B1: PII/notes/spec columns ที่ API ไม่คืน ต้อง denied
  IF has_column_privilege('rfq_read_api','rfq.rfq','contact_name','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq','contact_phone','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq','contact_email','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq','customer_notes','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_item','description','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_item','intended_use','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_item','notes','SELECT') THEN
    RAISE EXCEPTION 'M1 regression: rfq_read_api อ่าน PII/notes column ได้';
  END IF;
  -- B2: sensitive/trusted/ledger tables ต้อง denied ทุก column (ledger outcome เก็บ lease/ref/hash)
  IF has_column_privilege('rfq_read_api','rfq.rfq_ai_extraction_run','input_sha256','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_attachment','object_store_key','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_extraction_request','outcome','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_source_ingest','source_sha256','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_field_evidence','value_snapshot','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_redaction_attestation','redacted_sha256','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_egress_approval','reason','SELECT')
     OR has_column_privilege('rfq_read_api','rfq.rfq_ai_provider','provider_code','SELECT') THEN
    RAISE EXCEPTION 'M1 regression: rfq_read_api อ่าน sensitive/ledger column ได้';
  END IF;
END $assert$;

COMMIT;
-- Rollback (manual): REVOKE ... ; REASSIGN/DROP OWNED; DROP ROLE rfq_worker, rfq_read_api;
--   และคืน GRANT claim/apply/fail/list_claimable ให้ rfq_ingest ถ้าต้องย้อน M2
