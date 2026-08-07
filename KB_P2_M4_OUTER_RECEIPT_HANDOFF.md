# P2 M4a — outer receipt (host-authoritative) : bounded evidence rerun ปิด B1/B2/B3/M1

> **สืบเนื่อง:** `KB_P2_M4_REAL_RUN_CODEX_REVIEW_7DE8574.md` — Capability **ACCEPTED**, formal closure = **FIX-EVIDENCE-THEN-CLOSE**
> ทำตาม Codex DoD "หนึ่ง rerun พอ" : เพิ่ม concrete host controller + outer receipt (load-bearing) แล้วรัน M4a synthetic ซ้ำ **หนึ่งครั้ง**
> commit ที่รัน: **`5dade75`** (clean tree) · หลักฐาน: `KB_P2_M4_OUTER_RECEIPT.json` + `KB_P2_M4_INNER_BUNDLE.json`

## แนวคิดการปิด

เดิม (7de8574) inner bundle สร้าง **ในคอนเทนเนอร์** → image/isolation/cleanup เป็น **evaluator-declared จาก env**
เพิ่มชั้น **host controller** (`p2_m4_controller.DockerM4Controller`) ที่:

1. provision network/volume/qdrant/evaluator เอง
2. **observe จาก `docker inspect` จริง** (image id, network/volume identity, host-published ports) — ไม่รับ security assertion จาก evaluator env
3. run evaluator (blocking) + บันทึก **process จริง** (command, exit code, timestamps, stdout/stderr digest, deps digest, git commit)
4. teardown → **post-inspect ยืนยันทุก resource หายจริง**
5. ประกอบ **outer receipt** (`p2_m4_receipt`) — **terminal verdict คำนวณจาก observation** (fail-closed, ไม่เชื่อ status ที่เขียนมา) แล้ว hash-bind `inner bundle SHA-256` ทั้งก้อน

## แต่ละ finding → ปิดด้วยอะไร (จาก receipt จริง)

| # | ปิดด้วย observation ฝั่ง host (ไม่ใช่ declared) |
|---|---|
| **B1** executed-image self-assert | `docker inspect {{index .Image}}` ของ evaluator container = `sha256:27768971905ebd…` และ validator บังคับ **== controller pin == inner bundle.image_digest** ; evaluator ประกาศ digest ไม่ตรงที่รันจริง → `FAILED` |
| **B2** isolation env ไม่มี controller observation | (a) identity: controller สังเกต **raw** network/volume/project/collection id → `typed_id_sha256` (leaf helper เดียวกับ evaluator) เทียบ `isolation_proof.*_sha256` ในตัว inner (net/coll id hash == proof ✓) ; (b) **host-published ports = 0** จาก `.NetworkSettings.Ports` (นับเฉพาะ value ที่มี host binding — ไม่ใช่ EXPOSE) == declared |
| **B3** PASS ก่อน cleanup / ไม่มี durable cleanup proof | terminal resolve **หลัง** teardown + **post-inspect** ; `cleanup.confirmed=True, residual=[]` ; ถ้า resource ค้าง → `DEGRADED` (ไม่ PASS) — outer receipt อ้าง `bundle_sha256` + `attempt_id` เดียวกัน |
| **M1** synthetic process receipt | receipt.process บันทึกของจริง: docker `command` เต็ม, `exit_code=0`, `started/finished_utc`, `stdout/stderr_sha256`, `dependency_digest` (pip freeze ในคอนเทนเนอร์), `git_commit=5dade75` (`git_tree_dirty=False`) |

## ผลรัน (Codex DoD 5 ข้อ)

```
TERMINAL_STATUS   PASS
validate errs     []          (validate_m4_outer_receipt — fail-closed)
git_commit/dirty  5dade75 / False
bundle_sha256     d681d9d0…    outer_receipt_sha256  f08f4dbe…
teardown          confirmed=True residual=[]  (docker ps -a: no leftover)
```

1. ✅ provision + **Docker observations จากระบบจริง** (ไม่รับ identity/security จาก evaluator env)
2. ✅ เรียก evaluator + บันทึก command/exit/timestamps/stdout/stderr digest จริง
3. ✅ teardown + inspect ยืนยัน container/network/volume หายจริง
4. ✅ publish outer receipt **atomic no-clobber หลัง** cleanup — bind attempt id, bundle SHA-256, evaluator image observation, qdrant image ref, source/dependency identity, cleanup result
5. ✅ validator fail-closed เมื่อ observation ขาด/ไม่ตรง + **negative ≥3** (offline): image env ปลอม, isolation env ปลอม, cleanup fail หลัง inner PASS

## หลักฐาน + test

- `p2_m4_receipt.py` (outer receipt pure) · `p2_m4_controller.py` (controller over injected docker seam) · `p2_m4_real_run.py` (real subprocess seam)
- **offline 930/930** ไม่ regress ; `test_p2_m4_receipt.py` **19/19** (รวม N1/N2/N3 + lying-receipt + bundle-binding + integrity + no-clobber) ; `test_p2_m4_controller.py` **13/13**
- บั๊กที่ real run จับได้: `{{len .NetworkSettings.Ports}}` นับ EXPOSE → แก้เป็นนับ host-binding จริง (offline fake จับไม่ได้ — ยืนยันว่าเส้นทาง Docker จริงถูกใช้)

## ข้อจำกัดที่ bind ไว้ (bounded — Codex อนุญาตใน rerun นี้)

- bridge network (มี egress ให้ runtime `pip install qdrant-client`) ไม่ใช่ `--internal` — `network_internal=False` บันทึกใน receipt ; host-published ports = 0 (Docker-observed) ยังจริง
- `qdrant-client` pip runtime ไม่ baked → bind ด้วย `dependency_digest` + `git_commit` (ไม่ overclaim reproducible)
- synthetic corpus / dummy vectors เท่านั้น

## ขอ Codex — formal closure sign-off

ถามตรง: outer receipt (host-observed image/identity/ports + post-cleanup verify + fail-closed validator + negative 3) **ปิด B1/B2/B3/M1 เชิงหลักฐาน** พอ **freeze isolation/scorer slice** ตามแผน "หนึ่ง rerun แล้วปิด" หรือยังเหลือช่องที่ต้องปิดก่อน freeze?

**Gate ที่เสนอ (ไม่เปลี่ยนเงียบ):**
- isolation/scorer + real-run mechanics → **CLOSE + FREEZE** (finding ใหม่ → backlog เว้น leak-to-model/touch-production/false-PASS/cleanup-failure)
- **N-sweep / M4b / decision benchmark / production → ยัง NO-GO** ตาม `STATUS.md` จน Data Owner sign-off
- Data Owner pack (`DATA_OWNER_SIGNOFF_PACK.md`) ทำขนาน = คอขวดองค์กร
