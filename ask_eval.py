"""
Permission-leakage (security) + ask-quality harness — P5a rev2.1 (ปิด Codex FIX-THEN-GO รอบสอง)

สองแทร็คแยกกันชัด (Codex M2):
  SECURITY gate = /search + synthetic canary manifest + point-id oracle ($0, deterministic)
      ต่อ canary: positive ให้ **ทุก** authorized role (ต้องเจอ) + negative ให้ **ทุก** denied role (ต้องไม่เจอ)
      + auth preflight: spoof role ต้องได้ **exact 403** จึงถือ role-scope VERIFIED
      exit code ของ security = permission_ok และ (auth VERIFIED เมื่อ auth-gated)
  QUALITY track = /ask citation/no-answer/hit — รายงาน metric ครบ แต่ **ไม่ปน** security exit code
      (มี quality_gate แยกสำหรับ P5b)

รันจริง (บน stack + synthetic canary corpus = P5b):
    KB_EVAL_KEYS='{"qc":"k1","sales":"k2",...}' python ask_eval.py --api http://localhost:8002
    (ต้องมี role-scoped key ครบทุก role ใน manifest.known_roles ที่จะทดสอบ; retrieval-only ใช้ --retrieval-only)
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
def normalize_response(path: str, status, exc, resp, malformed: bool = False) -> dict:
    """
    raw (status/exc/body) → normalized record {transport, status, points, answer, error}
    แยกจาก network เพื่อ test seam (200 `{}` / `[{}]` → MALFORMED) ได้ offline (B1/M3)
    """
    transport = ec.classify_transport(status, exc, malformed)
    points, answer = [], ""
    if transport == ec.SUCCESS:
        try:
            points = ec.validate_search_response(resp) if path == "/search" \
                else ec.validate_ask_response(resp)
            answer = resp.get("answer", "") if isinstance(resp, dict) else ""
        except ValueError as e:
            transport, exc = ec.MALFORMED, e
    return {"transport": transport, "status": status, "points": points,
            "answer": answer, "error": (str(exc) if exc else None)}


def http_call(api: str, path: str, body: dict, key: str, timeout: int = 180) -> dict:
    """เรียก API จริง → normalize_response — ไม่กลืน error เป็น points ว่าง"""
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
    return normalize_response(path, status, exc, resp, malformed)


def _texts(points: list) -> list:
    return [f"{p.get('content', '')} {p.get('preview', '')}" for p in points]


def _probe(rec: dict, cid: str, tok: str, banned: list) -> dict:
    txt = _texts(rec["points"])
    return {
        "transport": rec["transport"],
        "found": ec.canary_found(rec["points"], cid, tok, txt),
        "banned_hit": [b for b in banned if any(b.lower() in t.lower() for t in txt)],
        "status": rec["status"],
        "point_ids": ec.point_ids(rec["points"]),
    }


# ── SECURITY: manifest-driven /search, exhaustive roles (B2) ───────────────────
def run_permission_suite(call_fn, manifest: dict) -> list:
    known = set(manifest["known_roles"])
    out = []
    for c in manifest["canaries"]:
        cid, tok, q = c["point_id"], c.get("canary_token", ""), c["probe_query"]
        banned = c.get("banned_tokens", [])
        authorized = list(c["authorized_roles"])
        denied = sorted(known - set(authorized))

        positives, negatives = [], []
        for role in authorized:
            rec = call_fn("/search", {"query": q, "role": role, "top_k": TOP_K}, role)
            positives.append({"role": role, **_probe(rec, cid, tok, banned)})
        for role in denied:
            rec = call_fn("/search", {"query": q, "role": role, "top_k": TOP_K}, role)
            negatives.append({"role": role, **_probe(rec, cid, tok, banned)})

        verdict = ec.canary_verdict(positives, negatives)
        out.append({
            "canary": c["canary_name"], "point_id": cid, "verdict": verdict,
            "pos_fail": [p["role"] for p in positives if p["transport"] != ec.SUCCESS or not p["found"]],
            "neg_leak": [n["role"] for n in negatives if n.get("found") or n.get("banned_hit")],
            "neg_bad_transport": [n["role"] for n in negatives if n["transport"] != ec.SUCCESS],
            "n_pos": len(positives), "n_neg": len(negatives),
        })
    return out


# ── SECURITY: auth preflight — spoof ต้องได้ exact 403 (M1 auth) ────────────────
def run_auth_preflight(call_fn, spoof_pairs: list) -> list:
    """spoof_pairs = [(key_role, spoof_role)] ; คืน raw results (auth_gate_status ตัดสินทีหลัง)"""
    out = []
    for key_role, spoof_role in spoof_pairs:
        rec = call_fn("/search", {"query": "preflight", "role": spoof_role, "top_k": 1}, key_role)
        out.append({"key_role": key_role, "spoof_role": spoof_role,
                    "status": rec["status"], "transport": rec["transport"]})
    return out


# ── QUALITY: /ask (แยกจาก security) ────────────────────────────────────────────
def run_ask_quality(call_fn, items: list, role: str) -> list:
    recs = []
    for item in items:
        rec = call_fn("/ask", {"question": item["question"], "role": role, "top_k": 4}, role)
        pts = rec["points"]
        ci = ec.citation_integrity(rec["answer"], len(pts))
        recs.append({
            "category": item["category"], "transport": rec["transport"],
            "retrieval": ec.retrieval_outcome(len(pts)) if rec["transport"] == ec.SUCCESS else None,
            "hit": ec.source_hit(item.get("expected_source", ""), [p.get("source", "") for p in pts]),
            "citation_valid": ci["valid"], "cited_any": ci["cited_any"],
            "said_no_answer": any(k in rec["answer"] for k in ("ไม่พบข้อมูล", "ไม่มีข้อมูล", "ไม่สามารถตอบ")),
        })
    return recs


# ── orchestration (รับ call_fn — inject ได้เพื่อ test offline) ──────────────────
def run_suite(call_fn, manifest: dict, items: list, ask_role: str,
              spoof_pairs: list, require_auth: bool = True) -> dict:
    manifest_errs = ec.validate_manifest(manifest)
    if manifest_errs:
        return {"manifest_errs": manifest_errs, "exit_code": 1, "pairs": [], "verdicts": [],
                "auth_status": ec.UNVERIFIED, "spoof": [], "quality": None, "require_auth": require_auth}

    pairs = run_permission_suite(call_fn, manifest)
    spoof = run_auth_preflight(call_fn, spoof_pairs)
    auth_status = ec.auth_gate_status(spoof)
    verdicts = [p["verdict"] for p in pairs]
    quality = ec.quality_gate(run_ask_quality(call_fn, items, ask_role)) if items else None
    return {
        "manifest_errs": [], "pairs": pairs, "verdicts": verdicts,
        "auth_status": auth_status, "spoof": spoof, "quality": quality,
        "require_auth": require_auth,
        "exit_code": ec.security_exit_code(verdicts, auth_status, require_auth),
    }


def print_report(res: dict) -> None:
    from collections import Counter
    if res["manifest_errs"]:
        print("MANIFEST INVALID (fail ก่อนยิง API):")
        for e in res["manifest_errs"]:
            print(f"  - {e}")
        print(f"\n>>> exit_code = {res['exit_code']}")
        return
    pv = Counter(res["verdicts"])
    print("\n========== SECURITY: PERMISSION SUITE ==========")
    for p in res["pairs"]:
        mark = {"PASS": "ok  ", "LEAK": "LEAK", "INCONCLUSIVE": "??  "}[p["verdict"]]
        extra = ""
        if p["neg_leak"]:
            extra += f" LEAK->{p['neg_leak']}"
        if p["pos_fail"]:
            extra += f" pos-fail->{p['pos_fail']}"
        if p["neg_bad_transport"]:
            extra += f" neg-bad->{p['neg_bad_transport']}"
        print(f"  [{mark}] {p['canary']:26s} (+{p['n_pos']}/-{p['n_neg']} roles){extra}")
    print(f"  totals: PASS={pv['PASS']} LEAK={pv['LEAK']} INCONCLUSIVE={pv['INCONCLUSIVE']}")
    print(f"  auth-gate: {res['auth_status']} (require_auth={res['require_auth']}) "
          f"spoof={[(s['key_role'], s['spoof_role'], s['status']) for s in res['spoof']]}")
    if res["quality"] is not None:
        q = res["quality"]
        rep = q["report"]
        print("\n========== QUALITY (/ask) — รายงานเท่านั้น ไม่ปน security exit ==========")
        print(f"  has_answer: hit {rep['has_answer_hit']}/{rep['has_answer_n']} | "
              f"empty {rep['has_answer_empty']} | dangling-cite {rep['dangling_citation']} | "
              f"cited_any {rep['cited_any']}")
        print(f"  no_answer: honest {rep['no_answer_honest']}/{rep['no_answer_n']} | "
              f"transport-bad {len(rep['transport_bad'])}")
        print(f"  quality_gate: ok={q['ok']} {q['reasons']}")
    green = res["exit_code"] == 0
    print(f"\n>>> SECURITY exit_code = {res['exit_code']} "
          f"({'GREEN' if green else 'FAIL — ยังไม่พร้อมใช้ gate P1'})")
    print("================================================")


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
    ap.add_argument("--retrieval-only", action="store_true",
                    help="permission gate อย่างเดียว ไม่บังคับ auth VERIFIED (auth = UNVERIFIED ชัด ๆ)")
    args = ap.parse_args()

    keys = _key_map()
    single = os.getenv("KB_EVAL_API_KEY", "")
    if not keys and not single:
        print("WARNING: ไม่มี KB_EVAL_KEYS/KB_EVAL_API_KEY — enforce mode จะได้ DENIED ทุกข้อ -> FAIL", flush=True)

    def call_fn(path, body, role):
        return http_call(args.api, path, body, keys.get(role, single))

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    items = []
    if args.eval_set and os.path.exists(args.eval_set):
        with open(args.eval_set, encoding="utf-8") as f:
            items = json.load(f)

    # spoof pairs: ใช้ key ของ role หนึ่งขอ role อื่น (ต้องมี key แยก ≥2 role)
    spoof_pairs = []
    if len(keys) >= 2:
        rs = list(keys)
        spoof_pairs = [(rs[0], rs[1]), (rs[1], rs[0])]

    res = run_suite(call_fn, manifest, items, args.ask_role, spoof_pairs,
                    require_auth=not args.retrieval_only)
    with open("permission_eval_raw.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print_report(res)
    sys.exit(res["exit_code"])


if __name__ == "__main__":
    main()
