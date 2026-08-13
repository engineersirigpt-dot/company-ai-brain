"""
Adapter: policy filter spec (pure list[dict] จาก policy.compile_retrieval_filter)
         -> Qdrant Filter(must=[...])

แยกจาก app/main.py (torch-heavy) เพื่อให้ P5b seeder/conformance เรียก compiler + adapter ตัวเดียว
กับ API — ยิง filter เดียวกันเป๊ะ (Codex: "ใช้ compiled filter เดียวกับ API")
"""
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue


def to_qdrant_filter(spec: list) -> Filter:
    """
    explicit เสมอ (แม้ admin) — ห้ามคืน None. value -> MatchValue, any -> MatchAny

    **fail-closed (defense-in-depth):** `Filter(must=[])` ใน Qdrant = ไม่มีเงื่อนไข = **match ทุก point** →
    ถ้า spec ว่างจะกลายเป็น leak ทั้งคลังเงียบ ๆ. compiler ปกติคืน ≥4 เงื่อนไขเสมอ (unknown role ถูก reject ก่อน)
    แต่ adapter ตัวนี้อยู่ปลายทาง RBAC — กัน spec ว่าง/condition malformed เอง ไม่พึ่ง caller ทุกตัว
    """
    must = []
    for c in spec:
        if "value" in c:
            must.append(FieldCondition(key=c["key"], match=MatchValue(value=c["value"])))
        elif "any" in c:
            must.append(FieldCondition(key=c["key"], match=MatchAny(any=c["any"])))
        else:
            raise ValueError(f"filter condition ต้องมี 'value' หรือ 'any': {c!r}")
    if not must:
        raise ValueError("filter spec ว่าง — ปฏิเสธ (Filter(must=[]) = match-all = leak ทั้งคลัง)")
    return Filter(must=must)
