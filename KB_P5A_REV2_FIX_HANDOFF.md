# P5a rev2 — ปิด Codex FIX-THEN-GO (B1-B4, M1-M4, N1) → handoff review

> **สืบเนื่อง:** `KB_P5A_MEASUREMENT_CONTRACT_REVIEW.md` (commit `5ba9242`) verdict **FIX-THEN-GO**
> **แกนที่ redesign:** ทิ้ง collection-based /ask leak check → มาเป็น **/search + synthetic canary manifest + positive/negative pair + point-id leak + transport/retrieval แยกแกน + suite exit-code ที่ test ได้**
> **ขอบเขตเดิม:** local + synthetic · ยังไม่ deploy · NO-GO ห้ามแตะ Qdrant prod ยังบังคับ · end-to-end run จริง = P5b

## Blocker หลักที่ปิด
Codex: *"เมื่อทุก request ได้ DENIED หรือ NO_RESULT ตัว suite ยัง exit 0 ได้ บั๊ก 'เขียวผิดเหตุผล' ยังไม่ปิดจริง"*

**ปิดด้วย:** เขียวได้เฉพาะเมื่อ **ทุก canary pair == PASS** โดย PASS ต้องมี `positive control` (role มีสิทธิ์ **เจอ** canary) คู่กับ `negative control` (role ไม่มีสิทธิ์ **ไม่เจอ**). ถ้า transport ไม่ SUCCESS หรือ positive หา canary ไม่เจอ → `INCONCLUSIVE` → suite fail. deny/empty ทั้งชุดจึงเป็นไปไม่ได้ที่จะเขียว.

**พิสูจน์ (harness test รันจริง, offline):**
| scenario | ผล |
|---|---|
| DENIED ทั้งชุด | `exit 1` (ทุก pair INCONCLUSIVE) |
| NO_RESULT ทั้งชุด (ไม่มี positive control) | `exit 1` |
| ระบบถูกต้อง (pos เจอ, neg ไม่เจอ) | `exit 0` — **เส้นเดียวที่เขียวได้** |
| forbidden role ได้ canary point | `exit 1` (LEAK) |

## Findings → fix → proof
| # | Finding | Fix | Proof |
|---|---|---|---|
| **B1** | DENIED/NO_RESULT ยัง exit 0 | `pair_verdict` (positive+negative required) + `suite_exit_code`; ไม่เขียวถ้าไม่มี PASS ครบ | harness #2,#3,#6 + unit `all-INCONCLUSIVE→exit 1` |
| **B2** | outcome รวม transport กับ retrieval | แยก `classify_transport` (SUCCESS/DENIED/ERROR/MALFORMED) จาก `retrieval_outcome` (HAS/NO_RESULTS); no_answer ไม่หลุด denominator | unit transport×8 + retrieval×2 |
| **B3** | expected policy มาจาก config เดียวกับที่ทดสอบ | `permission_manifest.json` เขียนมือ อิสระจาก `rbac_config.py` (business-approved oracle) | manifest ไม่ import config |
| **B4** | claim เช็ค point-id แต่จริง ๆ ใช้ collection | leak ตัดด้วย **exact `point_id`** (`canary_found`); collection = diagnostic เฉย ๆ; `confidentiality_level` ยังไม่ผูก (รอ trusted clearance) | unit `canary_found`, harness LEAK |
| **M1** | positive control เป็น requirement | pos ไม่เจอ canary → `INCONCLUSIVE` (ไม่ pass) | unit `pos ไม่เจอ→INCONCLUSIVE` |
| **M2** | key เดียวไม่พิสูจน์ role-scope | `KB_EVAL_KEYS` role→key + `run_auth_preflight` (spoof role ต้องโดน 403) | harness #7,#8 |
| **M3** | malformed/partial JSON ไม่ผ่าน classifier | `http_call` จับ `JSONDecodeError/UnicodeDecodeError`→MALFORMED; `extract_points` validate shape (ไม่ log raw body) | unit extract×5, harness #5 |
| **M4** | blank source นับเป็น hit | `source_hit` reject `s==""` ก่อน substring | unit `blank source ไม่เป็น hit` |
| **N1** | test ล้ม cp874 | test ห่อ stdout เป็น UTF-8 เอง (`TextIOWrapper`) | **รันใต้ cp874 ไม่ crash** (ไม่ต้องตั้ง env) |

## ผลรัน (offline, ไม่มี stack)
```
test_eval_contract.py     45/45 passed   (pure decision core)
test_ask_eval_harness.py   8/8 passed    (inject call_fn, assert exit_code)
```

## ตรงกับ "Minimum acceptance ก่อน GO P1" ครบ
- [x] all-DENIED/all-NO_RESULT ไม่ exit 0 แบบไร้ positive control
- [x] transport แยกจาก retrieval outcome
- [x] independent synthetic point manifest (ไม่ derive จาก rbac_config)
- [x] /search positive/negative pair ต่อ canary (role-scoped keys)
- [x] point ID ตัด leak จริง; missing/deny → inconclusive (fail-closed)
- [x] malformed JSON/shape → MALFORMED + non-zero
- [x] blank source ไม่เป็น hit
- [x] test main()/exit behavior เพิ่มจาก pure helper tests

## ยังไม่ทำ (จงใจ — gated เป็น P5b)
- **end-to-end run จริง:** ต้อง ingest canary 4 ตัว (`CANARY-RECALL/SALES/HR/PURCHASING-001`) เข้า test collection พร้อม `authorized_roles` ตาม manifest + ออก role-scoped key ต่อ role → แล้วรัน `ask_eval.py` กับ stack. ทั้งหมดนี้ต้องมี Qdrant/keys — ยังไม่แตะตาม NO-GO
- **config-conformance test แยก:** เทียบ manifest กับ `rbac_config.COLLECTIONS` เพื่อจับ drift (Codex B3 แนะเป็นชุดแยก) — ยังไม่ทำ
- `confidentiality_level` ในการตัด leak — เว้นไว้จนมี trusted caller clearance (ตาม B4 + review เดิม)

## ขอ Codex ยืนยัน
1. **contract ปิด B1-B4/M1 ครบไหม** ในเชิง logic (proof = harness exit-code tests) — พอเป็น gate ของ P1 หรือยังมีช่องที่ต้องปิดก่อน
2. positive/negative pair ที่ /search + point-id oracle เป็นนิยาม leak ที่ยอมรับได้ไหม
3. ไฟเขียว **P1 (auth + policy compiler + effective ACL, fail-closed — ไม่ใช่ role AND conf AND group ตรง ๆ)** ได้หรือยัง หรือขอปิด P5b (end-to-end run) ก่อน
