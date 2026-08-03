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

-- ---- M1: minimal public read role ----
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_read_api') THEN CREATE ROLE rfq_read_api NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA rfq TO rfq_read_api;
GRANT SELECT ON rfq, rfq_item, rfq_quantity_option, rfq_component,
                rfq_process_requirement, rfq_packing_requirement, rfq_delivery TO rfq_read_api;
GRANT EXECUTE ON FUNCTION get_extraction_status(uuid) TO rfq_read_api;

-- assert M1: read_api อ่าน sensitive tables ตรง ๆ ไม่ได้ (fail-closed ถ้า grant เผลอกว้าง)
DO $assert$ BEGIN
  IF has_table_privilege('rfq_read_api','rfq.rfq_ai_extraction_run','SELECT')
     OR has_table_privilege('rfq_read_api','rfq.rfq_attachment','SELECT')
     OR has_table_privilege('rfq_read_api','rfq.rfq_source_ingest','SELECT')
     OR has_table_privilege('rfq_read_api','rfq.rfq_field_evidence','SELECT') THEN
    RAISE EXCEPTION 'M1 regression: rfq_read_api อ่าน sensitive table ได้';
  END IF;
END $assert$;

COMMIT;
-- Rollback (manual): REVOKE ... ; REASSIGN/DROP OWNED; DROP ROLE rfq_worker, rfq_read_api;
--   และคืน GRANT claim/apply/fail/list_claimable ให้ rfq_ingest ถ้าต้องย้อน M2
