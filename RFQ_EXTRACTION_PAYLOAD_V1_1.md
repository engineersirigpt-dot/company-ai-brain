# ENQ Extraction Provenance Contract — `extract-v1.1` (rev 3, post 2nd Codex review)

Contract + state machine ของเส้นทาง **AI ENQ → RFQ พร้อม field-level evidence**
สถานะ: **DESIGN — รอ Codex confirm (implement-ready) ก่อนเขียน SQL** — rev 3 ปิด R1-R8 จาก `CODEX_ENQ_V1_1_REV2_REVIEW.md`
(rev 2 ผ่าน architecture; rev 3 = implementability fix ไม่มี table/architecture ใหม่ เพิ่มเฉพาะ column artifact/revocation + แก้ behavior)

> หลักการที่ DB ต้องบังคับ:
> `trusted source state → egress decision (begin) → **re-validate + lease (claim = จุดอนุมัติจริง)** → authorized provider attempt → RFQ writes + evidence (atomic)`
> caller ส่งได้แค่ **ID ชี้ trusted record** — DB resolve ค่าจริงเอง; ห้าม caller ยืนยัน scan/classification/decision/approval/artifact เอง

---

## 0. โมเดลความเชื่อถือ (F1/R7) — ใครเขียนอะไร

| Trusted record | writer (capability/role) | `rfq_ingest` (extraction role) |
|---|---|---|
| `rfq_source_ingest` (scan+classify) | scanner/classifier | **ไม่มี direct privilege** |
| `rfq_ai_provider` (allowlist) | admin/config | **ไม่มี direct privilege** |
| `rfq_redaction_attestation` | redaction gateway | **ไม่มี direct privilege** |
| `rfq_egress_approval` | approval capability | **ไม่มี direct privilege** |

**R7 — permission wording ที่ถูกต้อง:** `rfq_ingest` **ไม่มี direct table privilege (ทั้ง SELECT/DML)** บน trusted tables;
อ่าน trusted state ได้เฉพาะ**ผลที่ SECURITY DEFINER function resolve แล้วคืนตาม run** → credential extraction ที่รั่ว **enumerate object key/approval ไม่ได้**

> **PoC decision (Codex ตอบข้อ 3):** รอบ prototype **seed trusted tables ด้วย privileged owner มือได้** เฉพาะ synthetic/redacted — โดยมีเงื่อนไข:
> (ก) `rfq_ingest` ไม่มี direct DML/SELECT บน trusted tables (ข) fixtures ระบุชัดว่า **ไม่ใช่หลักฐาน scan/redaction production** (ค) **ห้ามส่ง raw production RFQ ไป Cloud จาก record ที่ seed มือ**
> ก่อน Cloud pilot จริงต้องมี dedicated writer capability/role + audit (ยังไม่ทำใน 007)

## 1. State machine (F6/R1/R6)

```
begin_rfq_extraction(source_ingest_id, target, provider, model, purpose, attestation_id?, approval_id?, correlation, actor, service, request_id)
   │  resolve trusted state → egress decision (total §5) → เลือก provider_input_ref + input_sha256 (§6)
   ├── ไม่ผ่าน → status=BLOCKED  ── terminal (durable audit, ไม่มี lease, ไม่เรียก provider)
   └── ผ่าน → status=PENDING (queued; ยังไม่ใช่การอนุมัติให้เรียก provider)
           │
   claim_rfq_extraction(run_id, worker, service, request_id)   ← จุดอนุมัติ egress จริง (R1)
           │  lock RFQ→run → **re-resolve** source/provider/attestation/approval + policy version
           ├── invalid/revoked/expiring-ก่อน-lease → status=BLOCKED + stable error → should_execute=false
           ├── มี lease active ของ worker อื่น → should_execute=false (ไม่เรียกซ้ำ)
           └── ผ่าน → status=RUNNING (lease_token, claimed_by, lease_expires_at=now()+10min, attempt_no++)
                     คืน {should_execute=true, lease_token, provider_input_ref, input_sha256, provider, model, target}
           │
   ── Phase B: provider call นอก DB (เฉพาะผู้ถือ lease; app timeout ≤8 นาที < lease) ──
           │
   ┌───────┴────────┐
apply_rfq_extraction(run,lease,...)     fail_rfq_extraction(run,lease,error)
(RUNNING + lease ตรง)                    (RUNNING + lease ตรง)
   │                                        │
status=SUCCEEDED (terminal)              status=FAILED (terminal)
(tree + evidence atomic, §7)
```

**semantics ที่ล็อก (R6):**
- terminal = `SUCCEEDED | FAILED | BLOCKED` → **execution retry ใน run เดิมไม่ได้** (re-extract = capability แยก, out of scope)
- `fail` รับเฉพาะ `RUNNING + valid lease` (PENDING ไม่มี lease จึง fail ไม่ได้)
- **PENDING ที่ไม่เคย claim = queued state**; cleanup ทำโดย operational sweeper/admin expiry แยก (ไม่ปลอมว่าปิดด้วย lease)
- **reclaim ของ RUNNING ที่ lease หมดอายุ** ต้องใช้ **claim request_id ใหม่** (attempt_no++); exact replay ของ claim เดิม → คืน outcome เดิมจาก ledger (ไม่ reclaim)
- lease รับประกันแค่ "**ผู้ถือสิทธิ์ active หนึ่งราย**" ไม่การันตี provider exactly-once หลัง lease หมด → ใช้ `lease_token`/`attempt_no` เป็น **fencing** + ส่ง provider idempotency key เมื่อ provider รองรับ
- lock order (กัน deadlock/TOCTOU): **resolve run id → lock parent RFQ → lock run → recheck → write**
- `apply`/`fail` bind `(run_id, lease_token, service)` ไม่พึ่ง run UUID อย่างเดียว

## 2. Trusted tables (007; ไม่มี table ใหม่เกิน 5 ตัว — เพิ่มเฉพาะ column artifact/revocation)

**`rfq_source_ingest`** (immutable-หลังสร้าง; revoke ได้):
`id, object_store_key, original_filename, mime_type, size_bytes, source_sha256,`
`malware_scan_status(PENDING|CLEAN|BLOCKED|ERROR), classification_status(PENDING|CONFIRMED|REJECTED),`
`classification_code(UNCLASSIFIED|INTERNAL|CONFIDENTIAL|RESTRICTED), contains_personal_data bool, contains_trade_secret bool,`
`cloud_action_code(ALLOW|REDACT|LOCAL_ONLY|BLOCK), policy_version, registered_by_ref, registered_at,`
`is_active bool DEFAULT true, revoked_at timestamptz`   ← **R1**

**`rfq_ai_provider`**: `provider_code, model_code, execution_target(LOCAL|CLOUD), is_active, policy_version, PK(provider_code, model_code)`

**`rfq_redaction_attestation`** (R2 — มี artifact ref จริง):
`id, source_ingest_id, purpose_code, redactor_ref, redactor_version, source_sha256 (=source_ingest.source_sha256),`
`redacted_sha256, redacted_object_store_key (artifact ที่อนุญาตให้ส่ง — immutable), redaction_manifest jsonb (non-empty),`
`created_at, expires_at, is_active bool DEFAULT true, revoked_at timestamptz`

**`rfq_egress_approval`**:
`id, source_ingest_id, provider_code, model_code, purpose_code, approved_by_ref, reason(non-empty),`
`created_at, expires_at, is_active bool DEFAULT true, revoked_at timestamptz`

**ALTER `rfq_ai_extraction_run`:** +`RUNNING` status, +lease (`lease_token/claimed_by_ref/lease_expires_at/attempt_no`),
+`source_ingest_id`, +`redaction_attestation_id?`, +`egress_approval_id?`, +**`provider_input_ref` + `input_sha256`** (R2), +`blocked_reason_code`

**ALTER `rfq_field_evidence`:** +`derivation_type(HUMAN_EXTRACTED|AI_EXTRACTED|AI_INFERENCE|SYSTEM_RESOLVED)`;
CHECK ใหม่: `derivation_type IN ('AI_EXTRACTED','AI_INFERENCE') → extraction_run_id IS NOT NULL`; **ถอด CHECK เดิมที่อิง `source_type='AI_INFERENCE'`** (F5/R5-ล้าง `source_type='AI_INFERENCE'` value เดิม → เป็น medium ล้วน)

**`rfq_extraction_request`** (ledger): `PK(service, operation_code, request_id)` + `run_id, rfq_id, actor_ref, payload_sha256, outcome jsonb, created_at`

## 3. Phase A — `begin_rfq_extraction(...)`
- input: `p_source_ingest_id, p_target(LOCAL|CLOUD), p_provider_code, p_model_code, p_purpose_code, p_attestation_id?, p_approval_id?,`
  `p_correlation jsonb` (**เฉพาะ** `enquiry_ref, source_channel, source_channel_other`), `p_actor/p_service/p_request_id`
- resolve trusted state (source active+ไม่ revoked, provider active+target ตรง+policy ตรง) → egress decision (§5)
- **เลือก provider input ตาม decision (R2):**
  - `LOCAL_ONLY` → `provider_input_ref := source.object_store_key`, `input_sha256 := source.source_sha256`
  - `REDACTED_ALLOW` → resolve attestation (active, source_sha256 ตรง, ไม่หมดอายุ, manifest ไม่ว่าง) → `provider_input_ref := attestation.redacted_object_store_key`, `input_sha256 := attestation.redacted_sha256`
  - `APPROVED_EXCEPTION` → resolve approval (active, ตรง source/provider/purpose, ไม่หมดอายุ) → `provider_input_ref := source.object_store_key`, `input_sha256 := source.source_sha256`
- สร้าง shell (`DRAFT`, rev1, is_current, row_version=1) + DRAFT history + correlation; สร้าง `rfq_attachment` copy จาก source_ingest (trusted)
- insert run: ผ่าน→`PENDING` (+decision +provider_input_ref +input_sha256 +ids); ไม่ผ่าน→`BLOCKED` (+blocked_reason_code)
- คืน `{rfq_id, run_id, egress_decision_code, status}`
- idempotency: ledger `(service,'BEGIN',request_id)` → replay คืน `{rfq_id, run_id}` เดิม; payload/actor ต่าง → conflict

## 4. Phase claim / B / apply / fail
- **`claim_rfq_extraction(p_run_id, p_worker, p_service, p_request_id)`** (R1 = จุดอนุมัติจริง):
  lock RFQ→run; ต้อง `PENDING` หรือ (`RUNNING` + lease หมดอายุ); **re-resolve** source/provider/attestation/approval + policy version;
  require `attestation/approval.expires_at >= now()+10min` (ครอบ lease window); ถ้า invalid/revoked/expiring → run→`BLOCKED` + `blocked_reason_code`, `should_execute=false`;
  ถ้า valid → `RUNNING` + lease (10 นาที) + attempt_no++; คืน `{should_execute=true, lease_token, provider_input_ref, input_sha256, provider_code, model_code, execution_target}`
- **Phase B (นอก DB):** เฉพาะผู้ถือ lease โหลด artifact ตาม `provider_input_ref` (DB เลือกให้ — caller ไม่เลือกเอง) แล้วเรียก provider; `CLOUD` ส่งได้เฉพาะ bytes ที่ hash = `input_sha256`
- **`apply_rfq_extraction(p_run_id, p_lease_token, p_payload, p_actor, p_service, p_request_id)`**: lock RFQ→run; `RUNNING` + lease ตรง; `payload.input_sha256 = run.input_sha256`; สร้าง tree + evidence; completeness §7; run→`SUCCEEDED`; atomic
- **`fail_rfq_extraction(p_run_id, p_lease_token, p_error_code, p_actor, p_service, p_request_id)`**: `RUNNING` + lease ตรง; run→`FAILED`; ห้ามเก็บ raw provider response ที่มีข้อมูลลับ
- idempotency ทุก op: **ตรวจ ledger ก่อน terminal-state rejection** (F7); exact replay → outcome เดิม; actor/payload ต่าง → conflict

## 5. Egress decision table (F3 — total; ค่าจาก trusted record)
| # | เงื่อนไข | decision |
|---|---|---|
| 1 | source ไม่พบ/`is_active=false`/revoked, `malware<>'CLEAN'`, `classification_status<>'CONFIRMED'`, `classification_code='UNCLASSIFIED'`, `contains_personal_data IS NULL` หรือ `contains_trade_secret IS NULL`, provider ไม่ active/target ไม่ตรง/policy ไม่ตรง | **BLOCKED** |
| 2 | `CLOUD` + `cloud_action_code IN ('BLOCK','LOCAL_ONLY')` | **BLOCKED** |
| 3 | `CLOUD` + `REDACT` + valid attestation | **REDACTED_ALLOW** |
| 4 | `CLOUD` + `ALLOW` + valid approval | **APPROVED_EXCEPTION** |
| 5 | `LOCAL` + precondition (แถว 1) ผ่าน | **LOCAL_ONLY** |
| 6 | อื่น ๆ ทั้งหมด | **BLOCKED** |

**LOCAL+BLOCK (F3):** `cloud_action_code` = cloud-scope → **LOCAL_ONLY อนุญาตแม้ BLOCK** ถ้า precondition ผ่าน; Local-deny = แกน `processing_action_code` ภายหลัง (นอก v1.1)

## 6. Provider input binding (R2) + redaction ก่อน call (F2)
- redaction + attestation เกิด**ก่อน** `begin`/provider call; `begin` resolve attestation ที่มีอยู่แล้ว
- run เก็บ `provider_input_ref` + `input_sha256` ที่ **DB เลือก** (§3) — worker ไม่เลือก artifact เอง
- `apply` ตรวจ `payload.input_sha256 = run.input_sha256`; **ไม่รับ manifest/hash ใหม่**เพื่อ legitimize คำขอที่ส่งไปแล้ว
- DB ยืนยัน: attestation ครบ + hash/artifact binding + ผู้สร้าง record; **redact จริง = redaction gateway**

## 7. Evidence completeness = set equality (F4/R3/R4/R5)

**AI-written set** = ทุก **business field key** ที่ปรากฏจริงใน `apply.payload` (รวม `header.fields` + `items[].fields` + child `[].fields`) หลังตัด **structural key ราย subject** (R4):

| subject | structural key (ตัดออก) | relationship key (**อยู่ใน AI-written set, ต้องมี evidence**) |
|---|---|---|
| RFQ | — (ref = `RFQ`) | — |
| ITEM | `line_no` | — |
| QUANTITY | `line_no, option_no` | — |
| DESIGN_VARIANT | `line_no, variant_no` | — |
| COMPONENT | `line_no, component_no` | — |
| CORRUGATED | `line_no, component_no` | — |
| PROCESS | `line_no, sequence_no` | **`component_no`** (AI เลือกผูก process↔component) |
| PACKING | `line_no, sequence_no` | — |
| DELIVERY | `line_no, delivery_no` | **`option_no`** (AI เลือก quantity option) |

กติกา:
- field ไม่ทราบค่า → **omit key** (ห้าม JSON null แทน UNKNOWN)
- DB สร้าง tuple `(subject_type, natural_ref, field_name)` จาก key set → **set equality** กับ evidence: ทุก field ที่ปรากฏต้องมี evidence ≥1; **evidence ห้ามอ้าง field ที่ไม่ได้เขียน**
- `value_snapshot`: DB สร้าง/เทียบจาก `to_jsonb(actual_column)` หลัง typed cast
- evidence.source_attachment ต้องเป็น attachment **ของ run นี้** (source_ingest เดียวกัน)
- **clarification (R5):** field ไม่ทราบค่า → clarification เก็บแค่ `question/reason` (ไม่มี `assumed_value`, ไม่มี orphan evidence);
  ถ้าจะเก็บค่าที่สมมติ → **เขียนค่าใน `fields` + AI_INFERENCE evidence (ตาม set equality) + blocking clarification ที่อ้าง subject+field เดียวกัน** → ไม่มี evidence ที่ไม่มี actual snapshot ให้ตรวจ

## 8. Derivation (F5) — แยกผู้เขียนจาก medium
`rfq_field_evidence.derivation_type`: `AI_EXTRACTED`/`AI_INFERENCE` → require run; evidence จาก `apply` = AI + run นี้เสมอ;
`source_type` = medium ล้วน (`PDF|EMAIL|IMAGE|...`) → **manual PDF (HUMAN_EXTRACTED) ไม่ต้องมี run**; 007 ล้างค่า `source_type='AI_INFERENCE'` เดิมออกจาก enum

## 9. Unknown / assumption (สอดคล้อง R5)
- ไม่มีหลักฐาน → omit key
- ต้องสมมติ → เขียนค่าใน `fields` + `AI_INFERENCE` evidence + blocking clarification (subject+field เดียวกัน)
- `UNKNOWN` state ห้ามกลายเป็น `NONE`

## 10. Idempotency ledger (F7) — `rfq_extraction_request`
`PK(service, operation_code, request_id)` ใช้กับ `BEGIN|CLAIM|APPLY|FAIL`; ตรวจ ledger ก่อน terminal reject; exact replay→outcome เดิม; actor/payload ต่าง→conflict

## 11. `apply` payload contract (F8/R3)

```jsonc
{
  "schema_version": "extract-v1.1",
  "input_sha256": "<64-hex; = run.input_sha256>",
  "provider_result": { "provider_code":"...", "model_code":"...", "result_ref":"...", "produced_at":"<ts>" },
        // correlate เท่านั้น — provider/model/target/policy/decision อ่านจาก run; override ไม่ได้
  "header": { "fields": { "customer_name_raw":"...", "contact_email":"...", "quote_due_at":"...", "priority_code":"..." } },
        // R3: RFQ-level business fields → นับเป็น (RFQ,'RFQ',field) ใน set equality
  "items": [
    { "line_no":1,
      "fields": { "job_name":"box", "product_type_ref":"PT", "finished_width_mm":80 },
      "quantity_options":[ {"option_no":1, "fields":{"quantity":5000,"unit_ref":"PCS"}} ],
      "components":[ {"component_no":1, "fields":{"box_template_ref":"BT"}, "corrugated":{"fields":{"flute_code_snapshot":"B"}}} ],
      "design_variants":[ {"variant_no":1,"fields":{"design_code":"D1"}} ],
      "processes":[ {"sequence_no":1, "component_no":1, "fields":{"process_ref":"PRC-1"}} ],  // component_no = relationship → ต้องมี evidence
      "packings":[ {"sequence_no":1,"fields":{"packing_ref":"PK"}} ],
      "deliveries":[ {"delivery_no":1, "option_no":1, "fields":{"destination_ref":"DEST"}} ]   // option_no = relationship → ต้องมี evidence
    }
  ],
  "evidence": [
    { "subject_type":"RFQ", "ref":"RFQ", "field_name":"customer_name_raw", "derivation_type":"AI_EXTRACTED", "source_type":"EMAIL", "source_excerpt":"...", "confidence":0.9 },
    { "subject_type":"ITEM", "ref":{"line_no":1}, "field_name":"job_name", "derivation_type":"AI_EXTRACTED", "source_type":"PDF", "source_page":2, "confidence":0.92 },
    { "subject_type":"PROCESS", "ref":{"line_no":1,"sequence_no":1}, "field_name":"component_no", "derivation_type":"AI_EXTRACTED", "source_type":"PDF", "confidence":0.8 }
  ],
  "clarifications": [
    { "subject_type":"COMPONENT", "ref":{"line_no":1,"component_no":1}, "field_name":"paper_ref", "question":"ไม่ระบุกระดาษ", "reason":"เอกสารไม่มีข้อมูล" }
        // ไม่มี assumed_value; ถ้าจะสมมติค่า ต้องเขียนใน fields + AI_INFERENCE evidence คู่กัน (§9)
  ]
}
```

### allowlist / limit / unknown-key (บังคับใน DB)
- `header.fields`/`*.fields` allowlist = **business field ของ subject นั้นตาม draft-v1** (ไม่รวม lifecycle/correlation/server defaults)
- `schema_version`≠`extract-v1.1` / unknown key ทุก node / `input_sha256`≠run / override provider-decision / lifecycle ใน payload → reject `22023`
- `items` ว่าง/>100 / child array>200 / payload>1MB → `54000`
- business field เป็น JSON null → reject (ใช้ omit-key)
- field ปรากฏแต่ไม่มี evidence / evidence เกิน / cross-run source / relationship key ไม่มี evidence → reject (§7)
- fail กลาง insert → rollback ครบ

## 12. Permission (R7)
- `begin/claim/apply/fail` = `SECURITY DEFINER`, owner `rfq_owner`, pinned search_path, REVOKE PUBLIC, GRANT EXECUTE → `rfq_ingest` เท่านั้น
- `rfq_ingest` **ไม่มี direct table privilege (SELECT/DML)** บน trusted tables/run/evidence/ledger — เข้าถึงได้เฉพาะผลจาก function
- trusted tables เขียนโดย scanner/redactor/approver role แยก (007 documented; PoC seed ด้วย owner ตาม §0)

## 13. Migration 007 scope (ยืนยัน implement-ready แล้วค่อยเขียน)
5 ตารางใหม่ (§2) + ALTER run/evidence (§2) + `begin/claim/apply/fail` + ledger + grants (ingest-only) + roles สำหรับ trusted writers (documented) — **ไม่มี table/function เกิน scope นี้** (Codex gate)

## 14. DB บังคับ vs app
- **DB**: resolve trusted state + **re-validate ตอน claim**, total egress gate, run state machine + lease/fencing, atomic tree+evidence, set-equality completeness, subject/relationship resolve, reference integrity, idempotency ledger, permission
- **app/pipeline**: scan/classify→source_ingest; redaction จริง+artifact→attestation; approver→approval; provider registry; authenticate→actor; hard-code service; raw-body/JSON-Schema/depth validate; provider call (timeout ≤8 นาที) + provider idempotency key

## 15. Acceptance tests (15 เดิม + 12 rev2 + 8 rev3 = 35; ย่อ)
**rev3 เพิ่ม (Codex):** (1) begin ผ่าน แต่ approval/attestation หมดอายุ/provider disabled ก่อน claim → **claim BLOCKED, ไม่มี lease** (2) approval/attestation `expires_at` < lease window → claim reject (3) APPROVED ใช้ raw ref/hash; REDACTED ใช้ redacted ref/hash; สลับกันไม่ได้ (4) RFQ `header.fields` ไม่มี evidence → rollback (5) `PROCESS.component_no` / `DELIVERY.option_no` ไม่มี evidence → rollback (6) clarification ไม่มี committed field → ห้ามสร้าง orphan evidence (7) `fail` บน PENDING reject; FAILED exact replay คืน outcome; FAILED claim ใหม่ไม่ได้ (8) claim request เดิม replay หลัง lease หมด → ไม่ reclaim; claim request_id ใหม่ → reclaim ได้
**(รวม 27 เดิม: trusted-assert reject / decision-total / evidence set-equality / derivation manual-vs-AI / 2-worker claim / lease reclaim / apply-fail terminal เดียว / no-partial / ledger cross-op / ingest allowlist ฯลฯ)**

## 16. Out of scope (v1.1)
หน้าเว็บ, Ready validator/F5, estimate/costing, revision evidence carry-forward, deploy, `processing_action_code` (Local deny), **re-extract/add-source capability** (แยก function), scanner/redactor **ingest functions** (007 seed ด้วย owner ตาม §0), **Cloud extraction ของ RFQ จริง** (รอ Data Owner/DPO/Legal)

## 17. คำถาม §17 — Codex ตอบครบแล้ว (บันทึกเป็น decision)
1. **lease** = DB constant **10 นาที**, ไม่รับจาก caller; provider timeout ≤8 นาที; claim revalidate ครอบ lease window; ยังไม่เพิ่ม config table ใน 007 ✅
2. **claim แยก function** ✅ (เหมาะ async worker)
3. **trusted tables PoC** = owner seed มือได้เฉพาะ prototype/synthetic ตามเงื่อนไข §0 ✅
4. **implement-ready?** rev 2 = NO (ค้าง R1-R6); **rev 3 ปิด R1-R8 แล้ว** → Codex confirm: เติม C1-C4 (§18) แล้ว **GO เขียน 007** (ไม่ต้องวน design review อีก)

## 18. Final pre-007 decisions (C1-C4 — Codex confirm) — ล็อกก่อนเขียน SQL

**C1 — trusted reference "ไม่พบ" ≠ "policy denial"** (แยก malformed/forged ออกจากการปฏิเสธเชิงนโยบาย):
- `source_ingest_id`/provider/model key **ไม่มีจริง** → **reject `23503`, rollback, ไม่สร้าง shell/run/attachment** (ไม่ใช่ BLOCKED)
- record **มีจริงแต่** inactive/revoked/scan/classification/policy/target ไม่ผ่าน → สร้าง durable **`BLOCKED`** run
- ไม่เพิ่ม `attempted_source_id` column
- test: unknown ID → 23503 (ไม่มี RFQ/run/attachment); existing-but-revoked → BLOCKED durable

**C2 — relationship evidence: field_name = logical key, value_snapshot = natural key ของ target (ไม่ใช่ UUID)**:
- evidence `field_name` = `component_no` / `option_no` (logical payload key)
- DB resolve FK แล้วสร้าง `value_snapshot` จาก natural key ของ target ผ่าน join:
  - `PROCESS.component_no` → `to_jsonb(rfq_component.component_no)`
  - `DELIVERY.option_no` → `to_jsonb(rfq_quantity_option.option_no)`
- DB ตรวจ target อยู่ item/RFQ เดียวกันก่อนสร้าง evidence; extractor ห้ามส่ง UUID

**C3 — inline redaction/approval columns บน `rfq_ai_extraction_run` = immutable audit snapshot** (แก้ schema contradiction ของ CHECK เดิม):
- **คง** `redaction_applied, redaction_manifest, exception_approved_by_ref, exception_reason` ไว้เป็น audit snapshot บน run
- `begin` **copy จาก trusted record เท่านั้น** (caller ห้ามส่ง inline values):
  - REDACTED_ALLOW → `redaction_applied=true` + copy manifest จาก attestation + set `redaction_attestation_id`
  - APPROVED_EXCEPTION → copy `exception_approved_by_ref/reason` จาก approval + set `egress_approval_id`
- ปรับ CHECK เดิม (001:466-475) ให้ require **ทั้ง** trusted-record ID **และ** snapshot ที่สอดคล้องตาม decision → insert `PENDING+REDACTED_ALLOW` ผ่าน CHECK ได้ (เพราะ begin เติม snapshot ให้ครบตั้งแต่ insert)

**C4 — `apply`/`fail` ต้อง lease "ยังไม่หมดอายุ"** (fencing จริง):
- non-replay `apply`/`fail` require: `status='RUNNING'` + token ตรง + service ตรง + **`lease_expires_at > now()`**
- exact idempotent replay → คืน ledger outcome **ก่อน**ตรวจ lease/status
- expired lease → ต้องผ่าน `claim` ใหม่ (token/attempt ใหม่) เท่านั้น
- test: token ถูกแต่ lease หมด → apply/fail reject; หลัง reclaim → token เก่าถูก reject

**สถานะ: GO — เขียน migration 007 + acceptance tests ได้ (implementation review รอบถัดไปโฟกัส SQL constraints, SECURITY DEFINER, lock/ledger ordering, fail injection, 2-session claim/apply/fail races)**
