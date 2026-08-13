"""
LLM provider abstraction สำหรับ `/ask` — จุดสลับ cloud↔local **จุดเดียว**

สลับด้วย env `LLM_PROVIDER`:
- `anthropic` : Anthropic Messages API (Claude)                  ← default (cloud)
- `openai`    : OpenAI-compatible Chat Completions               ← Ollama / vLLM / OpenAI
                ตั้ง `LLM_BASE_URL` ชี้ endpoint local:
                  Ollama → http://<gpu-host>:11434/v1
                  vLLM   → http://<gpu-host>:8001/v1
                (Ollama/vLLM ไม่ต้องใช้ API key จริง)

- import SDK แบบ **lazy** → ไม่ต้องลง package ที่ไม่ใช้ + unit-test ด้วย mock ได้ (ไม่ต้องมี anthropic/openai)
- คืนผลเป็น **normalized tuple `(answer_text, model_name, refused)`** — caller ไม่ผูกกับ response shape ของ SDK
- ความปลอดภัย: provider=openai + base_url local = **generation ไม่ออก cloud** (กัน egress ข้อมูลบริษัท)
"""
from __future__ import annotations

import os

VALID_PROVIDERS = ("anthropic", "openai")


class LLMConfigError(Exception):
    """LLM_PROVIDER ผิด / config ไม่ครบ"""


def make_client(provider: str, *, base_url: str = "",
                anthropic_key_env: str = "ANTHROPIC_API_KEY",
                openai_key_env: str = "OPENAI_API_KEY",
                client_factory=None):
    """
    สร้าง LLM client ตาม provider ; คืน **None ถ้ายังไม่พร้อม** (ไม่มี key / ไม่มี package) → caller ตอบ 503 ตามเดิม
    client_factory: inject สำหรับ test (default None = สร้าง SDK จริง)
    """
    if provider not in VALID_PROVIDERS:
        raise LLMConfigError(f"invalid LLM_PROVIDER={provider!r} (ต้องเป็น {VALID_PROVIDERS})")
    if client_factory is not None:
        return client_factory(provider, base_url)
    if provider == "anthropic":
        if not os.getenv(anthropic_key_env):
            return None                                  # ไม่มี key → /ask ตอบ 503 (พฤติกรรมเดิม)
        import anthropic
        return anthropic.Anthropic()
    # openai-compatible (Ollama / vLLM / OpenAI)
    try:
        from openai import OpenAI
    except ImportError:
        return None                                      # ยังไม่ได้ลง openai package
    key = os.getenv(openai_key_env) or "local"           # Ollama/vLLM ไม่ต้องใช้ key จริง
    return OpenAI(base_url=base_url or None, api_key=key)


def generate(client, provider: str, *, model: str, max_tokens: int, system: str, user: str):
    """
    เรียก LLM แล้วคืน `(answer_text: str, model_name: str, refused: bool)` — normalize response ของ 2 provider
    (raise exception ของ SDK ตามเดิม → caller map เป็น HTTP error)
    """
    if provider == "anthropic":
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        refused = getattr(resp, "stop_reason", None) == "refusal"
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return text, getattr(resp, "model", model), refused
    # openai-compatible
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    msg = resp.choices[0].message
    text = (getattr(msg, "content", None) or "").strip()
    return text, getattr(resp, "model", model), False
