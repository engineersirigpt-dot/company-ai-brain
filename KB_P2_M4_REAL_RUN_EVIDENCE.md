# P2 M4a — real-run evidence (Docker + real bge-reranker + real Qdrant) — 2026-08-07

> **ครั้งแรกที่รันบน infra จริงบนเครื่อง** (Docker Desktop Linux engine) — **synthetic corpus เท่านั้น ไม่มีข้อมูลจริง**
> M4a synthetic mechanics = GO track (owner 2026-08-07) ; ไม่ต้อง Data Owner sign-off ; M4b/ข้อมูลจริง ยัง NO-GO

## Environment ที่ยืนยัน

| item | ค่าจริง |
|---|---|
| Docker | server 29.5.3 (linux engine) |
| reranker image (`plan.image_digest`) | `sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190` |
| qdrant image (`qdrant_image_ref`) | `qdrant/qdrant@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286` |
| baked model manifest (`/opt/model_file_manifest.sha256`) | `c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013` |
| torch (ใน image) | 2.3.1+cpu |

## 1. Real scorer — bge-reranker-v2-m3 ใน pinned container

รัน `load_pinned_cross_encoder(revision=PIN.MODEL_COMMIT)` ใน image (mount current source, baked model at `/opt/hf`):

```
kind                        pinned-cross-encoder
model_revision              953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e   (= PIN.MODEL_COMMIT)
tokenizer_revision          953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
model_file_manifest_sha256  c969d1f67f17f9bf1a7b1c65b4ea9843c0308c7715e1fc4b89d27ff73b689013  (= baked)
dtype (current code)        float32       ← M1 canonical_dtype fix ทำงานบน torch จริง (torch.float32 → float32)
score('a cat sits on the mat', ['the cat is on the mat','the stock market fell today'])
     = [10.2299, -11.0317]  → relevant > irrelevant ✅ (reranker ทำงานถูก)
```

**ยืนยัน:** real model โหลดได้ + metadata ตรง pin + **M1 fix ถูกต้องบน torch จริง** + reranker rerank ถูก

## 2. Real Qdrant facade — `QdrantSession` ต่อ Qdrant container จริง

```
observed_target_identity  {'collection_id':'m4-real','endpoint':'http://localhost:6399'}  (get_collections ping)
recreate_collection       -> count 0
write_marker/read_marker  'm4-run-uuid-REAL' round-trip ✅
seed (UUID point ids)     -> count marker+3 = 4 ✅
```

**บั๊กที่เจอจากการรันจริง (offline/fake จับไม่ได้):** `QdrantSession.seed` เดิม `str(pid)` → Qdrant reject
`400 "value 0 is not a valid point ID, valid values are either an unsigned integer or a UUID"`
→ **แก้:** `_valid_qdrant_id` — point id ต้องเป็น **unsigned int หรือ UUID string** (M4 corpus/frozen ต้องใช้ id ชนิดนี้ให้ตรงกัน)

## 3. Real RBAC permission-leak proof — QdrantM4Provider + real Qdrant

seed A(qc,admin/ta), B(sales,admin/tb), **S(management/ts = sentinel, vector คล้าย A)** ; provider adapter จริง + `compile_retrieval_filter` + `to_qdrant_filter` query Qdrant จริง:

```
qc filtered texts:    ['ta']    (เห็นเฉพาะ A)
sales filtered texts: ['tb']    (เห็นเฉพาะ B)
PERMISSION-LEAK: sentinel 'ts' reached qc/sales? -> False    ← กรองก่อนถึง scorer จริง
RBAC correct: qc={ta}=True | sales={tb}=True
```

**ยืนยัน:** RBAC filter ทำงานบน **Qdrant จริง** (ไม่ใช่แค่ fake `matches_policy` model) — sentinel ที่คล้ายเชิง semantic ถูกกรองก่อน retrieval → **ปิดช่องว่าง "matches_policy เป็น model ไม่ใช่ Qdrant oracle"** ระดับ smoke (P5b conformance เต็มยังเป็นงานแยก)

## 4. 🎯 FULL M4a real execution — runner ครบ pipeline ภายใน pinned container จริง

controller (host) provision network + volume + Qdrant container (no published ports) → รัน **evaluator ภายใน pinned
reranker container** (`p2_m4_evaluator.py`, mount current source, `pip install qdrant-client` runtime) บน network เดียวกับ
Qdrant → `run_m4a` ด้วย adapter จริงทั้ง 4 (real scorer + real Qdrant provider/oracle + container-side isolation):

```
M4A_RESULT status=PUBLISHED  evidence=PASS
  isolated_interlock=PASS   independent_oracle=PASS
  sentinel_reached_model=FALSE   unauthorized_in_model_inputs=0   decision_eligible=False
```

validate bundle บน host (public gate):
```
evidence.status               PASS
scorer_kind                   pinned-cross-encoder
image_digest                  sha256:27768971905e...   (= evaluator container image)
model_revision / dtype        953dc6f6... / float32     (real pin + M1 fix)
isolation_proof errs          []                        (distinct ids + count 0 + published_ports 0 + non-production + marker round-trip)
receipt.image_digest==evidence  True
executed image == plan.image_digest  True               ← B4 attestation load-bearing (runner รันใน pinned image จริง)
```

**ยืนยัน:** M4 permission-leak proof ผ่านครบบน **infra จริง** — sentinel ไม่ถึง scorer, IsolationProof valid, independent
oracle PASS, executed image = pinned evaluator image (B1/B4 execution ปิดด้วยการรันจริง)

### บั๊กที่รันจริงจับได้ (offline/fake จับไม่ได้)
1. `seed` str point id → Qdrant 400 (ต้อง uint/UUID) → `_valid_qdrant_id`
2. **marker point pollute `unfiltered_topn`** (oracle เห็น marker payload ที่ไม่มี rerank_text → crash) → `QdrantSession.seed`
   ลบ marker (`clear_marker`) หลัง interlock เสร็จ ก่อน seed corpus

## ข้อจำกัดของ run นี้ (synthetic mechanics — ยังไม่ production)
- network เป็น user-defined bridge (มี internet ให้ pip) + Qdrant **ไม่ publish port** (`published_ports=0`) ; production isolation
  ควร `--internal` + bake `qdrant-client` เข้า image (ไม่ pip runtime) — เป็นงาน hardening ของ real-run pinned image
- `qdrant-client` ยัง pip runtime ใน container (ไม่ baked) → reproducibility gap ของ image (executed image digest ยังตรง base)
- vectors เป็น deterministic dummy (permission proof ไม่ขึ้นกับ ranking) ; embedder จริง (BGE-M3) = งานแยก
- **synthetic corpus เท่านั้น** ; M4b/ข้อมูลจริง ยัง NO-GO จน Data Owner sign-off

ทุก container **teardown แล้ว** (network/volume/qdrant ลบหมด ไม่มีค้าง) ; ไม่แตะข้อมูลจริง

---

## 5. ✅ Outer receipt — host-authoritative closure (ปิด Codex real-run B1/B2/B3/M1)

**ปัญหาที่ Codex ชี้ (review 7de8574):** ผลรอบ §4 capability ผ่าน แต่ค่าที่ยืนยัน image/isolation/cleanup เป็นค่าที่
**evaluator ประกาศเองจาก env** (ปลอมได้ = false-PASS) และ PASS ถูก publish **ก่อน** cleanup → ต้องมี host controller
สังเกต Docker เอง + ยืนยัน cleanup ก่อน resolve verdict แล้ว hash-bind กับ inner bundle

**วิธีปิด:** `DockerM4Controller` (host) → provision → run evaluator (pinned image) → **observe จาก `docker inspect` จริง**
→ teardown → **post-inspect ยืนยันหาย** → ประกอบ **outer receipt** (fail-closed) — terminal verdict คำนวณจาก
observation (ไม่เชื่อค่าที่ evaluator/receipt เขียน)

### ผลรัน evidence (clean tree)

```
attempt_id            oc-e02f8f38
git_commit / dirty    5dade757702a / False        ← source ที่รันจริง = commit สะอาด (tracked ไม่แก้)
TERMINAL_STATUS       PASS
validate errs         []                          (validate_m4_outer_receipt fail-closed)
bundle_sha256         d681d9d02c284abb386a5f8e78bab99714e6ef0341fc4432700f0f7cbd230577   (bind inner bundle ทั้งก้อน)
outer_receipt_sha256  f08f4dbedfcaf854e6e954123abc2164174d98268733bdfa3337ec7a9df24b8e
```

หลักฐานเก็บ tracked: `KB_P2_M4_OUTER_RECEIPT.json` (outer) + `KB_P2_M4_INNER_BUNDLE.json` (inner ที่ถูก bind)

### แต่ละ finding ปิดด้วย observation จริง (ไม่ใช่ declared)

| Codex | ปิดด้วย (observed จาก Docker/subprocess) |
|---|---|
| **B1** image self-assert | `docker inspect {{index .Image}}` ของ evaluator container = `sha256:27768971…` **== pin == inner bundle image_digest** (evaluator ปลอม digest → mismatch → FAILED) |
| **B2** isolation env | identity: `typed_id_sha256(docker network/volume/project/collection)` **== isolation_proof.*_sha256** ✓ ; host-published ports = **0** (นับเฉพาะ host-binding จริง ไม่ใช่ EXPOSE) == declared 0 |
| **B3** PASS ก่อน cleanup | teardown → **post-inspect** ทุก resource → `residual=[]`, `confirmed=True` ; ถ้ายังค้าง → `DEGRADED` (ไม่ PASS) |
| **M1** synthetic receipt | บันทึกจริง: `command` (docker argv เต็ม), `exit_code=0`, `started/finished_utc`, `stdout/stderr_sha256`, `dependency_digest` (pip freeze ในคอนเทนเนอร์), `git_commit` |

### negative proof (offline, Codex DoD #5 — ครบ 3)
`test_p2_m4_receipt.py` 19/19 + `test_p2_m4_controller.py` 13/13:
- **N1 image env ปลอม** → observed evaluator image ≠ pin/inner → `FAILED` (+ lying receipt terminal=PASS → validator จับ)
- **N2 isolation env ปลอม** → observed published_ports≠0 / observed network id hash ≠ proof → `FAILED`
- **N3 cleanup fail หลัง inner PASS** → residual≠[] → `DEGRADED` (ไม่ publish PASS)

### บั๊กที่ real run จับได้ (offline จับไม่ได้)
`{{len .NetworkSettings.Ports}}` นับ **EXPOSE** ด้วย (Qdrant image EXPOSE 6333/6334 → ได้ 2 ทั้งที่ไม่ publish) →
รอบแรก terminal FAILED ผิด → แก้เป็นนับเฉพาะ port ที่มี **host binding จริง** (`.NetworkSettings.Ports` value ไม่ null)

### ข้อจำกัดที่ **bind ไว้ใน receipt** (bounded rerun — Codex อนุญาต)
- `network_internal=False` (bridge มี egress ให้ pip) ไม่ใช่ `--internal` ; host-published ports = 0 (Docker-observed) ยังจริง
- `qdrant-client` pip runtime ไม่ baked → dependency identity bind ด้วย `dependency_digest` ; source ด้วย `git_commit`
- synthetic corpus / dummy vectors เท่านั้น ; **M4b/ข้อมูลจริง ยัง NO-GO** จน Data Owner sign-off

ทุก container **teardown + post-inspect ยืนยันหายจริง** ; ไม่มี resource ค้าง ; ไม่แตะข้อมูลจริง

---

## 6. 🔒 Strict validator — ปิด Codex re-review B1/B2/B3 (field ต้อง "บังคับ verdict")

**Codex re-review (84b9cf8):** capability ผ่าน แต่ "field อยู่ใน JSON ≠ field บังคับ verdict" — ลบ M1 field หรือใส่
qdrant/run/network ผิด แล้ว recompute hash **ยังได้ PASS** → เข้า freeze exception (false-PASS + cleanup-failure)

ทำให้ทุกฟิลด์ที่ receipt อ้าง authority **load-bearing** (validator error + terminal ≠ PASS ถ้าขาด/ผิด):

| Codex | ปิดด้วย |
|---|---|
| **B1** inspect error = "หายแล้ว" → false cleanup PASS | `teardown_and_verify` ใช้ **three-state existence probe** (`docker ps -a`/`network ls`/`volume ls --filter` ที่ต้อง `rc==0` ก่อนเชื่อ empty=**ABSENT**) ; rc≠0 (daemon/permission ล้ม) = **UNKNOWN** แยกจาก ABSENT → `cleanup.unknown` → **DEGRADED** ไม่ใช่ PASS |
| **B2** M1 process/source/dep ไม่ load-bearing | validator บังคับ schema เต็ม: command non-empty, exit 0, timestamps ISO เรียงได้, stdout/stderr/dep = sha256, git_commit + **source_tree_digest** = 40-hex, **git_tree_dirty is False** ; mount source **`:ro`** + real_run **stage จาก `git archive HEAD`** (mounted tree = committed tree, ไม่มี untracked shadow) |
| **B3** qdrant/run/network ไม่ตรวจ | top `run_id == inner.run_id` ; `network_internal` เป็น bool ; `qdrant_image_ref` ต้องอยู่ใน observed **RepoDigests** (docker inspect — ไม่เอา manifest ไปเทียบ image id) ; cleanup schema exact |

### ผลรัน evidence (strict, clean tree)

```
git_commit / dirty     7aa4d61d468b / False
source_tree_digest     3174a6ee2022…              (HEAD^{tree} — source ที่รัน = committed tree)
TERMINAL_STATUS        PASS
strict validate errs   []   (required_field_errors=[]  false_pass_reasons=[])
cleanup                confirmed=True residual=[] unknown=[]        (three-state)
qdrant ref∈RepoDigests True | published_ports 0 | top run_id==inner True
outer_receipt_sha256   b97949f326539bdc…
```

### negative proof (offline) — Codex targeted acceptance 4 ข้อ ✅
- **1 (B1)** daemon/inspect error ตอน cleanup → `DEGRADED` (`test_p2_m4_controller` probe_unknown ; `test_p2_m4_receipt` UNKNOWN)
- **2 (B2)** ลบ/malformed process/source/dep field ทีละกลุ่ม → validate error + terminal ≠ PASS (5 กลุ่ม + dirty)
- **3 (B3)** qdrant RepoDigest ไม่ตรง / network mode หาย / top≠inner run id → validate error + terminal ≠ PASS
- **4** rerun synthetic 1 ครั้ง → inner+outer artifacts ผ่าน strict validator, cleanup confirmed จาก absence probe, no leftover จริง

หลักฐาน: `KB_P2_M4_OUTER_RECEIPT.json` + `KB_P2_M4_INNER_BUNDLE.json` · receipt **30/30** + controller **15/15** · offline **943/943**

---

## 7. 🔐 Cross-binding รอบสุดท้าย — ปิด Codex B2.1/B3.1 (evidence @72a1763)

**Codex re-review (7aa4d61):** B1 + schema ปิดแล้ว เหลือ cross-binding 2 จุดที่ยังไม่ผูก producer→artifact:

| Codex | ปิดด้วย |
|---|---|
| **B3.1** RepoDigests อ่านจาก requested ref ไม่ใช่ image ที่ container รันจริง | controller inspect RepoDigests จาก **`.Image` (actual container image id)** ; receipt เพิ่ม `qdrant_repo_digests_subject` + `qdrant_container_config_image` ; validator บังคับ **`subject == observed.qdrant_image`** + pinned ref ∈ RepoDigests(subject) → เปลี่ยน actual image = FAILED |
| **B2.1** git identity อ่านจาก live HEAD หลังรัน (race) | `p2_m4_real_run` resolve commit+tree **ก่อน** staging → `git archive <commit>` (ไม่ใช่ HEAD) → ส่ง **immutable `source_identity`** เข้า controller ; certify **ไม่ re-read live HEAD** (fallback `_git_identity` เฉพาะ offline test) |

### ผลรัน evidence (strict, @72a1763)

```
TERMINAL_STATUS        PASS      strict validate errs []
B3.1  qdrant_image = repo_digests_subject = sha256:0bd98fa7…   (subject == container image ที่รันจริง)
      pinned ref ∈ RepoDigests(subject) = True | container_config_image = qdrant/qdrant@sha256:0bd98fa7…
B2.1  git_commit 72a1763b… (staged, resolve ก่อน archive) | git_tree_dirty False | source_tree_digest f53e014c…
cleanup confirmed=True residual=[] unknown=[]  | docker ps -a/network ls/volume ls: no leftover | staging ลบแล้ว
outer_receipt_sha256   8cf7334d1e0d998a…
```

### negative proof (Codex acceptance 4 ข้อ) ✅
- **1 (B3.1)** actual container image คนละตัว / RepoDigests(subject) ไม่มี pinned ref → **FAILED** (receipt subject-mismatch + controller wrong-image)
- **2 (B2.1)** stage commit A แต่ live HEAD = B ก่อน certify → receipt bind **A** (staged) ไม่ตาม live HEAD (`test_p2_m4_controller` staged-vs-liveHEAD)
- **3** strict validator artifact ใหม่ผ่าน + suites ไม่ regress: receipt **32/32** · controller **19/19** · offline **949/949**
- **4** bounded rerun 1 ครั้ง → inner+outer artifacts ผ่าน strict validator, cleanup confirmed จาก absence probe, no leftover จริง

หลักฐาน: `KB_P2_M4_OUTER_RECEIPT.json` + `KB_P2_M4_INNER_BUNDLE.json` (commit 72a1763)
