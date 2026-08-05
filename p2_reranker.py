"""
P2 cross-encoder adapter (pinned bge-reranker-v2-m3) — interface + injectable mock
pure part = interface + deterministic mock + **pin validation** (offline-testable)
real model load = Slice 2 run (torch/model ใน container) ผ่าน load_pinned_cross_encoder()

scorer contract: score(query, texts) -> list[float] (len == len(texts), finite float32)
model usage = Transformers pair-scoring ตาม BAAI/bge-reranker-v2-m3 model card (logit สูง = relevant กว่า)
"""
from __future__ import annotations
import os
import re

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
ALLOWED_MODELS = frozenset({RERANKER_MODEL})
# M1: pin ต้องเป็น FULL immutable commit — abbreviated SHA (7 hex) ไม่ unique พอสำหรับ reproducibility
# (HF git snapshot = sha1 40-hex ; เผื่อ repo sha256 = 64-hex)
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def validate_pin(model_name: str, revision: str, max_length: int, batch_size: int) -> list:
    """
    B1/M1: pin ต้อง reproducible — model allowlist + revision เป็น **full** immutable commit SHA
    (40 หรือ 64 hex ; ห้าม main/branch/tag/abbreviated) + max_length/batch_size positive int.
    คืน list ของ error (ว่าง = ผ่าน)
    """
    errs = []
    if model_name not in ALLOWED_MODELS:
        errs.append(f"model_name ไม่อยู่ใน allowlist {sorted(ALLOWED_MODELS)}: {model_name!r}")
    if not isinstance(revision, str) or revision.lower() in ("main", "master", "head") or not _COMMIT.match(revision or ""):
        errs.append(f"revision ต้องเป็น full immutable commit SHA (40/64 hex) ห้าม main/branch/tag/abbreviated: {revision!r}")
    if type(max_length) is not int or max_length < 1:
        errs.append(f"max_length ต้อง positive int: {max_length!r}")
    if type(batch_size) is not int or batch_size < 1:
        errs.append(f"batch_size ต้อง positive int: {batch_size!r}")
    return errs


class MockScorer:
    """deterministic mock (ไม่โหลด model) — สำหรับ test harness offline. metadata ระบุ non-evidence ชัดเจน"""
    def __init__(self, score_map: dict | None = None):
        self.score_map = score_map or {}

    def score(self, query: str, texts: list) -> list:
        return [float(self.score_map.get(t, 0.0)) for t in texts]

    def metadata(self) -> dict:
        return {"model": "mock", "model_revision": "mock", "tokenizer_revision": "mock",
                "device": "cpu", "kind": "mock-non-evidence"}


class PinnedCrossEncoder:
    """
    cross-encoder จริง (Slice 2 container) — เก็บ metadata จริงที่โหลด (model/tokenizer commit,
    file-manifest sha256, dtype, torch/transformers version, device) สำหรับ M4/canary evidence
    """
    def __init__(self, model, tokenizer, *, model_name, model_commit, tokenizer_commit,
                 file_manifest_sha256, dtype, torch_version, transformers_version,
                 device="cpu", max_length=512, batch_size=16):
        self._model, self._tok = model, tokenizer
        self.model_name = model_name
        self.model_commit = model_commit
        self.tokenizer_commit = tokenizer_commit
        self.file_manifest_sha256 = file_manifest_sha256
        self.dtype = dtype
        self.torch_version = torch_version
        self.transformers_version = transformers_version
        self.device, self.max_length, self.batch_size = device, max_length, batch_size

    def score(self, query: str, texts: list) -> list:
        import torch
        out = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            enc = self._tok([query] * len(batch), batch, padding=True, truncation=True,
                            max_length=self.max_length, return_tensors="pt").to(self.device)
            with torch.no_grad():
                logits = self._model(**enc).logits.view(-1).float()   # float32 ตาม official recipe
            out.extend(float(x) for x in logits.tolist())
        return out

    def metadata(self) -> dict:
        # superset: M4 provenance contract (kind/model_name/model_file_manifest_sha256/inference_config
        # ตรง M4RunRequest) + legacy keys (file_manifest_sha256/torch/transformers) สำหรับ model smoke
        ic = {"model_name": self.model_name, "max_length": self.max_length,
              "batch_size": self.batch_size, "device": self.device, "dtype": self.dtype}
        return {"kind": "pinned-cross-encoder", "model_name": self.model_name, "model": self.model_name,
                "model_revision": self.model_commit, "tokenizer_revision": self.tokenizer_commit,
                "model_file_manifest_sha256": self.file_manifest_sha256, "file_manifest_sha256": self.file_manifest_sha256,
                "inference_config": ic, "dtype": self.dtype, "torch_version": self.torch_version,
                "transformers_version": self.transformers_version, "device": self.device,
                "max_length": self.max_length, "batch_size": self.batch_size}


def _resolved_commit(snapshot_path: str) -> str:
    """
    M1: HF snapshot layout = <cache>/models--<org>--<name>/snapshots/<commit_sha>/
    → basename ของ snapshot path คือ commit ที่ resolve จริง (ใช้ verify ตรงกับ pin ที่ขอ)
    """
    return os.path.basename(os.path.normpath(snapshot_path))


def _snapshot_manifest_sha256(path: str) -> str:
    """sha256 ของ file-manifest (ชื่อ+sha256 ของทุกไฟล์ใน snapshot) — reproducibility digest"""
    import hashlib
    rows = []
    for root, _dirs, files in os.walk(path):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            h = hashlib.sha256()
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            rows.append(f"{os.path.relpath(fp, path)}:{h.hexdigest()}")
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def load_pinned_cross_encoder(model_name: str = RERANKER_MODEL, *, revision: str,
                              device: str = "cpu", max_length: int = 512,
                              batch_size: int = 16) -> PinnedCrossEncoder:
    """
    โหลด cross-encoder จริง (Slice 2 container) — pin immutable + offline. raise ถ้า pin ไม่ผ่านหรือ deps ไม่มี
    (offline นี้ไม่มี torch/transformers → ใช้ MockScorer สำหรับ harness test)
    """
    errs = validate_pin(model_name, revision, max_length, batch_size)
    if errs:
        raise ValueError(f"pin ไม่ผ่าน: {errs}")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        import torch
        import transformers
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        from huggingface_hub import snapshot_download
    except ImportError as e:
        raise RuntimeError(f"load_pinned_cross_encoder ต้องมี torch/transformers/huggingface_hub (container): {e}")
    snap = snapshot_download(model_name, revision=revision, local_files_only=True)
    # M1: assert snapshot ที่โหลดจริง resolve ตรงกับ full commit ที่ pin (กัน tag/branch/สลับ snapshot)
    resolved = _resolved_commit(snap)
    if resolved != revision:
        raise RuntimeError(
            f"resolved snapshot commit {resolved!r} != requested pin {revision!r} "
            f"(pin ต้องเป็น full immutable commit ที่ตรงกับ snapshot ที่ bake จริง)")
    tok = AutoTokenizer.from_pretrained(snap, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(snap, local_files_only=True)
    model.eval().to(device)
    return PinnedCrossEncoder(
        model, tok, model_name=model_name, model_commit=resolved, tokenizer_commit=resolved,
        file_manifest_sha256=_snapshot_manifest_sha256(snap), dtype=str(model.dtype),
        torch_version=torch.__version__, transformers_version=transformers.__version__,
        device=device, max_length=max_length, batch_size=batch_size)
