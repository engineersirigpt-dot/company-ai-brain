-- login roles สำหรับ ENQ API — non-superuser, inherit rfq_ingest/rfq_app (Codex B3)
-- prod ควรใช้ credential แยก + secret manager; นี่คือ dev/synthetic เท่านั้น
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_ingest_login') THEN
    CREATE ROLE rfq_ingest_login LOGIN PASSWORD 'ingest' IN ROLE rfq_ingest;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_app_login') THEN
    CREATE ROLE rfq_app_login LOGIN PASSWORD 'app' IN ROLE rfq_app;
  END IF;
END $$;
ALTER ROLE rfq_ingest_login NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
ALTER ROLE rfq_app_login    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
