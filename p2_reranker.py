"""
P2 cross-encoder adapter (pinned bge-reranker-v2-m3) — interface + injectable mock
pure part = interface + deterministic mock + metadata contract (offline-testable)
real model load = Slice 2 run (torch/model ใน container) ผ่าน load_pinned_cross_encoder()

scorer contract: score(query, texts) -> list[float] (len == len(texts), finite) — เข้ากับ rerank.rerank_order
"""
from __future__ import annotations

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


class MockScorer:
    """deterministic mock (ไม่โหลด model) — สำหรับ test harness offline. revision='mock' (ห้ามใช้เป็น evidence)"""
    revision = "mock"
    tokenizer_revision = "mock"

    def __init__(self, score_map: dict | None = None):
        self.score_map = score_map or {}

    def score(self, query: str, texts: list) -> list:
        return [float(self.score_map.get(t, 0.0)) for t in texts]

    def metadata(self) -> dict:
        return {"model": "mock", "model_revision": self.revision,
                "tokenizer_revision": self.tokenizer_revision, "device": "cpu", "kind": "mock-non-evidence"}


class PinnedCrossEncoder:
    """
    cross-encoder จริง (lazy torch/model) — ใช้ใน Slice 2 container เท่านั้น
    บันทึก model/tokenizer revision + device สำหรับ M4/canary evidence hashes
    """
    def __init__(self, model, tokenizer, revision: str, tokenizer_revision: str,
                 device: str = "cpu", max_length: int = 512, batch_size: int = 16):
        self._model = model
        self._tok = tokenizer
        self.revision = revision
        self.tokenizer_revision = tokenizer_revision
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size

    def score(self, query: str, texts: list) -> list:
        import torch
        scores = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            enc = self._tok([query] * len(batch), batch, padding=True, truncation=True,
                            max_length=self.max_length, return_tensors="pt").to(self.device)
            with torch.no_grad():
                logits = self._model(**enc).logits.view(-1)
            scores.extend(float(x) for x in logits.tolist())
        return scores

    def metadata(self) -> dict:
        return {"model": RERANKER_MODEL, "model_revision": self.revision,
                "tokenizer_revision": self.tokenizer_revision, "device": self.device,
                "max_length": self.max_length, "batch_size": self.batch_size}


def load_pinned_cross_encoder(model_name: str = RERANKER_MODEL, revision: str = "main",
                              device: str = "cpu", max_length: int = 512,
                              batch_size: int = 16) -> PinnedCrossEncoder:
    """
    โหลด cross-encoder จริง (Slice 2 container) — pin revision. raise ชัดเจนถ้า deps ไม่มี
    (ในสภาพแวดล้อม offline นี้ torch/transformers ไม่มี → ใช้ MockScorer แทนสำหรับ harness test)
    """
    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    except ImportError as e:
        raise RuntimeError(f"load_pinned_cross_encoder ต้องมี transformers/torch (Slice 2 container): {e}")
    tok = AutoTokenizer.from_pretrained(model_name, revision=revision)
    model = AutoModelForSequenceClassification.from_pretrained(model_name, revision=revision)
    model.eval().to(device)
    tok_rev = getattr(tok, "name_or_path", model_name)
    return PinnedCrossEncoder(model, tok, revision=revision, tokenizer_revision=str(tok_rev),
                              device=device, max_length=max_length, batch_size=batch_size)
