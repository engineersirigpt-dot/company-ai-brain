# P2 eval-set rework — ปิด Codex B1-B6/M1-M2 → re-review (ยัง ai-reviewed, ไม่ freeze)

> **สืบเนื่อง:** `KB_P2_EVALSET_CODEX_REVIEW_241DA10.md` (REWORK-BEFORE-HUMAN-REVIEW)
> **สถานะ:** `label_status="ai-reviewed"` — **ยังไม่ human-reviewed** (B6: รอ Data Owner ลงชื่อจริง). ยังห้าม freeze/รัน decision benchmark/เลือก arm

## Finding → fix → proof
| # | Finding | Fix | Proof (generator report) |
|---|---|---|---|
| **B1** | authorized pool ต่อ role 2-14 → N sweep ไร้ความหมาย | **shared distractor bank 64 จุด (IT_SYSTEMS = ทุก role เห็น)** + answer/twin → pool ต่อ role | pool: qc=101, production=101, engineering=100, sales=93, purchasing=80, logistics=80, hr=75, it=66 — **ทุก role ≥60** |
| **B2** | paraphrase ข้าม dev/test → CI แคบเกินจริง | `intent_id` + **group-stratified split** (paraphrase อยู่ split เดียว, validator บังคับ) + arm_eligibility ≥50 test intents | **test 51 independent intents ≥50**, `arm_eligibility PASS` |
| **B3** | category ปน lang+challenge | `challenge_tags` แยกจาก `lang` (lang-independent ต่อ intent) + **real table-row** (rows param=value) + **current-superseded** (คู่ rev เก่า/ใหม่ + hard-neg=superseded) + gate ทุก tag ≥5 test intents | sibling 6, table 5, negation 5, superseded 5, lexical 5, multi 5 (ทุก gate ≥5) |
| **B4** | query ที่ chunk ไม่ตอบตรง (torque/PR-vs-PO/ISO-KPI/negation implication) | ลบ/เขียนใหม่: torque อยู่ในตารางมีตัวเลข, negation chunk มี rule บวก+ลบ ชัด, ตัด PR-vs-PO/ISO-KPI ที่คลุมเครือ | struct errors = 0 |
| **B5** | grade 3/2 ตามลำดับ ไม่มี rubric | `GRADE_RUBRIC` (3=ตอบครบโดยลำพัง, 2=supporting, 1=contextual) + **`grade_rationale` ต่อ pid บังคับ** เมื่อ multi-relevance | graded-multi ทุก case มี grade_rationale |
| **B6** | Codex/Claude review ≠ human-reviewed | `label_status="ai-reviewed"` + `reviewed_by`/`review_revision` ; validator ยังบังคับ human-reviewed เป็น benchmark gate (AI เปลี่ยนไม่ได้) | label_status errors = expected (รอ human) |
| **M1** | query templated/copy heading | query natural/colloquial + Thai/Thai-Eng variant (ไม่ copy heading), source ไม่โผล่ในคำถาม | 118 cases, variant tag |
| **M2** | source ดูเหมือนเอกสารจริง | source prefix **`P2-SYNTH-*`** + `payload.synthetic=true` | ทุก point |

## Validator ที่เพิ่ม (contract v2, `p2_eval.py`) — test_p2 **130/130**
- required fields: `intent_id`, `challenge_tags`, `hard_negative_ids` ; `hard_negative` ต้อง authorized + ไม่อยู่ใน relevance ; multi-relevance ต้องมี `grade_rationale`
- **split consistency**: paraphrase ของ intent เดียวข้าม split → error
- `arm_eligibility_errors(cases, gate_tags)`: ≥50 test intents + gate tag ≥5 (แยกจาก structural/hash gate → smoke set ยัง hash ได้)

## ตัวเลขชุดใหม่
```
corpus 159 points (bank 64 + answers/twins, synthetic=true) · cases 118 (dev 16 / test 102)
intents dev 8 / test 51 · corpus valid · struct errors 0 (เว้น label_status draft) · arm_eligibility PASS
```
regression: test_p2 130/130 · policy 69 · eval 64 · harness 12 · p5b 11 · auth 11

## Go/No-Go (คงตามเดิม)
- **GO:** ใช้ชุดนี้ smoke mechanics ของ Slice 2 (candidate provider/M4/scorer) โดยระบุ mechanics-only
- **NO-GO:** freeze / decision benchmark / เลือก arm / `human-reviewed` — จนกว่า Data Owner ลงชื่อ (B6) และ Codex ยืนยัน rework

## ขอ Codex re-review
1. B1-B6/M1-M2 ปิดครบไหมในเชิง structure/contract — เหลือจุด content quality ไหนก่อน human sign-off
2. hard-negative แข็งพอเป็น challenge จริงไหม (ตอนนี้ synthetic templated — human review ปรับได้)
3. อนุมัติให้ **เดิน Slice 2 infra + smoke ด้วยชุดนี้** ระหว่างรอ human label sign-off ได้ไหม (สอง track ขนานกัน)
