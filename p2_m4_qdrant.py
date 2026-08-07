"""
P2 M4 Qdrant provider + oracle adapter (injectable client, offline-testable) — GO/SHIP รอบ 12

M4 ports สองตัวสำหรับ permission-leak proof (ต่อยอด p2_provider ที่เป็น RBAC-filtered candidate provider):
  - **provider** (`QdrantM4Provider`): `filtered_candidates(role, qv, limit)` → compile RBAC filter (เดียวกับ production API)
    → `p2_provider.build_candidates` (fail-closed detector) → project เป็น `[(point_id, rerank_text)]` ให้ harness
  - **oracle** (`QdrantM4Oracle`): **independent** observer (client แยก, ไม่มี RBAC filter) —
    `unfiltered_topn(qv, limit)` (raw top-N รวม sentinel) + `observe_visibility(role)` = direct-scroll classify
    (authorized/sentinel pair_sha256) จาก payload จริงในคอลเลกชัน → ต้องตรงกับ frozen (tamper = detect)

ทั้งคู่ bind กับ isolated target ตอน runtime ผ่าน `bind(handle)` + `observed_target_identity()` (identity cross-check).
**รัน M4a จริงบน Qdrant/model/docker = ยัง NO-GO** จน adapter provenance review + Data Owner sign-off (hash-bound).
point_id str-coerce ให้ตรง convention ของ `p2_provider.build_candidates` (str(p.id)).
"""
from __future__ import annotations

import policy
import p2_provider
import p2_m4_harness as HN
from qdrant_filter import to_qdrant_filter


class AdapterError(Exception):
    """provider/oracle adapter ใช้ผิด lifecycle / target ไม่ถูก bind / collection ไม่ครบ observation plan"""


def default_principal_factory(role: str) -> policy.ServicePrincipal:
    """
    verified principal ที่ scope เฉพาะ role นี้ = ตัวแทน 'authenticated service ที่ authorized สำหรับ role R'
    (M4 internal trusted context) → resolve_effective_access ผ่าน enforce ; deployment จริง principal มาจาก auth layer
    """
    return policy.ServicePrincipal(service_id="m4-provider", allowed_roles=(role,),
                                   authenticated=True, auth_mode="enforce")


def _require_handle(handle) -> None:
    if not (isinstance(handle, dict) and isinstance(handle.get("collection_id"), str) and handle["collection_id"]
            and isinstance(handle.get("endpoint"), str) and handle["endpoint"]):
        raise AdapterError("handle ต้องมี collection_id/endpoint เป็น non-blank str")


def _require_bound(bound) -> dict:
    if bound is None:
        raise AdapterError("ยังไม่ bind target (ต้องเรียก bind(handle) ก่อน)")
    return bound


def _identity(bound: dict) -> dict:
    return {"collection_id": bound["collection_id"], "endpoint": bound["endpoint"]}


def _point_id(p) -> str:
    return str(getattr(p, "id", ""))                      # ตรง convention build_candidates


def _payload(p, pid: str) -> dict:
    payload = getattr(p, "payload", None)
    if not isinstance(payload, dict):
        raise AdapterError(f"backend คืน payload ผิดรูป (point {pid})")
    return payload


class QdrantM4Provider:
    """M4 ports.provider — RBAC-filtered candidates จาก isolated collection (client injected)"""

    def __init__(self, client, *, principal_factory=default_principal_factory,
                 filter_adapter=to_qdrant_filter, build_candidates=p2_provider.build_candidates):
        self._client = client
        self._principal_factory = principal_factory
        self._filter_adapter = filter_adapter
        self._build_candidates = build_candidates
        self._bound = None

    def bind(self, handle) -> None:
        _require_handle(handle)
        self._bound = dict(handle)

    def observed_target_identity(self) -> dict:
        return _identity(_require_bound(self._bound))

    def filtered_candidates(self, effective_role, query_vector, limit):
        """resolve role → **verified** access → build_candidates (RBAC filter + fail-closed detector) → tuples"""
        bound = _require_bound(self._bound)
        access = policy.resolve_effective_access(self._principal_factory(effective_role), effective_role)
        cands = self._build_candidates(self._client, bound["collection_id"], access,
                                       query_vector, limit, self._filter_adapter)
        pairs = [(c["point_id"], c["rerank_text"]) for c in cands]
        if not pairs:
            raise AdapterError("filtered_candidates ว่าง (case ต้องมี authorized candidate อย่างน้อยหนึ่ง)")
        return pairs


class QdrantM4Oracle:
    """
    M4 ports.oracle — **independent** unfiltered observer (client แยกจาก provider)
    observation_plan = {effective_role: [point_id, ...]} = universe ของ case (authorized ∪ sentinel) ที่ต้องสังเกต ;
    oracle อ่าน payload จริงจากคอลเลกชัน → classify เอง (ไม่เชื่อ frozen) → tamper (sentinel กลายเป็น authorized) = detect
    """

    def __init__(self, client, *, observation_plan, build_rerank_text=p2_provider.build_rerank_text,
                 principal_factory=default_principal_factory, scroll_page=256):
        if not isinstance(observation_plan, dict) or not observation_plan:
            raise AdapterError("observation_plan ต้องเป็น {role: [point_id,...]} ไม่ว่าง")
        self._client = client
        self._plan = {r: [str(pid) for pid in ids] for r, ids in observation_plan.items()}
        self._build_rerank_text = build_rerank_text
        self._principal_factory = principal_factory
        self._scroll_page = scroll_page
        self._bound = None

    def bind(self, handle) -> None:
        _require_handle(handle)
        self._bound = dict(handle)

    def observed_target_identity(self) -> dict:
        return _identity(_require_bound(self._bound))

    def unfiltered_topn(self, query_vector, limit):
        """raw top-N **ไม่มี RBAC filter** (ต้องมี sentinel ถ้ามันอยู่ top) → (point_id, rerank_text)"""
        bound = _require_bound(self._bound)
        res = self._client.query_points(collection_name=bound["collection_id"], query=query_vector,
                                        query_filter=None, limit=limit, with_payload=True)   # None = ไม่มี filter
        out = []
        for p in getattr(res, "points", []):
            pid = _point_id(p)
            out.append((pid, self._build_rerank_text(_payload(p, pid))))
        return out

    def observe_visibility(self, effective_role):
        """direct-scroll: อ่าน payload จริงของ point ใน plan → classify authorized/sentinel ด้วย matches_policy"""
        _require_bound(self._bound)
        if effective_role not in self._plan:
            raise AdapterError(f"ไม่มี observation_plan สำหรับ role: {effective_role!r}")
        access = policy.resolve_effective_access(self._principal_factory(effective_role), effective_role)
        spec = policy.compile_retrieval_filter(access)
        point_map = self._scroll_point_map()
        authorized, sentinel = [], []
        for pid in self._plan[effective_role]:
            if pid not in point_map:
                raise AdapterError(f"point ใน observation_plan ไม่พบในคอลเลกชัน (role {effective_role!r}): {pid}")
            payload = point_map[pid]
            pair = HN.component(pid, self._build_rerank_text(payload))["pair_sha256"]
            if policy.payload_is_policy_v1(payload) and policy.matches_policy(payload, spec):
                authorized.append(pair)
            else:
                sentinel.append(pair)
        return {"authorized_pairs": authorized, "sentinel_pairs": sentinel}

    def _scroll_point_map(self) -> dict:
        """scroll ทั้งคอลเลกชัน (paginated) → {point_id_str: payload} — direct observation (ไม่ผ่าน RBAC filter)"""
        bound = self._bound
        out, offset = {}, None
        while True:
            records, offset = self._client.scroll(collection_name=bound["collection_id"], with_payload=True,
                                                  limit=self._scroll_page, offset=offset)
            for p in records:
                pid = _point_id(p)
                out[pid] = _payload(p, pid)
            if offset is None:
                break
        return out
