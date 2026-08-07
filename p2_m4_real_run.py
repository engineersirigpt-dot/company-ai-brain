"""
P2 M4a **real-run driver** — สร้าง real docker/git `run` seam (subprocess) แล้วเรียก `DockerM4Controller.certify()`
รันจริงบนเครื่อง → **durable outer receipt** (host-authoritative: image/isolation/cleanup จาก Docker inspect จริง)

ปิด Codex real-run B1/B2/B3/M1: controller เป็นผู้สังเกต Docker เอง + ยืนยัน cleanup ก่อน resolve terminal (fail-closed)

ข้อจำกัดที่ **bind ไว้ใน receipt** (bounded rerun — Codex อนุญาต): bridge network (มี egress ให้ runtime pip) ไม่ใช่
`--internal` ; `qdrant-client` pip runtime ไม่ baked → dependency identity bind ด้วย `deps_sha256`, source ด้วย git commit

    python p2_m4_real_run.py
"""
import datetime
import json
import os
import secrets
import subprocess
import sys

import p2_m4_controller as CTL

REPO = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")

# pin = image .Id (= docker inspect <container> {{index .Image}} จะคืนค่านี้)
EVAL_IMG = "sha256:27768971905ebd3e16a9f6d2f3d2b774184b0c237ae9260f258982ba1e93a190"
QD_IMG = "qdrant/qdrant@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286"


def run(argv, *, input_bytes=None):
    """real seam: subprocess จริง (stdout/stderr = bytes, returncode) — ไม่ผ่าน shell (argv list, space-safe)"""
    return subprocess.run(argv, capture_output=True, input=input_bytes)


class Clock:
    def now_iso(self):
        return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def main() -> int:
    tok = secrets.token_hex(4)
    attempt = "oc-" + tok
    out_host = f"{REPO}/.p2_m4_out/{attempt}"
    os.makedirs(out_host, exist_ok=True)

    def read_bundle(container_path):                       # /out/run-1.bundle.json -> host out dir
        host = out_host + container_path[len("/out"):]
        with open(host, "rb") as f:
            return f.read()

    ctl = CTL.DockerM4Controller(
        run=run, source_dir_host=REPO, out_dir_host=out_host, read_bundle=read_bundle,
        evaluator_image=EVAL_IMG, qdrant_image=QD_IMG, project_id="m4-real-" + tok,
        attempt_id=attempt, token=tok, clock=Clock(), internal=False,     # bridge: egress ให้ runtime pip
        eval_entrypoint=["sh", "-lc",
                         "pip install --quiet --disable-pip-version-check qdrant-client "
                         "&& python /host/p2_m4_evaluator.py"])
    res = ctl.certify()
    print("=" * 70)
    print("TERMINAL_STATUS:", res["terminal_status"])
    print("RECEIPT_PATH:", res["path"])
    print("=" * 70)
    print(json.dumps(res["receipt"], indent=2, ensure_ascii=False))
    return 0 if res["terminal_status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
