"""Test model weights loading only — skip tokenizer"""
import torch
from transformers import AutoModel, BertTokenizer

# bert-base-uncased: well-known, tokenizer ไม่ต้องการ sentencepiece
MODEL = "google-bert/bert-base-uncased"

print(f"Loading tokenizer...")
tokenizer = BertTokenizer.from_pretrained(MODEL)
print("tokenizer OK")

print(f"Loading model weights...")
model = AutoModel.from_pretrained(MODEL)
print("model OK")

inputs = tokenizer(["hello world"], return_tensors="pt")
with torch.no_grad():
    out = model(**inputs)
vec = out.last_hidden_state[:, 0, :]
print(f"vector shape: {vec.shape}")
print("all OK")
