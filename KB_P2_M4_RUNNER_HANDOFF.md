# P2 — M4a real-path runner + atomic writer (injectable) → runner-review ก่อน GO M4a run

> **สืบเนื่อง:** `KB_P2_M4_HARNESS_FIX5_CODEX_REREVIEW_0A033C4.md` (GO เขียน real-path runner + atomic writer)
> **pure/injectable เท่านั้น** — runner ไม่ import qdrant_client/torch · **ยังไม่รัน M4a บน Qdrant/model จริง (NO-GO)**

## ส่งมอบรอบนี้
- **`p2_m4_runner.py`** — orchestrator ล้วน ฉีด infra ผ่าน `ports` (scorer/isolation/provider/oracle/clock) ; fail-closed ทุกเส้น
- **`p2_atomic.py`** — publisher: validate public bundle → temp→rename atomic → ไม่ทิ้ง PASS artifact ถ้า fail → immutable
- **`test_p2_m4_runner.py` 22/22**, **`test_p2_atomic.py` 14/14** — offline, fake ports

## Codex load-bearing checks (§84–91) → runner บังคับตรงไหน

| # | check | ในโค้ด |
|---|---|---|
| 1 | `initial_point_count` มาจาก count ก่อน seed จริง | `iso.observe_initial_count()` เรียก **ก่อน** `iso.seed()` ; ค่าเข้า `build_isolation_proof` ; validator บังคับ `==0` |
| 2 | `network_published_ports` มาจาก inspect network จริง | `iso.observe_published_ports()` (call แยก) → proof ; validator บังคับ `==0` |
| 3 | `endpoint_is_production` derive ไม่ hardcode | `iso.observe_endpoint_is_production()` (call แยก) → proof ; validator บังคับ `is False` |
| 4 | `marker_readback` = read-after-write ผ่าน target เดียว | `iso.write_marker(m)` → `iso.read_marker()` (คนละ call) ; runner ส่ง **ค่าที่อ่านกลับ** (ไม่ reuse `marker`) ; test ยืนยัน write ก่อน read + readback มาจาก read_marker |
| 5 | Oracle observation = independent direct scroll แยกจาก filtered provider | `ports.oracle` แยกจาก `ports.provider` ; `oracle.unfiltered_topn()` + `oracle.observe_visibility()` ; validator เทียบ observed == frozen exact |
| 6 | atomic writer ไม่ทิ้ง PASS artifact เมื่อ fail + validate bundle ก่อน publish | `p2_atomic.publish_m4_bundle`: `validate()` ก่อนเขียน ; temp dir → `os.replace` ; exception→ลบ temp ; immutable |

**เพิ่มเติมที่ runner บังคับเอง (fail-closed):**
- `validate_scorer_metadata` ก่อน provision — mock/pin ผิด → raise (ไม่มี artifact)
- `run_case` guard — sentinel ถึง model boundary → `PermissionError` (filter ต้อง load-bearing จริง)
- `build_run_verdicts` derive status/counts จาก proof+records จริง — interlock/oracle ผิด → `status=FAIL` → bundle invalid → publish **refused**
- `teardown()` ใน `finally` — เรียกเสมอ แม้ leak/refuse
- run metadata (pin/index/eval/corpus/run_id) derive จาก **RunPlan** ชุดเดียว (ไม่รับจาก caller ลอย)

## negative tests (offline) ที่ครอบ
mock scorer → TypeError+ไม่มี artifact · provider ปล่อย sentinel → PermissionError+teardown · `initial_count=5`/`published_ports=1`/`endpoint_is_production=True`/`readback` ไม่ตรง → publish refused+ไม่มี artifact · oracle observed ≠ frozen → refused · รัน run_id ซ้ำ → PublishRefused (immutable) · exception ระหว่างเขียน → temp ถูกลบ+ไม่มี final · effective_role/cases ผิด → RunnerError

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch ครบ)
```
test_p2_m4_runner 22/22   test_p2_atomic 14/14   test_p2_m4_harness 46/46   test_p2_m4 56/56
test_p2_runplan 95/95     test_p2 166/166        test_p2_provider 22/22     test_p2_harness 21/21
test_p2_pin 14/14  test_p2_adapter 22/22  test_p2_dockerbuild 41/41  test_policy 69/69
test_eval_contract 64/64  test_ask_eval_harness 12/12  test_auth 11/11  test_p5b_fixtures 11/11
```
- **รวมเครื่องนี้ (16 suites, มี qdrant_client): 686/686**
- **clean env (ไม่มี qdrant_client): 642/642** (core 606 + runner 22 + atomic 14 ; runner/atomic เป็น pure ไม่ต้องพึ่ง qdrant)

> `p2_m4_runner` import แค่ `p2_atomic/p2_eval/p2_m4_harness/p2_runplan` (clean-importable ทั้งหมด) — ยืนยันว่า runner ไม่ดึง qdrant_client/torch

## ยังไม่ได้ทำ (ขั้นถัดไป — real adapters, ยัง NO-GO ที่จะ *รัน*)
port contract ข้างบนคือ interface ที่ **real adapter** ต้อง implement บน infra จริง (จะเขียน+ให้ review เป็น slice ถัดไป):
- **IsolationController จริง** — สร้าง isolated project/network/volume/collection (UUID จริง) → count(before-seed) → inspect published ports → classify endpoint (non-prod) → write+read marker → seed synthetic corpus → teardown
- **FilteredProvider จริง** — wrap `p2_provider.build_candidates` (compiled RBAC filter ก่อน retrieval บน isolated Qdrant client)
- **OracleReader จริง** — **client แยก** direct scroll (unfiltered top-N + observed visibility ต่อ role) ไม่ผ่าน provider/compiler
- **PinnedScorer จริง** — `p2_reranker.PinnedCrossEncoder` (metadata ตรง M4RunRequest แล้ว)
- CLI wrapper — argv/stdout/stderr/exit จริง เข้า receipt

## ขอ Codex review (runner slice)
1. orchestration contract fail-closed ครบไหม — มีเส้นไหนที่ออก PASS artifact ได้โดย observation/​proof ไม่ครบ
2. `p2_atomic` all-or-nothing + validate-before-publish + immutable — พอเป็น atomic control ไหม (หรือควรบังคับ 2-file commit แบบอื่น)
3. port contract (§ตาราง) ครอบ provenance ที่ต้อง trace ใน real adapter ครบไหม ก่อนผมเขียน adapter slice
4. หลังผ่าน → GO เขียน real adapters ; **M4a run บน isolated Qdrant ยัง NO-GO** จน adapter review + Data Owner sign-off

**Gate:** runner/atomic implementation review = **FIX-THEN-GO** · real adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter provenance + negative controls review ผ่าน + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
