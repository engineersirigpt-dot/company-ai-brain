# P2 — ปิด runner B1-B4/M1-M2 (fail-before-mutate + provenance bind + lifecycle + single-file bundle)

> **สืบเนื่อง:** `KB_P2_M4_RUNNER_CODEX_REVIEW_BFA69A0.md` (FIX-THEN-GO real adapters — 4 blockers + 2 major)
> **pure/injectable เท่านั้น** — รัน M4a บน Qdrant/model จริง = **NO-GO** จน adapter-review + Data Owner sign-off

## Finding → fix → proof

| # | ช่อง | Fix |
|---|---|---|
| **B1** ⭐ | interlock ผิดยัง write marker + seed + เรียก model ก่อนถูกปฏิเสธตอน publish | **fail-before-mutate**: เช็ค exact `count==0`/`ports==0`/`prod is False` **ทันทีหลัง observe และก่อน `write_marker`** → ผิด = `RunnerError` (write/seed/model ไม่เกิด) ; หลัง write/read สร้าง+`validate_m4_isolation_proof` **ก่อน `seed`** → marker mismatch = abort ก่อน seed/model |
| **B2** ⭐ | corpus ที่ seed ไม่ผูก `RunPlan.corpus_manifest_sha256` (seed corpus A แต่ evidence อ้าง B) | ก่อน provision: บังคับ corpus เป็น dict → `E.validate_corpus == []` → `E.corpus_manifest_sha256(corpus) == RunPlan` exact ; mismatch = `RunnerError` ก่อน provision/seed/model |
| **B3** ⭐ | provider/oracle ไม่ผูก isolation target — evidence อ้าง collection_id แต่พิสูจน์ไม่ได้ว่า query collection นั้นจริง | เพิ่ม contract `provider/oracle.bind(handle)` + `.observed_target_identity()` ; runner เทียบ **exact == isolated handle {collection_id,endpoint}** ก่อน seed/query (client แยกยังคงแยก) ; mismatch = abort ก่อน seed |
| **B4** ⭐ | provision อยู่นอก try (teardown ข้าม) ; publish ก่อน teardown (teardown fail ทิ้ง PASS artifact ไว้) | cleanup ครอบ provision — `_safe_teardown` ทุก failure (teardown ต้อง idempotent/partial-safe) ไม่กลบ original exception ; **work → assemble evidence → teardown → build receipt → validate → publish** (teardown fail = `RunnerError` ไม่มี PASS artifact) |
| **M1** | `run_id` เป็น path injection (`x/../../escape` หลุดออก out_dir) | RunPlan บังคับ `SAFE_RUN_ID_RE ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` ; publisher reject separator/`.`/`..`/absolute/drive/UNC/**reserved** + `realpath` containment (parent == out_dir) |
| **M2** | dir rename ไม่ fsync parent (atomic-visibility แต่ไม่ crash-durable) | เปลี่ยนเป็น **single bundle file** `<run_id>.bundle.json` = `{evidence,receipt}` (Codex แนะ ง่าย+เสี่ยงน้อยกว่า) : temp fsync → `os.replace` → **fsync parent** (best-effort บน Windows, ระบุ durability level) |

## negative tests (offline) ที่เพิ่ม/แก้ (ปิด test-gap ที่ Codex ชี้)
- **B1**: count/ports/prod ผิด → assert **`write`/`seed` ไม่ถูกเรียก + provider/model ไม่ถูกเรียก** ; marker mismatch → write/read เกิด แต่ seed/model ไม่เกิด
- **B2**: corpus ผิด digest / ไม่ใช่ dict → `RunnerError` **ก่อน provision** (`isolation.calls == []`)
- **B3**: provider target mismatch / oracle target mismatch → abort **ก่อน seed** (model ไม่ถูกเรียก)
- **B4**: provision raise → **teardown ยังถูกเรียก** ; teardown raise หลัง work → `RunnerError` + ไม่มี artifact + work รันจริง (พิสูจน์ teardown ก่อน publish)
- **M1**: `run_id` traversal (`/`,`\`,`..`,absolute,drive,UNC,reserved,unicode,>128) → refuse + ไม่มีไฟล์หลุด ; run_id ผิดใน plan → `RunnerError` ก่อน provision
- (คงเดิม) mock scorer / sentinel leak / oracle observed≠frozen / immutable / bad case

## ผลรัน (offline — stdout จริง เครื่องนี้มี qdrant_client/torch)
```
test_p2_m4_runner 31/31   test_p2_atomic 16/16   test_p2_m4_harness 46/46   test_p2_m4 56/56
test_p2_runplan 95/95     test_p2 166/166        test_p2_provider 22/22     test_p2_harness 21/21
test_p2_pin 14  test_p2_adapter 22  test_p2_dockerbuild 41  test_policy 69  test_eval_contract 64
test_ask_eval_harness 12  test_auth 11  test_p5b_fixtures 11
```
- **รวมเครื่องนี้ (16 suites): 697/697**
- **clean env (ไม่มี qdrant_client): 653/653** (core 606 + runner 31 + atomic 16)

## port contract (หลัง B3) ที่ real adapter ต้อง implement
```
isolation.provision() -> {project_id,network_id,volume_id,collection_id,endpoint}
isolation.observe_initial_count()/observe_published_ports()/observe_endpoint_is_production()
isolation.write_marker(m)/read_marker()->readback · seed(corpus) · teardown()  [idempotent/partial-safe]
provider.bind(handle) · observed_target_identity()->{collection_id,endpoint} · filtered_candidates(role,qv,limit)
oracle.bind(handle)   · observed_target_identity()->{collection_id,endpoint} · unfiltered_topn(qv,limit) · observe_visibility(role)
clock.now_iso()
```

## ขอ Codex review (runner slice รอบ 2)
1. fail-before-mutate (B1) + corpus/target bind (B2/B3) + lifecycle (B4) ปิดครบไหม — มีเส้นไหน mutate/เรียก model ก่อน validate อีก
2. single-file bundle + run_id containment + fsync parent (M1/M2) พอเป็น atomic+durable control ไหม
3. port contract ครบก่อนเขียน real adapters ไหม
4. หลังผ่าน → **GO เขียน real adapters** ; M4a run ยัง NO-GO จน adapter provenance review + Data Owner sign-off

**Gate:** runner/atomic review = **FIX-THEN-GO** · real adapters = รอ review นี้ผ่าน · M4a run = **NO-GO** จน adapter review + Data Owner sign-off (hash-bound) · N-sweep = รอ validated M4a PASS · decision benchmark = NO-GO จน sign-off + M4b + validated canary
