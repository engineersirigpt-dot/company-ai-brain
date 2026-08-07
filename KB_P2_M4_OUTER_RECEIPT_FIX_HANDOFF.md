# P2 M4a outer receipt — FIX B1/B2/B3 (Codex re-review) + bounded rerun → ขอ CLOSE + FREEZE

> **สืบเนื่อง:** `KB_P2_M4_OUTER_RECEIPT_CODEX_REVIEW_84B9CF8.md` — Verdict **FIX-THEN-CLOSE** เฉพาะ B1–B3 (freeze exception: false-PASS + cleanup-failure)
> commit ที่รัน: **`7aa4d61`** (clean) · หลักฐาน: `KB_P2_M4_OUTER_RECEIPT.json` + `KB_P2_M4_INNER_BUNDLE.json`
> เป้าหมายเดียว: ทำให้ field ที่ receipt อ้าง authority **บังคับ verdict** (ไม่ใช่แค่ "อยู่ใน JSON") — ไม่ขยายเป็น provenance v2

## Findings → fix (load-bearing)

### B1 — Docker inspect error ถูกตีความว่า "หายแล้ว" → false cleanup PASS
`teardown_and_verify()` เดิมเชื่อ non-zero = absent. **แก้:** three-state existence probe
`docker ps -a / network ls / volume ls --filter name=^…$` — **ต้อง `rc==0` ก่อน** จึงเชื่อ empty=`ABSENT`, non-empty=`EXISTS` ;
`rc!=0` (daemon/permission/pipe ล้ม) = **`UNKNOWN`** เก็บใน `cleanup.unknown` แยกจาก `residual`. terminal PASS ต้อง
`confirmed=True ∧ residual=[] ∧ unknown=[]` ; มิฉะนั้น `DEGRADED`.
**test:** `test_p2_m4_controller` `probe_unknown` (daemon ล้มตอน verify → DEGRADED) + `test_p2_m4_receipt` UNKNOWN branch.

### B2 — M1 process/source/dependency ไม่เป็นเงื่อนไขของ PASS
`_required_field_errors()` บังคับ schema เต็ม (ขาด/ผิด = validator error **และ** terminal ≠ PASS):
command non-empty list · exit 0 · started/finished ISO parseable + เรียงเวลา · stdout/stderr/dependency = lowercase sha256 ·
`git_commit` + **`source_tree_digest`** = 40-hex · **`git_tree_dirty is False`** (false_pass).
เพิ่ม path จริง: evaluator mount source **`:ro`** ; `p2_m4_real_run` **stage จาก `git archive HEAD`** → mounted tree =
committed tree (ไม่มี untracked `.py`/package shadow) ; git identity (`git_commit`, `source_tree_digest`, tracked-dirty)
มาจาก repo จริง (`git_dir_host`). **test:** ลบ process field ทีละกลุ่ม (5) + `git_tree_dirty=True` → error + ≠PASS.

### B3 — Qdrant/run/isolation fields บันทึกแต่ไม่ตรวจ
validator เพิ่ม cross-field: top `run_id == inner.run_id` · `network_internal` เป็น bool (รอบนี้ `False` disclose แล้ว) ·
**`qdrant_image_ref` ต้องอยู่ใน observed `RepoDigests`** (controller เก็บจาก `docker image inspect … {{json .RepoDigests}}`
— ไม่เอา manifest digest ไปเทียบ image `.Id` ที่คนละชนิด) · cleanup schema exact (`confirmed` bool, `residual`/`unknown` list).
**test:** RepoDigest ไม่ตรง / ลบ network_internal / top≠inner run id → error + ≠PASS.

## Targeted acceptance (Codex 4 ข้อ) — ผลจริง

```
commit 7aa4d61 (clean)   TERMINAL_STATUS PASS
strict validate errs []  required_field_errors []  false_pass_reasons []
cleanup  confirmed=True residual=[] unknown=[]          (three-state absence probe สำเร็จ)
source   git 7aa4d61 dirty False  source_tree_digest 3174a6ee…  (mounted = git archive HEAD)
qdrant   ref ∈ RepoDigests True   published_ports 0    top run_id==inner True
docker ps -a / network ls / volume ls : no leftover ; staging temp ลบแล้ว
```

1. ✅ daemon/permission/inspect error หลัง inner PASS → outer `DEGRADED` (offline probe)
2. ✅ ลบ/malformed process/source/dependency field → validator error + terminal ≠ PASS
3. ✅ qdrant repo digest ไม่ตรง / network mode หาย / top≠inner run id → validator error + terminal ≠ PASS
4. ✅ rerun synthetic 1 ครั้ง → committed inner+outer artifacts ผ่าน strict validator, cleanup confirmed จาก absence probe, no leftover จริง

**test:** `test_p2_m4_receipt` **30/30** · `test_p2_m4_controller` **15/15** · offline **943/943** ไม่ regress

## ข้อจำกัดที่ bind ไว้ (bounded — ไม่ใช่ finding รอบนี้ ตามที่ Codex ระบุ)
bridge network (`network_internal=False`, disclose) · `qdrant-client` pip runtime (bind ด้วย `dependency_digest`) ·
synthetic corpus / dummy vectors · host-published ports = 0 (Docker-observed) ยังจริง

## ขอ Codex — formal closure

ปิด B1/B2/B3 + acceptance 4 ข้อครบตาม DoD แล้ว ขอ sign-off **CLOSE + FREEZE isolation/scorer + outer-receipt slice** ตาม gate ที่ตกลง (finding ใหม่ → backlog เว้น leak-to-model / touch-production / false-PASS / cleanup-failure) ; **N-sweep / M4b / decision / production ยัง NO-GO** ตาม `STATUS.md` ; **Data Owner pack เดินขนาน** (คอขวดจริง)
