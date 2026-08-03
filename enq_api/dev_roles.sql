-- login roles สำหรับ ENQ API/worker — non-superuser, inherit base roles (Codex B3 + M1/M2)
-- prod ควรใช้ credential แยก + secret manager; นี่คือ dev/synthetic เท่านั้น
DO $$ BEGIN
  -- inbound (public API): create_rfq_draft + begin เท่านั้น (หลัง 009)
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_ingest_login') THEN
    CREATE ROLE rfq_ingest_login LOGIN PASSWORD 'ingest' IN ROLE rfq_ingest;
  END IF;
  -- reviewer (internal): rfq_app (SELECT ALL + reviewer capability) — ไม่ใช่ credential ของ public API
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_app_login') THEN
    CREATE ROLE rfq_app_login LOGIN PASSWORD 'app' IN ROLE rfq_app;
  END IF;
  -- worker (M2): list_claimable + claim/apply/fail เท่านั้น
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_worker_login') THEN
    CREATE ROLE rfq_worker_login LOGIN PASSWORD 'worker' IN ROLE rfq_worker;
  END IF;
  -- public API read (M1): SELECT business tree tables + get_extraction_status เท่านั้น
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_read_api_login') THEN
    CREATE ROLE rfq_read_api_login LOGIN PASSWORD 'readapi' IN ROLE rfq_read_api;
  END IF;
END $$;
ALTER ROLE rfq_ingest_login   NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
ALTER ROLE rfq_app_login      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
ALTER ROLE rfq_worker_login   NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
ALTER ROLE rfq_read_api_login NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
