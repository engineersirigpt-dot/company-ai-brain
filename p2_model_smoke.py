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
import sys

import p2_pin as PIN
import p2_reranker as RK


def main() -> int:
    # load_pinned_cross_encoder: local_files_only + HF_HUB_OFFLINE + assert resolved==revision อยู่ข้างใน
    ce = RK.load_pinned_cross_encoder(PIN.RERANKER_MODEL, revision=PIN.MODEL_COMMIT)
    meta = ce.metadata()

    assert meta["model_revision"] == PIN.MODEL_COMMIT, f"resolved != pinned: {meta['model_revision']}"
    assert meta["tokenizer_revision"] == PIN.TOKENIZER_COMMIT, meta

    scores = ce.score("ตัวอย่างคำถามภาษาไทย", ["เอกสารที่เกี่ยวข้อง", "เอกสารที่ไม่เกี่ยวข้อง"])
    assert len(scores) == 2 and all(isinstance(s, float) and math.isfinite(s) for s in scores), scores

    print("SMOKE OK " + json.dumps({
        "model_revision": meta["model_revision"],
        "file_manifest_sha256": meta["file_manifest_sha256"],
        "dtype": meta["dtype"], "torch": meta["torch_version"], "transformers": meta["transformers_version"],
        "scores_finite": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
