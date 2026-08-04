# Codex Review — P5b Results / P1 closure (`7c8cbb3`)

**วันที่:** 2026-08-04  
**Target:** `ff9732e` + `16dc96a` + `7c8cbb3`  
**Scope:** ยืนยันว่า P5b พอปิด P1 track ในขอบเขต local + synthetic + single-writer หรือไม่  
**ไม่ได้แก้:** product code, `STATUS.md`, Qdrant หรือ service ที่รันอยู่

## Verdict: **FIX-EVIDENCE-THEN-CLOSE**

แกน P1 ทำงานถูกทิศและผลที่มีพิสูจน์ประเด็นสำคัญได้จริง แต่ยังไม่ควรเขียนว่า acceptance ครบทั้งหมดหรือปิด P1 อย่างเป็นทางการ เพราะ test matrix ที่รันจริงยังขาด `UNCLASSIFIED → admin-only` ซึ่งอยู่ใน acceptance ที่ล็อกไว้ก่อน P5b และ interlock ที่เรียกว่า run-marker ยังไม่ได้ตรวจ marker จริง

นี่ไม่ใช่คำตัดสินว่า retrieval path มี permission leak: จาก code path และหลักฐานที่มี **ยังไม่พบ fail-open ใน auth → effective role → compiled Qdrant filter** ประเด็นที่ค้างคือ closure evidence และความปลอดภัยของ test harness ก่อนรันซ้ำ

## สิ่งที่ยืนยันผ่านแล้ว

1. **A — real-Qdrant conformance ผ่านสำหรับ fixture matrix ปัจจุบัน**
   - list/scalar `allowed_roles`, null, missing, unknown role, stale schema, bool/float schema และ quarantine ถูกยิงด้วย adapter/filter ตัวเดียวกับ API
   - ผลที่รายงานสอดคล้องกับ contract: malformed/stale/quarantined ไม่ถูก retrieve แม้ admin

2. **B — writer lifecycle ผ่านสำหรับ serial single-writer**
   - `store_in_qdrant()` ถูกเรียกจริงบน collection ที่ inject เข้ามา
   - ACTIVE→QUARANTINED ถอน generation เก่า และ broad→narrow ถอนสิทธิ์ role เดิมได้
   - ยังไม่ใช่หลักฐาน concurrency/atomic cutover ซึ่งถูกแยกเป็น deploy gate ถูกต้องแล้ว

3. **C — auth และ business canaries ที่มีอยู่ผ่านจริง**
   - `permission_eval_raw.json` ที่อยู่ใน worktree ระบุ canary 7/7 PASS, `LEAK=0`, `INCONCLUSIVE=0`, auth `VERIFIED`, spoof 403 ครบ 11 role-scoped keys และ `exit_code=0`
   - positive/negative ถูกตรวจครบทุก known role ต่อ business canary แต่ละตัว

4. **runtime changes ไม่ทำให้ผลเสีย validity**
   - `TOP_K` 5→10 เหมาะกับ permission-presence test เพราะลด false negative จาก vector ranking; **ห้ามนำผลนี้ไปอ้างว่า retrieval quality/ranking ผ่าน**
   - bind-mount `policy.py`/`qdrant_filter.py` ทำให้ P5b ทดสอบโค้ดที่ต้องการจริง จึงไม่ต้อง rerun เพราะเหตุนี้เพียงอย่างเดียว แต่ผลนี้ยังไม่พิสูจน์ว่า immutable Docker image สำหรับ deploy รันได้

## Findings

### G1 — Closure gate: `UNCLASSIFIED → admin-only` ไม่ได้อยู่ใน P5b run จริง

Acceptance เดิมระบุไว้ที่ `P5B_RUNBOOK.md:58` และ review ก่อนหน้า แต่:

- `permission_manifest.json` มีเฉพาะ business canary 7 ตัว ไม่มี UNCLASSIFIED
- `p5b_fixtures.py:65` ยิง conformance เพียง `qc/admin/sales` และไม่มี fixture ที่ผ่าน resolver จาก unknown source ไปเป็น ACTIVE UNCLASSIFIED/admin-only
- raw report จึงพิสูจน์ business ACL + auth registry แต่ยังไม่พิสูจน์ default-deny mapping แบบ end-to-end

**ต้องทำก่อนปิด P1:**

1. สร้าง fixture ผ่าน write path จริงโดยใช้ `resolve_document_policy(..., get_rbac)`/`store_in_qdrant(..., rbac_lookup=get_rbac)` กับชื่อ source ที่ไม่รู้จัก ห้ามเขียน payload admin-only ด้วยมือ เพราะจะข้ามสิ่งที่ต้องพิสูจน์
2. ยิง `/search` ด้วย key ครบทุก known role: admin ต้องพบ exact point id/token; อีก 10 roles ต้องไม่พบ
3. เพิ่ม negative API probes สำหรับ missing ACL, stale schema และ quarantine ให้ทุก role รวม admin ต้องไม่พบ เพื่อปิด acceptance C ตามข้อความเดิมแบบตรงตัว (แม้ direct Qdrant conformance จะผ่านแล้ว)
4. ให้ suite fail non-zero เมื่อ probe ใดขาด, DENIED/ERROR แทน CLEAN หรือได้ผลผิด expected set

เมื่อ targeted cases นี้ผ่าน สามารถประกาศ P1 hardened **เฉพาะ local + synthetic + single-writer PoC** ได้

### M1 — P5b interlock ไม่มี run-marker ที่ตรวจตรงจริง

`p5b_seed.py:34` ส่ง `run_marker_ok=args.allow_nonempty or args.recreate` และ `p5b_lifecycle.py:47` ส่ง `run_marker_ok=True` แบบ unconditional ขณะที่ `policy.py:276-289` รับเพียง boolean ดังนั้น `--recreate` สามารถลบ collection ที่ไม่ว่างใด ๆ ที่ชื่อมีคำว่า `p5b` ได้ แม้ไม่ใช่ collection ของ run นี้

ผล `run1` ยังไม่เสีย validity เพราะ compose ใช้ volume/network/port แยกและรายงานชี้ collection เฉพาะ แต่ guard ถูกอธิบายแรงกว่าที่ทำจริง และไม่ปลอดภัยพอสำหรับ rerun

**ทางแก้ที่แนะนำ:** ใช้ชื่อ collection ใหม่ทุก run และ fail เมื่อ target มีอยู่/ไม่ว่าง โดยถอด `--recreate` กับ `--allow-nonempty` ออกจากเส้นทางปกติ หากจำเป็นต้อง resume ให้ seed marker ที่มี random run id แล้วอ่านกลับมาเทียบ exact value ก่อน mutation; lifecycle ห้ามส่ง `True` เอง

### M2 — หลักฐานผลรันยังไม่ durable/reproducible พอสำหรับ security gate

`permission_eval_raw.json` มีหลักฐานที่ดีแต่ถูก ignore (`.gitignore:19`) ส่วนผล A/B และ curl matrix เหลือเพียงข้อความสรุปใน `KB_P5B_RESULTS.md` นอกจากนี้ใช้ `qdrant/qdrant:latest` (`docker-compose.p5b.yml:12`) จึงไม่ทราบแน่ชัดว่ารันกับ version/digest ใดเมื่อย้อนตรวจภายหลัง

**ให้ทำพร้อม rerun G1:**

- เก็บ sanitized machine-readable artifacts ของ A/B/C/curl matrix ไว้ใต้โฟลเดอร์ evidence ของ run (ไม่มี plaintext key)
- บันทึก commit SHA, collection/run id, Qdrant version หรือ image digest, API image/source SHA, command และ exit code
- pin Qdrant tag/digest สำหรับ acceptance run หรืออย่างน้อยบันทึก digest ที่ใช้จริง

นี่เป็น evidence-hardening ไม่ใช่การพบ permission leak ใหม่

### M3 — Docker image หลักยังขาด P1 modules

`Dockerfile:19-20` copy เฉพาะ `app/` และ `rbac_config.py` แต่ `app/main.py` import `policy.py` กับ `qdrant_filter.py`; P5b ผ่านได้เพราะ bind-mount สองไฟล์ที่ `docker-compose.p5b.yml:36-37`

ดังนั้น:

- ไม่กระทบ validity ของ P5b logic run
- **ต้องเพิ่มเป็น deploy gate ชัด ๆ:** build/run image โดยไม่มี source bind-mount แล้ว health + auth smoke ต้องผ่าน ก่อน deploy/staging

### N1 — ปรับถ้อยคำไม่ให้ overclaim

`KB_P5B_RESULTS.md:27` ควรเปลี่ยนจาก “สอดคล้อง Qdrant จริงทุกจุด” เป็น “สอดคล้องกับ Qdrant จริงครบทุก case ใน fixture matrix ปัจจุบัน” เพราะ `matches_policy()` เองระบุว่าไม่ใช่ oracle ของทุก JSON/Qdrant edge

## คำตอบ 4 ข้อใน handoff

1. **ยังปิด P1 track ไม่ได้ใน commit ปัจจุบัน** เพราะ G1 ยังไม่ผ่าน acceptance เดิม หลังปิด G1 และเก็บ evidence ตาม M2 แล้วให้ GO ปิดเป็น `P1 hardened — PoC local/synthetic/single-writer` ได้
2. `TOP_K=10` และ bind-mount ไม่ทำให้ผลเดิม invalid; ไม่ต้อง rerunเพราะสองข้อนี้ล้วน ๆ แต่ต้อง rerun targeted/full P5b เพื่อปิด G1 และสร้าง durable evidence
3. ไม่พบ product retrieval fail-open เพิ่มจาก code path ที่รีวิว จุดที่ต้องแก้ก่อนรันซ้ำคือ test-target interlock M1 และ packaging M3 ต้องคงเป็น deploy gate
4. หลังปิด G1/M1/M2 แบบก้อนเล็ก ให้เดิน **P2 reranker แบบ offline/shadow** ต่อได้ตาม roadmap โดยห้ามรวมผล permission canary `TOP_K=10` เข้ากับ retrieval-quality claim ส่วน production deploy gates ทั้งหมด—including immutable image packaging, staging/backfill/atomic cutover, writer fencing, durable quarantine, legacy writers, real auth/OIDC และ egress approval—ยังเปิดอยู่

## Verification note

รีวิวจาก committed code, `KB_P5B_RESULTS.md` และ local ignored `permission_eval_raw.json` ซึ่งยืนยัน C ตามที่กล่าวข้างต้น รอบนี้ไม่สามารถ rerun Python/Docker จาก environment ของ Codex ได้เพราะ execution approval ถูกปฏิเสธจาก workspace credit ไม่ใช่ test failure จึงไม่อ้างว่าได้ reproduce A/B/C อิสระอีกครั้ง

## Handoff สั้นให้ Claude

**FIX-EVIDENCE-THEN-CLOSE:** ปิด M1 interlock ก่อน แล้วเพิ่ม resolver-driven UNCLASSIFIED canary + negative API probes (missing/stale/quarantine), rerun isolated P5b ด้วย pinned/recorded Qdrant image, commit sanitized raw A/B/C evidence และแก้ wording “ทุกจุด” เป็น “ทุก case ใน matrix” หากเขียวทั้งหมดให้ปิด P1 PoC track แล้วเริ่ม P2 offline/shadow ได้; ห้ามประกาศ deploy-ready
