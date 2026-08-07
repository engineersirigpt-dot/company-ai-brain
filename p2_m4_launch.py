"""
P2 M4a launcher — locked real entry point (B1/B4): ไม่รับ arbitrary session/scorer ที่ self-report

- **B4:** attest **runtime image digest** ของ evaluator/scorer container (docker inspect) == RunPlan.image_digest
  **ก่อน** build scorer/provision ; build scorer ด้วย real loader เอง (ไม่รับ arbitrary loader ที่ self-report metadata)
- **B1:** isolation session lock = `QdrantSession.connect` (identity มาจาก Docker inspect ใน driver, ไม่ใช่ self-report)

real path = concrete (`ISO.QdrantSession.connect`, `SC.build_m4_scorer` = real loader) ; test inject `docker_run`/loader/
session_connect เพื่อ offline-test **logic** ; docker/qdrant/model จริง = ยัง NO-GO จน real-run slice
"""
from __future__ import annotations

import p2_m4_isolation as ISO
import p2_m4_scorer as SC


class LaunchError(Exception):
    """runtime attestation ไม่ผ่าน (image digest ไม่ตรง RunPlan) / entry point ถูกป้อน component ที่ self-report"""


def attest_runtime_image(plan: dict, *, docker_run, scorer_container: str) -> str:
    """
    B4: observed runtime image digest ของ container ที่รัน evaluator/scorer จริง (docker inspect) ต้อง == RunPlan.image_digest —
    ไม่ใช่ค่า plan-declared ที่ self-report ; ไม่ตรง/ว่าง → LaunchError (publish ถูกปฏิเสธก่อน provision/model)
    """
    if not (isinstance(plan, dict) and isinstance(plan.get("image_digest"), str) and plan["image_digest"]):
        raise LaunchError("RunPlan.image_digest ไม่ถูกต้อง")
    observed = docker_run(["docker", "inspect", "-f", "{{index .Image}}", scorer_container]).strip()
    if not observed:
        raise LaunchError("inspect runtime image digest ไม่ได้ (evaluator/scorer container)")
    if observed != plan["image_digest"]:
        raise LaunchError(f"runtime image digest != RunPlan pin: observed={observed!r} expected={plan['image_digest']!r}")
    return observed


def build_attested_scorer(plan: dict, *, docker_run, scorer_container: str, _loader=SC.build_m4_scorer):
    """
    B4: attest runtime image **ก่อน** แล้ว build scorer ด้วย **real loader** (`_loader` default = build_m4_scorer →
    load_pinned_cross_encoder + verify metadata) ; entry point จริงไม่รับ arbitrary self-report scorer object
    """
    attest_runtime_image(plan, docker_run=docker_run, scorer_container=scorer_container)
    return _loader(plan)


def build_locked_isolation(plan: dict, *, docker_run, project_id: str, vector_size: int = 1024,
                           _session_connect=ISO.QdrantSession.connect):
    """
    B1: DockerQdrantDriver ที่ session factory **lock** เป็น `QdrantSession.connect` (concrete) — identity มาจาก Docker inspect
    ใน driver.provision (ไม่ใช่ self-report) ; real entry point ไม่มีช่องเลือก arbitrary factory (`_session_connect` inject ได้เฉพาะ test)
    """
    if not (isinstance(plan.get("image_digest"), str) and "@sha256:" in plan["image_digest"]):
        raise LaunchError("RunPlan.image_digest ต้อง pin ด้วย immutable digest สำหรับ real run")
    return ISO.QdrantDockerIsolation(driver=ISO.DockerQdrantDriver(
        run=docker_run, session_factory=_session_connect, project_id=project_id,
        image_digest=plan["image_digest"], vector_size=vector_size))
