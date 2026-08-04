# P2 — eval-set pre-signoff fixes + Slice 2 pure interfaces (offline) → review

> **สืบเนื่อง:** `KB_P2_EVALSET_REWORK_CODEX_REREVIEW_2621D40.md` (GO Slice 2 infra + FIX-BEFORE-HUMAN-SIGNOFF)
> **ทำขนานกันตามที่ Codex แนะ:** (A) แก้ 4 จุด eval-set ก่อน human sign-off · (B) เริ่ม Slice 2 pure interfaces (ไม่ต้อง Docker)

## A. Eval-set fixes → close B2.1/B4.1/B5.1/B6.1 (commit `30c09b5`)
| # | fix | proof (generator) |
|---|---|---|
| **B2.1** | `assign_splits` เลือก dev **2 intents/evaluated role** (prefer `direct` เพื่อไม่ดึง gate-challenge test ออก) + `dev_role_coverage_errors` validator | dev ครบ 8 role (2/role), test 50 intents, gate ทุก tag ≥6 |
| **B4.1** | q-0013 (D-MAINT) เปลี่ยน query เป็นสิ่งที่ chunk ตอบ ("ตรวจอะไร" ไม่ใช่ "รอบไหน") | struct errors 0 |
| **B5.1** | GRADED grade-3 ตอบครบทั้งสองส่วนจริง (ไม่ใช่ "สรุปครบ") + `grade_rationale` case-specific ; validator reject rationale generic/สั้น/ไม่ครบ pid | graded content + rationale ต่อ pid |
| **B6.1** | `decision_benchmark_errors` = **combined gate เดียว** (structural + human-reviewed labels + arm_eligibility + dev_role_coverage + **Data Owner sign-off ที่ hash ตรง**) ; `validate_signoff` ; `reviewed_by/review_revision` บังคับ | decision_benchmark **BLOCKED ถูกต้อง** (รอ human) |

**ตัวเลข:** corpus 166 · cases 132 (dev 32/test 100, test intents 50) · arm_eligibility + dev_role_coverage **PASS** · pool ต่อ role 68-105.
**ยัง `label_status="ai-reviewed"`** — decision_benchmark ปฏิเสธจนกว่า Data Owner ลงชื่อ + sign-off manifest hash ตรง (AI สร้าง sign-off เองไม่ได้)

## B. Slice 2 pure interfaces (GO-INFRA, offline) — `p2_provider.py`, test 13/13
- **candidate provider** รับเฉพาะ **trusted `EffectiveAccess`** (raw role → TypeError) → `compile_retrieval_filter` เดียวกับ API → `query_points(filter)` → Candidate list (validate_candidates) ; `top_n` internal (ไม่แตะ API cap)
- **build_rerank_text** deterministic (heading+child, ไม่สลับตามความยาว) + truncate
- **M4 pure proof** (`test_p2_provider.py`, fake Qdrant): SENTINEL (unauthorized twin, **score สูงสุด 0.99**) ถูก filter **ก่อน** retrieval → ไม่เข้า candidates → **spy scorer ไม่เคยเห็น id/text** ; scorer เห็นเฉพาะ authorized text
- `resolve_and_build`: principal + role → resolve (fail-closed, role นอก scope → AuthError) → build

## ผลรัน (offline)
```
test_p2 140/140 · test_p2_provider 13/13 · policy 69 · eval 64 · harness 12 · p5b 11 · auth 11
```

## ยังเหลือ = Slice 2 **run** (ต้อง Docker + model) — mandatory
- cross-encoder adapter จริง (`bge-reranker-v2-m3`, pinned model/tokenizer) แทน mock score_fn
- **M4 integration จริง**: seed unauthorized semantic twin ใน isolated Qdrant + independent scroll oracle + spy cross-encoder (พิสูจน์ id **และ text** ไม่ถึง model บน stack จริง) — pure test นี้พิสูจน์ logic แต่ยังไม่ใช่ integration
- N sweep {10,20,30,50} บน dev + benchmark harness (dense/rerank/fused arms) + durable evidence ผูก hashes/model/container + p5b canary ทุก arm
- **NO-GO decision benchmark / freeze / เลือก arm** จน (1) Data Owner sign-off (B6.1) + (2) Slice 2 real M4/canary PASS

## ขอ Codex review
1. eval-set B2.1/B4.1/B5.1/B6.1 ปิดครบก่อน human sign-off ไหม — เหลือ content จุดไหนให้ Data Owner ดู
2. `p2_provider.py` interface + M4 pure proof ถูกทิศไหม ก่อนต่อ cross-encoder adapter + real integration
3. อนุมัติเดิน Slice 2 run (Docker + model + real M4 + N sweep + evidence) ได้เลยไหม เมื่อพร้อมเปิด Docker
