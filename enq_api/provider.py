"""
Synthetic extraction provider — ENQ orchestration slice (LOCAL, mock)
=====================================================================
คืน extract-v1.1 payload แบบ deterministic — **ไม่เรียก cloud, ไม่ใช้ข้อมูลจริง**
provider จริง (vLLM/Typhoon local, Cloud) = increment ถัดไป — gated ตาม Data Owner/DPO/Legal

worker เรียกฟังก์ชันนี้ **นอก DB transaction** เท่านั้น (Codex flow) พร้อม timeout < lease
"""
from __future__ import annotations
from typing import Any


class ProviderError(Exception):
    """provider call ล้มเหลว → worker เรียก fail_rfq_extraction"""


def extract(*, input_ref: str | None, input_sha256: str, execution_target: str,
            correlation: dict[str, Any] | None = None, timeout_s: float | None = None) -> dict[str, Any]:
    """
    mock extraction: อ่าน input (ref/hash) → คืน tree + evidence (extract-v1.1)

    - รองรับเฉพาะ execution_target='LOCAL' (server policy v1 = local-only)
    - **ต้อง echo input_sha256** กลับใน payload (apply ตรวจว่าตรง run snapshot)
    - deterministic: ไม่มี randomness, ไม่มี network — เหมาะกับ test/CI
    - timeout_s = lease budget (provider จริงต้องใช้เป็น request timeout < lease) — synthetic ไม่ใช้
    """
    if execution_target != "LOCAL":
        raise ProviderError("synthetic provider รองรับเฉพาะ LOCAL (cloud extraction = increment ถัดไป)")
    if not input_sha256:
        raise ProviderError("input_sha256 หาย — claim ไม่คืน hash")
    return {
        "schema_version": "extract-v1.1",
        "input_sha256": input_sha256,                          # echo → apply ตรวจตรง run
        "items": [{
            "line_no": 1,
            "fields": {"job_name": "synthetic extracted job"},
            "quantity_options": [{"option_no": 1, "fields": {"quantity": 1000}}],
        }],
        "evidence": [
            {"subject_type": "ITEM", "ref": {"line_no": 1}, "field_name": "job_name",
             "source_type": "PDF", "confidence": 0.9},
            {"subject_type": "QUANTITY", "ref": {"line_no": 1, "option_no": 1}, "field_name": "quantity",
             "source_type": "PDF", "confidence": 0.9},
        ],
    }
