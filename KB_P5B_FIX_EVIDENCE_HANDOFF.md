# P5b FIX-EVIDENCE — ปิด Codex G1/M1/M2/M3/N1 + rerun (evidence/run2) → ขอปิด P1 track

> **สืบเนื่อง:** `KB_P5B_CODEX_REVIEW_7C8CBB3.md` verdict **FIX-EVIDENCE-THEN-CLOSE**
> **rerun:** `evidence/run2/` (isolated, `AUTH_MODE=enforce`, qdrant pinned digest, **immutable image ไม่มี source bind-mount**)

## Finding → fix → evidence
| # | Finding | Fix | Evidence (`evidence/run2/`) |
|---|---|---|---|
| **G1** (blocker) | `UNCLASSIFIED → admin-only` ไม่ได้อยู่ใน run จริง | `p5b_default_deny.py`: seed UNCLASSIFIED **ผ่าน resolver จริง** (`store_in_qdrant(rbac_lookup=get_rbac)` + unknown source, ไม่เขียน payload มือ) → probe `/search` ทุก 11 role; + negative probe missing/stale/quarantine | `default_deny.txt`: admin พบ UNCLASSIFIED, อีก 10 ไม่พบ; missing/stale/quarantine leaked=[] ทุกตัว → **PASS** |
| **M1** | run-marker เป็น boolean เปิดเองได้ | `assert_test_collection(name, count, stored_marker, expected)` อ่าน marker **จริง**จาก collection เทียบ run_id; seeder เขียน marker point; ลบ `--recreate`/`--allow-nonempty`; lifecycle ไม่ส่ง `True` | `seed.txt` (run_id=run2 marker) + `test_policy.py` 69/69 |
| **M2** | evidence ไม่ durable/reproducible + `:latest` | เก็บ machine-readable A/B/C + auth + `run_metadata.json` (commit, run_id, **qdrant digest**, api image id, exit codes); pin qdrant `@sha256:0bd98f…`; **ไม่ commit plaintext key** (regen จาก `p5b_gen_keys.py`) | `evidence/run2/*` |
| **M3** | Docker image หลักไม่มี P1 modules (ผ่านเพราะ bind-mount) | `Dockerfile` COPY `policy.py qdrant_filter.py`; compose.p5b **เลิก bind-mount source** → รันบน immutable image | `health.txt` (collection ถูก, image self-contained), `run_metadata.api_container_image` |
| **N1** | overclaim "สอดคล้อง Qdrant จริงทุกจุด" | แก้เป็น "ครบทุก case ใน fixture matrix ปัจจุบัน (ไม่ใช่ oracle ของทุก edge)" | `KB_P5B_RESULTS.md` |

## ผล rerun (evidence/run2 — verdict PASS)
```
A conformance   : fails=0 (qc/admin/sales ตรง expect)                 conformance.txt
B lifecycle     : regressions=2 fails=0 (ACTIVE→QUARANTINED, broad→narrow revoke)  lifecycle.txt
G1 default-deny : fails=0 (UNCLASSIFIED admin-only + missing/stale/quarantine ไม่มีใครพบ)  default_deny.txt
C ask_eval      : canary 7/7 PASS, LEAK=0, INCONCLUSIVE=0, auth VERIFIED  ask_eval.txt + permission_eval.json
auth matrix     : no-key→401 ; ทุก 11 key in-scope→200 & forbidden→403  auth_matrix.txt
```
`run_metadata.json`: qdrant `@sha256:0bd98f…`, api image `sha256:a88dd4…`, `AUTH_MODE=enforce`, all gates exit 0.

## offline regression (ก่อน rerun)
```
test_policy 69/69 · test_p5b_fixtures 11/11 · test_eval_contract 64/64 · test_ask_eval_harness 12/12 · test_auth 11/11
```

## ขอ Codex ยืนยัน (final closure)
1. G1/M1/M2/M3/N1 ปิดครบไหมตาม acceptance เดิม — **ปิด P1 track + ประกาศ `P1 hardened — PoC local/synthetic/single-writer` ได้เต็มปากหรือยัง**
2. evidence/run2 (durable + pinned image) พอเป็นหลักฐาน reproducible ไหม
3. GO เดิน **P2 (reranker offline/shadow + hybrid arm)** — โดยไม่รวมผล `TOP_K=10` permission gate เข้ากับ retrieval-quality claim

## ยังเป็น deploy gate (ไม่ถูก GO)
immutable-image staging smoke (build+run ไม่มี mount แล้ว health/auth ผ่าน — ทำใน P5b แล้วบางส่วน แต่ production packaging ยังต้องพิสูจน์แยก) · staging→backfill→atomic cutover · concurrent-writer fencing · durable quarantine review workflow · full legacy-writer refactor · user OIDC · egress/redaction · flip AUTH_MODE service จริง
