"""
Backfill payload["parent_text"] ลงทุก point ใน Qdrant — offline, ไม่ re-embed
parent_text = context ~2000 ตัวรอบตำแหน่ง chunk ใน source .md (snap ขอบย่อหน้า)

Usage:
    python build_parent_payload.py <parsed_output_dir> [--apply]
    (ไม่มี --apply = dry-run: คำนวณ+โชว์สถิติ ไม่เขียน Qdrant)
"""
import os, sys, io
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("COLLECTION_NAME", "company_docs")
TARGET = 2000
BATCH = 200

md_dir = Path(sys.argv[1])
APPLY = "--apply" in sys.argv

_cache = {}
def load_md(source):
    if source in _cache:
        return _cache[source]
    p = md_dir / f"{source}.md"
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        txt = ""
    _cache[source] = txt
    return txt

def locate(m, chunk_text, heading):
    """หาตำแหน่ง chunk ใน source ด้วย probe หลายชั้น -> (pos, length) หรือ None"""
    t = chunk_text.strip()
    if not t:
        return None
    # 1) exact
    p = m.find(t)
    if p >= 0:
        return p, len(t)
    # 2) ตัด heading ออกแล้วลอง body
    body = t
    if heading and t.startswith(heading.strip()):
        body = t[len(heading.strip()):].strip()
        p = m.find(body)
        if p >= 0:
            return p, len(body)
    # 3) probe slice ภายใน (60 ตัวจากกลาง) — ทน whitespace/heading เพี้ยน
    src = body if body else t
    if len(src) >= 60:
        mid = len(src) // 2
        for probe in (src[mid:mid+60], src[:60], src[-60:]):
            probe = probe.strip()
            if len(probe) >= 20:
                p = m.find(probe)
                if p >= 0:
                    return p, len(t)
    return None

def parent_window(m, pos, length, target=TARGET):
    if len(m) <= target:
        return m.strip()
    end = pos + length
    if length >= target:
        return m[pos:pos+target].strip()
    extra = target - length
    left = max(0, pos - extra // 2)
    right = min(len(m), end + (extra - (pos - left)))
    if right - left < target:
        left = max(0, right - target)
    if right - left < target:
        right = min(len(m), left + target)
    lb = m.rfind("\n\n", 0, left + 1)
    if lb != -1 and pos - lb <= target:
        left = lb + 2
    rb = m.find("\n\n", right)
    if rb != -1 and rb - left <= target:
        right = rb
    return m[left:right].strip()

def build_parent(payload):
    chunk = payload.get("text", "") or ""
    src = load_md(payload.get("source", "") or "")
    if not src:
        return chunk[:TARGET], "no_md"
    loc = locate(src, chunk, payload.get("heading", "") or "")
    if not loc:
        return chunk[:TARGET], "not_found"
    return parent_window(src, loc[0], loc[1]), "ok"

def main():
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=60)
    total = client.get_collection(COLLECTION).points_count
    print(f"collection={COLLECTION} points={total} md_dir={md_dir} apply={APPLY}")

    offset = None
    seen = 0
    stats = {"ok": 0, "not_found": 0, "no_md": 0}
    grew = 0
    sample = []
    while True:
        pts, offset = client.scroll(COLLECTION, limit=BATCH, offset=offset,
                                    with_payload=True, with_vectors=APPLY)
        if not pts:
            break
        updates = []
        for p in pts:
            parent, why = build_parent(p.payload)
            stats[why] += 1
            if len(parent) > len(p.payload.get("text", "") or ""):
                grew += 1
            if len(sample) < 5 and why == "ok":
                sample.append((len(p.payload.get("text","") or ""), len(parent), p.payload.get("source","")))
            if APPLY:
                new_payload = dict(p.payload)
                new_payload["parent_text"] = parent
                updates.append(PointStruct(id=p.id, vector=p.vector, payload=new_payload))
        if APPLY and updates:
            client.upsert(COLLECTION, points=updates)
        seen += len(pts)
        print(f"  {seen}/{total}")
        if offset is None:
            break

    print("\n=== stats ===")
    print(stats)
    print(f"parent ยาวกว่า chunk เดิม: {grew}/{seen} ({grew/seen*100:.0f}%)")
    print("--- ตัวอย่าง (chunk_len -> parent_len) ---")
    for a, b, s in sample:
        print(f"  {a:5d} -> {b:5d}   {s[:50]}")
    print("\n" + ("[APPLIED]" if APPLY else "[DRY-RUN] ยังไม่เขียน — ใส่ --apply เพื่อเขียนจริง"))

if __name__ == "__main__":
    main()
