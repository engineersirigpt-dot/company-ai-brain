# Codex Review — P2 model-load smoke + gate สำหรับ real M4

**Commit reviewed:** `4926a40`  
**Input:** `KB_P2_SMOKE_RESULT_HANDOFF.md` และ artifacts ใน `.p2_build/cpu-build-1/`  
**ขอบเขต:** review เท่านั้น — ไม่รัน model/Qdrant/M4 เพิ่ม, ไม่แก้ code หรือ `STATUS.md`

## Verdict

**GO / CLOSE model-load smoke (offline, CPU).**

หลักฐานยืนยันว่า image ที่ pin ไว้โหลด model/tokenizer จาก local snapshot ได้จริง, revision และ file manifest ตรงกับ build receipt, inference คืน score ที่เป็น finite float และคำสั่งรันด้วย `--network none` จบด้วย exit code 0 ไม่มี traceback หรือ download fallback

transformers offline-cache warning ที่ปรากฏใน stderr **รับได้สำหรับ smoke นี้**: เป็น warning ของ cache migration heuristic, มี `0it`, ไม่เกิด traceback และไม่หักล้างหลักฐาน runtime ที่โหลดสำเร็จจาก baked snapshot อย่างไรก็ตาม smoke นี้พิสูจน์เฉพาะ compatibility/reproducibility ไม่ใช่ quality, latency, permission isolation หรือความพร้อม production

## Independent verification

ตรวจเทียบจากไฟล์จริงแล้วได้ผลดังนี้:

| Check | Result |
|---|---|
| exit code เป็น 0 | PASS |
| SHA-256 ของ `smoke.stdout` ตรง `smoke_meta.json` | PASS |
| SHA-256 ของ `smoke.stderr` ตรง `smoke_meta.json` | PASS |
| full image ID ตรง `build_receipt.json` | PASS |
| model revision ตรง pinned/build receipt | PASS — `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |
| runtime file-manifest ตรง baked/build receipt | PASS — `c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013` |
| `baked_manifest_match` เป็น boolean `true` | PASS |
| `scores_finite` เป็น boolean `true` | PASS |
| stderr ไม่มี traceback/download/connection error | PASS |
| source code เปลี่ยนหลัง build หรือไม่ | ไม่มี — `7d68886..4926a40` เพิ่มเฉพาะเอกสาร |

ดังนั้น CPU image `sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190` ใช้ต่อเป็น artifact สำหรับ **M4 mechanics** ได้ แต่ห้ามเอาเวลารัน CPU ไปตัดสิน GPU/production ตาม gate เดิม

## Findings ที่ต้องปิดก่อน real M4 run

### B1 — M4 evidence ปัจจุบัน PASS แบบไม่ได้เรียก model เลยได้

**ตำแหน่ง:** `p2_eval.py:397-403`

`validate_m4_evidence()` ยอมรับ `model_input_id_hashes=[]` เพราะตรวจเพียงว่าเป็น list ของ SHA-256; เมื่อ list ว่างก็ disjoint กับ sentinel โดยอัตโนมัติ จึงสามารถสร้าง evidence ที่ระบุ `status=PASS`, `sentinel_reached_model=false`, `unauthorized_in_model_inputs=0` ทั้งที่ไม่มี authorized candidate ใดเข้า cross-encoder จริง

**ต้องแก้ก่อน run:** บังคับอย่างน้อย:

- `model_invocation_count` เป็น positive exact int;
- `model_input_id_hashes` ไม่ว่างและ count ตรง trace จริง;
- มี positive control ว่า authorized input ถึง **real pinned cross-encoder** และได้ finite scores;
- negative/error/empty path ต้องเป็น `FAIL` หรือ `INCONCLUSIVE` ไม่ใช่ PASS

มิฉะนั้น M4 มีช่อง vacuous pass ซึ่งขัดกับเป้าหมาย “พิสูจน์ว่าข้อมูลที่ได้รับอนุญาตถึงโมเดล แต่ sentinel ที่ห้ามเห็นไม่ถึงโมเดล”

### B2 — ID hash อย่างเดียวยังพิสูจน์ข้อความจริงที่โมเดลเห็นไม่ได้

**ตำแหน่ง:** `p2_eval.py:397-403` เทียบกับ contract ที่บันทึกใน `KB_P2_SLICE2_INFRA_FIX_CODEX_REREVIEW_2364BB1.md`

contract ที่ review ไว้ต้องมี candidate/model-input **ID + text hash sets** แต่ validator ปัจจุบันตรวจแค่ `unauthorized_sentinel_id_hashes` กับ `model_input_id_hashes` ไม่มี candidate ID set, model-input text hashes หรือ sentinel text hashes ดังนั้น point ID ที่ถูกต้องแต่ข้อความถูกสลับ/แทนที่ก่อนเรียก model จะไม่ถูกตรวจพบ

**ต้องแก้ก่อน run:** evidence ต้องเก็บ hash เท่านั้น (ห้าม log ข้อความดิบ) อย่างน้อย:

- `authorized_candidate_id_hashes` และ `authorized_candidate_text_hashes` จาก independent oracle;
- `provider_candidate_id_hashes` และ text hashes หลัง Qdrant/filter;
- `model_input_id_hashes` และ `model_input_text_hashes` ที่ spy จับตรง boundary ก่อนเรียก real scorer;
- `unauthorized_sentinel_id_hashes` และ `unauthorized_sentinel_text_hashes`;
- exact/subset assertions ที่พิสูจน์ provider/model input มาจาก authorized oracle และ disjoint กับ sentinel ทั้ง ID และ text

### M1 — ลำดับ “M4 ก่อน N-sweep” ยังผูก final decision evidence ไม่ได้

**ตำแหน่ง:** `p2_runplan.py:235-238`, `p2_runplan.py:492-518`, `p2_runplan.py:552-560`

final decision contract บังคับ M4 อ้าง `selection_digest` ซึ่งสร้างได้หลัง dev N-sweep เลือก `selected_n` แล้ว แต่แผนปัจจุบันวาง real M4 ก่อน N-sweep จึงยังสร้าง M4 evidence สำหรับ final decision โดยตรงไม่ได้

**ทางที่แนะนำ:** แยกชัดเจนเป็นสองระยะ

1. **M4a preflight mechanics** ตอนนี้ — รัน synthetic safety proof ที่ `N=50` (พื้นผิว candidate มากสุด), ติดป้าย `decision_eligible=false`; ใช้ปลดทางให้ N-sweep เท่านั้น
2. หลัง dev sweep ได้ `selection_digest` แล้ว ทำ **M4b selected-N confirmation** ด้วย pipeline/image/index เดิม และ bind final evidence เข้ากับ root + selection digest

อย่าเติม `selection_digest` ย้อนหลังลง raw evidence เดิมโดยไม่มี immutable raw-evidence digest/reference เพราะจะกลายเป็น post-hoc evidence mutation

## Plan/gate ที่ต้องมีเพื่อปลด real M4

ให้เริ่มร่าง `KB_P2_M4_REAL_RUN_PLAN.md` ได้ทันทีแบบ pure/offline โดยต้องล็อกหัวข้อต่อไปนี้ก่อนขอ GO run:

1. **Isolation interlock** — Qdrant ต้องใช้ fresh project/network/volume/port/collection และ run marker ของตัวเอง; runner ต้อง reject prod URL, prod collection, port 6333 และ config/env ที่ไม่ครบแบบ fail-closed การต่างกันแค่ชื่อ collection ยังไม่พอ
2. **Synthetic-only corpus** — freeze seed manifest พร้อม point ID, payload ACL, vector/index metadata และ text hash; ห้ามใช้เอกสารบริษัท/ข้อมูลลูกค้า
3. **Adversarial sentinels** — มี unauthorized semantic twin/hard negative ที่ตั้งใจให้ relevance สูงกว่า authorized item รวมกรณี missing ACL, malformed ACL, stale policy version และ quarantine
4. **Independent oracle** — direct scroll จาก isolated collection แล้วเทียบ frozen seed manifest; ห้าม reuse `compile_filter`, `matches_policy`, provider output หรือผล query ที่กำลังทดสอบเป็น oracle
5. **Production-like authorization boundary** — `AUTH_MODE=enforce`, role-scoped synthetic key/principal และ server-resolved trusted `EffectiveAccess`; ห้ามส่ง raw role จาก request เข้า provider โดยตรง
6. **Exact call path** — real Qdrant → compiled filter → provider postcondition → spy → pinned real cross-encoder; spy จับ ID/text hashes ก่อน model call และต้องไม่เปลี่ยน input
7. **Network boundary** — model/runner อยู่บน isolated internal Docker network เท่านั้น ไม่มี internet egress; ระบุให้ชัดว่าใครคุยกับ Qdrantและใครโหลด model จาก baked snapshot
8. **Fail-closed outcomes** — auth/filter/oracle/index mismatch, partial query, exception, empty/vacuous input, sentinel ถึง boundary หรือ evidence เขียนไม่ครบ ต้องเป็น FAIL/INCONCLUSIVE และ exit non-zero
9. **Negative controls** — ทดสอบว่า validator จับ empty model trace, sentinel injection, wrong index/run/image/hash และ oracle mismatch ได้จริง โดย negative control ต้องหยุดก่อนส่ง unauthorized text เข้า real model
10. **Durable evidence** — เก็บ canonical raw receipt ของ seed, index, oracle, provider candidates, spy trace, model metadata, command/timestamps/exit code และ stdout/stderr hashes; summary ต้องอ้าง raw-evidence digest/path ไม่พึ่งไฟล์ gitignored บนเครื่องเพียงชุดเดียว
11. **Acceptance** — interlock/oracle PASS, real model invocation > 0, finite scores, unauthorized count exact 0, ID/text sets disjoint, provider/model inputsอยู่ใน authorized oracle, rerank outputเป็น permutation ของ authorized candidates และทุก required case มีผลครบ
12. **Teardown/retention** — export evidence ก่อน teardown; ระบุ collection UUID/volume/network ที่ลบ และเก็บเฉพาะ evidence ที่ไม่มี secret หรือข้อความดิบ

## Go / No-Go ล่าสุด

| งาน | Verdict |
|---|---|
| ปิด CPU model-load smoke | **GO / CLOSED** |
| ร่าง M4 real-run plan + แก้ pure validator/tests สำหรับ B1/B2 | **GO NOW** |
| รัน real M4 บน isolated Qdrant | **FIX-BEFORE-RUN** — ปิด B1/B2/M1 และให้ review plan ก่อน |
| ใช้ M4a ปลด N-sweep mechanics | **GO หลัง M4a PASS** |
| N-sweep/quality | **ยัง NO-GO จน M4a PASS** |
| ใช้ CPU latency ตัดสิน GPU/production | **NO-GO** |
| decision benchmark | **NO-GO** จน Data Owner sign-off + selected-N M4b + validated canary + evidence bundle ครบ |
| production/deploy/cloud/ข้อมูลจริง | **NO-GO** ตาม gate เดิม |

Data Owner sign-off **ไม่จำเป็นต่อ M4a ที่เป็น isolated synthetic mechanics** แต่ M4a ต้องติดป้าย non-decision ชัดเจนและไม่สามารถถูกส่งเข้า `decide_p2()` แทน M4b ได้

## Final answer

**Smoke ผ่านและปิดได้ครับ ส่วนงานถัดไปควร “เริ่มร่าง M4 plan ตอนนี้” ไม่ควรรอเฉย ๆ แต่ยังห้ามรัน M4 จริงจนปิด evidence gap B1/B2 และแก้ลำดับการ bind `selection_digest` ตาม M1 ก่อน** แนวทาง M4a preflight ที่ N=50 แล้ว M4b ยืนยัน selected N เป็นทางที่ตรงกับ contract ปัจจุบันและลดความเสี่ยงต้องทิ้ง evidence หลังรัน
