"""
P2 M4a **host controller** — provision → run evaluator → **observe จาก Docker inspect จริง** → teardown →
post-inspect (ยืนยัน cleanup) → ประกอบ+publish **outer receipt** (p2_m4_receipt)

ปิด Codex real-run B1/B2/B3/M1 เชิงโครงสร้าง: ค่าที่ยืนยัน image/isolation/cleanup มาจาก **controller สังเกต Docker เอง**
ไม่ใช่ evaluator ประกาศจาก env — evaluator แค่ผลิต inner bundle (RBAC/oracle/scorer proof) ; controller เป็น authority
ของ execution/isolation/cleanup แล้ว hash-bind กับ inner bundle ทั้งก้อน

**ทุก Docker/subprocess ผ่าน seam เดียว `run(argv, *, input_bytes=None) -> result(stdout,stderr,returncode)`**
→ orchestration/observation/receipt logic **offline-testable เต็ม** ด้วย fake run ; real = subprocess จริง (untested seam)

flow (`certify`):
  provision(net --internal, volume, qdrant container pinned) → inspect qdrant (image/ports/network attach/mount)
  → run evaluator container (pinned) แบบ blocking, capture stdout/stderr/exit + timestamps → inspect evaluator image
  → read inner bundle จาก out dir → teardown → **post-inspect ยืนยันหาย** → build_outer_receipt → validate → publish

**real docker/qdrant/model = รันจริงได้เมื่อ inject real run seam** ; terminal verdict มาจาก outer receipt (fail-closed)
"""
from __future__ import annotations

import json

import p2_eval as E
import p2_m4_receipt as RCPT


class ControllerError(Exception):
    """provision/observe/teardown ไม่เป็นไปตาม contract (docker คืนค่าผิด / resource ไม่เกิด / inspect ล้ม)"""


def _stdout_bytes(res) -> bytes:
    b = getattr(res, "stdout", b"")
    return b if isinstance(b, (bytes, bytearray)) else str(b).encode("utf-8")


def _stderr_bytes(res) -> bytes:
    b = getattr(res, "stderr", b"")
    return b if isinstance(b, (bytes, bytearray)) else str(b).encode("utf-8")


def _text(res) -> str:
    return _stdout_bytes(res).decode("utf-8", "replace")


def _rc(res) -> int:
    return int(getattr(res, "returncode", 0))


class DockerM4Controller:
    """
    run             : callable(argv:list[str], *, input_bytes=None) -> result(stdout:bytes,stderr:bytes,returncode:int)
    source_dir_host : path ของ source (mount → /host ใน evaluator) — controller อ่าน git identity จากที่นี่
    out_dir_host    : path ที่ mount → /out ; inner bundle + outer receipt ออกที่นี่ (host filesystem)
    read_bundle     : callable(path)->bytes อ่านไฟล์ bundle จาก out dir (inject เพื่อ offline-test ; real = open)
    evaluator_image / qdrant_image : **pinned immutable** (sha256 / repo@sha256) — trusted infra config
    clock           : .now_iso()->str (timestamps process) ; token : run-scoped unique (test inject; real = secrets)
    """

    def __init__(self, *, run, source_dir_host, out_dir_host, read_bundle,
                 evaluator_image, qdrant_image, project_id, attempt_id, token, clock,
                 vector_size=4, internal=True, eval_entrypoint=("python", "/host/p2_m4_evaluator.py")):
        if not (isinstance(evaluator_image, str) and evaluator_image.startswith("sha256:")
                and len(evaluator_image) == 71):
            raise ControllerError("evaluator_image ต้อง pin เป็น sha256:<64hex> (image id ที่จะ run + attest)")
        if not (isinstance(qdrant_image, str) and "@sha256:" in qdrant_image):
            raise ControllerError("qdrant_image ต้อง pin เป็น repo@sha256:<64hex>")
        self._run = run
        self._src = source_dir_host
        self._out = out_dir_host
        self._read_bundle = read_bundle
        self._eval_img = evaluator_image
        self._qd_img = qdrant_image
        self._project = project_id
        self._attempt = attempt_id
        self._tok = token
        self._clock = clock
        self._vs = vector_size
        self._internal = bool(internal)                     # False = bridge (มี egress ให้ runtime pip) — ต้องบันทึกเป็นข้อจำกัด
        self._entry = list(eval_entrypoint)                 # คำสั่งใน evaluator container (บันทึกใน receipt.process.command)
        self._net = self._vol = self._qd = self._evalc = None
        self._collection = f"m4coll-{token}"
        self._endpoint = None

    # ── docker helpers (ผ่าน run seam เดียว) ─────────────────────────────────
    def _checked(self, argv) -> str:
        res = self._run(argv)
        if _rc(res) != 0:
            raise ControllerError(f"docker ล้ม (rc={_rc(res)}): {argv} :: {_stderr_bytes(res).decode('utf-8','replace')[:200]}")
        return _text(res).strip()

    def _inspect(self, ref, fmt) -> str:
        return self._checked(["docker", "inspect", "-f", fmt, ref])

    # ── provision + observe (Docker เป็น authority) ──────────────────────────
    def provision(self) -> None:
        net_argv = ["docker", "network", "create"] + (["--internal"] if self._internal else []) + [f"m4net-{self._tok}"]
        self._net = self._checked(net_argv)
        self._vol = self._checked(["docker", "volume", "create", f"m4vol-{self._tok}"])
        self._qd = self._checked(["docker", "run", "-d", "--network", self._net, "--name", f"m4qd-{self._tok}",
                                  "-v", f"{self._vol}:/qdrant/storage", self._qd_img])
        self._endpoint = f"http://m4qd-{self._tok}:6333"    # DNS ภายใน network (controller เป็นผู้ตั้งชื่อ)
        # ยืนยัน qdrant container อยู่บน network + mount volume ที่เราสร้าง (ไม่เชื่อ default)
        nets = json.loads(self._inspect(self._qd, "{{json .NetworkSettings.Networks}}") or "{}")
        if self._net not in nets and f"m4net-{self._tok}" not in nets:   # key เป็น id หรือชื่อ network ก็รับ
            raise ControllerError(f"qdrant ไม่ได้อยู่บน network ที่สร้าง: keys={list(nets)}")
        mounts = json.loads(self._inspect(self._qd, "{{json .Mounts}}") or "[]")
        if not any(m.get("Name") == self._vol or self._vol in str(m.get("Name", "")) for m in mounts):
            raise ControllerError("qdrant ไม่ได้ mount volume ที่สร้าง")

    def _published_ports(self) -> int:
        """
        นับเฉพาะ port ที่ **bind ออก host จริง** (B2) — `.NetworkSettings.Ports` map: value=null คือ EXPOSE เฉย ๆ
        (เห็นในเครือข่ายภายในเท่านั้น ไม่ publish) ; value=[{HostPort..}] คือ publish ออก host → นับตัวนั้น
        (`{{len ...}}` เดิมนับ EXPOSE ด้วย → Qdrant image EXPOSE 6333/6334 ทำให้ได้ 2 ทั้งที่ไม่ได้ publish)
        """
        raw = self._inspect(self._qd, "{{json .NetworkSettings.Ports}}")
        try:
            ports = json.loads(raw or "{}") or {}
        except ValueError:
            raise ControllerError(f".NetworkSettings.Ports inspect ไม่ใช่ json: {raw!r}")
        return sum(1 for v in ports.values() if v)          # v truthy = มี host binding = publish จริง

    def _observe(self) -> dict:
        qd_image = self._inspect(self._qd, "{{index .Image}}")           # docker-observed qdrant image id
        eval_image = self._inspect(self._evalc, "{{index .Image}}")      # B1 authority: image ที่ evaluator รันจริง
        published = self._published_ports()                              # B2 authority: host-published ports
        return {
            "expected_evaluator_image": self._eval_img,
            "evaluator_image": eval_image,
            "qdrant_image_ref": self._qd_img,
            "qdrant_image": qd_image,
            "network_id": self._net,
            "volume_id": self._vol,
            "project_id": self._project,
            "collection_id": self._collection,
            "published_ports": published,
            "endpoint": self._endpoint,
            "endpoint_is_production": False,     # ephemeral container บน network ที่ controller สร้าง
            "network_internal": self._internal,  # False = bridge (มี egress ให้ runtime pip) — ข้อจำกัดที่ bind ไว้
        }

    # ── run evaluator (blocking) + capture process จริง ──────────────────────
    def run_evaluator(self) -> dict:
        env = {
            "M4_QDRANT_ENDPOINT": self._endpoint, "M4_EVAL_IMAGE_DIGEST": self._eval_img,
            "M4_NETWORK_ID": self._net, "M4_VOLUME_ID": self._vol, "M4_PROJECT_ID": self._project,
            "M4_COLLECTION_ID": self._collection, "M4_OUT": "/out",
        }
        argv = ["docker", "run", "--name", f"m4eval-{self._tok}", "--network", self._net,
                "-v", f"{self._src}:/host", "-v", f"{self._out}:/out"]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        argv += [self._eval_img] + self._entry
        self._evalc = f"m4eval-{self._tok}"
        started = self._clock.now_iso()
        res = self._run(argv)
        finished = self._clock.now_iso()
        return {
            "command": argv, "exit_code": _rc(res),
            "started_utc": started, "finished_utc": finished,
            "stdout": _stdout_bytes(res), "stderr": _stderr_bytes(res),
            "stdout_sha256": RCPT._sha256_hex(_stdout_bytes(res)),
            "stderr_sha256": RCPT._sha256_hex(_stderr_bytes(res)),
        }

    def _parse_result(self, stdout: bytes) -> dict:
        for line in stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("M4A_RESULT "):
                return json.loads(line[len("M4A_RESULT "):])
        raise ControllerError("ไม่พบบรรทัด M4A_RESULT ใน stdout ของ evaluator")

    def _git_identity(self) -> dict:
        try:
            commit = self._checked(["git", "-C", self._src, "rev-parse", "HEAD"])
            porcelain = _text(self._run(["git", "-C", self._src, "status", "--porcelain"]))
            return {"git_commit": commit, "git_tree_dirty": bool(porcelain.strip())}
        except ControllerError:
            return {"git_commit": "", "git_tree_dirty": True}   # ระบุไม่ได้ = ถือว่า dirty (fail-safe)

    # ── teardown + ยืนยัน cleanup (post-inspect) ─────────────────────────────
    def teardown_and_verify(self) -> dict:
        for argv in ([["docker", "rm", "-f", self._evalc]] if self._evalc else []) + \
                    ([["docker", "rm", "-f", self._qd]] if self._qd else []) + \
                    ([["docker", "volume", "rm", "-f", self._vol]] if self._vol else []) + \
                    ([["docker", "network", "rm", self._net]] if self._net else []):
            self._run(argv)                                  # best-effort ; ยืนยันจริงด้วย post-inspect ข้างล่าง
        residual = []
        for kind, ref in (("container", self._evalc), ("container", self._qd),
                          ("volume", self._vol), ("network", self._net)):
            if not ref:
                continue
            probe = (["docker", "inspect", ref] if kind == "container" else
                     ["docker", kind, "inspect", ref])
            res = self._run(probe)
            if _rc(res) == 0:                                # inspect สำเร็จ = resource ยังอยู่ = cleanup ไม่ยืนยัน
                residual.append({"kind": kind, "id": ref})
        return {"confirmed": len(residual) == 0, "residual": residual}

    # ── entrypoint: certify (real-run authority) ─────────────────────────────
    def certify(self) -> dict:
        """รันครบ flow แล้วคืน outer receipt (validated) + path ที่ publish ; teardown เสมอ (finally)"""
        try:
            self.provision()
            proc = self.run_evaluator()
            observed = self._observe()
            result = self._parse_result(proc["stdout"])
            bundle_bytes = self._read_bundle(result["bundle_path"])
            inner = json.loads(bundle_bytes.decode("utf-8"))
            inner_evidence = inner["evidence"]
            proc_meta = {
                "command": proc["command"], "exit_code": proc["exit_code"],
                "started_utc": proc["started_utc"], "finished_utc": proc["finished_utc"],
                "stdout_sha256": proc["stdout_sha256"], "stderr_sha256": proc["stderr_sha256"],
                "dependency_digest": result.get("deps_sha256", ""),
                **self._git_identity(),
            }
            cleanup = self.teardown_and_verify()
        except BaseException:
            self._safe_teardown()
            raise
        receipt = RCPT.build_outer_receipt(
            attempt_id=self._attempt, run_id=inner_evidence.get("run_id"),
            inner_bundle_bytes=bundle_bytes, inner_evidence=inner_evidence,
            observed=observed, process=proc_meta, cleanup=cleanup)
        errs = RCPT.validate_m4_outer_receipt(receipt, inner_bundle_bytes=bundle_bytes)
        if errs:
            raise ControllerError(f"outer receipt invalid (bug — build/validate ไม่ตรง): {errs[:3]}")
        path = RCPT.publish_outer_receipt(out_dir=self._out, attempt_id=self._attempt, receipt=receipt)
        return {"receipt": receipt, "path": path, "terminal_status": receipt["terminal_status"]}

    def _safe_teardown(self) -> None:
        try:
            self.teardown_and_verify()
        except Exception:
            pass
