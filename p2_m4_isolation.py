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


class IsolationError(Exception):
    """isolation adapter ใช้ผิด lifecycle / driver คืน handle/observation ผิด contract / provision บน production"""


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


# ── real driver (docker + qdrant) — รันจริง = NO-GO จน slice review + Data Owner (M4b) / infra approval ──
# offline test ใช้ fake driver ; real path นี้ไม่ถูก unit-test (ต้อง docker + qdrant_client จริง) — reviewable seam
class DockerQdrantDriver:
    """
    real isolation infra: docker network (internal, no published ports) + volume + qdrant container + fresh collection.
    marker = point payload policy-v1 `allowed_roles=[]` (deny-all → ไม่โผล่ retrieval) เขียน/อ่านกลับผ่าน collection จริง.
    **ยังไม่รันในเทสต์** — imports qdrant_client แบบ lazy ; ทุก docker op ผ่าน injected `run` (subprocess) เพื่อ audit
    """

    _MARKER_ID = 1
    _MARKER_KEY = "m4_run_marker"

    def __init__(self, *, run, client_factory, project_id, image="qdrant/qdrant:latest",
                 vector_size=1024, production_endpoints=frozenset()):
        self._run = run                                     # callable(list[str]) -> str (docker CLI stdout) — injectable/audit
        self._client_factory = client_factory               # callable(endpoint) -> qdrant client
        self._project_id = project_id
        self._image = image
        self._vector_size = vector_size
        self._prod = frozenset(production_endpoints)
        self._net = self._vol = self._container = self._endpoint = self._collection = self._client = None

    def provision(self) -> dict:
        import secrets
        tok = secrets.token_hex(6)
        self._net = self._run(["docker", "network", "create", "--internal", f"m4net-{tok}"]).strip()
        self._vol = self._run(["docker", "volume", "create", f"m4vol-{tok}"]).strip()
        # container ภายใน network เดียว, ไม่ publish port (internal only) — endpoint = ชื่อ container ใน network
        self._container = self._run(["docker", "run", "-d", "--rm", "--network", self._net,
                                     "-v", f"{self._vol}:/qdrant/storage", "--name", f"m4qd-{tok}", self._image]).strip()
        self._endpoint = f"http://m4qd-{tok}:6333"
        self._collection = f"m4-isolated-{tok}"
        self._client = self._client_factory(self._endpoint)
        self._client.recreate_collection(self._collection, vector_size=self._vector_size)
        return {"project_id": self._project_id, "network_id": self._net, "volume_id": self._vol,
                "collection_id": self._collection, "endpoint": self._endpoint}

    def count(self) -> int:
        return int(self._client.count(self._collection))

    def published_ports(self) -> int:
        out = self._run(["docker", "inspect", "-f", "{{len .NetworkSettings.Ports}}", self._container]).strip()
        return int(out or "0")

    def endpoint_is_production(self) -> bool:
        return self._endpoint in self._prod                 # ephemeral internal endpoint → False

    def write_marker(self, marker) -> None:
        self._client.upsert_marker(self._collection, self._MARKER_ID, self._MARKER_KEY, marker)

    def read_marker(self):
        return self._client.read_marker(self._collection, self._MARKER_ID, self._MARKER_KEY)

    def seed(self, corpus) -> None:
        self._client.seed(self._collection, corpus)

    def teardown(self) -> None:
        for cmd in (["docker", "rm", "-f", self._container] if self._container else None,
                    ["docker", "volume", "rm", "-f", self._vol] if self._vol else None,
                    ["docker", "network", "rm", self._net] if self._net else None):
            if cmd:
                try:
                    self._run(cmd)
                except Exception:
                    pass                                    # idempotent / partial-provision-safe
