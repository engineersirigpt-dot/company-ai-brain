"""
P2 M4 Qdrant provider + oracle adapter (client-factory + offline-testable) — adapter review round 1 fixes

M4 ports สองตัวสำหรับ permission-leak proof:
  - **provider** (`QdrantM4Provider`): `filtered_candidates(role, qv, limit)` → RBAC filter (เดียวกับ production API)
    → `p2_provider.build_candidates` (fail-closed detector) → `[(point_id, rerank_text)]`
  - **oracle** (`QdrantM4Oracle`): **independent** observer (session แยก) — `unfiltered_topn(qv, limit)` +
    `observe_visibility(case_id, effective_role)` = direct-scroll classify (case-scoped, ต่อ case ไม่ใช่ต่อ role)

trust boundary (B1): adapter **ไม่รับ ready-made client** — รับ `client_factory(endpoint)` แล้ว `bind(handle)` สร้าง session
จาก **exact handle["endpoint"]** + cross-check `session.observed_target_identity(collection_id)` ต้องตรง handle
(session ที่ชี้ production/คนละที่ → abort ก่อน seed) ; identity ที่คืน = ของ session ที่ยิง query จริง ไม่ใช่ copy handle.

authorization (M1): ไม่มี default principal — ต้อง inject `principal_factory` แบบ explicit ; ใช้
`approved_probe_principal_factory(evaluated_roles)` = **evaluation-only approved probe authorization**
(ไม่ใช่ authenticated principal จริง) ที่จำกัด role ด้วย approved set ผูก RunPlan/frozen ; role นอกชุด (รวม admin) → PermissionError.

**รัน M4a จริงบน Qdrant/model/docker = ยัง NO-GO** จน adapter provenance/isolation review + Data Owner sign-off (hash-bound).
"""
from __future__ import annotations

import policy
import p2_provider
import p2_m4_harness as HN
from qdrant_filter import to_qdrant_filter


class AdapterError(Exception):
    """provider/oracle adapter ใช้ผิด lifecycle / target ไม่ตรง handle / plan ไม่ครบ / collection ไม่ครบ"""


def approved_probe_principal_factory(approved_probe_roles):
    """
    **approved M4 probe authorization** (evaluation-only — ไม่ใช่ authenticated principal จริง) :
    mint verified probe principal เฉพาะ role ที่อยู่ใน approved set (ผูกกับ evaluated_roles ของ RunPlan/frozen ;
    deployment จริงผูก Data Owner sign-off hash) ; role นอกชุด (รวม admin) → PermissionError.
    **ห้าม reuse เป็น production request adapter.**
    """
    allowed = frozenset(approved_probe_roles)
    if not allowed:
        raise AdapterError("approved_probe_roles ว่าง (ต้องผูก evaluated_roles)")

    def _factory(role):
        if role not in allowed:
            raise PermissionError(f"role นอก approved M4 probe set: {role!r} (approved={sorted(allowed)})")
        return policy.ServicePrincipal(service_id="m4-probe", allowed_roles=(role,),
                                       authenticated=True, auth_mode="enforce")
    return _factory


def _require_handle(handle) -> None:
    if not (isinstance(handle, dict) and isinstance(handle.get("collection_id"), str) and handle["collection_id"]
            and isinstance(handle.get("endpoint"), str) and handle["endpoint"]):
        raise AdapterError("handle ต้องมี collection_id/endpoint เป็น non-blank str")


def _session_identity(session, collection_id: str) -> dict:
    """B1: identity มาจาก **session ที่ยิง query จริง** (session ยืนยัน endpoint/collection ของตัวเอง) ไม่ใช่ copy handle"""
    ident = session.observed_target_identity(collection_id)
    if not (isinstance(ident, dict) and isinstance(ident.get("collection_id"), str) and ident["collection_id"]
            and isinstance(ident.get("endpoint"), str) and ident["endpoint"]):
        raise AdapterError("session.observed_target_identity ผิดรูป")
    return {"collection_id": ident["collection_id"], "endpoint": ident["endpoint"]}


class _BoundTarget:
    """สร้าง session จาก exact handle endpoint + cross-check identity (ใช้ร่วม provider/oracle)"""

    def __init__(self, client_factory):
        self._client_factory = client_factory
        self._session = None
        self._bound = None
        self._identity = None

    def bind(self, handle) -> None:
        _require_handle(handle)
        if self._session is not None:
            raise AdapterError("bind ซ้ำ — ต้อง instance ใหม่ (session ผูก endpoint เดิม)")   # rebind = fail (B1)
        session = self._client_factory(handle["endpoint"])          # สร้างจาก exact endpoint ใน handle
        observed = _session_identity(session, handle["collection_id"])
        expected = {"collection_id": handle["collection_id"], "endpoint": handle["endpoint"]}
        if observed != expected:                                    # session ชี้คนละที่ (เช่น production) → abort
            raise AdapterError(f"session target ไม่ตรง handle (client ชี้คนละ endpoint?): {observed} != {expected}")
        self._session, self._bound, self._identity = session, dict(handle), observed

    def observed_target_identity(self) -> dict:
        if self._bound is None:
            raise AdapterError("ยังไม่ bind target (ต้องเรียก bind(handle) ก่อน)")
        return dict(self._identity)

    def _require(self):
        if self._bound is None:
            raise AdapterError("ยังไม่ bind target (ต้องเรียก bind(handle) ก่อน)")
        return self._session, self._bound


def _point_id(p) -> str:
    return str(getattr(p, "id", ""))                              # ตรง convention build_candidates


def _payload(p, pid: str) -> dict:
    payload = getattr(p, "payload", None)
    if not isinstance(payload, dict):
        raise AdapterError(f"backend คืน payload ผิดรูป (point {pid})")
    return payload


class QdrantM4Provider(_BoundTarget):
    """M4 ports.provider — RBAC-filtered candidates จาก isolated collection (session สร้างจาก handle endpoint)"""

    def __init__(self, client_factory, *, principal_factory,
                 filter_adapter=to_qdrant_filter, build_candidates=p2_provider.build_candidates):
        super().__init__(client_factory)
        if not callable(principal_factory):
            raise AdapterError("ต้อง inject principal_factory (ไม่มี default — ดู approved_probe_principal_factory)")
        self._principal_factory = principal_factory
        self._filter_adapter = filter_adapter
        self._build_candidates = build_candidates

    def filtered_candidates(self, effective_role, query_vector, limit):
        """resolve role → **verified probe** access → build_candidates (RBAC filter + fail-closed detector) → tuples"""
        session, bound = self._require()
        access = policy.resolve_effective_access(self._principal_factory(effective_role), effective_role)
        cands = self._build_candidates(session, bound["collection_id"], access,
                                       query_vector, limit, self._filter_adapter)
        pairs = [(c["point_id"], c["rerank_text"]) for c in cands]
        if not pairs:
            raise AdapterError("filtered_candidates ว่าง (case ต้องมี authorized candidate อย่างน้อยหนึ่ง)")
        return pairs


def _validate_plan(observation_plan) -> dict:
    """B2: observation_plan = {case_id: {effective_role, point_ids}} — case-scoped ; reject shape/empty/dup"""
    if not isinstance(observation_plan, dict) or not observation_plan:
        raise AdapterError("observation_plan ต้องเป็น {case_id: {effective_role, point_ids}} ไม่ว่าง")
    out = {}
    for cid, entry in observation_plan.items():
        if not (isinstance(entry, dict) and isinstance(entry.get("effective_role"), str) and entry["effective_role"]):
            raise AdapterError(f"observation_plan[{cid!r}] ต้องมี effective_role (str)")
        ids = entry.get("point_ids")
        if not (isinstance(ids, (list, tuple)) and ids):
            raise AdapterError(f"observation_plan[{cid!r}] point_ids ต้องเป็น list ไม่ว่าง")
        sids = [str(x) for x in ids]
        if len(set(sids)) != len(sids):
            raise AdapterError(f"observation_plan[{cid!r}] point_ids ซ้ำ")
        out[cid] = {"effective_role": entry["effective_role"], "point_ids": sids}
    return out


class QdrantM4Oracle(_BoundTarget):
    """
    M4 ports.oracle — **independent** unfiltered observer (session แยกจาก provider)
    observation_plan **case-scoped**: {case_id: {effective_role, point_ids}} = universe ต่อ case (authorized ∪ sentinel) ;
    oracle อ่าน payload จริง → classify เอง (ไม่เชื่อ frozen) → tamper (sentinel→authorized) = detect
    """

    def __init__(self, client_factory, *, observation_plan, principal_factory,
                 build_rerank_text=p2_provider.build_rerank_text, scroll_page=256):
        super().__init__(client_factory)
        if not callable(principal_factory):
            raise AdapterError("ต้อง inject principal_factory (ไม่มี default — ดู approved_probe_principal_factory)")
        self._plan = _validate_plan(observation_plan)
        self._principal_factory = principal_factory
        self._build_rerank_text = build_rerank_text
        self._scroll_page = scroll_page

    def unfiltered_topn(self, query_vector, limit):
        """raw top-N **ไม่มี RBAC filter** (ต้องมี sentinel ถ้าอยู่ top) → (point_id, rerank_text)"""
        session, bound = self._require()
        res = session.query_points(collection_name=bound["collection_id"], query=query_vector,
                                   query_filter=None, limit=limit, with_payload=True)   # None = ไม่มี filter
        out = []
        for p in getattr(res, "points", []):
            pid = _point_id(p)
            out.append((pid, self._build_rerank_text(_payload(p, pid))))
        return out

    def observe_visibility(self, case_id, effective_role):
        """direct-scroll case-scoped: อ่าน payload จริงของ point ใน plan[case] → classify ด้วย matches_policy"""
        self._require()
        entry = self._plan.get(case_id)
        if entry is None:
            raise AdapterError(f"case ไม่มีใน observation_plan: {case_id!r}")
        if entry["effective_role"] != effective_role:
            raise AdapterError(f"role ไม่ตรง plan entry (case {case_id!r}): {effective_role!r} != {entry['effective_role']!r}")
        access = policy.resolve_effective_access(self._principal_factory(effective_role), effective_role)
        spec = policy.compile_retrieval_filter(access)
        point_map = self._scroll_point_map()
        authorized, sentinel = [], []
        for pid in entry["point_ids"]:
            if pid not in point_map:
                raise AdapterError(f"point ใน observation_plan ไม่พบในคอลเลกชัน (case {case_id!r}): {pid}")
            payload = point_map[pid]
            pair = HN.component(pid, self._build_rerank_text(payload))["pair_sha256"]
            if policy.payload_is_policy_v1(payload) and policy.matches_policy(payload, spec):
                authorized.append(pair)
            else:
                sentinel.append(pair)
        return {"authorized_pairs": authorized, "sentinel_pairs": sentinel}

    def _scroll_point_map(self) -> dict:
        """scroll ทั้งคอลเลกชัน (paginated) → {point_id_str: payload} — direct observation (ไม่ผ่าน RBAC filter)"""
        session, bound = self._require()
        out, offset = {}, None
        while True:
            records, offset = session.scroll(collection_name=bound["collection_id"], with_payload=True,
                                             limit=self._scroll_page, offset=offset)
            for p in records:
                pid = _point_id(p)
                out[pid] = _payload(p, pid)
            if offset is None:
                break
        return out
