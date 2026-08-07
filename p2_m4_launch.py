"""
P2 M4a locked entry — `run_m4a_locked`: single flow, attestation load-bearing (Codex simpler path) — B1/B4

- แยกสองค่าให้ชัด: **`plan.image_digest`** = scorer/evaluator runtime image ID `sha256:<64hex>` (ตาม RunPlan contract) ·
  **`qdrant_image_ref`** = immutable Qdrant repo ref `<repo>@sha256:<64hex>` (จาก trusted infra config, **ไม่ใช่จาก plan**)
- **single flow:** `controller` รัน scorer + M4 runner **ภายใน pinned evaluator container** (`plan.image_digest`) บน internal
  network เดียวกับ Qdrant (`qdrant_image_ref`) แล้วคืน `{executed_image_digest, bundle}` ; launcher **verify
  `executed_image_digest == plan.image_digest`** (พิสูจน์ scorer รันใน pinned image จริง — attestation load-bearing) +
  validate bundle ผ่าน public gate
- real entry **ไม่รับ** `_loader`/`scorer_container` จาก caller (ตัดช่อง caller จับคู่ container A กับ scorer process B) ;
  `controller` (docker run/exec จริง) = seam ; test inject fake controller เพื่อ offline-test orchestration/verify logic

**docker/qdrant/model จริง = ยัง NO-GO** จน real-run slice (controller.execute ตัวจริง) review + รันจริงบนเครื่อง
"""
from __future__ import annotations

import re

import p2_eval as E
import p2_runplan as RP

_QDRANT_REF = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}")


class LaunchError(Exception):
    """input/attestation ไม่ผ่าน: RunPlan invalid / digest schema ผิด / executed image != pin / bundle ไม่ผ่าน gate"""


def _is_qdrant_ref(x) -> bool:
    return isinstance(x, str) and bool(_QDRANT_REF.fullmatch(x))


def run_m4a_locked(*, plan, frozen, cases, corpus, marker, out_dir, qdrant_image_ref, controller):
    """
    controller ต้องมี `.execute(*, plan, frozen, cases, corpus, marker, out_dir, qdrant_image_ref)` ที่
    **รัน scorer+runner ภายใน pinned evaluator container จริง** แล้วคืน `{"executed_image_digest", "bundle": {evidence,receipt}}`
    (real: `docker run/exec` + inspect container image ; test: fake ที่คืนผลจำลอง)
    """
    perrs = RP.validate_run_plan(plan) if isinstance(plan, dict) else ["plan ไม่ใช่ dict"]
    if perrs:
        raise LaunchError(f"RunPlan invalid: {perrs[:2]}")
    if not E._is_image_digest(plan["image_digest"]):
        raise LaunchError("plan.image_digest ต้องเป็น sha256:<64hex> (scorer/evaluator runtime image)")
    if not _is_qdrant_ref(qdrant_image_ref):
        raise LaunchError("qdrant_image_ref ต้องเป็น <repo>@sha256:<64hex> (immutable Qdrant image แยกจาก plan)")
    if not hasattr(controller, "execute"):
        raise LaunchError("ต้อง inject controller ที่มี .execute (real: docker run/exec ใน pinned container)")

    result = controller.execute(plan=plan, frozen=frozen, cases=cases, corpus=corpus, marker=marker,
                                out_dir=out_dir, qdrant_image_ref=qdrant_image_ref)
    if not isinstance(result, dict):
        raise LaunchError("controller.execute ต้องคืน dict {executed_image_digest, bundle}")
    # B4: attestation load-bearing — scorer/runner ต้องรันใน pinned evaluator image จริง (executed == plan pin)
    if result.get("executed_image_digest") != plan["image_digest"]:
        raise LaunchError(f"executed image != plan.image_digest — scorer ไม่ได้รันใน pinned evaluator container: "
                          f"{result.get('executed_image_digest')!r} != {plan['image_digest']!r}")
    bundle = result.get("bundle")
    if not (isinstance(bundle, dict) and isinstance(bundle.get("evidence"), dict) and isinstance(bundle.get("receipt"), dict)):
        raise LaunchError("controller คืน bundle ผิดรูป (ต้องมี evidence/receipt dict)")
    berrs = RP.validate_m4_preflight_bundle(plan, frozen, bundle["evidence"], bundle["receipt"])
    if berrs:
        raise LaunchError(f"bundle ไม่ผ่าน public gate: {berrs[:2]}")
    return {"executed_image_digest": result["executed_image_digest"],
            "evidence": bundle["evidence"], "receipt": bundle["receipt"]}
