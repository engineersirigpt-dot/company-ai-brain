"""
Unit test ของ llm_provider — provider abstraction (anthropic / openai-compatible) offline ด้วย mock client
(ไม่ต้องมี anthropic/openai package จริง)

    python test_llm_provider.py
"""
import io
import sys
import types

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import llm_provider as LP

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc=Exception):
    try:
        fn(); return False
    except exc:
        return True


# ── make_client: provider validation ──
check("invalid provider -> LLMConfigError", raises(lambda: LP.make_client("gemini"), LP.LLMConfigError))
check("anthropic ไม่มี key -> None (จะตอบ 503)",
      LP.make_client("anthropic", anthropic_key_env="UNSET_VAR_XYZ_123") is None)

# client_factory inject (ไม่แตะ SDK จริง)
seen = {}
def fake_factory(provider, base_url):
    seen["provider"], seen["base_url"] = provider, base_url
    return f"client:{provider}"
check("client_factory ถูกเรียกด้วย provider+base_url (anthropic)",
      LP.make_client("anthropic", client_factory=fake_factory) == "client:anthropic")
check("client_factory ถูกเรียกด้วย base_url (openai/ollama)",
      LP.make_client("openai", base_url="http://gpu:11434/v1", client_factory=fake_factory) == "client:openai"
      and seen["base_url"] == "http://gpu:11434/v1")


# ── generate: anthropic response shape ──
class _Block:
    def __init__(s, type, text): s.type = type; s.text = text
class _AnthResp:
    def __init__(s, blocks, stop_reason="end_turn", model="claude-opus-4-8"):
        s.content = blocks; s.stop_reason = stop_reason; s.model = model
class _AnthMessages:
    def __init__(s, resp): s._resp = resp; s.last = None
    def create(s, **kw): s.last = kw; return s._resp
class _AnthClient:
    def __init__(s, resp): s.messages = _AnthMessages(resp)

ac = _AnthClient(_AnthResp([_Block("text", "  คำตอบไทย [1]"), _Block("thinking", "x"), _Block("text", " ต่อ")]))
text, model, refused = LP.generate(ac, "anthropic", model="claude-opus-4-8", max_tokens=100,
                                   system="SYS", user="ข้อมูล... คำถาม...")
check("anthropic: รวมเฉพาะ block type=text (ข้าม thinking) + strip", text == "คำตอบไทย [1] ต่อ", repr(text))
check("anthropic: คืน model จาก resp", model == "claude-opus-4-8")
check("anthropic: refused=False เมื่อ stop_reason ปกติ", refused is False)
check("anthropic: ส่ง system + user เข้า create ถูก",
      ac.messages.last["system"] == "SYS" and ac.messages.last["messages"][0]["content"].startswith("ข้อมูล"))

ac2 = _AnthClient(_AnthResp([_Block("text", "-")], stop_reason="refusal"))
_, _, refused2 = LP.generate(ac2, "anthropic", model="m", max_tokens=10, system="s", user="u")
check("anthropic: stop_reason=refusal -> refused=True", refused2 is True)


# ── generate: openai-compatible (Ollama/vLLM) response shape ──
class _Msg:
    def __init__(s, content): s.content = content
class _Choice:
    def __init__(s, content): s.message = _Msg(content)
class _OAResp:
    def __init__(s, content, model="qwen2.5:7b"): s.choices = [_Choice(content)]; s.model = model
class _OAComp:
    def __init__(s, resp): s._resp = resp; s.last = None
    def create(s, **kw): s.last = kw; return s._resp
class _OAChat:
    def __init__(s, resp): s.completions = _OAComp(resp)
class _OAClient:
    def __init__(s, resp): s.chat = _OAChat(resp)

oc = _OAClient(_OAResp("  ตอบจาก local  "))
t2, m2, r2 = LP.generate(oc, "openai", model="qwen2.5:7b", max_tokens=100, system="SYS", user="U")
check("openai: อ่าน choices[0].message.content + strip", t2 == "ตอบจาก local", repr(t2))
check("openai: คืน model จาก resp", m2 == "qwen2.5:7b")
check("openai: refused=False (openai ไม่มี refusal flag)", r2 is False)
check("openai: ส่ง system+user เป็น 2 messages",
      len(oc.chat.completions.last["messages"]) == 2
      and oc.chat.completions.last["messages"][0]["role"] == "system")

# openai content=None (บาง endpoint) -> ""
ocn = _OAClient(_OAResp(None))
tn, _, _ = LP.generate(ocn, "openai", model="m", max_tokens=10, system="s", user="u")
check("openai: content=None -> '' (ไม่ crash)", tn == "")

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
