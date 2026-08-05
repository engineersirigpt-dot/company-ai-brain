"""
P2 runtime model-load smoke (offline) — รันในคอนเทนเนอร์ **หลัง** Codex review + FIX-BEFORE-RUN ผ่าน
**ไม่ถูกรันตอน docker build** — เป็นขั้น manual/compose-run แยก (gotcha #9)

พิสูจน์: resolved snapshot SHA == pinned, tokenizer+model โหลดได้ offline, score finite,
metadata (commit/file-manifest/dtype/versions) ออกมาจริงสำหรับ M4/canary evidence ภายหลัง

    docker compose -f docker-compose.p2.yml run --rm reranker python p2_model_smoke.py
"""
from __future__ import annotations
import json
import math
import os
import sys

import p2_pin as PIN
import p2_reranker as RK

# baked manifest ที่ fetch stage เขียนไว้ (ต้องตรงกับ snapshot ที่โหลดจริง)
MANIFEST_PATH = os.environ.get("P2_MANIFEST_PATH", "/opt/model_file_manifest.sha256")


def _fail(msg: str):
    # M2: ห้ามใช้ assert เป็น evidence gate (ปิดได้ด้วย -O/PYTHONOPTIMIZE) — ใช้ SystemExit เสมอ
    raise SystemExit(f"SMOKE FAIL: {msg}")


def _read_baked_manifest(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        _fail(f"อ่าน baked manifest ไม่ได้ ({path}): {e}")


def main() -> int:
    # load_pinned_cross_encoder: local_files_only + HF_HUB_OFFLINE + raise ถ้า resolved != revision (ข้างใน)
    ce = RK.load_pinned_cross_encoder(PIN.RERANKER_MODEL, revision=PIN.MODEL_COMMIT)
    meta = ce.metadata()

    if meta["model_revision"] != PIN.MODEL_COMMIT:
        _fail(f"resolved model commit != pinned: {meta['model_revision']}")
    if meta["tokenizer_revision"] != PIN.TOKENIZER_COMMIT:
        _fail(f"resolved tokenizer commit != pinned: {meta['tokenizer_revision']}")

    # M2: cross-check baked manifest กับ file-manifest ของ snapshot ที่โหลดจริง (exact)
    baked = _read_baked_manifest(MANIFEST_PATH)
    if baked != meta["file_manifest_sha256"]:
        _fail(f"baked manifest != snapshot file-manifest ({baked} != {meta['file_manifest_sha256']})")

    scores = ce.score("ตัวอย่างคำถามภาษาไทย", ["เอกสารที่เกี่ยวข้อง", "เอกสารที่ไม่เกี่ยวข้อง"])
    if not (len(scores) == 2 and all(isinstance(s, float) and math.isfinite(s) for s in scores)):
        _fail(f"scores ไม่ finite/จำนวนผิด: {scores}")

    print("SMOKE OK " + json.dumps({
        "model_revision": meta["model_revision"],
        "file_manifest_sha256": meta["file_manifest_sha256"], "baked_manifest_match": True,
        "dtype": meta["dtype"], "torch": meta["torch_version"], "transformers": meta["transformers_version"],
        "scores_finite": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
