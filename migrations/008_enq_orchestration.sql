-- ============================================================================
-- 008_enq_orchestration.sql — durable-worker support สำหรับ extraction orchestration
-- ----------------------------------------------------------------------------
-- เพิ่ม 2 helper (SECURITY DEFINER, owner=rfq_owner, pinned search_path, no PUBLIC exec):
--   list_claimable_extractions(limit) → run_id ที่ claim ได้ (PENDING หรือ RUNNING lease หมด)
--       EXECUTE = rfq_ingest — worker poll งาน durable โดยไม่ต้องมี broad SELECT บน run table
--       (create-only role คงเดิม; คืนเฉพาะ run_id ไม่เผย lease/ref/hash)
--   get_extraction_status(run_id) → projection ปลอดภัยสำหรับ public GET
--       EXECUTE = rfq_app — คืนเฉพาะ {run_id, rfq_id, status_code, attempt_no}
--       ไม่เผย lease_token / provider_input_ref / input_sha256 / provider/model
--
-- B2: ทั้งไฟล์ = transaction เดียว → พังกลางทาง rollback หมด ไม่มี PUBLIC EXECUTE ค้าง
-- Rollback: DROP FUNCTION list_claimable_extractions(int), get_extraction_status(uuid)
-- local + synthetic เท่านั้น — ยังไม่ deploy
-- ============================================================================
BEGIN;
SET search_path TO rfq;                                         -- session: สร้าง object ใน rfq (ไม่ใช่ pg_catalog)

-- ---- worker poll: run ที่ claim ได้ (durable queue จาก run table เอง — ไม่ต้อง outbox) ----
CREATE OR REPLACE FUNCTION list_claimable_extractions(p_limit int DEFAULT 50)
RETURNS TABLE(run_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
    SELECT id FROM rfq_ai_extraction_run
    WHERE status_code = 'PENDING'
       OR (status_code = 'RUNNING' AND lease_expires_at <= now())      -- lease หมด → reclaim ได้
    ORDER BY started_at
    LIMIT least(greatest(coalesce(p_limit, 50), 1), 500);
$$;
ALTER FUNCTION list_claimable_extractions(int) OWNER TO rfq_owner;

-- ---- public GET status: projection ปลอดภัย (ไม่เผย lease/ref/hash/provider) ----
CREATE OR REPLACE FUNCTION get_extraction_status(p_run_id uuid)
RETURNS TABLE(run_id uuid, rfq_id uuid, status_code text, attempt_no int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
    SELECT id, rfq_id, status_code, attempt_no FROM rfq_ai_extraction_run WHERE id = p_run_id;
$$;
ALTER FUNCTION get_extraction_status(uuid) OWNER TO rfq_owner;

-- ---- grants ----
REVOKE ALL ON FUNCTION list_claimable_extractions(int) FROM PUBLIC;
REVOKE ALL ON FUNCTION get_extraction_status(uuid)     FROM PUBLIC;
GRANT EXECUTE ON FUNCTION list_claimable_extractions(int) TO rfq_ingest;   -- worker (write role) poll
GRANT EXECUTE ON FUNCTION get_extraction_status(uuid)     TO rfq_app;      -- public GET (read role)

COMMIT;
