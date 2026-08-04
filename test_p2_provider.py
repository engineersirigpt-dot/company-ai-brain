"""
Unit test ของ P2 candidate provider (Slice 2 pure interface) — offline, fake Qdrant
พิสูจน์ M4: unauthorized point (แม้ score สูงสุด/semantically-perfect) ถูก filter ก่อน retrieval →
ไม่เข้า candidates และ **ไม่ถึง cross-encoder scorer**

    python test_p2_provider.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import policy as P
import rerank as R
import p2_provider as PROV

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))
def raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True


class _Res:
    def __init__(self, pts): self.points = pts
class _Pt:
    def __init__(self, pid, payload, score): self.id = pid; self.payload = payload; self.score = score
class FakeQdrant:
    """บังคับ filter เหมือน Qdrant จริง (matches_policy) **ก่อน** ranking — filter อยู่ใน query_points"""
    def __init__(self, points): self.points = points
    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        hit = [p for p in self.points if P.matches_policy(p.payload, query_filter)]
        return _Res(sorted(hit, key=lambda p: -p.score)[:limit])


def pl(roles, coll="RECALL"):
    return {"acl_schema_version": 1, "policy_version": "poc-v1", "policy_status": "ACTIVE",
            "collection_group": coll, "confidentiality_level": 3, "allowed_roles": roles}
def pt(pid, roles, score, heading, text, source="D1"):
    p = pl(roles); p.update({"heading": heading, "text": text, "source": source})
    return _Pt(pid, p, score)
def access(role):
    return P.EffectiveAccess(P.ServicePrincipal("t", (role,), True, "enforce"), role)
IDENTITY = lambda spec: spec   # test: ส่ง spec ตรง ๆ ให้ fake (จริงใช้ to_qdrant_filter)

# corpus: qc เห็น A,B ; SENTINEL เป็น sales-only แต่ score สูงสุด (semantically-perfect unauthorized twin)
POINTS = [pt("A", ["qc", "admin"], 0.90, "RECALL A", "เนื้อหา A"),
          pt("B", ["qc", "admin"], 0.80, "RECALL B", "เนื้อหา B"),
          pt("SENTINEL", ["sales"], 0.99, "SENTINEL", "unauthorized ความลับ ห้ามเห็น")]
fake = FakeQdrant(POINTS)

cands = PROV.build_candidates(fake, "c", access("qc"), [0.0] * 4, 10, filter_adapter=IDENTITY)
ids = [c["point_id"] for c in cands]
check("M4: candidates = authorized เท่านั้น (ไม่มี SENTINEL แม้ score สูงสุด)", set(ids) == {"A", "B"}, ids)
check("dense_rank sequential + score desc (A>B)", ids == ["A", "B"] and [c["dense_rank"] for c in cands] == [1, 2])
check("candidate contract valid (validate_candidates ผ่าน)", R.validate_candidates(cands) is not None)

# M4 spy: sentinel id/text ไม่เคยถึง cross-encoder scorer
seen = {"texts": []}
def spy(q, texts): seen["texts"].extend(texts); return [0.0] * len(texts)
R.rerank_order("q", cands, spy)
check("M4 spy: sentinel text ไม่ถึง scorer", not any(("SENTINEL" in t or "ห้ามเห็น" in t) for t in seen["texts"]), seen["texts"])
check("scorer เห็นเฉพาะ candidate text (A,B)", set(seen["texts"]) == {"RECALL A เนื้อหา A", "RECALL B เนื้อหา B"})

# รับเฉพาะ trusted EffectiveAccess ไม่รับ raw role
check("build_candidates ปฏิเสธ raw role (str)",
      raises(lambda: PROV.build_candidates(fake, "c", "qc", [0.0] * 4, 10, IDENTITY), TypeError))
check("top_n ไม่ positive int -> fail",
      raises(lambda: PROV.build_candidates(fake, "c", access("qc"), [0.0] * 4, 0, IDENTITY), ValueError))

# resolve_and_build: principal + role -> resolve (fail-closed) -> build
princ = P.ServicePrincipal("s", ("qc",), True, "enforce")
c2 = PROV.resolve_and_build(fake, "c", princ, "qc", [0.0] * 4, 10, IDENTITY)
check("resolve_and_build authorized -> candidates", {c["point_id"] for c in c2} == {"A", "B"})
check("resolve_and_build role นอก scope -> AuthError (ไม่ leak)",
      raises(lambda: PROV.resolve_and_build(fake, "c", princ, "sales", [0.0] * 4, 10, IDENTITY), P.AuthError))

# build_rerank_text: heading+child deterministic, ไม่สลับ, truncate
check("rerank_text = heading + child", PROV.build_rerank_text({"heading": "H", "text": "C"}) == "H C")
check("rerank_text ใช้ field rerank_text ถ้ามี", PROV.build_rerank_text({"rerank_text": "RT", "heading": "H", "text": "C"}) == "RT")
check("rerank_text truncate ตาม max_chars", len(PROV.build_rerank_text({"heading": "x" * 600, "text": ""}, max_chars=512)) == 512)

# top_n เล็กกว่า authorized pool -> ตัดตาม dense rank (N sweep)
c3 = PROV.build_candidates(fake, "c", access("qc"), [0.0] * 4, 1, filter_adapter=IDENTITY)
check("top_n=1 -> คืน 1 candidate (dense rank สูงสุด)", [c["point_id"] for c in c3] == ["A"])

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
