"""
P2 pinned model identity — single source of truth (pure, ไม่มี dependency)
ใช้ร่วมกันโดย: Dockerfile.p2 (build ARG), p2_fetch_model (build stage), p2_model_smoke (runtime),
RunPlan (model_commit/tokenizer_commit)

Model  : BAAI/bge-reranker-v2-m3
Commit : 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e   (full 40-hex, immutable — ยืนยันจาก HF API + Codex cross-check)
         KB_P2_MODEL_COMMIT_CODEX_CROSSCHECK.md — HEAD ของ main, tree ครบ 6 ไฟล์บังคับ
tokenizer อยู่ snapshot เดียวกัน → tokenizer_commit = model_commit

NOTE: model_file_manifest_sha256 + image_digest **ไม่อยู่ที่นี่** — คำนวณจาก snapshot/image จริงตอน build
      (ห้าม hardcode/เดา ; p2_fetch_model เขียน manifest ออกมา, image_digest มาจาก docker inspect หลัง build)
"""
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
MODEL_COMMIT = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
TOKENIZER_COMMIT = MODEL_COMMIT

# ไฟล์บังคับใน snapshot (fail build ทันทีถ้าขาด — gotcha #5)
REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
)
# model.safetensors ต้องเป็น LFS blob จริง ไม่ใช่ pointer (~2.2GB) — floor กัน pointer (gotcha #4)
MIN_SAFETENSORS_BYTES = 100 * 1024 * 1024
