"""
Unit test ของ p2_m4_receipt — outer receipt build/validate/publish (fail-closed) offline
ครอบ Codex DoD #5: **negative ≥3** — (N1) image env ปลอม, (N2) isolation env ปลอม, (N3) cleanup fail หลัง inner PASS

    python test_p2_m4_receipt.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_eval as E
import p2_m4_harness as HN
import p2_m4_receipt as RCPT

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))

IMG = "sha256:" + "e" * 64                                   # evaluator image id (pin)
QREF = "qdrant/qdrant@sha256:" + "f" * 64
# raw identity ที่ "Docker เห็นจริง" — controller สังเกต แล้ว hash ต้องตรง isolation_proof
RAW = {"project_id": "proj-u", "network_id": "net-u", "volume_id": "vol-u", "collection_id": "coll-u"}


def mk_evidence(*, status="PASS", pub_ports=0, prod=False):
    iso = HN.build_isolation_proof(project_id=RAW["project_id"], network_id=RAW["network_id"],
                                   volume_id=RAW["volume_id"], collection_id=RAW["collection_id"],
                                   marker="m4-marker", marker_readback="m4-marker", initial_point_count=0,
                                   network_published_ports=pub_ports, endpoint_is_production=prod)
    return {"status": status, "run_id": "run-1", "image_digest": IMG, "isolation_proof": iso}


def mk_bundle(evidence):
    bundle = {"evidence": evidence, "receipt": {"run_id": "run-1", "status": "PASS"}}
    return json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def mk_observed(**over):
    o = {"expected_evaluator_image": IMG, "evaluator_image": IMG, "qdrant_image_ref": QREF,
         "qdrant_image": "sha256:" + "a" * 64, "network_id": RAW["network_id"], "volume_id": RAW["volume_id"],
         "project_id": RAW["project_id"], "collection_id": RAW["collection_id"], "published_ports": 0,
         "endpoint": "http://m4qd-x:6333", "endpoint_is_production": False}
    o.update(over)
    return o


def mk_process(**over):
    p = {"command": ["docker", "run", "img"], "exit_code": 0, "started_utc": "2026-08-07T01:00:00+07:00",
         "finished_utc": "2026-08-07T02:00:00+07:00", "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64,
         "git_commit": "c" * 40, "git_tree_dirty": False, "dependency_digest": "d" * 64}
    p.update(over)
    return p


def build(evidence, bb, observed, *, cleanup=None, process=None, attempt="att-1"):
    return RCPT.build_outer_receipt(attempt_id=attempt, run_id="run-1", inner_bundle_bytes=bb,
                                    inner_evidence=evidence, observed=observed,
                                    process=process or mk_process(),
                                    cleanup=cleanup or {"confirmed": True, "residual": []})


# ── positive: observed==declared ครบ + inner PASS + cleanup confirmed -> PASS ──
ev = mk_evidence(); bb = mk_bundle(ev)
r_pass = build(ev, bb, mk_observed())
check("positive: observed==declared + inner PASS + cleanup -> terminal PASS", r_pass["terminal_status"] == RCPT.PASS,
      r_pass["terminal_status"])
check("positive: validate errs ว่าง", RCPT.validate_m4_outer_receipt(r_pass, inner_bundle_bytes=bb) == [],
      RCPT.validate_m4_outer_receipt(r_pass, inner_bundle_bytes=bb))

# ── N1: image env ปลอม — evaluator รันคนละ image กับที่ประกาศ/pin -> FAILED ──
r_n1 = build(ev, bb, mk_observed(evaluator_image="sha256:" + "9" * 64))
check("N1 fake image: docker-observed evaluator image != pin/inner -> FAILED (false-PASS ปิด)",
      r_n1["terminal_status"] == RCPT.FAILED, r_n1["terminal_status"])
check("N1: reason ระบุ image mismatch", any("image" in x for x in RCPT.false_pass_reasons(r_n1)))

# lying receipt: force terminal=PASS แล้ว re-hash -> validator recompute จับได้
r_lie = dict(r_n1); r_lie["terminal_status"] = "PASS"
r_lie["outer_receipt_sha256"] = RCPT.outer_receipt_sha256(r_lie)
lie_errs = RCPT.validate_m4_outer_receipt(r_lie, inner_bundle_bytes=bb)
check("N1 fail-closed: receipt โกหก terminal=PASS (re-hash แล้ว) -> validator จับ status ปลอม",
      any("status ปลอม" in x or "recompute" in x for x in lie_errs), lie_errs)

# ── N2: isolation env ปลอม — (a) published_ports ที่ Docker เห็น != 0 ; (b) network id ที่เห็นจริง != ที่ประกาศ ──
r_n2a = build(ev, bb, mk_observed(published_ports=2))
check("N2a fake isolation: observed published_ports=2 (Qdrant เปิด port) -> FAILED",
      r_n2a["terminal_status"] == RCPT.FAILED, r_n2a["terminal_status"])
r_n2b = build(ev, bb, mk_observed(network_id="net-ATTACKER"))
check("N2b fake isolation: Docker network id != isolation_proof.network_id_sha256 -> FAILED",
      r_n2b["terminal_status"] == RCPT.FAILED, r_n2b["terminal_status"])
check("N2b: reason ระบุ network identity", any("network_id" in x for x in RCPT.false_pass_reasons(r_n2b)))

# declared (evaluator) โกหก published_ports=0 แต่ Docker เห็น 2 -> ยัง FAILED (observed!=declared ก็จับ)
ev_lie_pp = mk_evidence(pub_ports=0)   # evaluator ประกาศ 0
bb2 = mk_bundle(ev_lie_pp)
r_n2c = build(ev_lie_pp, bb2, mk_observed(published_ports=2))   # Docker เห็น 2
check("N2c: evaluator ประกาศ ports=0 แต่ Docker เห็น 2 -> FAILED (observed เป็น authority)",
      r_n2c["terminal_status"] == RCPT.FAILED, r_n2c["terminal_status"])

# ── N3: cleanup fail หลัง inner PASS — capability ok แต่ teardown ยืนยันไม่ได้ -> DEGRADED (ไม่ใช่ PASS) ──
r_n3 = build(ev, bb, mk_observed(), cleanup={"confirmed": False, "residual": [{"kind": "container", "id": "m4qd-x"}]})
check("N3 cleanup fail: inner PASS + observed match แต่ residual != [] -> DEGRADED (ไม่ PASS)",
      r_n3["terminal_status"] == RCPT.DEGRADED, r_n3["terminal_status"])
check("N3: validate ยังผ่าน (receipt ซื่อสัตย์ว่า DEGRADED)",
      RCPT.validate_m4_outer_receipt(r_n3, inner_bundle_bytes=bb) == [])

# ── inner ไม่ PASS -> FAILED ──
ev_fail = mk_evidence(status="FAIL"); bbf = mk_bundle(ev_fail)
r_if = build(ev_fail, bbf, mk_observed())
check("inner evidence status != PASS -> FAILED", r_if["terminal_status"] == RCPT.FAILED)

# exit_code != 0 -> FAILED
r_ec = build(ev, bb, mk_observed(), process=mk_process(exit_code=1))
check("process.exit_code != 0 -> FAILED", r_ec["terminal_status"] == RCPT.FAILED)

# ── bundle sha256 binding: validate ด้วย bytes อื่น -> errs ──
other = mk_bundle(mk_evidence(status="PASS", pub_ports=0)) + b" "
be = RCPT.validate_m4_outer_receipt(r_pass, inner_bundle_bytes=other)
check("bundle binding: validate ด้วยไฟล์ bundle ที่ต่างไป -> errs (bundle_sha256 ไม่ตรง)",
      any("bundle_sha256" in x for x in be), be)

# ── outer receipt integrity: แก้ body โดยไม่ re-hash -> errs ──
r_tam = dict(r_pass); r_tam["attempt_id"] = "att-EVIL"
te = RCPT.validate_m4_outer_receipt(r_tam, inner_bundle_bytes=bb)
check("integrity: แก้ body ไม่ re-hash -> outer_receipt_sha256 ไม่ตรง", any("ไม่ตรง body" in x for x in te), te)

# ── atomic publish no-clobber ──
d = tempfile.mkdtemp(prefix="p2rcpt-")
p1 = RCPT.publish_outer_receipt(out_dir=d, attempt_id="att-1", receipt=r_pass)
check("publish: เขียนไฟล์ atomic", os.path.isfile(p1))
try:
    RCPT.publish_outer_receipt(out_dir=d, attempt_id="att-1", receipt=r_pass)
    check("publish: no-clobber ครั้งสอง -> OuterReceiptError", False)
except RCPT.OuterReceiptError:
    check("publish: no-clobber ครั้งสอง -> OuterReceiptError", True)
# unsafe attempt_id
try:
    RCPT.publish_outer_receipt(out_dir=d, attempt_id="../evil", receipt=r_pass)
    check("publish: attempt_id ไม่ปลอดภัย -> OuterReceiptError", False)
except RCPT.OuterReceiptError:
    check("publish: attempt_id ไม่ปลอดภัย -> OuterReceiptError", True)
# ไฟล์ที่ publish อ่านกลับ validate ผ่าน
with open(p1, "rb") as f:
    disk = json.loads(f.read())
check("publish->read: receipt จากดิสก์ validate ผ่าน", RCPT.validate_m4_outer_receipt(disk, inner_bundle_bytes=bb) == [])
shutil.rmtree(d, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
