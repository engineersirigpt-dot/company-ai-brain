"""
Automated transport tests (Codex H5) — FastAPI TestClient + live PostgreSQL
รันผ่าน enq_api/run_api_tests.sh (สร้าง ephemeral PG + login roles + set env)
ครอบ: auth ทุก route + startup fail-closed, idempotency required/replay/conflict,
       oversize body, schema_version required, unknown-key + error no-leak, non-superuser
"""
import os, sys, json, subprocess

# ต้องตั้ง env ก่อน import app (main อ่าน config ตอน import + fail-closed)
os.environ.setdefault("ENQ_API_KEY", "test-key")
os.environ.pop("ENQ_DEV_MODE", None)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient
from enq_api import main as m

client = TestClient(m.app)
KEY = {"X-API-Key": "test-key"}
DRAFT = {"schema_version": "draft-v1", "header": {"enquiry_ref": "T"},
         "items": [{"line_no": 1, "job_name": "box",
                    "quantity_options": [{"option_no": 1, "quantity": 5000, "unit_ref": "PCS", "is_primary": True}]}]}
res = []
def check(name, cond, detail=""):
    res.append(cond); print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(detail)[:300]))

# happy
r = client.post("/enq/draft", json=DRAFT, headers={**KEY, "X-Request-Id": "t-happy"})
check("happy → 200 + rfq_id", r.status_code == 200 and "rfq_id" in r.json(), r.text)
rid = r.json().get("rfq_id")

# B1 auth ทุก route
check("POST no-auth → 401", client.post("/enq/draft", json=DRAFT, headers={"X-Request-Id": "z"}).status_code == 401)
check("GET no-auth → 401", client.get(f"/enq/rfq/{rid}").status_code == 401)
check("GET with auth → 200", client.get(f"/enq/rfq/{rid}", headers=KEY).status_code == 200)

# H1 idempotency
check("POST no X-Request-Id → 400", client.post("/enq/draft", json=DRAFT, headers=KEY).status_code == 400)
r2 = client.post("/enq/draft", json=DRAFT, headers={**KEY, "X-Request-Id": "t-happy"})
check("replay same key+payload → same rfq_id", r2.status_code == 200 and r2.json().get("rfq_id") == rid, r2.text)
rc = client.post("/enq/draft", json={**DRAFT, "header": {"enquiry_ref": "CHANGED"}}, headers={**KEY, "X-Request-Id": "t-happy"})
check("same key + diff payload → 409", rc.status_code == 409, rc.text)

# H3 schema strictness
check("missing schema_version → 422",
      client.post("/enq/draft", json={"items": [{"line_no": 1}]}, headers={**KEY, "X-Request-Id": "t-nv"}).status_code == 422)
check("empty items → 422",
      client.post("/enq/draft", json={"schema_version": "draft-v1", "items": []}, headers={**KEY, "X-Request-Id": "t-e"}).status_code == 422)

# H4 unknown key → 422 + ไม่รั่ว DB message
ru = client.post("/enq/draft", json={"schema_version": "draft-v1", "items": [{"line_no": 1, "EVIL": 1}]},
                 headers={**KEY, "X-Request-Id": "t-unk"})
check("unknown key → 422", ru.status_code == 422)
check("error ไม่รั่ว DB message", "allowlist" not in ru.text and ru.json().get("detail") == "invalid payload", ru.text)

# B2 oversize
big = {"schema_version": "draft-v1", "items": [{"line_no": 1, "job_name": "x" * 1_100_000}]}
check("oversize body → 413", client.post("/enq/draft", json=big, headers={**KEY, "X-Request-Id": "t-big"}).status_code == 413)

# B3 non-superuser session
h = client.get("/health").json()
check("read role ไม่ใช่ superuser", h.get("read_role_superuser") is False, str(h))

# B1 startup fail-closed (no key, no dev mode → RuntimeError)
env = {k: v for k, v in os.environ.items() if k not in ("ENQ_API_KEY", "ENQ_DEV_MODE")}
env["PYTHONIOENCODING"] = "utf-8"
cp = subprocess.run([sys.executable, "-c", "import enq_api.main"], cwd=REPO, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
err = (cp.stderr or "") + (cp.stdout or "")
check("startup fail-closed (no auth config) → error", cp.returncode != 0 and "fail-closed" in err, err[-200:])

nfail = res.count(False)
print(f"===== API TEST: {res.count(True)} passed, {nfail} failed =====")
sys.exit(1 if nfail else 0)
