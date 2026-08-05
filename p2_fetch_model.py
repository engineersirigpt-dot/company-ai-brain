"""
P2 build-stage fetch + verify (network ON) — รันใน Dockerfile.p2 fetch stage เท่านั้น
prefetch snapshot ตาม pinned SHA เข้า HF cache แล้ว **fail build ทันที** ถ้า contract ไม่ครบ
เขียน canonical file-manifest sha256 ออกไฟล์ให้ runtime/RunPlan ใช้ (ห้ามเดาค่าล่วงหน้า)

gotchas ที่ปิด: #1 build-time network, #2 คง snapshots/<SHA> layout, #4 real blob ไม่ใช่ pointer,
                #5 required files + canonical manifest, #8 ห้าม fallback main (revision=SHA ตรง ๆ)

    python p2_fetch_model.py            # เขียน manifest ไป MANIFEST_OUT (default /opt/model_file_manifest.sha256)
"""
from __future__ import annotations
import json
import os
import sys

import p2_pin as PIN
import p2_reranker as RK

MANIFEST_OUT = os.environ.get("P2_MANIFEST_OUT", "/opt/model_file_manifest.sha256")


def _assert_expected_commit() -> None:
    """
    B2: p2_pin = single source of truth. ถ้า build ส่ง `P2_EXPECT_COMMIT` (จาก --build-arg MODEL_COMMIT)
    มาแต่ไม่ตรง p2_pin → fail ก่อน network fetch (กัน build arg เป็น control ปลอมที่ไม่มีผล)
    """
    expect = os.environ.get("P2_EXPECT_COMMIT")
    if expect and expect != PIN.MODEL_COMMIT:
        raise SystemExit(f"FAIL build MODEL_COMMIT {expect!r} != p2_pin.MODEL_COMMIT {PIN.MODEL_COMMIT!r} "
                         f"(single source of truth — แก้ p2_pin.py หรือ build arg ให้ตรง)")


def fetch_and_verify() -> dict:
    _assert_expected_commit()
    from huggingface_hub import snapshot_download   # network ในสเตจนี้ (ไม่ตั้ง HF_HUB_OFFLINE)

    # revision = full immutable SHA ตรง ๆ — ถ้าโหลดไม่ได้ให้ fail (ห้าม fallback main; gotcha #8)
    snap = snapshot_download(PIN.RERANKER_MODEL, revision=PIN.MODEL_COMMIT)

    # #2: snapshot ต้องอยู่ใต้ snapshots/<SHA> เพื่อให้ _resolved_commit ผ่านตอน runtime
    resolved = RK._resolved_commit(snap)
    if resolved != PIN.MODEL_COMMIT:
        raise SystemExit(f"FAIL resolved snapshot commit {resolved!r} != pinned {PIN.MODEL_COMMIT!r}")

    # #5: required files ครบ
    missing = [f for f in PIN.REQUIRED_FILES if not os.path.exists(os.path.join(snap, f))]
    if missing:
        raise SystemExit(f"FAIL required files ขาด: {missing}")

    # #4: model.safetensors ต้องเป็น blob จริง (ไม่ใช่ LFS pointer)
    st = os.path.join(snap, "model.safetensors")
    size = os.path.getsize(os.path.realpath(st))
    if size < PIN.MIN_SAFETENSORS_BYTES:
        raise SystemExit(f"FAIL model.safetensors {size} bytes ดูเหมือน pointer (ต้อง >= {PIN.MIN_SAFETENSORS_BYTES})")

    # #5: canonical file-manifest sha256 จาก snapshot จริง → RunPlan.model_file_manifest_sha256
    manifest = RK._snapshot_manifest_sha256(snap)
    os.makedirs(os.path.dirname(MANIFEST_OUT) or ".", exist_ok=True)
    with open(MANIFEST_OUT, "w", encoding="utf-8") as f:
        f.write(manifest + "\n")

    return {"resolved_commit": resolved, "model_file_manifest_sha256": manifest,
            "safetensors_bytes": size, "snapshot": snap, "manifest_out": MANIFEST_OUT}


if __name__ == "__main__":
    info = fetch_and_verify()
    print(json.dumps(info, ensure_ascii=False))
    sys.exit(0)
