"""
Pre-verify P5b fixtures offline (ไม่ต้อง Qdrant) — matches_policy (conservative model) ต้องตรงกับ
expect_roles ที่นิยามไว้. p5b_conformance.py จะ assert ชุดเดียวกันกับ Qdrant จริง → model vs reality

    python test_p5b_fixtures.py
"""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import policy as P
import eval_contract as ec
import p5b_fixtures as FX

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))


def _filter(role):
    acc = P.EffectiveAccess(P.ServicePrincipal("t", (role,), True, "enforce"), role)
    return P.compile_retrieval_filter(acc)


# conformance fixtures: matches_policy ต้องตรง expect_roles ทุก role
all_ok = True
for fx in FX.conformance_fixtures():
    for role in FX.CONFORMANCE_ROLES:
        got = P.matches_policy(fx["payload"], _filter(role))
        want = role in fx["expect_roles"]
        if got != want:
            all_ok = False
            print(f"    MISMATCH {fx['name']} role={role} got={got} want={want}")
check("conformance fixtures: matches_policy == expect_roles ทุกจุด/ทุก role", all_ok)

# จุดสำคัญเชิงความหมาย
fxmap = {f["name"]: f for f in FX.conformance_fixtures()}
check("scalar-qc: qc match (store-integrity) แต่ admin ไม่",
      P.matches_policy(fxmap["scalar-qc"]["payload"], _filter("qc")) is True
      and P.matches_policy(fxmap["scalar-qc"]["payload"], _filter("admin")) is False)
check("quarantined: admin ไม่ match", P.matches_policy(fxmap["quarantined"]["payload"], _filter("admin")) is False)
check("schema-true/float: qc ไม่ match",
      not P.matches_policy(fxmap["schema-true"]["payload"], _filter("qc"))
      and not P.matches_policy(fxmap["schema-float"]["payload"], _filter("qc")))
check("null/missing: admin ไม่ match",
      not P.matches_policy(fxmap["null-roles"]["payload"], _filter("admin"))
      and not P.matches_policy(fxmap["missing-roles"]["payload"], _filter("admin")))

# canary fixtures: ACTIVE + authorized role match, denied role ไม่ match
manifest = FX.load_manifest()
known = set(manifest["known_roles"])
canaries = FX.canary_fixtures(manifest)
check("canary fixtures: จำนวนตรง manifest", len(canaries) == len(manifest["canaries"]))
cok = True
for cf, cdef in zip(canaries, manifest["canaries"]):
    auth = set(cdef["authorized_roles"])
    for role in known:
        got = P.matches_policy(cf["payload"], _filter(role))
        want = role in auth
        if got != want:
            cok = False
            print(f"    CANARY MISMATCH {cf['name']} role={role} got={got} want={want}")
check("canary: authorized role match, denied role ไม่ match (ทุก known role)", cok)
check("canary: point_id เป็น UUID", all(ec.is_uuid(c["id"]) for c in canaries))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
