# P2 — isolation/scorer FIX รอบ 2 : Docker-inspect identity (B1) + locked launcher runtime image attest (B4)

> **สืบเนื่อง:** `KB_P2_M4_ISOLATION_SCORER_FIX_CODEX_REREVIEW_F359AFD.md` (M1/B2 CLOSED, B3 direction accepted ; B1/B4 OPEN)
> **pure/offline ทั้งหมด** — inject `docker_run`/loader/session · **รัน M4a จริง = ยัง NO-GO**
> ไฟล์: `p2_m4_isolation.py` (provision rework), `p2_m4_launch.py` (ใหม่), `test_p2_m4_isolation.py`, `test_p2_m4_launch.py`

## Finding → fix → proof

| # | ช่องเดิม (self-report) | Fix + test |
|---|---|---|
| **B1** | `observed_target_identity()` ping server แต่ยังคืน **ค่าที่ constructor ป้อน** (self._endpoint/collection) → misbound client ผ่าน identity check แล้ว mutate production ได้ ; production guard = empty denylist | `DockerQdrantDriver.provision` **derive identity จาก Docker inspect จริง**: `docker inspect {{json .NetworkSettings.Networks}}` → container ต้องอยู่บน **internal network ที่เราสร้าง** + **endpoint = container IP จาก inspect** (ไม่ใช่ค่า supplied) ; ไม่อยู่บน network → abort **ก่อน** recreate ; **✅ tested**: container บน network อื่น → abort + session/recreate ไม่ถูกสร้าง ; happy → endpoint = `http://<inspect-IP>:6333` + recreate หลัง verify |
| **B4** | `observed_image_digest()` ไม่มี caller + inspect **Qdrant** ไม่ใช่ scorer container ; fake scorer self-report → public-valid PASS ; image_digest จาก RunPlan | **`p2_m4_launch`** locked entry: `attest_runtime_image(plan, docker_run, scorer_container)` = observed runtime image digest ของ **evaluator/scorer** container == RunPlan.image_digest **ก่อน** build/provision ; `build_attested_scorer` = attest → build ด้วย **real loader** (`build_m4_scorer`) ไม่รับ arbitrary self-report ; `build_locked_isolation` lock session = `QdrantSession.connect` ; **✅ tested**: image ไม่ตรง/ว่าง → LaunchError ก่อนถึง loader ; image :latest → LaunchError |

## สอดคล้อง Codex "simpler path"

launcher เดียว (`p2_m4_launch`) ที่ **ไม่รับ arbitrary session_factory/scorer loader** ใน real entry — สร้าง scorer ด้วย real loader เอง, attest runtime image จาก Docker, lock session เป็น `QdrantSession.connect` (identity จาก Docker inspect) ; injectable seam (`docker_run`/`_loader`/`_session_connect`) มีไว้ **test เท่านั้น**

## ผลรัน (offline)

- **23 suites: 897/897** (เพิ่ม test_p2_m4_launch 8 ; isolation 29 ; ไม่มี regression)
- inject `docker_run` (Docker CLI seam) → identity/digest logic offline-testable ; docker/qdrant/model จริง = **ยังไม่รัน** (real API = seam)

## ยัง NO-GO — real M4a synthetic execution (slice ถัดไป)

- ต้องรันจริงบนเครื่อง: build p2-reranker image (immutable digest) + Qdrant container บน `--internal` network + โหลด bge-reranker จริง
- launcher wiring เต็ม: attest evaluator container image → build ports (isolation+provider+oracle+scorer) บน internal network เดียว → รัน `run_m4a` → verify PASS + teardown
- host-DNS/topology: controller/evaluator ต้องอยู่บน internal network เดียวกับ Qdrant (host resolve container ไม่ได้) — validate เมื่อรันจริง

## ขอ Codex re-review (targeted — B1/B4)

1. B1 identity จาก Docker inspect (container-on-internal-network + endpoint จาก inspect IP) แทน self-report — ปิดจริงไหม
2. B4 locked launcher (attest runtime image ก่อน build + real loader + no arbitrary self-report) — ปิด false-scorer/image attribution จริงไหม ; observed_image_digest มี caller แล้ว (launcher)
3. หลังผ่าน → real M4a synthetic execution (build image + run docker/qdrant/model จริง) = slice ถัดไป

**Gate (owner 2026-08-07):** B1/B4 re-review = FIX-THEN-GO/GO · real M4a synthetic execution = NO-GO จน re-review + real infra slice · M4b/ข้อมูลจริง = NO-GO จน Data Owner sign-off · safety/provenance v1 = FROZEN
