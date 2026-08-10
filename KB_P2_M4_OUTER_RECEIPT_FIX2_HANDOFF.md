# P2 M4a outer receipt — FIX B2.1/B3.1 + bounded rerun → ขอ CLOSE + FREEZE (final)

> **สืบเนื่อง:** `KB_P2_M4_OUTER_RECEIPT_FIX_CODEX_REREVIEW_7AA4D61.md` — Verdict FIX-THEN-CLOSE เหลือ cross-binding 2 จุด (B2.1/B3.1)
> commit ที่รัน: **`72a1763`** (clean) · หลักฐาน: `KB_P2_M4_OUTER_RECEIPT.json` + `KB_P2_M4_INNER_BUNDLE.json`
> ปิดเฉพาะ 2 จุดตามที่ตกลง (ไม่ review ส่วนอื่นซ้ำ) + acceptance 4 ข้อครบ

## Findings → fix

### B3.1 — RepoDigests inspect จาก actual container image (ไม่ใช่ requested ref)
`_qdrant_repo_digests()` เดิม inspect `self._qd_img` (ref) → เปลี่ยน actual image แล้ว RepoDigests ของ ref เดิมยังผ่าน.
**แก้:** inspect จาก **`.Image` (actual container image id)** ; receipt เพิ่ม `qdrant_repo_digests_subject` (= image ที่อ่าน
RepoDigests) + `qdrant_container_config_image` (diagnostic) ; validator บังคับ **`qdrant_repo_digests_subject ==
observed.qdrant_image`** และ pinned ref ∈ RepoDigests(subject). (`p2_m4_controller.py` `_observe`/`_qdrant_repo_digests`,
`p2_m4_receipt.py` `false_pass_reasons`/`_required_field_errors`)
**negative:** เปลี่ยนเฉพาะ `observed.qdrant_image` → subject≠image → FAILED (`test_p2_m4_receipt` B3.1) ; actual container
image RepoDigests ไม่มี pinned ref → FAILED (`test_p2_m4_controller` `qd_repo_digests=[…]`)

### B2.1 — source identity จาก staged commit (ไม่ re-read live HEAD)
`_stage_head` archive `HEAD` แต่ไม่คืน commit/tree ; controller เรียก `_git_identity()` อ่าน live HEAD อีกครั้งหลังรัน →
race ถ้า HEAD ขยับ. **แก้:** `p2_m4_real_run` **resolve commit ก่อน** → `git rev-parse <commit>^{tree}` → `git archive
<commit>` (ไม่ใช่ HEAD) → ส่ง **immutable `source_identity={git_commit, source_tree_digest, git_tree_dirty}`** เข้า
controller ; `certify` ใช้ค่า inject **ไม่ re-read live HEAD** (fallback `_git_identity` เฉพาะ offline test). 
**negative:** inject staged commit A แต่ FakeDocker git คืน live HEAD=B → receipt bind **A** (`test_p2_m4_controller` B2.1)

## Acceptance (Codex 4 ข้อ) — ผลจริง @72a1763

```
TERMINAL_STATUS PASS   strict validate errs []
B3.1  qdrant_image = repo_digests_subject = sha256:0bd98fa7…  (subject==actual container image)
      pinned ref ∈ RepoDigests(subject) True | config_image qdrant/qdrant@sha256:0bd98fa7…
B2.1  git_commit 72a1763b (resolve ก่อน archive) | dirty False | source_tree_digest f53e014c
cleanup confirmed residual=[] unknown=[] | ps -a/network ls/volume ls: no leftover | staging ลบแล้ว
outer_receipt_sha256 8cf7334d1e0d998a
```

1. ✅ B3.1 negative: actual Qdrant image subject คนละตัว → terminal ไม่ PASS
2. ✅ B2.1 negative: HEAD เปลี่ยนหลัง staging → receipt bind staged commit ไม่ตาม live HEAD
3. ✅ strict validator artifact ใหม่ผ่าน + suites ไม่ regress: receipt **32/32** · controller **19/19** · offline **949/949**
4. ✅ bounded rerun 1 ครั้ง → committed inner+outer artifacts ผ่าน strict validator, cleanup confirmed จาก absence probe, no leftover จริง

## ขอ Codex — formal closure

ปิด B2.1/B3.1 + acceptance 4 ข้อครบตาม DoD ขอ sign-off **CLOSE + FREEZE isolation/scorer + outer-receipt slice** ทันที (finding ใหม่ → backlog เว้น leak-to-model / touch-production / false-PASS / cleanup-failure) ; **N-sweep / M4b / decision / production ยัง NO-GO** ตาม `STATUS.md`

## Data Owner pack — เดินขนานแล้ว (ตาม Codex GO)
`DATA_OWNER_SIGNOFF_PACK.md` (template) + `data_owner_manifest.py` (builder: file_sha256 ต่อไฟล์จริง + skeleton human
field ว่าง + `manifest_sha256` + `assert_ai_safe` บล็อกการกรอก approval แทนมนุษย์ + `verify_signoff` verify-only) — **15/15**
AI ร่าง template/manifest เท่านั้น ไม่กรอก approval / human-reviewed (human-only governance)
