"""
Adapter: policy filter spec (pure list[dict] จาก policy.compile_retrieval_filter)
         -> Qdrant Filter(must=[...])

แยกจาก app/main.py (torch-heavy) เพื่อให้ P5b seeder/conformance เรียก compiler + adapter ตัวเดียว
กับ API — ยิง filter เดียวกันเป๊ะ (Codex: "ใช้ compiled filter เดียวกับ API")
"""
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue


def to_qdrant_filter(spec: list) -> Filter:
    """explicit เสมอ (แม้ admin) — ห้ามคืน None. value -> MatchValue, any -> MatchAny"""
    must = []
    for c in spec:
        if "value" in c:
            must.append(FieldCondition(key=c["key"], match=MatchValue(value=c["value"])))
        else:
            must.append(FieldCondition(key=c["key"], match=MatchAny(any=c["any"])))
    return Filter(must=must)
