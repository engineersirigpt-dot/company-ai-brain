# Codex targeted re-review — P2 M4a outer receipt fix (`7aa4d61`)

วันที่รีวิว: 2026-08-10  
ขอบเขต: B1/B2/B3 จาก `KB_P2_M4_OUTER_RECEIPT_CODEX_REVIEW_84B9CF8.md` เท่านั้น  
ตรวจ code path, committed inner/outer artifacts, Git identity และ targeted fault probes โดยไม่รัน Docker/model/Qdrant ซ้ำ  
ไม่ได้แก้ source, tests, evidence หรือ `STATUS.md`

## Verdict

**FIX-THEN-CLOSE — เหลือ cross-binding 2 จุดเท่านั้น**

- **B1 cleanup three-state: CLOSED**
- **B2 required process schema/read-only staged source: CLOSED ส่วน schema แต่เหลือ B2.1 source snapshot race**
- **B3 run/network/cleanup schema: CLOSED แต่เหลือ B3.1 actual Qdrant image binding**

หลักฐานรอบจริงปัจจุบันสอดคล้องกันและไม่มีข้อบ่งชี้ว่ารันผิด image/source: receipt commit/tree ตรงกับ `7aa4d61` และ Qdrant image ID มี digest เดียวกับ pinned ref อย่างไรก็ตาม formal closure ต้องอาศัย invariant ใน producer/validator ไม่ใช่ reviewer ตรวจ artifact ด้วยมือทุกครั้ง

ทางที่สั้นที่สุดคือแก้เฉพาะ B2.1/B3.1 เพิ่ม negative test สองตัว แล้ว rerun evidence จาก fixed producer หนึ่งครั้ง จากนั้น **CLOSE + FREEZE โดยไม่ review ส่วนอื่นซ้ำ**

## สิ่งที่ยืนยันว่าปิดแล้ว

- `teardown_and_verify()` แยก `EXISTS/ABSENT/UNKNOWN`; probe error กลายเป็น `cleanup.unknown` และ terminal `DEGRADED` (`p2_m4_controller.py:199-234`)
- receipt บังคับ process/source/dependency field, timestamp ordering, clean tracked tree และ exact cleanup container types (`p2_m4_receipt.py:145-210`)
- source mount เป็น `:ro` และ real runner stage จาก `git archive` จึงตัด untracked module shadowing ใน mounted tree (`p2_m4_controller.py:155-177`, `p2_m4_real_run.py:41-74`)
- top/inner run ID, network mode, Qdrant pinned ref membership และ cleanup UNKNOWN ถูกใช้คำนวณ terminal (`p2_m4_receipt.py:272-303`)
- committed receipt validate เป็น `PASS`; receipt commit `7aa4d61d468bb7d78ff4ef662bff0f05326aef3b` และ tree `3174a6ee20224b107d2d684764803f8f76c764f7` ตรงกับ Git จริง
- targeted tests ผ่าน **45/45**: receipt 30/30 + controller 15/15

## Findings

### B3.1 — RepoDigests ถูก inspect จาก requested ref ไม่ใช่ actual container image

**Finding:** controller อ่าน actual Qdrant container image ID จาก `.Image` แต่ `_qdrant_repo_digests()` กลับ inspect `self._qd_img` ซึ่งเป็น requested ref; validator เพียงตรวจว่า requested ref อยู่ใน list และไม่ใช้ `observed.qdrant_image` ใน cross-field invariant (`p2_m4_controller.py:125-152`, `p2_m4_receipt.py:276-279`)

**Why it matters:** actual container image ID เปลี่ยนเป็น image อื่นได้ แต่ receipt ยัง PASS หาก RepoDigests ที่อ่านจาก requested ref ถูกต้อง จึงยังไม่พิสูจน์ว่า RepoDigests เป็นของ image ที่ container รันจริง

**Evidence:** fault probe เปลี่ยนเฉพาะ `observed.qdrant_image` เป็น valid image ID อื่นแล้ว recompute receipt hash:

```text
wrong_actual_qdrant_image terminal=PASS errs=[]
```

**Suggested change:** ให้ inspect RepoDigests จาก actual `qd_image` ที่อ่านจาก container ไม่ใช่จาก `self._qd_img` และบันทึก/cross-bind subject ชัดเจน เช่น:

1. `container_config_image_ref` จาก Docker container inspect ต้องตรง controller pin
2. `container_image_id` จาก `.Image`
3. `repo_digests_subject_image_id == container_image_id`
4. pinned ref ต้องอยู่ใน RepoDigests ที่ inspect โดย subject image ID นั้น

เพิ่ม negative test: actual container image ID คนละตัว แต่ requested-ref RepoDigests ยังถูก → terminal ต้องไม่ PASS

### B2.1 — Receipt อ่าน Git identity จาก live HEAD หลัง execution ไม่ใช่ identity ที่ใช้สร้าง staged source

**Finding:** `_stage_head()` archive `HEAD` แต่ไม่คืน commit/tree ที่ archive จริง; หลัง evaluator และ cleanup controller จึงเรียก `_git_identity()` อ่าน live repository `HEAD`/tree อีกครั้ง (`p2_m4_real_run.py:41-74`, `p2_m4_controller.py:185-197`, `237-254`)

**Why it matters:** หาก checkout/HEAD เปลี่ยนเป็น clean commit อื่นระหว่าง staging กับ `_git_identity()`, mounted source คือ commit A แต่ receipt บันทึก commit/tree B พร้อม `git_tree_dirty=False` และยัง PASS นี่เป็น source-evidence false binding แม้ mounted directory จะสะอาดและ read-only

**Evidence:** validator ตรวจเพียงว่า `source_tree_digest` เป็น 40-hex ไม่ได้ผูกมันกับ commit/staged archive; fault probe เปลี่ยนเป็น unrelated valid tree hash แล้วได้:

```text
unrelated_source_tree terminal=PASS errs=[]
```

**Suggested change:** resolve commit/tree **ก่อน staging**, archive exact commit (`git archive <resolved_commit>`) และส่ง immutable `staged_commit`/`staged_tree` เข้า controller โดยตรง ห้าม re-read `HEAD` เพื่ออ้าง identity หลังรัน ถ้ายังต้องบันทึก live worktree state ให้เป็น diagnostic แยก ไม่ใช้แทน staged-source identity

เพิ่ม negative test: stage commit A แล้วจำลอง live HEAD เป็น B ก่อน certify receipt → receipt ต้องยัง bind A หรือ fail ห้ามรายงาน B เป็น source ที่รัน

## Acceptance ที่เหลือก่อน sign-off

1. B3.1 negative test ผ่าน: requested ref ถูกแต่ actual Qdrant image subject คนละตัว → terminal ไม่ PASS
2. B2.1 negative test ผ่าน: HEAD เปลี่ยนหลัง staging → receipt ไม่เปลี่ยน identity ตาม live HEAD
3. strict validator ของ artifact ใหม่ผ่าน และ targeted suites ไม่ regress
4. bounded M4a synthetic rerun จาก fixed producer สร้าง committed inner/outer artifacts พร้อม cleanup confirmed

เมื่อครบสี่ข้อนี้: **GO CLOSE + FREEZE isolation/scorer + outer-receipt slice** ทันที; finding อื่นเข้า backlog ตาม freeze policy เดิม

## Gate

- M4a capability: **DEMONSTRATED**
- formal closure: **FIX-THEN-CLOSE เฉพาะ B2.1/B3.1**
- Data Owner pack: **GO ทำขนานทันที**
- M4b / N-sweep / decision benchmark / production: **NO-GO** ตาม `STATUS.md`

## ขอบเขต Data Owner pack ที่เดินขนานได้

ให้ร่างเป็น template เท่านั้น ห้าม AI กรอก approval หรือเปลี่ยน label เป็น human-reviewed โดยควรมี: document manifest 30–50 ไฟล์ + SHA-256, business owner, classification, allowed roles/groups, purpose, Local/Cloud/No-egress decision, redaction/minimization, retention/deletion, label reviewer, DPO/Legal checkpoint, approver name/time/version และ hash ของชุดเอกสาร+labels ที่ลงชื่อ
