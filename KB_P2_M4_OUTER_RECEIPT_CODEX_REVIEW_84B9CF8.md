# Codex formal-closure review — P2 M4a outer receipt (`84b9cf8`, code run at `5dade75`)

วันที่รีวิว: 2026-08-07  
ขอบเขต: `p2_m4_controller.py`, `p2_m4_receipt.py`, `p2_m4_real_run.py`, committed inner/outer evidence และ targeted tests  
ไม่ได้แก้ source, tests, evidence หรือ `STATUS.md` และไม่ได้รัน Docker/model/Qdrant ซ้ำ

## Intent และทางที่สั้นที่สุด

เป้าหมายคือทำให้ host เป็นผู้ยืนยัน image/isolation/process/cleanup หลัง M4a synthetic run แล้วออก terminal receipt ที่ไม่สามารถรายงาน `PASS` เท็จได้

outer receipt เป็น layer ที่จำเป็น เพราะ inner bundle สร้างใน evaluator จึงยืนยัน Docker host และ post-cleanup เองไม่ได้ อย่างไรก็ตามไม่ควรขยายเป็น provenance v2 หรือเปิด review loop ใหม่: ปิดเฉพาะ **สอง false-PASS paths กับหนึ่ง cleanup path** ด้านล่าง แล้ว rerun bounded evidence รอบสุดท้ายได้เลย

## Verdict

**FIX-THEN-CLOSE — ยังไม่ sign-off formal closure**

รอบจริงและไฟล์หลักฐานมีน้ำหนักว่า capability ทำงาน แต่ validator/controller ยังยอมให้ evidence ที่ไม่ครบหรือ cleanup ที่ตรวจไม่ได้กลายเป็น `PASS` ได้ จึงเข้า freeze exception ที่ตกลงไว้โดยตรง: **false-PASS** และ **cleanup-failure**

หลังปิด B1–B3 และรัน evidence ใหม่หนึ่งครั้ง: **GO close + freeze isolation/scorer slice** โดยไม่ต้องรีวิวส่วนอื่นซ้ำ

## สิ่งที่ยืนยันแล้ว

- committed outer receipt bind inner bundle SHA-256 ตรงกับไฟล์ `KB_P2_M4_INNER_BUNDLE.json`
- receipt ของรอบจริงระบุ Docker-observed evaluator image, no host-published Qdrant port, real process timestamps/digests และ post-cleanup result
- happy-path validator ของ artifact ปัจจุบันผ่าน
- targeted suites ผ่าน **32/32**: receipt 19/19 และ controller 13/13
- image mismatch, declared/observed network mismatch และ explicit residual ถูก downgrade ตามที่ออกแบบ
- ข้อจำกัด bridge network, runtime pip และ synthetic/dummy-vector ถูกบันทึกตรงไปตรงมา จึงไม่เป็น finding ใน closure รอบนี้

## Findings

### B1 — Docker inspect error ถูกตีความเป็น “resource หายแล้ว” ทำให้ cleanup false PASS

**Finding:** `teardown_and_verify()` ถือว่า resource ยังอยู่เฉพาะเมื่อ inspect คืน `rc == 0`; non-zero ทุกชนิดถูกถือว่าหาย (`p2_m4_controller.py:183-199`)

**Why it matters:** ถ้า Docker daemon/pipe/permission ล้มหลังคำสั่ง remove ทั้ง remove และ post-inspect จะคืน non-zero จาก “ตรวจไม่ได้” ไม่ใช่ “ไม่มี resource” แต่ controller จะสร้าง `cleanup.confirmed=True, residual=[]` และ terminal สามารถเป็น `PASS`

**Evidence:** fault probe ที่ให้ทุก Docker call คืน `rc=1, stderr='daemon unavailable'` ได้ผล:

```text
{'confirmed': True, 'residual': []}
```

**Suggested change:** ใช้ existence probe ที่แยกสามสถานะชัดเจน: `EXISTS`, `ABSENT`, `UNKNOWN` เช่น `docker container/network/volume ls` ที่ต้อง `rc=0` ก่อนจึงใช้ผล empty/non-empty; rc non-zero ต้องเพิ่ม `cleanup.unknown`/residual และ terminal เป็น `DEGRADED` ห้ามยืนยัน cleanup จาก inspect error ทั่วไป Tests ต้องมี daemon-unavailable/permission-error probe ไม่ใช่แค่ not-found กับ residual

### B2 — M1 process/source/dependency evidence ไม่ได้เป็นเงื่อนไขของ PASS

**Finding:** `false_pass_reasons()` ตรวจเพียง `process.exit_code`; validator ไม่บังคับ command, timestamps, stdout/stderr digest, dependency digest, git commit หรือ dirty state (`p2_m4_receipt.py:123-188`, `205-248`)

**Why it matters:** receipt สามารถลบหลักฐาน M1 ทั้งหมด แล้ว recompute body hash ใหม่ได้ โดยยังได้ `terminal_status=PASS` และ validator คืน error ว่าง ดังนั้น outer receipt ยังไม่ fail-closed ตาม DoD ที่ระบุว่า source/dependency/process identity ต้อง load-bearing

**Evidence:** probe จาก committed receipt ลบ `dependency_digest`, `git_commit`, `git_tree_dirty`, `stdout_sha256`, `stderr_sha256`, `started_utc`, `finished_utc` แล้ว recompute `outer_receipt_sha256`:

```text
missing_m1 terminal= PASS errs= []
```

**Suggested change:** validator ต้องบังคับ exact required process schema และ semantics อย่างน้อย: non-empty argv list, exit code 0, parseable ordered timestamps, digest ทุกตัวเป็น lowercase SHA-256, full Git commit, `git_tree_dirty is False` และ source/dependency identity ห้ามว่าง

เพิ่มเติมที่ path จริงต้องปิดพร้อมกัน: evaluator ใส่ `/host` ไว้หน้า `sys.path` (`p2_m4_evaluator.py:11`) แต่ `_git_identity()` จงใจไม่ดู untracked files (`p2_m4_controller.py:172-180`) จึงยังมี untracked `.py`/package shadowing ได้โดย receipt บอก clean ทางแก้แบบ bounded คือ mount source read-only, reject untracked Python/package files และ bind tracked-tree/source-manifest digest ไม่ต้องบังคับให้ untracked docs/tmp ทำ run fail

### B3 — Qdrant/run/isolation fields บางตัวถูกบันทึกแต่ไม่ load-bearing

**Finding:** validator ไม่ตรวจ observed Qdrant image/ref, `network_internal`, endpoint, top-level run ID เทียบ inner run ID และ process/run bindings แม้ field เหล่านี้อยู่ใน receipt (`p2_m4_receipt.py:73-120`, `123-188`)

**Why it matters:** committed schema ดูครบ แต่ receipt ที่ Qdrant image ผิด, ลบ network mode หรืออ้าง run ID คนละตัว ยังสามารถถูก re-hash แล้วผ่าน `PASS` เป็น formal evidence ได้

**Evidence:** fault probes จาก committed artifact หลังแก้ body และ recompute receipt hash:

```text
wrong_qdrant_image      terminal= PASS errs= []
missing_network_internal terminal= PASS errs= []
top_run_mismatch        terminal= PASS errs= []
```

**Suggested change:** เพิ่ม exact/cross-field validation เฉพาะค่าที่ receipt อ้างเป็น authority:

- top-level `run_id == inner.run_id` และ attempt/run fields เป็น non-blank typed values
- `network_internal` ต้องเป็น bool และตรงกับ controller mode (รอบนี้ `False` ยอมรับได้เพราะ disclose แล้ว)
- Qdrant runtime identity ต้องพิสูจน์จาก Docker observation: เก็บ inspected `RepoDigests` แล้ว require pinned `qdrant_image_ref` อยู่ในชุดนั้น; อย่าเทียบ manifest digest กับ image ID โดยสมมติว่าเหมือนกันเสมอ
- endpoint/collection/project fields ที่ประกาศเป็น controller authority ต้องมี type/presence และ cross-bind กับ inner proof ตาม contract
- cleanup schema ต้อง exact: `confirmed` เป็น bool, `residual` เป็น list และ PASS ต้อง `confirmed is True` พร้อม empty list

## Targeted acceptance ก่อน close

ไม่ต้องเพิ่ม edge-case อื่นนอก freeze exception รอบนี้ ให้พิสูจน์เพียง:

1. Docker daemon/permission/inspect error หลัง inner PASS → outer terminal `DEGRADED`, ไม่ใช่ PASS
2. ลบหรือทำ malformed process/source/dependency field ทีละกลุ่ม → validator error + terminal ไม่ใช่ PASS
3. Qdrant repo digest ไม่ตรง, network mode หาย และ top/inner run ID ไม่ตรง → validator error + terminal ไม่ใช่ PASS
4. rerun synthetic หนึ่งครั้ง → committed inner+outer artifacts validate ผ่าน strict validator, cleanup confirmed จาก successful absence probes และ no leftover จริง

## Gate

- **M4a capability:** `DEMONSTRATED`
- **outer-receipt formal closure:** `FIX-THEN-CLOSE` เฉพาะ B1–B3
- **หลัง B1–B3 + bounded rerun:** `GO CLOSE + FREEZE`; finding อื่นเข้า backlog ตาม policy เดิม
- **Data Owner pack:** `GO` ทำขนานได้ทันทีและควรเริ่มระหว่างแก้รอบนี้
- **M4b / N-sweep / decision benchmark / production:** ยังคง `NO-GO` ตาม `STATUS.md`

สรุป: architecture outer receipt ถูกชั้นและรอบจริงน่าเชื่อว่ารันสำเร็จ แต่ “field อยู่ใน JSON” ยังไม่เท่ากับ “field บังคับ verdict” ให้ทำ schema/cross-field checks เป็น load-bearing และแยก cleanup `ABSENT` ออกจาก `UNKNOWN` แล้วปิด slice ได้เลย
