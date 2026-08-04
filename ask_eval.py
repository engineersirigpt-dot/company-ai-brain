"""
Permission-leakage + ask-quality harness — P5a rev2 (ปิด Codex FIX-THEN-GO B1-B4/M1-M4)

หลัก permission test = **/search + synthetic canary manifest** ($0, deterministic, ไม่ผ่าน LLM):
  ต่อ canary หนึ่งตัว ทำ 'คู่' เสมอ (M1):
    positive = role ที่มีสิทธิ์ → ต้องเจอ point_id/canary
    negative = role ที่ไม่มีสิทธิ์ → ต้องไม่เจอ
  suite เขียวได้เฉพาะเมื่อทุก pair == PASS (deny/empty ทั้งชุด = INCONCLUSIVE = fail — ปิด B1)
/ask ใช้วัด citation-integrity / no-answer แยกแกน (ไม่ปนกับ permission verdict — B2)

รันจริง (บน server ที่ stack พร้อม + synthetic canary corpus ingest แล้ว = P5b):
    KB_EVAL_KEYS='{"qc":"k1","logistics":"k2",...}' python ask_eval.py --api http://localhost:8002
ทุก decision ที่ตัด exit code อยู่ใน eval_contract.py (pure) — harness-test ขับผ่าน call_fn ได้โดยไม่ต้องมี stack
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import eval_contract as ec

DEFAULT_API = "http://localhost:8002"
TOP_K = 5


# ── transport layer ────────────────────────────────────────────────────────────
def http_call(api: str, path: str, body: dict, key: str, timeout: int = 180) -> dict:
    """
    เรียก API จริง → normalized record {transport, status, points, answer, error}
    - malformed/partial JSON / ผิด shape → MALFORMED (M3) โดยไม่เก็บ raw body (กันข้อมูลลับ)
    - ไม่กลืน error เป็น points ว่าง (B3 เดิม)
    """
    key_of = "results" if path == "/search" else "citations"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(f"{api}{path}", json.dumps(body).encode(), headers)
    status, exc, malformed, resp = None, None, False, {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        status = e.code
        exc = e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        exc = e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        malformed, exc = True, e

    transport = ec.classify_transport(status, exc, malformed)
    points, answer = [], ""
    if transport == ec.SUCCESS:
        try:
            points = ec.extract_points(resp, key_of)
            answer = resp.get("answer", "") if isinstance(resp, dict) else ""
        except ValueError as e:
            transport, exc = ec.MALFORMED, e
    return {"transport": transport, "status": status, "points": points,
            "answer": answer, "error": (str(exc) if exc else None)}


def _texts(points: list[dict]) -> list[str]:
    return [f"{p.get('content', '')} {p.get('preview', '')}" for p in points]


# ── permission suite (manifest-driven /search pairs) ───────────────────────────
def run_permission_suite(call_fn, manifest: dict) -> list[dict]:
    """
    call_fn(path, body, role) -> record (inject ได้เพื่อ test offline)
    คืน list ของ pair result: {canary, positive_role, forbidden_role, verdict, ...}
    """
    out = []
    for c in manifest["canaries"]:
        cid, tok = c["point_id"], c.get("canary_token", "")
        q = c["probe_query"]
        banned = c.get("banned_tokens", [])

        prec = call_fn("/search", {"query": q, "role": c["positive_role"], "top_k": TOP_K},
                       c["positive_role"])
        pos = {"transport": prec["transport"],
               "found": ec.canary_found(prec["points"], cid, tok, _texts(prec["points"])),
               "banned_hit": []}

        for frole in c["forbidden_roles"]:
            nrec = call_fn("/search", {"query": q, "role": frole, "top_k": TOP_K}, frole)
            ntexts = _texts(nrec["points"])
            neg = {"transport": nrec["transport"],
                   "found": ec.canary_found(nrec["points"], cid, tok, ntexts),
                   "banned_hit": [b for b in banned
                                  if any(b.lower() in t.lower() for t in ntexts)]}
            out.append({
                "canary": cid, "positive_role": c["positive_role"], "forbidden_role": frole,
                "verdict": ec.pair_verdict(pos, neg),
                "pos_transport": pos["transport"], "pos_found": pos["found"],
                "neg_transport": neg["transport"], "neg_found": neg["found"],
                "neg_banned_hit": neg["banned_hit"],
                "neg_point_ids": ec.point_ids(nrec["points"]),
            })
    return out


# ── auth preflight (M2: key ต้อง scope role — spoof ต้องโดน 403) ────────────────
def run_auth_preflight(call_fn, spoof_pairs: list) -> dict:
    """
    spoof_pairs = [(key_role, spoof_role), ...] — ใช้ key ของ key_role ขอ spoof_role (นอก scope)
    ต้องได้ DENIED (403). ถ้าไม่มี key แยก role (spoof_pairs ว่าง) → unverified (ผ่านแบบมี warning)
    """
    if not spoof_pairs:
        return {"ok": True, "verified": False, "detail": "ไม่มี role->key แยก — ข้าม spoof check (M2 unverified)"}
    fails = []
    for key_role, spoof_role in spoof_pairs:
        rec = call_fn("/search", {"query": "preflight", "role": spoof_role, "top_k": 1}, key_role)
        if rec["transport"] != ec.DENIED:
            fails.append(f"key[{key_role}] ขอ role={spoof_role} -> {rec['transport']} (คาด DENIED/403)")
    return {"ok": not fails, "verified": True, "fails": fails}


# ── ask-quality suite (/ask — citation/no-answer, แยกจาก permission) ────────────
def run_ask_quality(call_fn, items: list, role: str) -> list:
    recs = []
    for item in items:
        rec = call_fn("/ask", {"question": item["question"], "role": role, "top_k": 4}, role)
        pts = rec["points"]
        sources = [p.get("source", "") for p in pts]
        ci = ec.citation_integrity(rec["answer"], len(pts))
        recs.append({
            "category": item["category"],
            "transport": rec["transport"],
            "retrieval": ec.retrieval_outcome(len(pts)) if rec["transport"] == ec.SUCCESS else None,
            "hit": ec.source_hit(item.get("expected_source", ""), sources),
            "citation_valid": ci["valid"], "cited_any": ci["cited_any"],
            "said_no_answer": any(k in rec["answer"] for k in ("ไม่พบข้อมูล", "ไม่มีข้อมูล", "ไม่สามารถตอบ")),
        })
    return recs


# ── orchestration (รับ call_fn — inject ได้เพื่อ test offline) ──────────────────
def run_suite(call_fn, manifest: dict, items: list, ask_role: str, spoof_pairs: list) -> dict:
    pairs = run_permission_suite(call_fn, manifest)
    preflight = run_auth_preflight(call_fn, spoof_pairs)
    ask = run_ask_quality(call_fn, items, ask_role) if items else []
    verdicts = [p["verdict"] for p in pairs]
    code = ec.suite_exit_code(verdicts, ask, preflight["ok"])
    return {"pairs": pairs, "preflight": preflight, "ask": ask,
            "exit_code": code, "verdicts": verdicts}


def print_report(res: dict) -> None:
    from collections import Counter
    pv = Counter(res["verdicts"])
    print("\n========== PERMISSION SUITE ==========")
    for p in res["pairs"]:
        mark = {"PASS": "ok ", "LEAK": "LEAK", "INCONCLUSIVE": "?? "}[p["verdict"]]
        print(f"  [{mark}] {p['canary']:22s} +{p['positive_role']:10s} -{p['forbidden_role']:10s}"
              f" (pos {p['pos_transport']}/{p['pos_found']}  neg {p['neg_transport']}/{p['neg_found']})")
    print(f"  totals: PASS={pv['PASS']} LEAK={pv['LEAK']} INCONCLUSIVE={pv['INCONCLUSIVE']}")
    pf = res["preflight"]
    print(f"  auth-preflight: ok={pf['ok']} verified={pf['verified']} "
          f"{pf.get('detail', pf.get('fails', ''))}")
    if res["ask"]:
        af = ec.ask_quality_failures(res["ask"])
        has = [r for r in res["ask"] if r["category"] == "has_answer"]
        hit = sum(1 for r in has if r["hit"])
        print("\n========== ASK QUALITY ==========")
        print(f"  has_answer retrieval-hit {hit}/{len(has)} | ask hard-fails: {len(af)}")
        for f in af:
            print(f"    FAIL {f}")
    print(f"\n>>> exit_code = {res['exit_code']} "
          f"({'GREEN' if res['exit_code'] == 0 else 'FAIL — ยังไม่พร้อมใช้ gate P1'})")
    print("======================================")


def _key_map() -> dict:
    raw = os.getenv("KB_EVAL_KEYS", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print("WARNING: KB_EVAL_KEYS ไม่ใช่ JSON — ไม่ใช้", flush=True)
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--manifest", default="permission_manifest.json")
    ap.add_argument("--eval-set", default="")
    ap.add_argument("--ask-role", default="admin")
    args = ap.parse_args()

    keys = _key_map()
    single = os.getenv("KB_EVAL_API_KEY", "")
    if not keys and not single:
        print("WARNING: ไม่มี KB_EVAL_KEYS/KB_EVAL_API_KEY — enforce mode จะได้ DENIED ทุกข้อ -> suite FAIL "
              "(ตามสัญญาใหม่ ไม่ใช่เขียวลวง)", flush=True)
    elif single and not keys:
        print("WARNING: มี key เดียว — พิสูจน์ retrieval filter ได้ แต่ไม่พิสูจน์ role-scope (M2 unverified)", flush=True)

    def call_fn(path, body, role):
        return http_call(args.api, path, body, keys.get(role, single))

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    items = []
    if args.eval_set and os.path.exists(args.eval_set):
        with open(args.eval_set, encoding="utf-8") as f:
            items = json.load(f)

    # spoof pairs สำหรับ preflight: ใช้ key ของ role หนึ่งขอ role อื่น (เฉพาะเมื่อมี key แยก >=2)
    spoof_pairs = []
    if len(keys) >= 2:
        roles = list(keys)
        spoof_pairs = [(roles[0], roles[1]), (roles[1], roles[0])]

    res = run_suite(call_fn, manifest, items, args.ask_role, spoof_pairs)
    with open("permission_eval_raw.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print_report(res)
    sys.exit(res["exit_code"])


if __name__ == "__main__":
    main()
