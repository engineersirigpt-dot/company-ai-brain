-- ============================================================================
-- 003_field_policy.sql — PRODUCTION seed: RFQ field egress policy (default-deny)
-- นี่คือ config จริง (ไม่ใช่ test fixture) — runner รันไฟล์นี้ใน prod ได้
-- test fixtures ย้ายไป migrations/test/ (Codex M4/M5: ห้าม prod runner สร้าง RFQ-TEST-*)
-- ============================================================================
BEGIN;
SET search_path TO rfq;

INSERT INTO rfq_field_policy VALUES
('ANY','*','RESTRICTED','MIXED','BLOCK','NONE','rfq-egress-v1',
 'Unregistered RFQ field is blocked from Cloud by default'),
('RFQ','customer_ref','CONFIDENTIAL','TRADE_SECRET','REDACT','TOKENIZE','rfq-egress-v1',NULL),
('RFQ','customer_name_raw','CONFIDENTIAL','MIXED','REDACT','TOKENIZE','rfq-egress-v1',NULL),
('RFQ','contact_name','CONFIDENTIAL','PERSONAL','REDACT','TOKENIZE','rfq-egress-v1',NULL),
('RFQ','contact_phone','CONFIDENTIAL','PERSONAL','REDACT','MASK','rfq-egress-v1',NULL),
('RFQ','contact_email','CONFIDENTIAL','PERSONAL','REDACT','MASK','rfq-egress-v1',NULL),
('RFQ','customer_notes','RESTRICTED','MIXED','LOCAL_ONLY','NONE','rfq-egress-v1',NULL),
('ITEM','*','CONFIDENTIAL','TRADE_SECRET','LOCAL_ONLY','NONE','rfq-egress-v1',NULL),
('COMPONENT','*','CONFIDENTIAL','TRADE_SECRET','LOCAL_ONLY','NONE','rfq-egress-v1',NULL),
('ATTACHMENT','*','RESTRICTED','MIXED','LOCAL_ONLY','NONE','rfq-egress-v1',NULL);

COMMIT;
