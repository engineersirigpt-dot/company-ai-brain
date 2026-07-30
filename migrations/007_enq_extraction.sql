-- ============================================================================
-- 007_enq_extraction.sql — ENQ AI extraction (two-phase provenance) — v1.1
-- ต้นทาง: RFQ_EXTRACTION_PAYLOAD_V1_1.md rev 3 + C1-C4 (Codex confirm)
--
-- two-phase: begin (egress decision) → claim (revalidate + lease = จุดอนุมัติจริง)
--            → provider call (นอก DB) → apply (tree+evidence atomic) / fail
-- boundary: functions = SECURITY DEFINER owner rfq_owner, grant EXECUTE เฉพาะ rfq_ingest
--           trusted tables: rfq_ingest ไม่มี direct privilege (อ่านผ่าน function เท่านั้น — R7)
--
-- PART 1 (ไฟล์นี้): schema — trusted/ledger tables + ALTER run/evidence + grants
-- PART 2: begin/claim/apply/fail (เติมต่อในไฟล์เดียวกัน)
-- Rollback: DROP FUNCTION ...; DROP TABLE rfq_extraction_request, rfq_egress_approval,
--           rfq_redaction_attestation, rfq_ai_provider, rfq_source_ingest;
--           ALTER TABLE ... (revert) — prototype ใช้ DROP SCHEMA rfq CASCADE
-- ============================================================================
BEGIN;
SET search_path TO rfq;

-- ---- trusted: source ingest (scan+classify) — writer = scanner/classifier (§0) ----
CREATE TABLE rfq_source_ingest (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_store_key       text NOT NULL,
    original_filename      text,
    mime_type              text,
    size_bytes             bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    source_sha256          char(64) NOT NULL,
    malware_scan_status    text NOT NULL CHECK (malware_scan_status IN ('PENDING','CLEAN','BLOCKED','ERROR')),
    classification_status  text NOT NULL CHECK (classification_status IN ('PENDING','CONFIRMED','REJECTED')),
    classification_code    text NOT NULL CHECK (classification_code IN ('UNCLASSIFIED','INTERNAL','CONFIDENTIAL','RESTRICTED')),
    contains_personal_data boolean,
    contains_trade_secret  boolean,
    cloud_action_code      text NOT NULL CHECK (cloud_action_code IN ('ALLOW','REDACT','LOCAL_ONLY','BLOCK')),
    policy_version         text NOT NULL,
    registered_by_ref      text NOT NULL,
    registered_at          timestamptz NOT NULL DEFAULT now(),
    is_active              boolean NOT NULL DEFAULT true,   -- R1 revocation
    revoked_at             timestamptz
);

-- ---- trusted: provider/model allowlist ----
CREATE TABLE rfq_ai_provider (
    provider_code    text NOT NULL,
    model_code       text NOT NULL,
    execution_target text NOT NULL CHECK (execution_target IN ('LOCAL','CLOUD')),
    is_active        boolean NOT NULL DEFAULT true,
    policy_version   text NOT NULL,
    PRIMARY KEY (provider_code, model_code)
);

-- ---- trusted: redaction attestation (ก่อน Cloud; R2 มี artifact ref) ----
CREATE TABLE rfq_redaction_attestation (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_ingest_id         uuid NOT NULL REFERENCES rfq_source_ingest(id),
    purpose_code             text NOT NULL,
    redactor_ref             text NOT NULL,
    redactor_version         text NOT NULL,
    source_sha256            char(64) NOT NULL,   -- ต้องตรงกับ source_ingest.source_sha256
    redacted_sha256          char(64) NOT NULL,   -- = run.input_sha256 (REDACTED_ALLOW)
    redacted_object_store_key text NOT NULL,      -- artifact ที่อนุญาตให้ส่ง (R2)
    redaction_manifest       jsonb NOT NULL CHECK (redaction_manifest <> '{}'::jsonb),
    created_at               timestamptz NOT NULL DEFAULT now(),
    expires_at               timestamptz NOT NULL,
    is_active                boolean NOT NULL DEFAULT true,
    revoked_at               timestamptz
);

-- ---- trusted: egress approval (APPROVED_EXCEPTION) ----
CREATE TABLE rfq_egress_approval (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_ingest_id  uuid NOT NULL REFERENCES rfq_source_ingest(id),
    provider_code     text NOT NULL,
    model_code        text NOT NULL,
    purpose_code      text NOT NULL,
    approved_by_ref   text NOT NULL,
    reason            text NOT NULL CHECK (NULLIF(btrim(reason), '') IS NOT NULL),
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    is_active         boolean NOT NULL DEFAULT true,
    revoked_at        timestamptz
);

-- ---- idempotency ledger (F7) — ใช้กับ BEGIN/CLAIM/APPLY/FAIL ----
CREATE TABLE rfq_extraction_request (
    service        text NOT NULL,
    operation_code text NOT NULL CHECK (operation_code IN ('BEGIN','CLAIM','APPLY','FAIL')),
    request_id     text NOT NULL,
    rfq_id         uuid,
    run_id         uuid,
    actor_ref      text NOT NULL,
    payload_sha256 char(64) NOT NULL,
    outcome        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (service, operation_code, request_id)
);

-- ---- ALTER run: RUNNING + lease + trusted ids + provider_input_ref + blocked_reason ----
-- B1/M1: drop เฉพาะ status-enum CHECK ตัวเดียวด้วยชื่อแน่นอน (ห้าม match กว้าง — เดิมเผลอลบ
--   invariant 'egress BLOCKED ⇒ status IN (BLOCKED,FAILED,PENDING)' จาก 001 ไปด้วย)
ALTER TABLE rfq_ai_extraction_run DROP CONSTRAINT IF EXISTS rfq_ai_extraction_run_status_code_check;
ALTER TABLE rfq_ai_extraction_run
    ADD CONSTRAINT rfq_ai_extraction_run_status_code_check
    CHECK (status_code IN ('PENDING','RUNNING','SUCCEEDED','FAILED','BLOCKED'));
-- invariant เดิม (001) ยังต้องอยู่: assert ว่าไม่ถูกลบโดยไม่ตั้งใจ
DO $assert$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='rfq.rfq_ai_extraction_run'::regclass
        AND contype='c' AND pg_get_constraintdef(oid) LIKE '%egress_decision_code%BLOCKED%status_code%') THEN
        RAISE EXCEPTION 'invariant BLOCKED-decision status CHECK หายไป (B1 regression)';
    END IF;
END
$assert$;

ALTER TABLE rfq_ai_extraction_run
    ADD COLUMN source_ingest_id        uuid REFERENCES rfq_source_ingest(id),
    ADD COLUMN redaction_attestation_id uuid REFERENCES rfq_redaction_attestation(id),
    ADD COLUMN egress_approval_id      uuid REFERENCES rfq_egress_approval(id),
    ADD COLUMN provider_input_ref      text,
    ADD COLUMN purpose_code            text,           -- B3: ให้ claim re-bind purpose
    ADD COLUMN blocked_reason_code     text,
    ADD COLUMN lease_token             uuid,
    ADD COLUMN claimed_by_ref          text,
    ADD COLUMN claimed_service         text,           -- B6: bind service ที่ claim
    ADD COLUMN lease_expires_at        timestamptz,
    ADD COLUMN attempt_no              integer NOT NULL DEFAULT 0 CHECK (attempt_no >= 0);

-- ---- ALTER evidence: derivation_type (F5/R5) + ล้าง AI_INFERENCE จาก source_type enum ----
-- M1: drop เจาะจง — source_type enum ด้วยชื่อ column-check แน่นอน; AI_INFERENCE→run check
--   ด้วย def ที่มี "AI_INFERENCE" + "extraction_run_id" พร้อมกัน (ไม่แตะ CHECK อื่น)
ALTER TABLE rfq_field_evidence DROP CONSTRAINT IF EXISTS rfq_field_evidence_source_type_check;
DO $ev$
DECLARE c text;
BEGIN
    SELECT conname INTO c FROM pg_constraint WHERE conrelid='rfq.rfq_field_evidence'::regclass AND contype='c'
        AND pg_get_constraintdef(oid) LIKE '%AI_INFERENCE%' AND pg_get_constraintdef(oid) LIKE '%extraction_run_id%';
    IF c IS NOT NULL THEN EXECUTE format('ALTER TABLE rfq.rfq_field_evidence DROP CONSTRAINT %I', c); END IF;
END
$ev$;
-- source_type = medium ล้วน (ไม่มี AI_INFERENCE)
ALTER TABLE rfq_field_evidence
    ADD CONSTRAINT rfq_field_evidence_source_type_medium_check
    CHECK (source_type IN ('MANUAL','EMAIL','LINE','PDF','DOCX','XLSX','IMAGE','MASTER_DATA','PREVIOUS_JOB'));
ALTER TABLE rfq_field_evidence
    ADD COLUMN derivation_type text NOT NULL DEFAULT 'HUMAN_EXTRACTED'
    CHECK (derivation_type IN ('HUMAN_EXTRACTED','AI_EXTRACTED','AI_INFERENCE','SYSTEM_RESOLVED'));
-- author-axis: AI derivation ต้องอ้าง extraction run (แทน CHECK เดิมที่อิง source_type)
ALTER TABLE rfq_field_evidence
    ADD CONSTRAINT rfq_field_evidence_derivation_run_check
    CHECK (derivation_type NOT IN ('AI_EXTRACTED','AI_INFERENCE') OR extraction_run_id IS NOT NULL);

-- ---- ownership: trusted/ledger tables → rfq_owner (ให้ SECURITY DEFINER function เข้าถึง) ----
ALTER TABLE rfq_source_ingest         OWNER TO rfq_owner;
ALTER TABLE rfq_ai_provider           OWNER TO rfq_owner;
ALTER TABLE rfq_redaction_attestation OWNER TO rfq_owner;
ALTER TABLE rfq_egress_approval       OWNER TO rfq_owner;
ALTER TABLE rfq_extraction_request    OWNER TO rfq_owner;

-- R7: rfq_ingest ไม่มี direct privilege บน trusted/ledger tables (ไม่ grant SELECT/DML)
REVOKE ALL ON rfq_source_ingest, rfq_ai_provider, rfq_redaction_attestation,
              rfq_egress_approval, rfq_extraction_request FROM rfq_app, rfq_ingest;

-- ============================================================================
-- PART 2 — service functions: begin / claim / fail  (apply อยู่ท้ายไฟล์)
-- B2: ทั้ง 007 = transaction เดียว (ไม่มี COMMIT กลาง) → พังกลางทาง rollback หมด ไม่มี PUBLIC EXECUTE ค้าง
-- ----------------------------------------------------------------------------
-- Error taxonomy (Codex F1) — safe SQLSTATE ให้ transport/worker แยกประเภทได้โดยไม่ parse message:
--   RFS01  state_conflict         (409) — claim/apply/fail: wrong status หรือ lease/actor/service ไม่ตรง/หมดอายุ
--   RFN01  run_not_found          (404) — claim/apply/fail: run_id ไม่พบ
--   RFR01  invalid_extraction_result (422) — apply: evidence completeness/derivation/inference
--                                            หรือ provider result อ้าง ref ที่ resolve ไม่ได้
--   RFI01  idempotency_conflict   (409) — explicit ledger conflict (request_id ซ้ำด้วย payload/actor ต่าง)
--   23503  begin input ไม่พบ (source/provider/attestation/approval) → transport map 422 invalid_request
--   22023  invalid input value    (422) · 54000 payload limit (413)
--   23505  = duplicate business key จริง (unique constraint) → transport map 422 (draft/apply) ไม่ใช่ idempotency
-- apply ใช้ RF* เพราะ 23514/23503/23505 เดิมกำกวม (state-vs-result / run-vs-ref / idem-vs-dupkey อยู่ op เดียวกัน)
-- ============================================================================

-- ---- helper: normalize+validate trusted args (เหมือน create_rfq_draft) ----
CREATE OR REPLACE FUNCTION _norm_ctx(INOUT p_actor text, INOUT p_service text, INOUT p_request_id text)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, rfq, pg_temp AS $$
BEGIN
    IF p_actor IS NULL OR p_service IS NULL OR p_request_id IS NULL THEN
        RAISE EXCEPTION 'actor/service/request_id ต้องมาจาก trusted context (ห้าม NULL)' USING ERRCODE='22023'; END IF;
    p_actor := btrim(p_actor, E' \t\n\r\f\v');
    p_service := btrim(p_service, E' \t\n\r\f\v');
    p_request_id := btrim(p_request_id, E' \t\n\r\f\v');
    IF p_actor='' OR p_service='' OR p_request_id='' THEN
        RAISE EXCEPTION 'actor/service/request_id ห้ามว่าง' USING ERRCODE='22023'; END IF;
    IF length(p_actor)>200 OR length(p_service)>100 OR length(p_request_id)>200 THEN
        RAISE EXCEPTION 'actor/service/request_id ยาวเกิน' USING ERRCODE='22023'; END IF;
    IF p_actor ~ '[[:cntrl:]]' OR p_service ~ '[[:cntrl:]]' OR p_request_id ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'actor/service/request_id มี control char' USING ERRCODE='22023'; END IF;
END;
$$;
ALTER FUNCTION _norm_ctx(text, text, text) OWNER TO rfq_owner;

-- ---- helper: egress decision (logic เดียว ใช้ทั้ง begin และ claim — B3.2 กัน policy drift) ----
-- resolve จาก trusted state ปัจจุบัน → total decision + authorized artifact/hash
-- att/apr not-found = 23503 (bad reference; ที่ claim เป็น FK จึงไม่เกิด); policy fail = BLOCKED
-- p_min_valid = att/apr.expires_at ต้อง >= ค่านี้ (begin=now(), claim=now()+lease window)
CREATE OR REPLACE FUNCTION _egress_decide(
    p_src rfq_source_ingest, p_prov rfq_ai_provider, p_target text, p_purpose text,
    p_att_id uuid, p_apr_id uuid, p_min_valid timestamptz
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    att rfq_redaction_attestation%ROWTYPE; apr rfq_egress_approval%ROWTYPE;
    v_dec text; v_blk text := NULL; v_ref text := NULL; v_hash char(64) := NULL;
    v_red boolean := false; v_manifest jsonb := '{}'::jsonb; v_exc_by text := NULL; v_exc_reason text := NULL;
    v_att_id uuid := NULL; v_apr_id uuid := NULL;
BEGIN
    IF NOT p_src.is_active OR p_src.revoked_at IS NOT NULL THEN v_blk:='source revoked/inactive';
    ELSIF p_src.malware_scan_status <> 'CLEAN' THEN v_blk:='malware='||p_src.malware_scan_status;
    ELSIF p_src.classification_status <> 'CONFIRMED' THEN v_blk:='classification_status='||p_src.classification_status;
    ELSIF p_src.classification_code = 'UNCLASSIFIED' THEN v_blk:='classification=UNCLASSIFIED';
    ELSIF p_src.contains_personal_data IS NULL OR p_src.contains_trade_secret IS NULL THEN v_blk:='privacy flags incomplete';
    ELSIF NOT p_prov.is_active THEN v_blk:='provider inactive';
    ELSIF p_prov.execution_target IS DISTINCT FROM p_target THEN v_blk:='provider target mismatch';
    ELSIF p_prov.policy_version IS DISTINCT FROM p_src.policy_version THEN v_blk:='policy version mismatch';
    END IF;

    IF v_blk IS NOT NULL THEN v_dec:='BLOCKED';
    ELSIF p_target='CLOUD' AND p_src.cloud_action_code IN ('BLOCK','LOCAL_ONLY') THEN
        v_dec:='BLOCKED'; v_blk:='cloud_action='||p_src.cloud_action_code;
    ELSIF p_target='CLOUD' AND p_src.cloud_action_code='REDACT' THEN
        IF p_att_id IS NULL THEN v_dec:='BLOCKED'; v_blk:='REDACT ต้องมี attestation';
        ELSE
            SELECT * INTO att FROM rfq_redaction_attestation WHERE id=p_att_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'attestation % ไม่พบ', p_att_id USING ERRCODE='23503'; END IF;
            IF NOT att.is_active OR att.revoked_at IS NOT NULL OR att.expires_at < p_min_valid
               OR att.source_ingest_id IS DISTINCT FROM p_src.id OR att.source_sha256 IS DISTINCT FROM p_src.source_sha256
               OR att.purpose_code IS DISTINCT FROM p_purpose THEN
                v_dec:='BLOCKED'; v_blk:='attestation invalid';
            ELSE v_dec:='REDACTED_ALLOW'; v_ref:=att.redacted_object_store_key; v_hash:=att.redacted_sha256;
                 v_red:=true; v_manifest:=att.redaction_manifest; v_att_id:=att.id; END IF;
        END IF;
    ELSIF p_target='CLOUD' AND p_src.cloud_action_code='ALLOW' THEN
        IF p_apr_id IS NULL THEN v_dec:='BLOCKED'; v_blk:='ALLOW ต้องมี approval';
        ELSE
            SELECT * INTO apr FROM rfq_egress_approval WHERE id=p_apr_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'approval % ไม่พบ', p_apr_id USING ERRCODE='23503'; END IF;
            IF NOT apr.is_active OR apr.revoked_at IS NOT NULL OR apr.expires_at < p_min_valid
               OR apr.source_ingest_id IS DISTINCT FROM p_src.id OR apr.provider_code IS DISTINCT FROM p_prov.provider_code
               OR apr.model_code IS DISTINCT FROM p_prov.model_code OR apr.purpose_code IS DISTINCT FROM p_purpose THEN
                v_dec:='BLOCKED'; v_blk:='approval invalid';
            ELSE v_dec:='APPROVED_EXCEPTION'; v_ref:=p_src.object_store_key; v_hash:=p_src.source_sha256;
                 v_exc_by:=apr.approved_by_ref; v_exc_reason:=apr.reason; v_apr_id:=apr.id; END IF;
        END IF;
    ELSIF p_target='LOCAL' THEN
        v_dec:='LOCAL_ONLY'; v_ref:=p_src.object_store_key; v_hash:=p_src.source_sha256;
    ELSE v_dec:='BLOCKED'; v_blk:='no matching egress branch';
    END IF;

    RETURN jsonb_build_object('decision',v_dec,'input_ref',v_ref,'input_sha256',v_hash,'redaction_applied',v_red,
        'manifest',v_manifest,'exc_by',v_exc_by,'exc_reason',v_exc_reason,'att_id',v_att_id,'apr_id',v_apr_id,'blocked_reason',v_blk);
END;
$$;
ALTER FUNCTION _egress_decide(rfq_source_ingest, rfq_ai_provider, text, text, uuid, uuid, timestamptz) OWNER TO rfq_owner;

-- ---- begin_rfq_extraction: resolve trusted → egress decision → shell+attachment+run ----
CREATE OR REPLACE FUNCTION begin_rfq_extraction(
    p_source_ingest_id uuid, p_target text, p_provider_code text, p_model_code text, p_purpose_code text,
    p_attestation_id uuid, p_approval_id uuid, p_correlation jsonb,
    p_actor text, p_service text, p_request_id text
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    c_corr constant text[] := ARRAY['enquiry_ref','source_channel','source_channel_other'];
    c_policy constant text := 'rfq-egress-v1';
    src rfq_source_ingest%ROWTYPE; prov rfq_ai_provider%ROWTYPE;
    att rfq_redaction_attestation%ROWTYPE; apr rfq_egress_approval%ROWTYPE;
    v_dec text; v_blk text := NULL; v_ref text; v_hash char(64);
    v_red_applied boolean := false; v_manifest jsonb := '{}'::jsonb;
    v_exc_by text := NULL; v_exc_reason text := NULL; v_att_id uuid := NULL; v_apr_id uuid := NULL;
    v_rfq uuid; v_run uuid; v_att uuid; v_reqhash char(64); v_prev jsonb; v_prev_hash char(64); v_prev_actor text;
    hdr jsonb; v_ej jsonb;
BEGIN
    SELECT * INTO p_actor, p_service, p_request_id FROM _norm_ctx(p_actor, p_service, p_request_id);
    IF p_target NOT IN ('LOCAL','CLOUD') THEN RAISE EXCEPTION 'target ต้อง LOCAL|CLOUD' USING ERRCODE='22023'; END IF;
    -- B3.1: purpose ต้อง non-blank / no control char / ≤100 (กัน NULL bypass purpose binding)
    IF NULLIF(btrim(p_purpose_code, E' \t\n\r\f\v'), '') IS NULL
       OR length(p_purpose_code)>100 OR p_purpose_code ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'purpose_code ต้อง non-blank / no control char / ≤100' USING ERRCODE='22023'; END IF;
    hdr := COALESCE(p_correlation, '{}'::jsonb);
    PERFORM _reject_unknown_keys(hdr, c_corr, 'correlation');

    -- idempotency (BEGIN) — advisory lock + ledger
    v_reqhash := encode(sha256((p_source_ingest_id::text||'|'||p_target||'|'||coalesce(p_provider_code,'')||'|'||
                 coalesce(p_model_code,'')||'|'||coalesce(p_purpose_code,'')||'|'||coalesce(p_attestation_id::text,'')||'|'||
                 coalesce(p_approval_id::text,'')||'|'||hdr::text)::bytea),'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(p_service||':BEGIN:'||p_request_id, 0));
    SELECT outcome, payload_sha256, actor_ref INTO v_prev, v_prev_hash, v_prev_actor
        FROM rfq_extraction_request WHERE service=p_service AND operation_code='BEGIN' AND request_id=p_request_id;
    IF FOUND THEN
        IF v_prev_hash=v_reqhash AND v_prev_actor=p_actor THEN RETURN v_prev;
        ELSE RAISE EXCEPTION 'BEGIN request_id ซ้ำด้วย payload/actor ต่าง' USING ERRCODE='RFI01'; END IF;
    END IF;

    -- C1: trusted ref ไม่พบ = reject 23503 (ไม่สร้าง shell); provider ไม่พบ = 23503
    SELECT * INTO src FROM rfq_source_ingest WHERE id=p_source_ingest_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'source_ingest % ไม่พบ', p_source_ingest_id USING ERRCODE='23503'; END IF;
    SELECT * INTO prov FROM rfq_ai_provider WHERE provider_code=p_provider_code AND model_code=p_model_code;
    IF NOT FOUND THEN RAISE EXCEPTION 'provider %/% ไม่พบ', p_provider_code, p_model_code USING ERRCODE='23503'; END IF;

    -- egress decision ผ่าน helper เดียว (ใช้ชุดเดียวกับ claim — B3.2 กัน policy drift); min_valid=now() ที่ begin
    v_ej := _egress_decide(src, prov, p_target, p_purpose_code, p_attestation_id, p_approval_id, now());
    v_dec := v_ej->>'decision'; v_blk := v_ej->>'blocked_reason';
    v_ref := v_ej->>'input_ref'; v_hash := v_ej->>'input_sha256';
    v_red_applied := (v_ej->>'redaction_applied')::boolean; v_manifest := v_ej->'manifest';
    v_exc_by := v_ej->>'exc_by'; v_exc_reason := v_ej->>'exc_reason';
    v_att_id := (v_ej->>'att_id')::uuid; v_apr_id := (v_ej->>'apr_id')::uuid;

    -- สร้าง shell (DRAFT header-only) + attachment (copy trusted) + run
    INSERT INTO rfq (rfq_number_source, revision_no, is_current, status_code, row_version,
        enquiry_ref, source_channel, source_channel_other, created_by_ref, updated_by_ref)
    VALUES ('RFQ_ESTIMATE_API', 1, true, 'DRAFT', 1,
        hdr->>'enquiry_ref', hdr->>'source_channel', hdr->>'source_channel_other', p_actor, p_actor)
    RETURNING id INTO v_rfq;
    INSERT INTO rfq_status_history (rfq_id, from_status_code, to_status_code, changed_by_ref, reason)
    VALUES (v_rfq, NULL, 'DRAFT', p_actor, 'created via ENQ extraction begin ('||p_service||'/'||p_request_id||')');
    INSERT INTO rfq_attachment (rfq_id, purpose_code, original_filename, object_store_key, mime_type, size_bytes,
        sha256, classification_code, contains_personal_data, contains_trade_secret, cloud_action_code,
        classification_status, classified_by_ref, classified_at, malware_scan_status, uploaded_by_ref)
    VALUES (v_rfq, 'ENQUIRY', COALESCE(src.original_filename, 'enq-source'), src.object_store_key, src.mime_type, src.size_bytes,
        src.source_sha256, src.classification_code, src.contains_personal_data, src.contains_trade_secret,
        src.cloud_action_code, src.classification_status,
        CASE WHEN src.classification_status='CONFIRMED' THEN src.registered_by_ref END,
        CASE WHEN src.classification_status='CONFIRMED' THEN src.registered_at END,
        src.malware_scan_status, p_actor)
    RETURNING id INTO v_att;

    INSERT INTO rfq_ai_extraction_run (rfq_id, source_attachment_id, source_ingest_id, input_sha256,
        execution_target, provider_name, model_name, egress_policy_version, egress_decision_code,
        redaction_applied, redaction_manifest, exception_approved_by_ref, exception_reason,
        redaction_attestation_id, egress_approval_id, provider_input_ref, purpose_code, blocked_reason_code, status_code)
    VALUES (v_rfq, v_att, src.id, v_hash,
        p_target, p_provider_code, p_model_code, c_policy, v_dec,
        v_red_applied, v_manifest, v_exc_by, v_exc_reason,
        v_att_id, v_apr_id, v_ref, p_purpose_code, v_blk,
        CASE WHEN v_dec='BLOCKED' THEN 'BLOCKED' ELSE 'PENDING' END)
    RETURNING id INTO v_run;

    v_prev := jsonb_build_object('rfq_id', v_rfq, 'run_id', v_run, 'egress_decision_code', v_dec,
                                 'status', CASE WHEN v_dec='BLOCKED' THEN 'BLOCKED' ELSE 'PENDING' END);
    INSERT INTO rfq_extraction_request (service, operation_code, request_id, rfq_id, run_id, actor_ref, payload_sha256, outcome)
    VALUES (p_service, 'BEGIN', p_request_id, v_rfq, v_run, p_actor, v_reqhash, v_prev);
    RETURN v_prev;
END;
$$;
ALTER FUNCTION begin_rfq_extraction(uuid,text,text,text,text,uuid,uuid,jsonb,text,text,text) OWNER TO rfq_owner;

-- ---- claim_rfq_extraction: revalidate trusted (R1) + lease (จุดอนุมัติจริง) ----
CREATE OR REPLACE FUNCTION claim_rfq_extraction(
    p_run_id uuid, p_worker text, p_service text, p_request_id text
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    c_lease_min constant int := 10;
    r rfq_ai_extraction_run%ROWTYPE; src rfq_source_ingest%ROWTYPE; prov rfq_ai_provider%ROWTYPE;
    att rfq_redaction_attestation%ROWTYPE; apr rfq_egress_approval%ROWTYPE;
    v_blk text := NULL; v_lease uuid; v_out jsonb; v_prev jsonb; v_prev_hash char(64); v_prev_actor text; v_reqhash char(64);
    v_lease_exp timestamptz; v_ej jsonb;
BEGIN
    SELECT * INTO p_worker, p_service, p_request_id FROM _norm_ctx(p_worker, p_service, p_request_id);
    v_reqhash := encode(sha256((p_run_id::text)::bytea),'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(p_service||':CLAIM:'||p_request_id, 0));
    SELECT outcome, payload_sha256, actor_ref INTO v_prev, v_prev_hash, v_prev_actor
        FROM rfq_extraction_request WHERE service=p_service AND operation_code='CLAIM' AND request_id=p_request_id;
    IF FOUND THEN
        IF v_prev_hash=v_reqhash AND v_prev_actor=p_worker THEN RETURN v_prev;   -- exact replay (คืนก่อนตรวจ lease/status)
        ELSE RAISE EXCEPTION 'CLAIM request_id ซ้ำด้วย run/worker ต่าง' USING ERRCODE='RFI01'; END IF;
    END IF;

    -- lock order: parent rfq → run
    PERFORM 1 FROM rfq WHERE id=(SELECT rfq_id FROM rfq_ai_extraction_run WHERE id=p_run_id) FOR UPDATE;
    SELECT * INTO r FROM rfq_ai_extraction_run WHERE id=p_run_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run % ไม่พบ', p_run_id USING ERRCODE='RFN01'; END IF;

    IF r.status_code='RUNNING' AND r.lease_expires_at > now() THEN
        v_out := jsonb_build_object('should_execute', false, 'reason', 'active lease held', 'run_id', p_run_id);
        INSERT INTO rfq_extraction_request(service,operation_code,request_id,rfq_id,run_id,actor_ref,payload_sha256,outcome)
        VALUES (p_service,'CLAIM',p_request_id,r.rfq_id,p_run_id,p_worker,v_reqhash,v_out); RETURN v_out;
    END IF;
    IF r.status_code NOT IN ('PENDING','RUNNING') THEN
        RAISE EXCEPTION 'claim ทำได้เฉพาะ PENDING/expired-RUNNING (พบ %)', r.status_code USING ERRCODE='RFS01';
    END IF;

    -- R1/B3.2: re-evaluate egress ด้วย helper เดียว (min_valid ครอบ lease window) แล้วเทียบกับ run snapshot
    -- → จับ policy/cloud_action/attestation/approval/purpose drift หลัง begin ครบในชุดเดียว (ไม่มี logic ซ้ำ)
    v_lease_exp := now() + make_interval(mins => c_lease_min);
    SELECT * INTO src FROM rfq_source_ingest WHERE id=r.source_ingest_id;
    SELECT * INTO prov FROM rfq_ai_provider WHERE provider_code=r.provider_name AND model_code=r.model_name;
    IF src.id IS NULL THEN v_blk:='source missing at claim';
    ELSIF prov.provider_code IS NULL THEN v_blk:='provider missing at claim';
    ELSE
        v_ej := _egress_decide(src, prov, r.execution_target, r.purpose_code,
                               r.redaction_attestation_id, r.egress_approval_id, v_lease_exp);
        IF (v_ej->>'decision') IS DISTINCT FROM r.egress_decision_code
           OR (v_ej->>'input_sha256') IS DISTINCT FROM r.input_sha256
           OR (v_ej->>'input_ref') IS DISTINCT FROM r.provider_input_ref
           OR (v_ej->>'decision') = 'BLOCKED' THEN
            v_blk := COALESCE(v_ej->>'blocked_reason', 'egress state changed since begin');
        END IF;
    END IF;

    IF v_blk IS NOT NULL THEN
        UPDATE rfq_ai_extraction_run SET status_code='BLOCKED', blocked_reason_code=v_blk WHERE id=p_run_id;
        v_out := jsonb_build_object('should_execute', false, 'status', 'BLOCKED', 'reason', v_blk, 'run_id', p_run_id);
        INSERT INTO rfq_extraction_request(service,operation_code,request_id,rfq_id,run_id,actor_ref,payload_sha256,outcome)
        VALUES (p_service,'CLAIM',p_request_id,r.rfq_id,p_run_id,p_worker,v_reqhash,v_out); RETURN v_out;
    END IF;

    v_lease := gen_random_uuid();
    UPDATE rfq_ai_extraction_run SET status_code='RUNNING', lease_token=v_lease, claimed_by_ref=p_worker,
        claimed_service=p_service, lease_expires_at=v_lease_exp, attempt_no=attempt_no+1 WHERE id=p_run_id;
    v_out := jsonb_build_object('should_execute', true, 'lease_token', v_lease, 'provider_input_ref', r.provider_input_ref,
        'input_sha256', r.input_sha256, 'provider_code', r.provider_name, 'model_code', r.model_name,
        'execution_target', r.execution_target, 'run_id', p_run_id);
    INSERT INTO rfq_extraction_request(service,operation_code,request_id,rfq_id,run_id,actor_ref,payload_sha256,outcome)
    VALUES (p_service,'CLAIM',p_request_id,r.rfq_id,p_run_id,p_worker,v_reqhash,v_out);
    RETURN v_out;
END;
$$;
ALTER FUNCTION claim_rfq_extraction(uuid,text,text,text) OWNER TO rfq_owner;

-- ---- fail_rfq_extraction: RUNNING + lease ตรง + ไม่หมดอายุ (C4) → FAILED durable ----
CREATE OR REPLACE FUNCTION fail_rfq_extraction(
    p_run_id uuid, p_lease_token uuid, p_error_code text, p_actor text, p_service text, p_request_id text
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE r rfq_ai_extraction_run%ROWTYPE; v_out jsonb; v_prev jsonb; v_prev_hash char(64); v_prev_actor text; v_reqhash char(64);
BEGIN
    SELECT * INTO p_actor, p_service, p_request_id FROM _norm_ctx(p_actor, p_service, p_request_id);
    v_reqhash := encode(sha256((p_run_id::text||'|'||coalesce(p_error_code,''))::bytea),'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(p_service||':FAIL:'||p_request_id, 0));
    SELECT outcome, payload_sha256, actor_ref INTO v_prev, v_prev_hash, v_prev_actor
        FROM rfq_extraction_request WHERE service=p_service AND operation_code='FAIL' AND request_id=p_request_id;
    IF FOUND THEN   -- ledger-first (คืนก่อนตรวจ lease/status)
        IF v_prev_hash=v_reqhash AND v_prev_actor=p_actor THEN RETURN v_prev;
        ELSE RAISE EXCEPTION 'FAIL request_id ซ้ำด้วย run/actor ต่าง' USING ERRCODE='RFI01'; END IF;
    END IF;
    PERFORM 1 FROM rfq WHERE id=(SELECT rfq_id FROM rfq_ai_extraction_run WHERE id=p_run_id) FOR UPDATE;
    SELECT * INTO r FROM rfq_ai_extraction_run WHERE id=p_run_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run % ไม่พบ', p_run_id USING ERRCODE='RFN01'; END IF;
    -- C4/B6: RUNNING + lease token + actor + service ตรง + ยังไม่หมดอายุ
    IF r.status_code<>'RUNNING' OR r.lease_token IS DISTINCT FROM p_lease_token
       OR r.claimed_by_ref IS DISTINCT FROM p_actor OR r.claimed_service IS DISTINCT FROM p_service
       OR r.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'fail ต้อง RUNNING + lease/actor/service ตรง + ยังไม่หมดอายุ' USING ERRCODE='RFS01';
    END IF;
    UPDATE rfq_ai_extraction_run SET status_code='FAILED', error_code=p_error_code, completed_at=now() WHERE id=p_run_id;
    v_out := jsonb_build_object('status','FAILED','run_id',p_run_id);
    INSERT INTO rfq_extraction_request(service,operation_code,request_id,rfq_id,run_id,actor_ref,payload_sha256,outcome)
    VALUES (p_service,'FAIL',p_request_id,r.rfq_id,p_run_id,p_actor,v_reqhash,v_out);
    RETURN v_out;
END;
$$;
ALTER FUNCTION fail_rfq_extraction(uuid,uuid,text,text,text,text) OWNER TO rfq_owner;

-- ============================================================================
-- PART 3 — apply_rfq_extraction: tree + evidence (set equality) atomic
-- ============================================================================

-- ---- helper: collect .fields keys → written-set (reject JSON null — B4) ----
CREATE OR REPLACE FUNCTION _ext_collect(p_written jsonb, p_subject text, p_sid uuid, p_fields jsonb, p_row jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, rfq, pg_temp AS $c$
DECLARE k text; w jsonb := p_written;
BEGIN
    FOR k IN SELECT jsonb_object_keys(p_fields) LOOP
        IF jsonb_typeof(p_fields->k)='null' THEN
            RAISE EXCEPTION 'field %.% ห้ามเป็น JSON null (ใช้ omit-key)', p_subject, k USING ERRCODE='22023'; END IF;
        w := w || jsonb_build_object(p_subject||'|'||p_sid||'|'||k, p_row->k);
    END LOOP;
    RETURN w;
END;
$c$;
ALTER FUNCTION _ext_collect(jsonb,text,uuid,jsonb,jsonb) OWNER TO rfq_owner;

CREATE OR REPLACE FUNCTION apply_rfq_extraction(
    p_run_id uuid, p_lease_token uuid, p_payload jsonb, p_actor text, p_service text, p_request_id text
) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE
    c_max_bytes constant int := 1000000; c_max_items constant int := 100; c_max_child constant int := 200;
    c_top  constant text[] := ARRAY['schema_version','input_sha256','provider_result','header','items','evidence','clarifications'];
    c_hdr  constant text[] := ARRAY['customer_ref','customer_code_snapshot','customer_name_snapshot','customer_name_raw',
        'is_new_customer','contact_name','contact_phone','contact_email','sales_owner_ref','sales_owner_code_snapshot',
        'sales_owner_name_snapshot','customer_notes','quote_due_at','priority_code'];
    c_itemk constant text[] := ARRAY['line_no','fields','quantity_options','design_variants','components','processes','packings','deliveries'];
    c_itemf constant text[] := ARRAY['job_name','product_type_ref','product_type_code_snapshot','product_type_name_snapshot',
        'product_type_raw','description','intended_use','finished_width_mm','finished_length_mm','finished_depth_mm',
        'is_reprint','previous_job_ref','use_previous_plate','is_multiple_design','finishing_state','packing_state',
        'artwork_state','sample_state','sample_description','notes'];
    c_qtyf  constant text[] := ARRAY['quantity','unit_ref','unit_code_snapshot','unit_name_snapshot','unit_raw','is_primary','notes'];
    c_varf  constant text[] := ARRAY['design_code','quantity','unit_ref','unit_code_snapshot','notes'];
    c_compf constant text[] := ARRAY['component_name','component_type_ref','component_type_code_snapshot','component_type_name_snapshot',
        'component_type_raw','paper_ref','paper_code_snapshot','paper_name_snapshot','paper_gsm_snapshot','paper_source_code',
        'print_sides_code','color_outside_count','color_inside_count','ink_type_ref','ink_type_code_snapshot','box_template_ref',
        'box_template_code_snapshot','box_template_name_snapshot','box_width_mm','box_length_mm','box_depth_mm','flap_mm','glue_mm','tuck_mm','notes'];
    c_corrf constant text[] := ARRAY['corrugated_board_ref','corrugated_code_snapshot','corrugated_name_snapshot','layer_count_snapshot','flute_code_snapshot','notes'];
    c_procf constant text[] := ARRAY['process_ref','process_code_snapshot','process_name_snapshot','process_name_raw','option_ref',
        'option_code_snapshot','option_name_snapshot','option_name_raw','side_code','width_mm','height_mm','depth_mm',
        'color_ref','color_code_snapshot','color_name_snapshot','notes'];
    c_packf constant text[] := ARRAY['packing_ref','packing_code_snapshot','packing_name_snapshot','packing_name_raw','quantity_per_pack','unit_ref','unit_code_snapshot','specification'];
    c_dlvf  constant text[] := ARRAY['destination_ref','destination_code_snapshot','destination_name_snapshot','destination_raw','requested_date','quantity','unit_ref','unit_code_snapshot','is_split_delivery','notes'];
    c_evk   constant text[] := ARRAY['subject_type','ref','field_name','derivation_type','source_type','source_page','source_excerpt','confidence'];
    c_clrk  constant text[] := ARRAY['subject_type','ref','field_name','question','reason'];
    r rfq_ai_extraction_run%ROWTYPE;
    v_prev jsonb; v_prev_hash char(64); v_prev_actor text; v_reqhash char(64);
    v_rfq uuid; v_items jsonb; hdr jsonb; f jsonb; it jsonb; ch jsonb; comp jsonb; corr jsonb; ev jsonb; cl jsonb;
    v_item uuid; v_comp uuid; v_q uuid; v_row jsonb; k text; v_sid uuid; v_ref jsonb; v_att uuid;
    v_written jsonb := '{}'::jsonb; v_evseen jsonb := '{}'::jsonb; v_infer jsonb := '{}'::jsonb;
    v_clar jsonb := '{}'::jsonb; keystr text; v_deriv text; v_stype text; v_miss int;
BEGIN
    SELECT * INTO p_actor, p_service, p_request_id FROM _norm_ctx(p_actor, p_service, p_request_id);
    v_reqhash := encode(sha256((p_run_id::text||'|'||md5(p_payload::text))::bytea),'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended(p_service||':APPLY:'||p_request_id, 0));
    -- ledger-first idempotency (คืนก่อนตรวจ lease/status — C4)
    SELECT outcome, payload_sha256, actor_ref INTO v_prev, v_prev_hash, v_prev_actor
        FROM rfq_extraction_request WHERE service=p_service AND operation_code='APPLY' AND request_id=p_request_id;
    IF FOUND THEN
        IF v_prev_hash=v_reqhash AND v_prev_actor=p_actor THEN RETURN v_prev;
        ELSE RAISE EXCEPTION 'APPLY request_id ซ้ำด้วย payload/actor ต่าง' USING ERRCODE='RFI01'; END IF;
    END IF;

    PERFORM 1 FROM rfq WHERE id=(SELECT rfq_id FROM rfq_ai_extraction_run WHERE id=p_run_id) FOR UPDATE;
    SELECT * INTO r FROM rfq_ai_extraction_run WHERE id=p_run_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'run % ไม่พบ', p_run_id USING ERRCODE='RFN01'; END IF;
    -- C4/B6: RUNNING + lease token + actor + service ตรง + ยังไม่หมดอายุ
    IF r.status_code<>'RUNNING' OR r.lease_token IS DISTINCT FROM p_lease_token
       OR r.claimed_by_ref IS DISTINCT FROM p_actor OR r.claimed_service IS DISTINCT FROM p_service
       OR r.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'apply ต้อง RUNNING + lease/actor/service ตรง + ยังไม่หมดอายุ' USING ERRCODE='RFS01';
    END IF;
    v_rfq := r.rfq_id; v_att := r.source_attachment_id;

    -- envelope
    IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' THEN RAISE EXCEPTION 'payload ต้องเป็น object' USING ERRCODE='22023'; END IF;
    IF octet_length(p_payload::text) > c_max_bytes THEN RAISE EXCEPTION 'payload เกินขนาด' USING ERRCODE='54000'; END IF;
    IF p_payload->>'schema_version' <> 'extract-v1.1' THEN RAISE EXCEPTION 'schema_version ต้อง extract-v1.1' USING ERRCODE='22023'; END IF;
    IF p_payload->>'input_sha256' IS DISTINCT FROM r.input_sha256 THEN RAISE EXCEPTION 'input_sha256 ไม่ตรง run' USING ERRCODE='22023'; END IF;
    PERFORM _reject_unknown_keys(p_payload, c_top, 'payload');

    -- B5: ไม่ใช้ caller-visible temp table ใน SECURITY DEFINER — ใช้ jsonb accumulator แทน (กัน temp-table hijack)

    -- ---- header.fields → update shell + written-set (RFQ) ----
    hdr := p_payload->'header'; f := COALESCE(hdr->'fields','{}'::jsonb);
    IF hdr IS NOT NULL THEN PERFORM _reject_unknown_keys(hdr, ARRAY['fields'], 'header'); END IF;
    PERFORM _reject_unknown_keys(f, c_hdr, 'header.fields');
    UPDATE rfq SET
        customer_ref=COALESCE(f->>'customer_ref',customer_ref), customer_code_snapshot=COALESCE(f->>'customer_code_snapshot',customer_code_snapshot),
        customer_name_snapshot=COALESCE(f->>'customer_name_snapshot',customer_name_snapshot), customer_name_raw=COALESCE(f->>'customer_name_raw',customer_name_raw),
        is_new_customer=COALESCE((f->>'is_new_customer')::boolean,is_new_customer), contact_name=COALESCE(f->>'contact_name',contact_name),
        contact_phone=COALESCE(f->>'contact_phone',contact_phone), contact_email=COALESCE(f->>'contact_email',contact_email),
        sales_owner_ref=COALESCE(f->>'sales_owner_ref',sales_owner_ref), sales_owner_code_snapshot=COALESCE(f->>'sales_owner_code_snapshot',sales_owner_code_snapshot),
        sales_owner_name_snapshot=COALESCE(f->>'sales_owner_name_snapshot',sales_owner_name_snapshot), customer_notes=COALESCE(f->>'customer_notes',customer_notes),
        quote_due_at=COALESCE((f->>'quote_due_at')::timestamptz,quote_due_at), priority_code=COALESCE(f->>'priority_code',priority_code),
        updated_at=now(), updated_by_ref=p_actor
    WHERE id=v_rfq;
    SELECT to_jsonb(x.*) INTO v_row FROM rfq x WHERE x.id=v_rfq;
    v_written := _ext_collect(v_written, 'RFQ', v_rfq, f, v_row);

    -- ---- items + spec tree ----
    v_items := _child_array(p_payload,'items',c_max_items,'items');
    IF jsonb_array_length(v_items) < 1 THEN RAISE EXCEPTION 'ต้องมี item ≥1' USING ERRCODE='22023'; END IF;
    FOR it IN SELECT * FROM jsonb_array_elements(v_items) LOOP
        PERFORM _reject_unknown_keys(it, c_itemk, 'item');
        f := COALESCE(it->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_itemf, 'item.fields');
        INSERT INTO rfq_item (rfq_id, line_no, job_name, product_type_ref, product_type_code_snapshot,
            product_type_name_snapshot, product_type_raw, description, intended_use, finished_width_mm, finished_length_mm,
            finished_depth_mm, is_reprint, previous_job_ref, use_previous_plate, is_multiple_design,
            finishing_state, packing_state, artwork_state, sample_state, sample_description, notes)
        VALUES (v_rfq, (it->>'line_no')::smallint, f->>'job_name', f->>'product_type_ref', f->>'product_type_code_snapshot',
            f->>'product_type_name_snapshot', f->>'product_type_raw', f->>'description', f->>'intended_use',
            (f->>'finished_width_mm')::numeric, (f->>'finished_length_mm')::numeric, (f->>'finished_depth_mm')::numeric,
            COALESCE((f->>'is_reprint')::boolean,false), f->>'previous_job_ref', COALESCE((f->>'use_previous_plate')::boolean,false),
            COALESCE((f->>'is_multiple_design')::boolean,false), COALESCE(f->>'finishing_state','UNKNOWN'),
            COALESCE(f->>'packing_state','UNKNOWN'), COALESCE(f->>'artwork_state','UNKNOWN'), COALESCE(f->>'sample_state','UNKNOWN'),
            f->>'sample_description', f->>'notes')
        RETURNING id INTO v_item;
        SELECT to_jsonb(x.*) INTO v_row FROM rfq_item x WHERE x.id=v_item;
        v_written := _ext_collect(v_written, 'ITEM', v_item, f, v_row);

        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'quantity_options',c_max_child,'quantity_options')) LOOP
            PERFORM _reject_unknown_keys(ch, ARRAY['option_no','fields'], 'quantity_option');
            f := COALESCE(ch->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_qtyf, 'quantity_option.fields');
            INSERT INTO rfq_quantity_option (rfq_item_id, option_no, quantity, unit_ref, unit_code_snapshot, unit_name_snapshot, unit_raw, is_primary, notes)
            VALUES (v_item, (ch->>'option_no')::smallint, (f->>'quantity')::numeric, f->>'unit_ref', f->>'unit_code_snapshot',
                f->>'unit_name_snapshot', f->>'unit_raw', COALESCE((f->>'is_primary')::boolean,false), f->>'notes')
            RETURNING id INTO v_q;
            SELECT to_jsonb(x.*) INTO v_row FROM rfq_quantity_option x WHERE x.id=v_q;
            v_written := _ext_collect(v_written, 'QUANTITY', v_q, f, v_row);
        END LOOP;

        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'design_variants',c_max_child,'design_variants')) LOOP
            PERFORM _reject_unknown_keys(ch, ARRAY['variant_no','fields'], 'design_variant');
            f := COALESCE(ch->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_varf, 'design_variant.fields');
            INSERT INTO rfq_design_variant (rfq_item_id, variant_no, design_code, quantity, unit_ref, unit_code_snapshot, notes)
            VALUES (v_item, (ch->>'variant_no')::smallint, f->>'design_code', (f->>'quantity')::numeric, f->>'unit_ref', f->>'unit_code_snapshot', f->>'notes')
            RETURNING id INTO v_sid;
            SELECT to_jsonb(x.*) INTO v_row FROM rfq_design_variant x WHERE x.id=v_sid;
            v_written := _ext_collect(v_written, 'DESIGN_VARIANT', v_sid, f, v_row);
        END LOOP;

        FOR comp IN SELECT * FROM jsonb_array_elements(_child_array(it,'components',c_max_child,'components')) LOOP
            PERFORM _reject_unknown_keys(comp, ARRAY['component_no','fields','corrugated'], 'component');
            f := COALESCE(comp->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_compf, 'component.fields');
            INSERT INTO rfq_component (rfq_item_id, component_no, component_name, component_type_ref, component_type_code_snapshot,
                component_type_name_snapshot, component_type_raw, paper_ref, paper_code_snapshot, paper_name_snapshot, paper_gsm_snapshot,
                paper_source_code, print_sides_code, color_outside_count, color_inside_count, ink_type_ref, ink_type_code_snapshot,
                box_template_ref, box_template_code_snapshot, box_template_name_snapshot, box_width_mm, box_length_mm, box_depth_mm, flap_mm, glue_mm, tuck_mm, notes)
            VALUES (v_item, (comp->>'component_no')::smallint, f->>'component_name', f->>'component_type_ref', f->>'component_type_code_snapshot',
                f->>'component_type_name_snapshot', f->>'component_type_raw', f->>'paper_ref', f->>'paper_code_snapshot', f->>'paper_name_snapshot',
                (f->>'paper_gsm_snapshot')::numeric, f->>'paper_source_code', f->>'print_sides_code', (f->>'color_outside_count')::smallint,
                (f->>'color_inside_count')::smallint, f->>'ink_type_ref', f->>'ink_type_code_snapshot', f->>'box_template_ref',
                f->>'box_template_code_snapshot', f->>'box_template_name_snapshot', (f->>'box_width_mm')::numeric, (f->>'box_length_mm')::numeric,
                (f->>'box_depth_mm')::numeric, (f->>'flap_mm')::numeric, (f->>'glue_mm')::numeric, (f->>'tuck_mm')::numeric, f->>'notes')
            RETURNING id INTO v_comp;
            SELECT to_jsonb(x.*) INTO v_row FROM rfq_component x WHERE x.id=v_comp;
            v_written := _ext_collect(v_written, 'COMPONENT', v_comp, f, v_row);
            corr := comp->'corrugated';
            IF corr IS NOT NULL AND jsonb_typeof(corr)<>'null' THEN
                IF jsonb_typeof(corr)<>'object' THEN RAISE EXCEPTION 'corrugated ต้องเป็น object' USING ERRCODE='22023'; END IF;
                PERFORM _reject_unknown_keys(corr, ARRAY['fields'], 'corrugated');
                f := COALESCE(corr->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_corrf, 'corrugated.fields');
                INSERT INTO rfq_component_corrugated (rfq_component_id, corrugated_board_ref, corrugated_code_snapshot,
                    corrugated_name_snapshot, layer_count_snapshot, flute_code_snapshot, notes)
                VALUES (v_comp, f->>'corrugated_board_ref', f->>'corrugated_code_snapshot', f->>'corrugated_name_snapshot',
                    (f->>'layer_count_snapshot')::smallint, f->>'flute_code_snapshot', f->>'notes')
                RETURNING id INTO v_sid;
                SELECT to_jsonb(x.*) INTO v_row FROM rfq_component_corrugated x WHERE x.id=v_sid;
                v_written := _ext_collect(v_written, 'CORRUGATED', v_sid, f, v_row);
            END IF;
        END LOOP;

        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'processes',c_max_child,'processes')) LOOP
            PERFORM _reject_unknown_keys(ch, ARRAY['sequence_no','component_no','fields'], 'process');
            f := COALESCE(ch->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_procf, 'process.fields');
            v_comp := NULL;
            IF ch ? 'component_no' AND jsonb_typeof(ch->'component_no')<>'null' AND ch->>'component_no' IS NOT NULL THEN
                SELECT id INTO v_comp FROM rfq_component WHERE rfq_item_id=v_item AND component_no=(ch->>'component_no')::smallint;
                IF v_comp IS NULL THEN RAISE EXCEPTION 'process.component_no % ไม่พบใน item', ch->>'component_no' USING ERRCODE='RFR01'; END IF;
            END IF;
            INSERT INTO rfq_process_requirement (rfq_item_id, rfq_component_id, sequence_no, process_ref, process_code_snapshot,
                process_name_snapshot, process_name_raw, option_ref, option_code_snapshot, option_name_snapshot, option_name_raw,
                side_code, width_mm, height_mm, depth_mm, color_ref, color_code_snapshot, color_name_snapshot, notes)
            VALUES (v_item, v_comp, (ch->>'sequence_no')::smallint, f->>'process_ref', f->>'process_code_snapshot',
                f->>'process_name_snapshot', f->>'process_name_raw', f->>'option_ref', f->>'option_code_snapshot',
                f->>'option_name_snapshot', f->>'option_name_raw', f->>'side_code', (f->>'width_mm')::numeric, (f->>'height_mm')::numeric,
                (f->>'depth_mm')::numeric, f->>'color_ref', f->>'color_code_snapshot', f->>'color_name_snapshot', f->>'notes')
            RETURNING id INTO v_sid;
            SELECT to_jsonb(x.*) INTO v_row FROM rfq_process_requirement x WHERE x.id=v_sid;
            v_written := _ext_collect(v_written, 'PROCESS', v_sid, f, v_row);
            -- relationship key (R4/C2): component_no → natural key ของ target
            IF v_comp IS NOT NULL THEN
                v_written := v_written || jsonb_build_object('PROCESS|'||v_sid||'|component_no', to_jsonb((ch->>'component_no')::smallint)); END IF;
        END LOOP;

        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'packings',c_max_child,'packings')) LOOP
            PERFORM _reject_unknown_keys(ch, ARRAY['sequence_no','fields'], 'packing');
            f := COALESCE(ch->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_packf, 'packing.fields');
            INSERT INTO rfq_packing_requirement (rfq_item_id, sequence_no, packing_ref, packing_code_snapshot, packing_name_snapshot,
                packing_name_raw, quantity_per_pack, unit_ref, unit_code_snapshot, specification)
            VALUES (v_item, (ch->>'sequence_no')::smallint, f->>'packing_ref', f->>'packing_code_snapshot', f->>'packing_name_snapshot',
                f->>'packing_name_raw', (f->>'quantity_per_pack')::numeric, f->>'unit_ref', f->>'unit_code_snapshot', f->>'specification')
            RETURNING id INTO v_sid;
            SELECT to_jsonb(x.*) INTO v_row FROM rfq_packing_requirement x WHERE x.id=v_sid;
            v_written := _ext_collect(v_written, 'PACKING', v_sid, f, v_row);
        END LOOP;

        FOR ch IN SELECT * FROM jsonb_array_elements(_child_array(it,'deliveries',c_max_child,'deliveries')) LOOP
            PERFORM _reject_unknown_keys(ch, ARRAY['delivery_no','option_no','fields'], 'delivery');
            f := COALESCE(ch->'fields','{}'::jsonb); PERFORM _reject_unknown_keys(f, c_dlvf, 'delivery.fields');
            v_q := NULL;
            IF ch ? 'option_no' AND jsonb_typeof(ch->'option_no')<>'null' AND ch->>'option_no' IS NOT NULL THEN
                SELECT id INTO v_q FROM rfq_quantity_option WHERE rfq_item_id=v_item AND option_no=(ch->>'option_no')::smallint;
                IF v_q IS NULL THEN RAISE EXCEPTION 'delivery.option_no % ไม่พบใน item', ch->>'option_no' USING ERRCODE='RFR01'; END IF;
            END IF;
            INSERT INTO rfq_delivery (rfq_item_id, quantity_option_id, delivery_no, destination_ref, destination_code_snapshot,
                destination_name_snapshot, destination_raw, requested_date, quantity, unit_ref, unit_code_snapshot, is_split_delivery, notes)
            VALUES (v_item, v_q, (ch->>'delivery_no')::smallint, f->>'destination_ref', f->>'destination_code_snapshot',
                f->>'destination_name_snapshot', f->>'destination_raw', (f->>'requested_date')::date, (f->>'quantity')::numeric,
                f->>'unit_ref', f->>'unit_code_snapshot', COALESCE((f->>'is_split_delivery')::boolean,false), f->>'notes')
            RETURNING id INTO v_sid;
            SELECT to_jsonb(x.*) INTO v_row FROM rfq_delivery x WHERE x.id=v_sid;
            v_written := _ext_collect(v_written, 'DELIVERY', v_sid, f, v_row);
            IF v_q IS NOT NULL THEN
                v_written := v_written || jsonb_build_object('DELIVERY|'||v_sid||'|option_no', to_jsonb((ch->>'option_no')::smallint)); END IF;
        END LOOP;
    END LOOP;

    -- ---- evidence: extra-check + B4 (AI derivation, source_type required, no default) + insert + accumulate ----
    FOR ev IN SELECT * FROM jsonb_array_elements(COALESCE(p_payload->'evidence','[]'::jsonb)) LOOP
        PERFORM _reject_unknown_keys(ev, c_evk, 'evidence');
        v_sid := _resolve_subject(v_rfq, v_item, ev->>'subject_type', ev->'ref');
        keystr := (ev->>'subject_type') || '|' || v_sid || '|' || (ev->>'field_name');
        IF NOT (v_written ? keystr) THEN
            RAISE EXCEPTION 'evidence อ้าง field ที่ไม่ได้เขียน (%)', keystr USING ERRCODE='RFR01'; END IF;
        v_deriv := COALESCE(ev->>'derivation_type','AI_EXTRACTED');
        IF v_deriv NOT IN ('AI_EXTRACTED','AI_INFERENCE') THEN
            RAISE EXCEPTION 'apply evidence ต้องเป็น AI derivation (พบ %)', v_deriv USING ERRCODE='RFR01'; END IF;
        v_stype := ev->>'source_type';
        IF v_stype IS NULL THEN RAISE EXCEPTION 'evidence.source_type ต้องระบุ (ห้าม default)' USING ERRCODE='22023'; END IF;
        INSERT INTO rfq_field_evidence (rfq_id, subject_type, subject_id, field_name, value_snapshot, source_type,
            extraction_run_id, source_attachment_id, derivation_type, source_page, source_excerpt, confidence)
        VALUES (v_rfq, ev->>'subject_type', v_sid, ev->>'field_name', v_written->keystr, v_stype,
            p_run_id, v_att, v_deriv, (ev->>'source_page')::int, ev->>'source_excerpt', (ev->>'confidence')::numeric);
        v_evseen := v_evseen || jsonb_build_object(keystr, true);
        IF v_deriv='AI_INFERENCE' THEN v_infer := v_infer || jsonb_build_object(keystr, true); END IF;
    END LOOP;

    -- ---- set equality: ทุก written field ต้องมี evidence (F4/R3/R4) ----
    SELECT count(*) INTO v_miss FROM jsonb_object_keys(v_written) w WHERE NOT (v_evseen ? w);
    IF v_miss > 0 THEN RAISE EXCEPTION 'AI-written field ไม่มี evidence (% field)', v_miss USING ERRCODE='RFR01'; END IF;

    -- ---- clarifications (question/reason only — R5) + AI_INFERENCE ต้องมี clarification subject+field เดียวกัน (B4) ----
    FOR cl IN SELECT * FROM jsonb_array_elements(COALESCE(p_payload->'clarifications','[]'::jsonb)) LOOP
        PERFORM _reject_unknown_keys(cl, c_clrk, 'clarification');
        v_sid := _resolve_subject(v_rfq, v_item, cl->>'subject_type', cl->'ref');
        v_clar := v_clar || jsonb_build_object((cl->>'subject_type')||'|'||v_sid||'|'||(cl->>'field_name'), true);
        INSERT INTO rfq_clarification (rfq_id, subject_type, subject_id, field_name, question, reason, is_blocking, raised_by_type, raised_by_ref)
        VALUES (v_rfq, cl->>'subject_type', v_sid, cl->>'field_name', cl->>'question', cl->>'reason', true, 'AI', p_actor);
    END LOOP;
    SELECT count(*) INTO v_miss FROM jsonb_object_keys(v_infer) i WHERE NOT (v_clar ? i);
    IF v_miss > 0 THEN RAISE EXCEPTION 'AI_INFERENCE ต้องมี blocking clarification subject+field เดียวกัน (% field)', v_miss USING ERRCODE='RFR01'; END IF;

    UPDATE rfq_ai_extraction_run SET status_code='SUCCEEDED', completed_at=now() WHERE id=p_run_id;
    v_prev := jsonb_build_object('rfq_id', v_rfq, 'run_id', p_run_id, 'status', 'SUCCEEDED');
    INSERT INTO rfq_extraction_request (service, operation_code, request_id, rfq_id, run_id, actor_ref, payload_sha256, outcome)
    VALUES (p_service, 'APPLY', p_request_id, v_rfq, p_run_id, p_actor, v_reqhash, v_prev);
    RETURN v_prev;
END;
$$;
ALTER FUNCTION apply_rfq_extraction(uuid,uuid,jsonb,text,text,text) OWNER TO rfq_owner;

-- ---- helper: resolve subject natural-ref → UUID (ตรวจ membership โดย trigger เดิมตอน insert evidence) ----
CREATE OR REPLACE FUNCTION _resolve_subject(p_rfq uuid, p_item_ignored uuid, p_subject text, p_ref jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, rfq, pg_temp AS $$
DECLARE v_id uuid; v_item uuid; v_ln smallint;
BEGIN
    IF p_subject='RFQ' THEN RETURN p_rfq; END IF;
    v_ln := (p_ref->>'line_no')::smallint;
    SELECT id INTO v_item FROM rfq_item WHERE rfq_id=p_rfq AND line_no=v_ln;
    IF v_item IS NULL THEN RAISE EXCEPTION 'ref line_no % ไม่พบ', v_ln USING ERRCODE='RFR01'; END IF;
    CASE p_subject
        WHEN 'ITEM' THEN RETURN v_item;
        WHEN 'QUANTITY' THEN SELECT id INTO v_id FROM rfq_quantity_option WHERE rfq_item_id=v_item AND option_no=(p_ref->>'option_no')::smallint;
        WHEN 'DESIGN_VARIANT' THEN SELECT id INTO v_id FROM rfq_design_variant WHERE rfq_item_id=v_item AND variant_no=(p_ref->>'variant_no')::smallint;
        WHEN 'COMPONENT' THEN SELECT id INTO v_id FROM rfq_component WHERE rfq_item_id=v_item AND component_no=(p_ref->>'component_no')::smallint;
        WHEN 'CORRUGATED' THEN SELECT cc.id INTO v_id FROM rfq_component_corrugated cc JOIN rfq_component c ON c.id=cc.rfq_component_id
            WHERE c.rfq_item_id=v_item AND c.component_no=(p_ref->>'component_no')::smallint;
        WHEN 'PROCESS' THEN SELECT id INTO v_id FROM rfq_process_requirement WHERE rfq_item_id=v_item AND sequence_no=(p_ref->>'sequence_no')::smallint;
        WHEN 'PACKING' THEN SELECT id INTO v_id FROM rfq_packing_requirement WHERE rfq_item_id=v_item AND sequence_no=(p_ref->>'sequence_no')::smallint;
        WHEN 'DELIVERY' THEN SELECT id INTO v_id FROM rfq_delivery WHERE rfq_item_id=v_item AND delivery_no=(p_ref->>'delivery_no')::smallint;
        ELSE RAISE EXCEPTION 'subject_type % ไม่รองรับ', p_subject USING ERRCODE='22023';
    END CASE;
    IF v_id IS NULL THEN RAISE EXCEPTION 'ref %/% ไม่พบ subject', p_subject, p_ref USING ERRCODE='RFR01'; END IF;
    RETURN v_id;
END;
$$;
ALTER FUNCTION _resolve_subject(uuid,uuid,text,jsonb) OWNER TO rfq_owner;

-- ---- H1: กัน RFQ ที่มี extraction run ยังไม่ SUCCEEDED เข้า READY_FOR_ESTIMATE ----
-- (blocked/failed/pending/running extraction shell จะเข้า Ready path ไม่ได้; manual draft ไม่มี run → ผ่าน)
CREATE OR REPLACE FUNCTION rfq_block_ready_open_extraction()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, rfq, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM rfq_ai_extraction_run WHERE rfq_id=NEW.id AND status_code <> 'SUCCEEDED') THEN
        RAISE EXCEPTION 'RFQ มี extraction run ที่ยังไม่ SUCCEEDED — เข้า READY_FOR_ESTIMATE ไม่ได้ (H1)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
ALTER FUNCTION rfq_block_ready_open_extraction() OWNER TO rfq_owner;
CREATE TRIGGER trg_rfq_block_ready_open_extraction
    BEFORE UPDATE OF status_code ON rfq
    FOR EACH ROW WHEN (NEW.status_code = 'READY_FOR_ESTIMATE')
    EXECUTE FUNCTION rfq_block_ready_open_extraction();

-- ---- grants: EXECUTE เฉพาะ rfq_ingest; helper hidden ----
REVOKE ALL ON FUNCTION _norm_ctx(text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION _ext_collect(jsonb,text,uuid,jsonb,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION _egress_decide(rfq_source_ingest, rfq_ai_provider, text, text, uuid, uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION rfq_block_ready_open_extraction() FROM PUBLIC;   -- trigger fn: ไม่ให้โผล่ใน effective allowlist
REVOKE ALL ON FUNCTION _resolve_subject(uuid,uuid,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION begin_rfq_extraction(uuid,text,text,text,text,uuid,uuid,jsonb,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_rfq_extraction(uuid,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION apply_rfq_extraction(uuid,uuid,jsonb,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION fail_rfq_extraction(uuid,uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION begin_rfq_extraction(uuid,text,text,text,text,uuid,uuid,jsonb,text,text,text) TO rfq_ingest;
GRANT EXECUTE ON FUNCTION claim_rfq_extraction(uuid,text,text,text) TO rfq_ingest;
GRANT EXECUTE ON FUNCTION apply_rfq_extraction(uuid,uuid,jsonb,text,text,text) TO rfq_ingest;
GRANT EXECUTE ON FUNCTION fail_rfq_extraction(uuid,uuid,text,text,text,text) TO rfq_ingest;

COMMIT;
