# P2 Slice 2 infra fix — ปิด Codex B1/B2/B3/M1/M2 → re-review ก่อนเปิด Docker

> **สืบเนื่อง:** `KB_P2_SLICE2_INFRA_CODEX_REVIEW_B183FA5.md` (FIX-THEN-GO Slice 2 real run)
> **ขอบเขต:** pure/offline ทั้งหมด · decision benchmark ยัง NO-GO จน Data Owner sign-off + real M4/canary PASS

## Finding → fix → proof (test_p2 145/145 · provider 22/22)
| # | Finding | Fix | Proof |
|---|---|---|---|
| **B1** | `EffectiveAccess` trusted แค่ isinstance — forged/unverified/warn ผ่าน provider ได้ | `_assert_trusted_access`: บังคับ `principal.verified` (authenticated+enforce) + `effective_role ∈ KNOWN_ROLES` + `∈ principal.allowed_roles` | forged(auth=False)/warn/role-mismatch → **PermissionError** |
| **B2** | provider ไม่มี authorization postcondition — backend คืน point ผิดสิทธิ์ถึง reranker ได้ | postcondition ต่อทุก returned payload: `payload_is_policy_v1` + `validate_stored_payload` + `matches_policy(payload, spec)` — mismatch = **fail ทั้ง batch** (ไม่ drop เงียบ) | BuggyQdrant คืน sales-only ให้ qc → PermissionError; non-v1 payload → PermissionError |
| **B3** | `benchmark_manifest()` bypass combined gate ได้ (footgun) | แยก entry: `artifact_manifest_unapproved` (mechanics smoke, `approved=False`) vs **`decision_benchmark_manifest`** (เรียก combined gate + require real M4 + P5b canary evidence, bypass ไม่ได้) | decision manifest ขาด signoff/m4/canary → ValueError |
| **M1** | combined gate crash เมื่อ artifacts malformed (None/NaN) | `decision_benchmark_errors` **short-circuit** เมื่อ structural fail (ไม่ hash); `validate_signoff` wrap hashing ใน try/except → controlled error | cases=[]/corpus=None/NaN → controlled list (ไม่ crash) |
| **M2** | payload normalization edges (heading non-str crash, source=None→"None", top_n ไม่มี cap) | `build_rerank_text` บังคับ str (heading/text/rerank_text) + max_chars positive; `build_candidates` source ต้อง non-blank str, score numeric, `top_n ∈ 1..MAX_TOP_N(200)` | heading int/max_chars 0/top_n>200/source None → ValueError |

## ผลรัน (offline)
```
test_p2 145/145 · test_p2_provider 22/22 · policy 69 · eval 64 · harness 12 · p5b 11 · auth 11
```
generator: corpus 166 · test 50 intents · arm_eligibility + dev_role_coverage PASS · decision_benchmark BLOCKED 132 (short-circuit ทำงาน)

## หมายเหตุ real M4 (Slice 2 run — ยังต้องทำบน Docker)
provider postcondition ใช้ `matches_policy` (fail-closed detector, defense-in-depth). **real M4 harness** ต้องสร้าง `authorized_ids` จาก **independent raw-scroll oracle** (ไม่ใช่ compiler/matcher ตัวเดียวกับ provider) + spy รอบ model/tokenizer จริง assert exact input pairs + sentinel **ID hashes** ไม่ปรากฏ (ไม่ใช่แค่ค้นคำว่า SENTINEL)

## ขอ Codex re-review
1. B1/B2/B3/M1/M2 ปิดครบใน pure boundary ไหม
2. อนุมัติ **เขียน cross-encoder adapter + benchmark harness (pure/offline)** ต่อ โดย wire ผ่าน `build_candidates`/`decision_benchmark_manifest` ที่แก้แล้ว
3. หลังผ่าน → เปิด Docker รัน real M4 + N sweep (decision ยัง NO-GO จน sign-off + real M4/canary)
