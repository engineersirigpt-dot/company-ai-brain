"""
Evaluation Script: วัดคุณภาพ retrieval

Metrics:
  Hit@3    — expected_source อยู่ใน top 3 ไหม
  Score@1  — similarity score ของผลอันดับ 1
  MRR      — Mean Reciprocal Rank (ยิ่งสูงยิ่งดี, max=1.0)

Usage:
    python eval.py
    python eval.py eval_set.json
"""
import sys
import json
sys.stdout.reconfigure(encoding="utf-8")
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient

QDRANT_PATH = "./qdrant_storage"
COLLECTION_NAME = "company_docs"
TOP_K = 3
NO_MATCH_THRESHOLD = 0.62  # score ต่ำกว่านี้ถือว่า "ไม่พบ" (ปรับจาก 0.55 หลัง corpus ขยายเป็น 143 ไฟล์)


def embed(tokenizer, model, text: str) -> list[float]:
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs)
    vec = out.last_hidden_state[:, 0, :]
    vec = F.normalize(vec, p=2, dim=1)
    return vec[0].tolist()


def run_eval(eval_path: str):
    cases = json.loads(open(eval_path, encoding="utf-8").read())

    print("Loading BGE-M3...")
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", local_files_only=True)
    model = AutoModel.from_pretrained("BAAI/bge-m3", local_files_only=True)
    model.eval()

    client = QdrantClient(path=QDRANT_PATH)

    hits, mrr_scores, score1_list = [], [], []
    no_match_correct = 0
    no_match_total = 0

    print(f"\n{'='*70}")
    print(f"{'#':<3} {'Question':<38} {'Hit@3':<6} {'Score@1':<8} {'Rank'}")
    print(f"{'='*70}")

    for i, case in enumerate(cases, 1):
        q = case["question"]
        expected = case.get("expected_source")

        vec = embed(tokenizer, model, q)
        resp = client.query_points(
            collection_name=COLLECTION_NAME,
            query=vec,
            limit=TOP_K,
            with_payload=True,
        )
        results = resp.points

        score1 = results[0].score if results else 0.0
        sources = [r.payload.get("source", "") for r in results]

        if expected is None:
            # คำถามที่ไม่มีคำตอบ — คาดว่า score ต่ำ
            no_match_total += 1
            correct = score1 < NO_MATCH_THRESHOLD
            if correct:
                no_match_correct += 1
            status = "✓ low" if correct else "✗ high"
            print(f"{i:<3} {q[:37]:<38} {status:<6} {score1:.4f}")
        else:
            # คำถามที่มีคำตอบ
            rank = next((r+1 for r, s in enumerate(sources) if s == expected), None)
            hit = rank is not None
            hits.append(hit)
            score1_list.append(score1)
            mrr_scores.append(1 / rank if rank else 0)

            hit_str = f"✓ #{rank}" if hit else "✗"
            print(f"{i:<3} {q[:37]:<38} {hit_str:<6} {score1:.4f}")

    # --- Summary ---
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    if hits:
        print(f"Hit@{TOP_K}         : {sum(hits)}/{len(hits)} = {sum(hits)/len(hits)*100:.0f}%")
        print(f"Avg Score@1   : {sum(score1_list)/len(score1_list):.4f}")
        print(f"MRR           : {sum(mrr_scores)/len(mrr_scores):.4f}")
    if no_match_total:
        print(f"No-match acc  : {no_match_correct}/{no_match_total} = {no_match_correct/no_match_total*100:.0f}%  (score < {NO_MATCH_THRESHOLD})")

    print(f"\nเกณฑ์อ้างอิง:")
    print(f"  Hit@3 > 80%  = ดี   | MRR > 0.7 = ดี   | Score@1 > 0.65 = ดี")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "eval_set.json"
    run_eval(path)
