# Codex Re-review — P2 eval-set rework

**Commit reviewed:** `2621d40`  
**Input:** `KB_P2_EVALSET_REWORK_HANDOFF.md`  
**Verdict:** **GO Slice 2 infra + mechanics smoke / FIX-BEFORE-HUMAN-SIGNOFF**

โครงสร้าง rework ปิดปัญหาใหญ่ของ draft แรกเกือบทั้งหมดแล้ว ใช้ seed isolated Qdrant, candidate-provider, M4 sentinel และ scorer smoke ได้ทันที แต่ยังไม่ควรส่งให้ Data Owner ลงชื่อหรือใช้เลือก arm จนปิด findings ด้านล่าง

## Intent / simpler alternative

ไม่ต้องขยาย framework หรือรื้อ generator อีกครั้ง วิธีเล็กที่สุดคือ:

1. ปรับ split ให้ dev ครอบ role ที่ใช้เลือก N
2. รวม human-label + sample-coverage เป็น decision gate เดียว
3. แก้ content labels ที่เหลือและทำ review sheet ให้ Data Owner

shared bank และ challenge families ปัจจุบันเก็บไว้ใช้ mechanics smoke ได้

## Findings

### B2.1 — dev set มีเฉพาะ sales/qc จึงใช้เลือก N ให้ 8 roles ไม่ได้

**ตำแหน่ง:** `p2_build_eval_set.py:71-85`, family calls ที่ใช้ `dev_first=1`, `p2_build_eval_set.py:291-299`

group split ปิด paraphrase leakage แล้วจริง แต่เลือก intent แรกของแต่ละ family เป็น dev ทำให้ distribution เป็น:

| role | dev intents | test intents |
|---|---:|---:|
| sales | 5 | 3 |
| qc | 3 | 11 |
| engineering | 0 | 5 |
| hr | 0 | 6 |
| it | 0 | 2 |
| logistics | 0 | 8 |
| production | 0 | 8 |
| purchasing | 0 | 8 |

N ถูกเลือกบน dev แต่ candidate pools/query domains แตกต่างตาม role การเลือกจาก sales/qc แปด intentsจึงไม่ใช่ group-stratified by role ตามที่ review เดิมกำหนด

**Required change:** ให้ dev มีอย่างน้อย 1 intent ต่อ evaluated role และแนะนำ 2 ต่อ role หากจะใช้ CandidateRecall>=0.95 เลือก N เพิ่ม intents รวมให้ test ยังคง >=50 independent intents และ validator ต้องมี `dev_role_coverage` gate

### B6.1 — `arm_eligibility PASS` ยังไม่ใช่ decision gate และไม่ได้ผูก Data Owner sign-off

**ตำแหน่ง:** `p2_eval.py:220-248`, `p2_eval.py:274-287`, `p2_build_eval_set.py:341-360`

ตอนนี้ `arm_eligibility_errors()` คืน PASS กับ 118 cases ที่ยังเป็น `ai-reviewed` เพราะตรวจเพียงจำนวน test intents/tags ส่วน `benchmark_manifest()` ตรวจ human label แต่ไม่เรียก arm-eligibility gate เมื่อแยกสองฟังก์ชัน caller สามารถลืม gate หนึ่งฝั่งแล้วประกาศผลผิดได้

นอกจากนี้ validator ไม่บังคับ `reviewed_by/reviewed_at/review_revision` และทุก case ปัจจุบันยังเขียน `reviewed_by="claude-ai (generator draft)"` (`p2_build_eval_set.py:308-315`) การเปลี่ยน string เป็น `human-reviewed` อย่างเดียวจึงทำให้ structural validator ผ่านได้โดยไม่มีหลักฐาน Data Owner จริง

**Required change:**

- เพิ่ม decision entry point เดียว เช่น `decision_benchmark_errors(cases, corpus, roles, gate_tags, signoff)` ที่รวม structural/human-label/coverage gate
- หรือ rename ปัจจุบันเป็น `sample_coverage_errors()` เพื่อไม่รายงาน misleading ว่า arm eligible
- ทำ sign-off manifest แยกที่ผูก final `eval_set_sha256`, `corpus_manifest_sha256`, contract version, git commit, reviewer name+Data Owner role, reviewed_at และ decision
- benchmark runner ต้อง fail หาก sign-off hash ไม่ตรง artifacts ปัจจุบัน
- AI ห้ามสร้าง/กรอก human sign-off แทนคน

### B4.1 — q-0013 ยังถาม interval ที่ relevant chunk ไม่มีคำตอบ

**ตำแหน่ง:** `p2_build_eval_set.py:107-108`, `p2_eval_set.json:317`

Query `preventive maintenance รอบไหน` ถูก label grade 3 แต่ chunk บอกเพียง “บำรุงรักษาเชิงป้องกันตามรอบ” โดยไม่ระบุว่ารอบไหน

**Required change:** เปลี่ยน query เป็นสิ่งที่ chunk ตอบได้ เช่น “preventive maintenance ต้องตรวจอะไร” หรือเพิ่ม interval ที่ชัดใน chunk

### B5.1 — `grade_rationale` เป็นข้อความ rubric ซ้ำ ไม่ใช่เหตุผลต่อ label และ grade-3 บาง chunk ยังเป็น meta-summary

**ตำแหน่ง:** `p2_build_eval_set.py:268-299`, `p2_eval.py:169-173`, cases q-0108–q-0117

ทุก multi case ใช้ rationale เดียวกันว่า “ตอบครบโดยลำพัง” / “supporting/partial” ตัว validatorตรวจแค่ non-blank จึงใส่ข้อความใดก็ผ่าน และ primary chunks หลายตัวกล่าวเพียง “สรุปครบทั้ง...” โดยไม่ได้ให้รายละเอียดที่ทำให้ตอบ query ได้ครบจริง เช่น G-QC และ G-SHIP

**Required change:**

- grade rationale ต้องระบุ case-specific reason ว่า chunk นี้ตอบส่วนใดของ query
- primary grade-3 ต้องมีเนื้อหาที่ตอบได้จริง ไม่ใช้คำว่า “สรุปครบ” เป็นหลักฐานว่าครบ
- เพิ่ม negative test ว่า rationale generic/ไม่ตรง relevant pid ไม่ผ่าน หรือให้ Data Owner review sheet บังคับ comment ต่อ graded label

## Hard-negative assessment

**เพียงพอสำหรับ mechanics smoke แต่ยังไม่แข็งพอสำหรับ decision benchmark**

- จำนวน/coverage ผ่าน: gated tags มี 5–6 test intents และ hard-negative IDs authorized จริง
- twins หลายชุดแยกง่ายด้วย token ตรง ๆ เช่น code 721/722 หรือคำว่า “ไม่ใช่/ยกเลิกแล้ว”
- shared bank 64 จุดเกิดจาก 16 topics ซ้ำสี่รอบ ต่างกันหลัก ๆ ที่เลขท้าย จึงเพิ่ม candidate depth/latency ได้ แต่ไม่เพิ่ม semantic challenge เทียบเท่า 64 independent distractors

ก่อน human sign-off ให้ Data Owner ตรวจ answer-vs-hard-negative เป็นคู่ และเพิ่มอย่างน้อยบาง twins ที่ใช้ศัพท์/รหัสใกล้กันโดยไม่เฉลยด้วยคำว่า “ไม่ใช่” หากคง corpus ปัจจุบัน ผลต้องประกาศว่า synthetic mechanics benchmark ไม่ใช่หลักฐาน business/hardware

## Status B1–B6 / M1–M2

| Finding | Re-review |
|---|---|
| B1 candidate depth | **CLOSED offline** — ทุก evaluated role 66–101 points; actual Qdrant return depth ยังเป็น Slice 2 gate |
| B2 intent grouping | **PARTIAL** — no cross-split leakage, test 51; dev role coverage ยังไม่ครบ |
| B3 lang/challenge/table/revision structure | **CLOSED structurally** |
| B4 answer-bearing labels | **PARTIAL** — เหลือ q-0013 และ human content pass |
| B5 rubric/rationale | **PARTIAL** — fields มี แต่ rationale/content ยัง generic |
| B6 human review | **OPEN BY DESIGN** — รอ Data Owner; ต้องผูก sign-off hash |
| M1 natural/hard queries | **SUFFICIENT FOR SMOKE; weak for decision** |
| M2 synthetic identity | **CLOSED** — ทุก point source prefix + `synthetic=true` |

## Independent verification

Codex rerun/ตรวจ artifacts ที่ commit `2621d40`:

- `test_p2.py` — **130/130 PASS**
- policy / P5b / eval / harness / auth — **69/69 · 11/11 · 64/64 · 12/12 · 11/11 PASS**
- corpus = 159, corpus validation = 0 errors
- cases = 118, intents dev/test = 8/51
- structural non-label errors = 0; expected ai-label errors = 118
- `arm_eligibility_errors()` = [] ตาม sample-count gate ปัจจุบัน
- intent metadata invariants ที่ตรวจอิสระ = 0 mismatches
- pool ต่อ role = engineering 100, hr 75, it 66, logistics 80, production 101, purchasing 80, qc 101, sales 93
- hashes:  
  eval `6cf1830e1cb628733402926adf67465126908afcdcd266695f86f76cdef76e9b`  
  corpus `57f7e59cd69eaad9630d2a04362579961183d835027b321761ccd234e2269189`

## Go / No-Go

| การทำต่อ | Verdict |
|---|---|
| เขียน pure Slice 2 interfaces/unit tests | **GO** |
| เปิด Docker, seed isolated collection, candidate-provider/M4/scorer smoke | **GO — mechanics-only** |
| ส่ง artifacts ปัจจุบันให้ Data Owner เซ็น | **FIX B2.1/B4.1/B5.1 ก่อน** |
| เปลี่ยนเป็น `human-reviewed` | **NO-GO จน Data Owner ลงชื่อจริง** |
| freeze / decision benchmark / เลือก arm | **NO-GO จน combined gate + sign-off ผ่าน** |
| production/cloud/real data | **NO-GO** |

## Final verdict

**GO-INFRA / FIX-BEFORE-HUMAN-SIGNOFF.** ให้ Claude เดิน Slice 2 infrastructure และ smoke ขนานได้ แต่แก้ dev-role coverage, q-0013, graded content/rationale และ hash-bound Data Owner gate ก่อน freeze labels
