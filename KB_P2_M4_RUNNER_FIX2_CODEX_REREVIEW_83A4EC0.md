# Codex targeted re-review — P2 M4 runner FIX2 (`83a4ec0`)

วันที่รีวิว: 2026-08-06  
ขอบเขต: re-review เฉพาะ B1–B2/M1–M3 จาก `KB_P2_M4_RUNNER_FIX2_HANDOFF.md`  
ข้อจำกัดที่รักษาไว้: pure/offline เท่านั้น — ไม่เขียน real adapters, ไม่แตะ Docker/Qdrant/model, ไม่แก้ `STATUS.md`

## Intent

ทำให้ M4a runner ปฏิเสธ input ที่ผิดก่อน provision/seed/model, publish evidence แบบ immutable ภายใต้ concurrent writers และรายงาน cleanup/durability failure ตามความจริง ก่อนอนุญาตให้เขียน real adapters

## Verdict

**FIX-THEN-GO real adapters**

ของเดิมที่ปิดได้จริง:

- `os.link(temp, final)` ปิด check-then-replace overwrite race บน filesystem ที่รองรับ hard link และ atomic create-if-absent
- concurrent test บนเครื่องนี้ได้ผู้ชนะหนึ่งรายและ `PublishRefused` หนึ่งรายจริง
- shared `is_safe_run_id()` ใช้ `fullmatch` และปฏิเสธ reserved/control/newline ก่อน provision แล้ว
- isolation teardown failure ถูกแนบไว้กับ primary exception โดยไม่กลบ root cause
- case set/role/query text/query vector ถูก preflight ก่อน provision จริง

แต่ยังเหลือ **1 blocker** ที่ทำให้ frozen input บางแบบผ่าน preflight แล้วค่อยล้มหลัง seed/model และ **2 major** ใน artifact cleanup/durability semantics จึงยังไม่ปลด gate ให้เขียน real adapters

## ทางเลือกที่เล็กที่สุด

ไม่ต้องเปลี่ยน architecture หรือทิ้ง hard-link publisher: ขยาย frozen validator/preflight อีกสอง invariant, แยก temp-unlink failure ออกจาก best-effort cleanup และจำแนก directory-open failure ตาม platform/errno แทนการเหมารวมว่า unsupported ทุกกรณี

## Findings

### B2.1 — frozen preflight ยังไม่เท่ากับ public gate จึงยังเรียก model ก่อนพบ manifest ที่ไม่สอดคล้อง (blocker)

ตำแหน่ง: `p2_m4_runner.py:61-98`, `p2_eval.py:134-190`, `p2_eval.py:588-593`, `p2_eval.py:831-832`

`_preflight_frozen_cases()` เรียก `validate_m4_frozen_manifest()` แล้วตรวจ digest/roles/categories/case query แต่ validator ปัจจุบันตรวจ `role_identity_sha256` แค่ว่าเป็น hash ไม่ได้ recompute ว่าตรงกับ `effective_role` และไม่ตรวจ `frozen.m4_case_manifest_sha256` ว่า ถ้ามี ต้องเท่ากับ digest ของ body

ผลคือ manifest สองแบบนี้ผ่าน preflight:

1. `role_identity_sha256` เป็น hash รูปแบบถูกแต่ไม่ใช่ hash ของ `effective_role` โดย RunPlan ถูกสร้างจาก digest ของ manifest ชุดนั้น
2. embedded `m4_case_manifest_sha256` เป็นค่าผิด (field นี้ไม่อยู่ใน body ที่ใช้คำนวณ digest)

แบบแรกถูก public gate จับที่ `p2_eval.py:588-589`; แบบที่สองถูกจับที่ `p2_eval.py:831-832` — ทั้งสองจุดเกิดหลัง `seed()` และ scorer ทำงานแล้ว

targeted probes:

```text
bad_role_identity:
  frozen validator errors=[]
  runner preflight=PREFLIGHT_OK
  role_identity_matches=False

bad_embedded_manifest_digest:
  frozen validator errors=[]
  runner preflight=PREFLIGHT_OK
  embedded_matches=False
```

ผลกระทบ: real M4 run สามารถ provision/seed/เรียก model จนครบแล้วจึงได้ `PublishRefused` ทั้งที่ input ผิดตั้งแต่ก่อนเริ่ม ขัดกับ fail-before-mutate/cost contract ของ slice นี้

แก้ขั้นต่ำ:

1. ใน authoritative frozen validator ตรวจ `role_identity_sha256 == typed-id hash(effective_role)`
2. หาก frozen มี `m4_case_manifest_sha256` ให้บังคับว่าเท่ากับ recomputed digest; หาก contract ไม่ต้องใช้ field นี้ ให้ reject/remove ไปเลยเพื่อตัด two-source ambiguity
3. เพิ่ม tests ของทั้งสองรูปแบบ โดย assert `isolation.calls == []` และ `scorer.queries == []`

### M3.1 — `_fsync_dir()` เหมารวม directory-open failure ทุกชนิดเป็น “unsupported” และอาจรายงาน PUBLISHED ทั้งที่ POSIX durability ล้ม (major)

ตำแหน่ง: `p2_atomic.py:41-51`, `p2_atomic.py:101-105`

โค้ดจับ `OSError` ทุกตัวจาก `os.open(path, O_RDONLY)` แล้วคืน `"unsupported"` โดยไม่ดู platform หรือ errno ดังนั้นบน POSIX ทั้ง `EACCES`, `EIO`, `EMFILE`, `ENOENT` และ failure จริงอื่น ๆ ถูกลดระดับเป็น Windows limitation และ publisher คืน success

targeted probe:

```text
os.open(directory) -> OSError("simulated directory open I/O failure")
_fsync_dir(...) -> "unsupported"
```

ผลกระทบ: contract ที่ว่า “POSIX genuine failure → DurabilityUnconfirmed” ยังไม่จริงครบ และ artifact อาจถูกประกาศ `PUBLISHED` โดยไม่มีหลักฐานว่า parent-directory metadata durable

แก้ขั้นต่ำ:

- บน POSIX ให้ propagate directory-open `OSError` ทุกชนิด
- อนุญาต `unsupported` เฉพาะ platform/capability ที่ระบุชัด (เช่น Windows limitation ที่ตรวจแบบ explicit) ไม่ใช่จาก catch-all
- เพิ่ม fault-injection แยก `os.open` fail กับ `os.fsync` fail; ทั้งคู่ต้องได้ `DurabilityUnconfirmed` บน POSIX
- ถ้า Windows atomic-visibility-only เป็นสถานะที่ยอมรับ ให้ expose/persist durability mode ใน result/receipt หรือ operational manifest ไม่ใช่อยู่เพียง docstring

### M3.2 — temp unlink failure ถูกกลืน ทำให้คืน PUBLISHED พร้อม hidden hard-link หรือทิ้ง losing body หลัง collision (major)

ตำแหน่ง: `p2_atomic.py:54-58`, `p2_atomic.py:93-104`

`_unlink()` กลืน `OSError` ทั้งใน success path และ exception/collision path หลัง `os.link()` สำเร็จ หาก unlink temp ล้ม publisher ยังคืน final path ตามปกติ ทั้งที่ temp name ยังอยู่และชี้ inode เดียวกับ final; หากเป็น concurrent loser temp อาจเก็บ body ของผู้แพ้ไว้แม้ caller ได้ `PublishRefused`

targeted probe:

```text
publish returned: run-temp.bundle.json
directory names: [.run-temp.<random>.tmp, run-temp.bundle.json]
same inode: True
```

ผลกระทบ: contract “ไม่มี temp ค้าง” ไม่จริง, อาจมี evidence body ที่ไม่ authoritative ค้างใน output directory และ success status ซ่อน cleanup failure

แก้ขั้นต่ำ:

- success path: unlink failure หลัง final ถูก link แล้วต้อง surface เป็นสถานะแยก เช่น `CleanupUnconfirmed(final_path=...)`; ห้ามคืน `PUBLISHED` ปกติ
- collision/error path: คง primary `PublishRefused`/error แต่แนบ cleanup failure ให้ operator เห็นแบบเดียวกับ runner teardown
- เพิ่ม fault-injection ทั้ง winner-unlink และ loser-unlink พร้อม assert ว่าไม่รายงาน clean success

## Platform/filesystem note สำหรับ `os.link`

การสร้าง hard link เป็น atomic create-if-absent เมื่อ filesystem รองรับ semantics นี้ และ temp/final อยู่ filesystem เดียวกัน (โค้ดทำถูกเพราะอยู่ directory เดียวกัน) แต่ FAT/exFAT, object-backed/FUSE บางชนิด และ network filesystem/configuration บางแบบอาจไม่รองรับ hard link หรือไม่ควรถูกสมมติ semantics โดยไม่ทดสอบ

นี่ไม่เปิด overwrite fail-open — `os.link` จะล้มก่อนสร้าง final — แต่จะทำให้เสีย model run ทั้งรอบเพราะความสามารถของ output filesystem ถูกค้นพบตอน publish หลัง teardown ดังนั้น real adapter/runbook ควรมี startup capability probe บน output filesystem ที่เลือก และบันทึก filesystem/durability mode ไว้ใน run provenance

## Verification

รันบนเครื่องนี้:

```text
test_p2_m4_runner.py  41/41 PASS
test_p2_atomic.py     20/20 PASS
core suites ที่รันต่อได้ก่อน optional dependency  424/424 PASS
```

การรันต่อหยุดที่ `test_p2_provider.py` เพราะ Python environment ของ Codex ไม่มี `qdrant_client`; จึงไม่ได้อ้างว่า reproduce ตัวเลข 711/711 ของ handoff ใน environment นี้ อย่างไรก็ดี targeted suites ของ diff ผ่านครบ และ probes ด้านบนเป็นช่องที่ tests เหล่านั้นยังไม่ครอบ

## Gate หลัง review

- runner/atomic: **FIX-THEN-GO**
- real adapters: **NO-GO** จนปิด B2.1 + M3.1 + M3.2 และ targeted re-review ผ่าน
- M4a real run: **NO-GO** ตามเดิม จน adapter provenance review + Data Owner sign-off แบบ hash-bound
- N-sweep: รอ validated M4a PASS
- decision benchmark: NO-GO จน Data Owner sign-off + M4b + validated canary

**สรุป:** hard-link no-clobber และ findings ด้าน run-id/teardown ปิดจริง แต่ fail-before-mutate กับ durable-clean publish ยังไม่ครบ จึงยังไม่ควรเริ่ม real adapters
