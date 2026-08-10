# Codex final targeted re-review — P2 M4a outer receipt FIX2 (`72a1763`)

วันที่รีวิว: 2026-08-10  
ขอบเขต: B2.1/B3.1 และ acceptance 4 ข้อจาก `KB_P2_M4_OUTER_RECEIPT_FIX_CODEX_REREVIEW_7AA4D61.md` เท่านั้น  
ตรวจ actual call path, committed inner/outer artifacts, Git tree binding, targeted suites และ fault probes โดยไม่รัน Docker/model/Qdrant ซ้ำ  
ไม่ได้แก้ source, tests, evidence หรือ `STATUS.md`

## Verdict

**GO/SHIP — CLOSE + FREEZE isolation/scorer + outer-receipt slice**

B2.1 และ B3.1 ปิดครบตาม Definition of Done ไม่พบ blocker/major ใหม่ใน targeted scope หลักฐานรอบ `72a1763` รองรับ formal closure สำหรับ **PoC local + synthetic M4a permission-leak mechanics**

ทางที่เรียบง่ายที่สุดหลังจากนี้คือ **หยุด hardening slice นี้** ไม่เพิ่ม receipt/provenance surface อีก Finding ใหม่ให้เข้า backlog ตาม freeze policy เว้นแต่พิสูจน์ได้ว่าเกิด leak-to-model, touch-production, false-PASS หรือ cleanup failure ที่กระทบรอบถัดไป

## Trace ที่ตรวจ

### B3.1 — actual Qdrant image binding

Path จริง:

1. controller อ่าน container `.Image` เป็น actual image ID (`p2_m4_controller.py:140-152`)
2. `_qdrant_repo_digests(actual_image_id)` inspect RepoDigests จาก image ID ตัวนั้น ไม่ใช่ requested ref (`p2_m4_controller.py:127-138`)
3. receipt เก็บ `qdrant_repo_digests_subject=actual_image_id`
4. terminal logic บังคับ subject เท่ากับ `observed.qdrant_image` และ pinned ref ต้องอยู่ใน RepoDigests (`p2_m4_receipt.py:278-285`)

ผล: actual container image, RepoDigests subject และ pinned ref ถูก cross-bind ครบ

### B2.1 — staged source identity

Path จริง:

1. real runner resolve immutable commit ก่อน staging
2. resolve tree จาก commit ที่ pin แล้ว `git archive <commit>` โดยตรง (`p2_m4_real_run.py:45-60`)
3. source staged ถูก mount read-only
4. immutable `source_identity` ถูก inject เข้า controller
5. receipt ใช้ staged identity และไม่ re-read live `HEAD` หลัง execution (`p2_m4_controller.py:257-263`)

ผล: checkout/HEAD เปลี่ยนหลัง staging ไม่สามารถเปลี่ยน identity ของ source ที่ receipt อ้างว่ารันได้

## Verification evidence

- `test_p2_m4_receipt.py`: **32/32 passed**
- `test_p2_m4_controller.py`: **19/19 passed**
- targeted รวม: **51/51 passed**
- committed outer artifact: `terminal_status=PASS`
- strict validation: `[]`
- fault probe เปลี่ยน actual Qdrant image โดยไม่เปลี่ยน RepoDigest subject: terminal recompute เป็น `FAILED`
- fault probe เปลี่ยน RepoDigest subject: terminal recompute เป็น `FAILED`
- source binding:

```text
receipt commit = 72a1763bfb8426d4b1e93de2bcf52f0101039775
receipt tree   = f53e014cebbbb5f4fd3afd8ee92fac52d41c8aa1
git commit tree= f53e014cebbbb5f4fd3afd8ee92fac52d41c8aa1
match          = true
```

- cleanup evidence: `confirmed=true`, `residual=[]`, `unknown=[]`
- handoff รายงาน offline suite เต็ม **949/949** และ real bounded rerun ไม่มี leftover; targeted review นี้ไม่พบหลักฐานขัดแย้ง

## Acceptance 4 ข้อ

1. **PASS** — actual Qdrant image subject คนละตัว → terminal ไม่ PASS
2. **PASS** — live HEAD เปลี่ยนหลัง staging → receipt ยัง bind staged commit
3. **PASS** — strict validator และ targeted suites ไม่ regress
4. **PASS** — bounded synthetic rerun สร้าง committed inner/outer artifacts ที่ validate ผ่านและ cleanup confirmed

## Formal closure statement

ตั้งแต่ review นี้:

- **M4a isolation/scorer + outer-receipt slice:** `CLOSED + FROZEN`
- **permission-leak proof track:** `COMPLETE` เฉพาะขอบเขต **PoC local/synthetic/single-run mechanics**
- ผลนี้ **ไม่ใช่** production-security approval และไม่ใช่หลักฐานตัดสิน GPU ทั้งระบบ
- bridge network, runtime `pip`, synthetic corpus และ dummy vectors คงเป็น disclosed PoC limitations ไม่ใช่ production baseline

## Gates ที่ยังคงเดิม

- **Data Owner pack/template/manifest:** `GO` เดินต่อได้
- **human approval, classification และ human-reviewed labels:** ต้องทำโดยผู้มีอำนาจจริง; AI ห้ามกรอกหรือรับรองแทน
- **M4b / ข้อมูลจริง / N-sweep / decision benchmark:** `NO-GO` จน Data Owner sign-off แบบ hash-bound ครบ
- **production:** `NO-GO` จน auth, deployment approval, DPO/Legal และ governance ครบ

**Final verdict: SHIP AND FREEZE — B2.1/B3.1 ปิดแล้ว และ evidence chain เพียงพอสำหรับ formal closure ในขอบเขต M4a local/synthetic**
