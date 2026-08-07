"""
Unit test ของ p2_m4_controller — DockerM4Controller orchestration ผ่าน **fake run seam** (offline)
พิสูจน์: provision -> run evaluator -> **observe จาก inspect** -> teardown -> post-inspect -> outer receipt
+ terminal ที่ถูกต้องเมื่อ (a) happy (b) image observed ปลอม (c) published_ports observed != 0 (d) cleanup residual

    python test_p2_m4_controller.py
"""
import io
import json
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import p2_m4_controller as CTL
import p2_m4_harness as HN
import p2_m4_receipt as RCPT

res = []
def check(name, cond, detail=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  :: {detail}"))

PIN = "sha256:" + "e" * 64                                    # evaluator image pin
QREF = "qdrant/qdrant@sha256:" + "f" * 64


def build_inner_bundle(*, net, vol, proj, coll, image, ports, prod, status="PASS"):
    iso = HN.build_isolation_proof(project_id=proj, network_id=net, volume_id=vol, collection_id=coll,
                                   marker="m4-marker", marker_readback="m4-marker", initial_point_count=0,
                                   network_published_ports=ports, endpoint_is_production=prod)
    evidence = {"status": status, "run_id": "run-1", "image_digest": image, "isolation_proof": iso}
    bundle = {"evidence": evidence, "receipt": {"run_id": "run-1", "status": "PASS"}}
    return json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class R:
    def __init__(s, stdout=b"", stderr=b"", rc=0): s.stdout = stdout; s.stderr = stderr; s.returncode = rc


def _filter_name(j):
    sel = j[j.index("--filter") + 1]                                # name=^NAME$
    return sel[len("name=^"):-1]


class FakeDocker:
    """
    จำลอง docker CLI + evaluator run ; ค่าที่ inspect/ls คืน = สิ่งที่ controller ถือเป็น authority
    knobs: observed_eval_image, qd_ports, bake_wrong_network, eval_rc, eval_declared_ports, leak_container, probe_unknown
    """
    def __init__(s, **k):
        s.observed_eval_image = k.get("observed_eval_image", PIN)
        s.qd_ports = k.get("qd_ports", 0)
        s.bake_wrong_network = k.get("bake_wrong_network", False)
        s.eval_rc = k.get("eval_rc", 0)
        s.eval_declared_ports = k.get("eval_declared_ports", None)   # None = ตรงกับ observed
        s.leak_container = k.get("leak_container", False)
        s.probe_unknown = k.get("probe_unknown", False)              # existence probe rc!=0 = daemon ล้มตอน cleanup (B1)
        s.removed = set()
        s.net = s.vol = None
        s.calls = []
        s.bundle_bytes = None

    def _exists(s, name):
        if s.probe_unknown:
            return R(stderr=b"Cannot connect to the Docker daemon", rc=1)   # UNKNOWN
        present = (s.leak_container and name == "m4qd-tok01") or (name not in s.removed)
        return R(stdout=(name.encode() if present else b""))

    def run(self, argv, *, input_bytes=None):
        s = self
        s.calls.append(argv)
        j = argv
        # provisioning
        if j[:3] == ["docker", "network", "create"]:
            s.net = j[-1]; return R(stdout=s.net.encode())          # id = ชื่อ network
        if j[:3] == ["docker", "volume", "create"]:
            s.vol = j[-1]; return R(stdout=s.vol.encode())
        if j[:3] == ["docker", "run", "-d"]:                        # qdrant container
            return R(stdout=b"qd-container")
        if j[:3] == ["docker", "run", "--name"] and "python" in j:  # evaluator (blocking)
            env = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for i, kv in enumerate(j) if j[i - 1] == "-e"}
            net = "net-EVIL" if s.bake_wrong_network else env["M4_NETWORK_ID"]
            ports = s.eval_declared_ports if s.eval_declared_ports is not None else s.qd_ports
            s.bundle_bytes = build_inner_bundle(net=net, vol=env["M4_VOLUME_ID"], proj=env["M4_PROJECT_ID"],
                                                coll=env["M4_COLLECTION_ID"], image=env["M4_EVAL_IMAGE_DIGEST"],
                                                ports=ports, prod=False)
            result = {"status": "PUBLISHED", "evidence_status": "PASS", "bundle_path": "/out/run-1.bundle.json",
                      "declared_image_digest": env["M4_EVAL_IMAGE_DIGEST"], "deps_sha256": "d" * 64}
            return R(stdout=b"M4A_RESULT " + json.dumps(result).encode(), rc=s.eval_rc)
        # image RepoDigests (B3)
        if j[:3] == ["docker", "image", "inspect"]:
            return R(stdout=json.dumps([QREF]).encode())
        # container inspect (-f) — observe
        if j[:2] == ["docker", "inspect"]:
            fmt = j[3] if j[2] == "-f" else None
            ref = j[-1]
            if fmt == "{{index .Image}}":
                return R(stdout=(s.observed_eval_image if ref == "m4eval-tok01" else "sha256:" + "a" * 64).encode())
            if fmt == "{{json .NetworkSettings.Networks}}":
                return R(stdout=json.dumps({s.net: {"IPAddress": "172.20.0.2"}}).encode())
            if fmt == "{{json .Mounts}}":
                return R(stdout=json.dumps([{"Name": s.vol}]).encode())
            if fmt == "{{json .NetworkSettings.Ports}}":
                # 6333/6334 EXPOSE เสมอ (value null = ไม่ publish) ; qd_ports ตัวแรก ๆ = publish ออก host จริง
                pmap = {"6333/tcp": None, "6334/tcp": None}
                for i in range(s.qd_ports):
                    pmap[f"{7000 + i}/tcp"] = [{"HostIp": "0.0.0.0", "HostPort": str(7000 + i)}]
                return R(stdout=json.dumps(pmap).encode())
            return R(stderr=b"No such object", rc=1)
        # three-state existence probe (B1)
        if j[:3] == ["docker", "ps", "-a"] and "--filter" in j:
            return s._exists(_filter_name(j))
        if j[:3] == ["docker", "network", "ls"] and "--filter" in j:
            return s._exists(_filter_name(j))
        if j[:3] == ["docker", "volume", "ls"] and "--filter" in j:
            return s._exists(_filter_name(j))
        # teardown
        if j[:3] == ["docker", "rm", "-f"] or (j[:2] == ["docker", "volume"] and j[2] == "rm") or \
           (j[:2] == ["docker", "network"] and j[2] == "rm"):
            s.removed.add(j[-1]); return R()
        # git identity (rev-parse HEAD / HEAD^{tree} ; status --porcelain)
        if j[:2] == ["git", "-C"]:
            if "rev-parse" in j:
                return R(stdout=(b"f" * 40 if "HEAD^{tree}" in j else b"c" * 40))
            return R(stdout=b"")                                    # status --porcelain: clean
        return R(stderr=b"unhandled " + " ".join(j).encode(), rc=127)


class FakeClock:
    def __init__(s): s.n = 0
    def now_iso(s): s.n += 1; return "2026-08-07T0%d:00:00+07:00" % s.n


def mk(fake, attempt="att-ctl-1"):
    return CTL.DockerM4Controller(run=fake.run, source_dir_host="/host/src", out_dir_host="/host/out",
                                  read_bundle=lambda path: fake.bundle_bytes, evaluator_image=PIN,
                                  qdrant_image=QREF, project_id="proj-run", attempt_id=attempt,
                                  token="tok01", clock=FakeClock())


# monkeypatch publish -> avoid real fs; ตรวจ receipt object แทน
_pub = RCPT.publish_outer_receipt
RCPT.publish_outer_receipt = lambda *, out_dir, attempt_id, receipt: f"{out_dir}/{attempt_id}.outer-receipt.json"

# ── happy path ──
f = FakeDocker()
out = mk(f).certify()
check("happy: certify -> terminal PASS", out["terminal_status"] == RCPT.PASS, out["terminal_status"])
check("happy: outer receipt validate ผ่าน (bind bundle จริง)",
      RCPT.validate_m4_outer_receipt(out["receipt"], inner_bundle_bytes=f.bundle_bytes) == [])
check("happy: observed.evaluator_image มาจาก inspect (= pin)", out["receipt"]["observed"]["evaluator_image"] == PIN)
check("happy: process บันทึก exit_code/timestamps/stdout digest จริง",
      out["receipt"]["process"]["exit_code"] == 0 and out["receipt"]["process"]["stdout_sha256"] and
      out["receipt"]["process"]["dependency_digest"] == "d" * 64)
check("happy: teardown ถูกเรียก (rm container/volume/network)",
      any(c[:3] == ["docker", "rm", "-f"] for c in f.calls) and
      any(c[:3] == ["docker", "network", "rm"] for c in f.calls))
check("happy: cleanup confirmed (post-inspect ไม่เจอ residual)", out["receipt"]["cleanup"]["confirmed"] is True)

# ── image observed ปลอม: evaluator container รันคนละ image กับ pin -> FAILED ──
f2 = FakeDocker(observed_eval_image="sha256:" + "9" * 64)
o2 = mk(f2, attempt="att-2").certify()
check("fake image: docker inspect evaluator image != pin -> FAILED", o2["terminal_status"] == RCPT.FAILED)

# ── published_ports observed != 0 (Qdrant เปิด port ออก host) -> FAILED ──
f3 = FakeDocker(qd_ports=2)
o3 = mk(f3, attempt="att-3").certify()
check("observed published_ports=2 -> FAILED", o3["terminal_status"] == RCPT.FAILED)

# ── network identity ที่ evaluator bake != ที่ Docker เห็น -> FAILED ──
f4 = FakeDocker(bake_wrong_network=True)
o4 = mk(f4, attempt="att-4").certify()
check("evaluator bake network id ปลอม != observed -> FAILED", o4["terminal_status"] == RCPT.FAILED)

# ── cleanup residual: container ยังอยู่หลัง teardown -> DEGRADED ──
f5 = FakeDocker(leak_container=True)
o5 = mk(f5, attempt="att-5").certify()
check("cleanup residual (container ค้าง) -> DEGRADED (ไม่ PASS)", o5["terminal_status"] == RCPT.DEGRADED,
      o5["terminal_status"])
check("cleanup residual: receipt ระบุ residual", o5["receipt"]["cleanup"]["residual"] != [])

# ── B1 (Codex re-review): daemon/inspect error ตอน cleanup verify -> UNKNOWN -> DEGRADED (ไม่ false-PASS) ──
f5b = FakeDocker(probe_unknown=True)
o5b = mk(f5b, attempt="att-5b").certify()
check("cleanup probe UNKNOWN (daemon ล้มตอน verify) -> DEGRADED (ไม่ยืนยัน clean)",
      o5b["terminal_status"] == RCPT.DEGRADED, o5b["terminal_status"])
check("cleanup UNKNOWN: receipt แยก unknown ออกจาก residual",
      o5b["receipt"]["cleanup"]["unknown"] != [] and o5b["receipt"]["cleanup"]["residual"] == [])

# ── evaluator exit != 0 -> FAILED ──
f6 = FakeDocker(eval_rc=1)
o6 = mk(f6, attempt="att-6").certify()
check("evaluator exit_code=1 -> FAILED", o6["terminal_status"] == RCPT.FAILED)

# ── safety: provision fail -> teardown ยังถูกเรียก (ไม่ leak) ──
class Boom(FakeDocker):
    def run(self, argv, *, input_bytes=None):
        if argv[:3] == ["docker", "run", "-d"]:
            raise RuntimeError("docker daemon down")
        return super().run(argv, input_bytes=input_bytes)
fb = Boom()
try:
    mk(fb, attempt="att-7").certify()
    check("safety: provision fail -> raise", False)
except Exception:
    check("safety: provision fail -> raise + teardown เรียก (network rm หลัง fail)",
          any(c[:3] == ["docker", "network", "rm"] for c in fb.calls))

RCPT.publish_outer_receipt = _pub
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
