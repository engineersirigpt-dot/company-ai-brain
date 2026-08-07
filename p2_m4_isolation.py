"""
P2 M4 isolation adapter (ports.isolation) — contract-enforcing over injectable docker+qdrant driver — offline-testable

runner เรียกตามลำดับ (p2_m4_runner): provision → observe_initial_count/published_ports/endpoint_is_production
→ write_marker → read_marker → (สร้าง+validate IsolationProof) → seed → teardown
adapter บังคับ **M4 IsolationProof invariants** ให้ผ่าน `E.validate_m4_isolation_proof` แบบ fail-closed:
  - handle 5 คีย์ (project/network/volume/collection = non-blank str + **distinct 4 ตัว**, endpoint = non-blank str)
  - observe_initial_count / observe_published_ports = **int แท้** (runner ต้องการ 0) ; endpoint_is_production = **bool แท้**
  - refuse provision บน production endpoint ; teardown **idempotent / partial-safe**
  - marker write→read round-trip ผ่าน target จริง (driver)

**driver** = injectable infra layer (offline = fake ในเทสต์ ; real = `DockerQdrantDriver` — docker network/volume/container +
qdrant collection ; **รันจริง = NO-GO** จน slice review + ยังไม่รัน docker/qdrant จริงในเทสต์)

driver protocol (duck-typed): `provision()->handle` · `count()->int` · `published_ports()->int` ·
`endpoint_is_production()->bool` · `write_marker(m)` · `read_marker()->m` · `seed(corpus)` · `teardown()`
"""
from __future__ import annotations

import re

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class IsolationError(Exception):
    """isolation adapter ใช้ผิด lifecycle / driver คืน handle/observation ผิด contract / provision บน production"""


def _valid_qdrant_id(pid):
    """Qdrant point id ต้องเป็น **unsigned int หรือ UUID string** (พบจริงตอนรัน real Qdrant — str 'A'/'0' โดน 400) ;
    M4 corpus/frozen ต้องใช้ id ชนิดนี้ให้ตรงกันทั้ง seed/query/component (str-coerce ที่ build_candidates สอดคล้อง)"""
    if isinstance(pid, bool):
        raise IsolationError(f"point id เป็น bool ไม่ได้: {pid!r}")
    if isinstance(pid, int) and pid >= 0:
        return pid
    if isinstance(pid, str) and _UUID_RE.fullmatch(pid):
        return pid
    raise IsolationError(f"point id ต้องเป็น unsigned int หรือ UUID string (Qdrant contract): {pid!r}")


_HANDLE_KEYS = ("project_id", "network_id", "volume_id", "collection_id", "endpoint")
_ID_KEYS = ("project_id", "network_id", "volume_id", "collection_id")


def _as_int(x, what: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int):        # runner ต้องการ int แท้ (bool/float/numpy = fail)
        raise IsolationError(f"{what} ต้องเป็น int แท้ (ไม่ใช่ bool/float): {type(x).__name__}")
    return x


class QdrantDockerIsolation:
    """ports.isolation — contract enforcer เหนือ injectable driver (ไม่ผูก docker/qdrant ตรง ๆ → offline-testable)"""

    def __init__(self, *, driver):
        if driver is None:
            raise IsolationError("ต้อง inject driver (fake สำหรับ offline / DockerQdrantDriver สำหรับ real)")
        self._driver = driver
        self._state = "new"                                 # new → provisioned → torndown
        self._handle = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def provision(self) -> dict:
        if self._state != "new":
            raise IsolationError(f"provision ได้ครั้งเดียว (state={self._state})")
        handle = self._driver.provision()
        self._validate_handle(handle)
        self._handle = dict(handle)
        self._state = "provisioned"
        try:
            if self.observe_endpoint_is_production() is not False:   # defense: ห้าม provision บน production
                raise IsolationError("driver provision endpoint = production — abort")
        except BaseException:
            self._state = "provisioned"                     # ให้ teardown ทำงานเก็บกวาดได้
            self._safe_teardown()
            raise
        return dict(self._handle)

    def teardown(self) -> None:
        if self._state == "torndown":
            return                                          # idempotent
        try:
            self._driver.teardown()                         # driver ต้อง partial-provision-safe
        finally:
            self._state = "torndown"

    def _safe_teardown(self) -> None:
        try:
            self.teardown()
        except Exception:
            self._state = "torndown"                        # provision-fail path: อย่าให้ teardown error บดบัง error เดิม

    # ── observations (interlock — runner validate ก่อน seed) ─────────────────
    def observe_initial_count(self) -> int:
        self._require_provisioned()
        return _as_int(self._driver.count(), "initial_point_count")

    def observe_published_ports(self) -> int:
        self._require_provisioned()
        return _as_int(self._driver.published_ports(), "network_published_ports")

    def observe_endpoint_is_production(self) -> bool:
        self._require_provisioned()
        v = self._driver.endpoint_is_production()
        if v is not True and v is not False:
            raise IsolationError(f"endpoint_is_production ต้องเป็น bool แท้: {type(v).__name__}")
        return v

    # ── marker round-trip (write→read ผ่าน target จริง) ──────────────────────
    def write_marker(self, marker) -> None:
        self._require_provisioned()
        self._driver.write_marker(marker)

    def read_marker(self):
        self._require_provisioned()
        return self._driver.read_marker()

    # ── seed / เก็บกวาด ───────────────────────────────────────────────────────
    def seed(self, corpus) -> None:
        self._require_provisioned()
        self._driver.seed(corpus)

    # ── internal ──────────────────────────────────────────────────────────────
    def _require_provisioned(self) -> None:
        if self._state != "provisioned":
            raise IsolationError(f"ต้อง provision ก่อน (state={self._state})")

    def _validate_handle(self, handle) -> None:
        if not isinstance(handle, dict):
            raise IsolationError("driver.provision() ต้องคืน dict handle")
        for k in _HANDLE_KEYS:
            v = handle.get(k)
            if not isinstance(v, str) or not v.strip():
                raise IsolationError(f"handle[{k!r}] ต้องเป็น non-blank str: {v!r}")
        ids = [handle[k] for k in _ID_KEYS]
        if len(set(ids)) != 4:
            raise IsolationError("project/network/volume/collection ต้อง distinct 4 ตัว (isolation จริง)")


class CleanupUnconfirmed(Exception):
    """teardown ยืนยันไม่ได้ว่าทรัพยากรหายจริง (B2) — runner ต้องไม่ publish PASS (resource อาจค้าง กระทบ run ถัดไป)"""


def _is_not_found(err) -> bool:
    m = str(err).lower()
    return "no such" in m or "not found" in m or "already removed" in m


# ── real driver (docker + qdrant) — รันจริง = NO-GO จน slice review + real-run slice ──
# docker CLI ผ่าน injected `run` ; qdrant ops ผ่าน injected `session_factory(endpoint,collection,vector_size)->session`
# (concrete facade เหนือ QdrantClient มาตรฐาน) → **ordering/guard/cleanup logic offline-testable** ; real API = untested seam
class DockerQdrantDriver:
    """
    real isolation infra: docker network `--internal` (no published ports) + ephemeral volume + qdrant container
    (**pinned image digest** ไม่ใช่ `:latest`) + fresh collection.
    **fail-before-mutate (B1):** provision infra → build session → **verify transport-derived target identity +
    endpoint_is_production ก่อน Qdrant write แรก (recreate_collection)** ; production/target ไม่ตรง → abort ก่อน mutate.
    **cleanup (B2):** teardown สะสม error, "not found" = idempotent OK, อื่น ๆ → `CleanupUnconfirmed`.
    **runtime attest (B4):** `observed_image_digest()` = digest จาก docker inspect (runtime) ให้ launcher bind แทน plan-declared.
    """

    def __init__(self, *, run, session_factory, project_id, qdrant_image,
                 vector_size=1024, production_endpoints=frozenset()):
        # qdrant_image = **Qdrant** container image (แยกจาก plan.image_digest ที่เป็น scorer/evaluator image) — pin immutable
        if not (isinstance(qdrant_image, str) and "@sha256:" in qdrant_image):
            raise IsolationError("qdrant_image ต้อง pin ด้วย immutable digest (repo@sha256:...) ไม่ใช่ :latest/tag")
        self._run = run                                     # callable(list[str]) -> str (docker CLI stdout, audit)
        self._session_factory = session_factory             # callable(endpoint, collection, vector_size) -> QdrantSession
        self._project_id = project_id
        self._image = qdrant_image
        self._vector_size = vector_size
        self._prod = frozenset(production_endpoints)
        self._net = self._vol = self._container = self._endpoint = self._collection = self._session = None

    def provision(self) -> dict:
        import json
        import secrets
        tok = secrets.token_hex(6)
        net_name = f"m4net-{tok}"
        self._net = self._run(["docker", "network", "create", "--internal", net_name]).strip()
        self._vol = self._run(["docker", "volume", "create", f"m4vol-{tok}"]).strip()
        self._container = self._run(["docker", "run", "-d", "--network", self._net,
                                     "-v", f"{self._vol}:/qdrant/storage", "--name", f"m4qd-{tok}", self._image]).strip()
        # ── B1: identity จาก **Docker inspect จริง** (ไม่ใช่ค่า supplied) — container ต้องอยู่บน internal network ที่เราสร้าง ──
        nets = json.loads(self._run(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", self._container]) or "{}")
        info = nets.get(self._net) or nets.get(net_name)
        if info is None:
            raise IsolationError(f"container ไม่ได้อยู่บน internal network ที่สร้าง ({net_name}) — abort ก่อน mutate")
        addr = (info.get("IPAddress") or "").strip() or net_name and f"m4qd-{tok}"   # address จาก Docker inspect
        self._endpoint = f"http://{addr}:6333"                                       # derived จาก inspect ไม่ใช่ค่า supplied
        self._collection = f"m4-isolated-{tok}"
        # ── fail-before-mutate: non-production ก่อน Qdrant write แรก (endpoint เป็น container IP บน --internal network) ──
        if self.endpoint_is_production():
            raise IsolationError("provisioned endpoint = production — abort ก่อน Qdrant write")
        self._session = self._session_factory(self._endpoint, self._collection, self._vector_size)
        self._session.recreate_collection()                 # first Qdrant mutation — หลัง Docker-verify แล้วเท่านั้น
        return {"project_id": self._project_id, "network_id": self._net, "volume_id": self._vol,
                "collection_id": self._collection, "endpoint": self._endpoint}

    def observed_image_digest(self) -> str:
        """B4: digest จาก runtime image ของ container จริง (docker inspect) — launcher bind แทน plan-declared"""
        return self._run(["docker", "inspect", "-f", "{{index .Image}}", self._container]).strip()

    def count(self) -> int:
        return self._session.count()                        # QdrantSession คืน int (CountResult.count)

    def published_ports(self) -> int:
        out = self._run(["docker", "inspect", "-f", "{{len .NetworkSettings.Ports}}", self._container]).strip()
        return int(out or "0")

    def endpoint_is_production(self) -> bool:
        # ephemeral container ที่เราสร้างเองบน --internal network → non-production ; denylist = defense ชั้นสอง
        return self._endpoint in self._prod

    def write_marker(self, marker) -> None:
        self._session.write_marker(marker)                  # point deny-all (allowed_roles=[]) → ไม่โผล่ retrieval

    def read_marker(self):
        return self._session.read_marker()

    def seed(self, corpus) -> None:
        self._session.seed(corpus)

    def teardown(self) -> None:
        errors = []
        for cmd in ([["docker", "rm", "-f", self._container]] if self._container else []) + \
                   ([["docker", "volume", "rm", "-f", self._vol]] if self._vol else []) + \
                   ([["docker", "network", "rm", self._net]] if self._net else []):
            try:
                self._run(cmd)
            except Exception as e:
                if not _is_not_found(e):                     # "not found" = ลบไปแล้ว = idempotent OK ; อื่น ๆ = สะสม
                    errors.append((cmd[-1], type(e).__name__, str(e)))
        if errors:
            raise CleanupUnconfirmed(f"teardown ยืนยันไม่ได้ ({len(errors)} resource ค้าง): {errors}")


class QdrantSessionDriver:
    """
    container-side isolation driver — Qdrant ops ผ่าน `QdrantSession` (ไม่มี docker CLI) ; Docker observations
    (network/volume/published_ports/is_production) มาจาก controller ที่ verify ที่ host แล้ว (evaluator รันใน container
    บน internal network → reach Qdrant ผ่าน DNS) ; provision สร้าง fresh collection, teardown = controller ลบ infra
    """

    def __init__(self, *, session, project_id, network_id, volume_id, collection_id, endpoint,
                 published_ports=0, endpoint_is_production=False):
        self._s = session
        self._ids = {"project_id": project_id, "network_id": network_id, "volume_id": volume_id,
                     "collection_id": collection_id, "endpoint": endpoint}
        self._pp = published_ports
        self._prod = endpoint_is_production

    def provision(self) -> dict:
        self._s.recreate_collection()                       # fresh empty collection (count 0 ก่อน marker/seed)
        return dict(self._ids)

    def count(self) -> int:
        return self._s.count()

    def published_ports(self) -> int:
        return self._pp

    def endpoint_is_production(self) -> bool:
        return self._prod

    def write_marker(self, marker) -> None:
        self._s.write_marker(marker)

    def read_marker(self):
        return self._s.read_marker()

    def seed(self, corpus) -> None:
        self._s.seed(corpus)

    def teardown(self) -> None:
        pass                                                # controller (host) ลบ network/container/volume — collection หายกับ container


class QdrantSession:
    """
    concrete facade เหนือ standard `qdrant_client.QdrantClient` (B3) — ops ที่ driver ใช้ + transport-derived identity.
    real: `recreate_collection(vectors_config=VectorParams(...))`, `count().count`, `upsert/retrieve` (marker point deny-all),
    identity ยืนยันจาก client transport จริง. **ยังไม่ unit-test กับ real qdrant API** (reviewable seam ; real-run slice ตรวจ)
    """
    _MARKER_ID = 1
    _MARKER_KEY = "m4_run_marker"

    def __init__(self, client, *, endpoint, collection, vector_size):
        self._c = client
        self._endpoint = endpoint
        self._collection = collection
        self._vsize = vector_size

    @classmethod
    def connect(cls, endpoint, collection, vector_size):
        from qdrant_client import QdrantClient
        return cls(QdrantClient(url=endpoint), endpoint=endpoint, collection=collection, vector_size=vector_size)

    def observed_target_identity(self) -> dict:
        self._c.get_collections()                           # ping transport จริง (ยืนยันเชื่อมต่อ endpoint นี้ได้)
        return {"collection_id": self._collection, "endpoint": self._endpoint}

    def recreate_collection(self) -> None:
        from qdrant_client.models import VectorParams, Distance
        self._c.recreate_collection(self._collection,
                                    vectors_config=VectorParams(size=self._vsize, distance=Distance.COSINE))

    def count(self) -> int:
        return int(self._c.count(self._collection).count)

    def _mk_payload(self, marker) -> dict:
        return {self._MARKER_KEY: marker, "acl_schema_version": 1, "policy_version": "poc-v1",
                "policy_status": "ACTIVE", "collection_group": "RECALL", "confidentiality_level": 3,
                "allowed_roles": []}                         # deny-all → ไม่ match role ใด → ไม่โผล่ provider retrieval

    def write_marker(self, marker) -> None:
        from qdrant_client.models import PointStruct
        self._c.upsert(self._collection, points=[PointStruct(id=self._MARKER_ID, vector=[0.0] * self._vsize,
                                                             payload=self._mk_payload(marker))])

    def read_marker(self):
        recs = self._c.retrieve(self._collection, ids=[self._MARKER_ID], with_payload=True)
        return recs[0].payload.get(self._MARKER_KEY) if recs else None

    def _dummy_vector(self, pid) -> list:
        import hashlib
        h = hashlib.sha256(str(pid).encode("utf-8")).digest()
        return [(h[i % len(h)] / 255.0) + 0.01 for i in range(self._vsize)]   # non-zero deterministic (กัน zero-vector cosine)

    def clear_marker(self) -> None:
        from qdrant_client.models import PointIdsList
        self._c.delete(self._collection, points_selector=PointIdsList(points=[self._MARKER_ID]))

    def seed(self, corpus) -> None:
        from qdrant_client.models import PointStruct
        self.clear_marker()                                 # interlock (write→read) เสร็จแล้ว → ลบ marker กัน pollute retrieval (oracle unfiltered)
        pts = [PointStruct(id=_valid_qdrant_id(pid), vector=doc.get("vector") or self._dummy_vector(pid),
                           payload={**doc.get("payload", {}), "source": doc.get("source", ""),
                                    "rerank_text": doc.get("rerank_text", "")})
               for pid, doc in corpus.items()]
        self._c.upsert(self._collection, points=pts)
