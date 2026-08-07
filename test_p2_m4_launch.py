"""
Unit test ของ p2_m4_launch — locked entry point (B1/B4): runtime image attestation + locked scorer/session (offline)
runtime image digest == RunPlan -> attest ; ผิด/ว่าง -> LaunchError (ก่อน build scorer) ; scorer จาก real loader

    python test_p2_m4_launch.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_m4_isolation as ISO
import p2_m4_launch as LA
import p2_reranker as RK

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True

_IMG = "company-ai-brain/p2-reranker@sha256:" + "e" * 64
PLAN = {"run_id": "run-1", "image_digest": _IMG, "inference_config": {"model_name": RK.RERANKER_MODEL}}


def run_returning(digest):
    return lambda cmd: digest


# ── B4: attest runtime image digest ต้องตรง RunPlan.image_digest ──────────────
check("B4: runtime image digest ตรง RunPlan -> attest ผ่าน (คืน observed)",
      LA.attest_runtime_image(PLAN, docker_run=run_returning(_IMG), scorer_container="eval-1") == _IMG)
check("B4: runtime image digest ไม่ตรง -> LaunchError (false pin ผ่านไม่ได้)",
      raises(lambda: LA.attest_runtime_image(PLAN, docker_run=run_returning("sha256:WRONG"), scorer_container="eval-1"), LA.LaunchError))
check("B4: inspect ว่าง -> LaunchError", raises(lambda: LA.attest_runtime_image(PLAN, docker_run=run_returning("  "), scorer_container="eval-1"), LA.LaunchError))
check("B4: RunPlan ไม่มี image_digest -> LaunchError", raises(lambda: LA.attest_runtime_image({"run_id": "x"}, docker_run=run_returning(_IMG), scorer_container="eval-1"), LA.LaunchError))

# build_attested_scorer: attest **ก่อน** build ; wrong image -> ไม่ถึง loader
_loaded = {"called": False}
def _fake_loader(plan): _loaded["called"] = True; return "SCORER"
check("B4: build_attested_scorer(image ตรง) -> attest แล้ว build ด้วย real loader",
      LA.build_attested_scorer(PLAN, docker_run=run_returning(_IMG), scorer_container="eval-1", _loader=_fake_loader) == "SCORER" and _loaded["called"])
_loaded["called"] = False
check("B4: build_attested_scorer(image ผิด) -> LaunchError ก่อนเรียก loader (ไม่ build scorer)",
      raises(lambda: LA.build_attested_scorer(PLAN, docker_run=run_returning("sha256:BAD"), scorer_container="eval-1", _loader=_fake_loader), LA.LaunchError) and _loaded["called"] is False)

# ── B1: build_locked_isolation lock session = QdrantSession.connect (ไม่รับ arbitrary factory ใน real entry) ──
_iso = LA.build_locked_isolation(PLAN, docker_run=run_returning("x"), project_id="proj-1")
check("B1: build_locked_isolation -> QdrantDockerIsolation (session lock = QdrantSession.connect)",
      isinstance(_iso, ISO.QdrantDockerIsolation) and _iso._driver._session_factory == ISO.QdrantSession.connect)
check("B1: image_digest ไม่ pin (:latest) -> LaunchError (real entry ต้อง immutable digest)",
      raises(lambda: LA.build_locked_isolation({"image_digest": "img:latest"}, docker_run=run_returning("x"), project_id="p"), LA.LaunchError))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
