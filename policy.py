"""
Policy compiler + effective-ACL contracts — P1 (Codex KB_P5A_REV2_1_CODEX_REVIEW §1-6)

pure deterministic server-side code — ไม่ import fastapi/qdrant/torch → unit-test ได้ล้วน
caller/LLM/Router **แก้สิทธิ์เองไม่ได้**: request body `role` ถูกใช้ได้เฉพาะหลังผ่าน resolve_effective_access

flow:
    X-API-Key
      -> authenticate_service()        -> ServicePrincipal (trusted identity + role scopes)
      -> resolve_effective_access()     -> EffectiveAccess (role เดียวที่ request นี้ใช้จริง)
      -> compile_retrieval_filter()     -> explicit filter spec (แม้ admin ก็มี filter — ห้าม None)
      -> Qdrant query_points(filter)    -> authorized candidates เท่านั้น

ขอบเขต P1: **service authentication** (ยังไม่ใช่ user/OIDC) · ยังไม่ทำ group/confidentiality AND ·
confidentiality_level = egress/classification signal เท่านั้น ยังไม่ใช่ query-time clearance
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass

# ── versioned policy constants (กัน payload/กติกาเก่าหลุดเข้า query) ─────────────
ACL_SCHEMA_VERSION = 1
POLICY_VERSION = "poc-v1"
ACTIVE, QUARANTINED = "ACTIVE", "QUARANTINED"
MAX_CONFIDENTIALITY_LEVEL = 3   # 0..3 (egress/classification signal — ยังไม่ใช้ AND สิทธิ์อ่าน)

VALID_AUTH_MODES = frozenset({"enforce", "warn", "off"})

# canonical role set (self-contained; main.VALID_ROLES ต้องอ้างอิงชุดนี้กัน drift)
KNOWN_ROLES = frozenset({
    "admin", "management", "production", "prepress", "qc", "engineering",
    "sales", "purchasing", "logistics", "hr", "it",
})

_MISSING = object()


class AuthError(Exception):
    """auth/authorization ปฏิเสธ — code = 401 (พิสูจน์ตัวไม่ได้) หรือ 403 (นอก scope/role ผิด)"""
    def __init__(self, code: int, reason: str):
        super().__init__(f"{code}:{reason}")
        self.code = code
        self.reason = reason


# ── identity / access contracts ────────────────────────────────────────────────
@dataclass(frozen=True)
class ServicePrincipal:
    service_id: str                 # stable server-side identity
    allowed_roles: tuple            # roles จาก API-key registry (frozen)
    authenticated: bool             # true เฉพาะ key valid
    auth_mode: str                  # enforce | warn | off (สำหรับ audit)

    @property
    def verified(self) -> bool:
        """หลักฐาน role-scope เชื่อได้ เฉพาะ authenticated + enforce (warn/off = unverified)"""
        return self.authenticated and self.auth_mode == "enforce"


@dataclass(frozen=True)
class EffectiveAccess:
    principal: ServicePrincipal
    effective_role: str             # role **เดียว** ที่ resolve แล้วสำหรับ request นี้ (ไม่ union)


@dataclass(frozen=True)
class DocumentPolicy:
    acl_schema_version: int
    policy_version: str
    policy_status: str              # ACTIVE | QUARANTINED
    collection_group: str
    confidentiality_level: int
    allowed_roles: tuple            # canonical effective read ACL (resolve แล้วตอน ingestion)
    quarantine_reason: str = ""

    def payload(self) -> dict:
        """payload v1 สำหรับ upsert ลง Qdrant"""
        return {
            "acl_schema_version": self.acl_schema_version,
            "policy_version": self.policy_version,
            "policy_status": self.policy_status,
            "collection_group": self.collection_group,
            "confidentiality_level": self.confidentiality_level,
            "allowed_roles": list(self.allowed_roles),
        }


# ── auth + resolve (fail-closed §2, §6) ────────────────────────────────────────
def authenticate_service(registry_entry: dict | None, service_hint: str,
                         auth_mode: str) -> ServicePrincipal:
    """
    registry_entry = ข้อมูลจาก API-key registry ที่ lookup ด้วย hash ของ key แล้ว (None = key ผิด/หาย)
    ไม่ raise ที่นี่ — การบังคับ enforce อยู่ที่ resolve_effective_access (แยก identity ออกจาก decision)
    """
    if registry_entry is None:
        return ServicePrincipal(service_id=service_hint or "unknown",
                                allowed_roles=(), authenticated=False, auth_mode=auth_mode)
    roles = tuple(registry_entry.get("allowed_roles", []))
    return ServicePrincipal(service_id=registry_entry.get("service", "unnamed"),
                            allowed_roles=roles, authenticated=True, auth_mode=auth_mode)


def resolve_effective_access(principal: ServicePrincipal, requested_role: str) -> EffectiveAccess:
    """
    เลือก role เดียวสำหรับ request แล้วตรวจว่าอยู่ใน scope (ห้าม union ทุก role อัตโนมัติ)
    fail-closed:
      - role ว่าง/ไม่รู้จัก → 403 (ทุก mode — malformed input)
      - enforce: key พิสูจน์ไม่ได้ → 401 ; requested role นอก scope → 403
      - warn/off: ผ่านได้แต่ principal.verified = False (ห้ามใช้เป็นหลักฐาน hardened)
    """
    if not requested_role or requested_role not in KNOWN_ROLES:
        raise AuthError(403, f"unknown_or_empty_role:{requested_role!r}")
    # N1: auth_mode ที่ไม่รู้จัก (เช่น typo "enfore") → fail-closed = บังคับเหมือน enforce
    # ไม่พึ่งให้ caller ทุกตัว validate เอง (FastAPI startup guard เป็นแค่ชั้นเสริม)
    mode = principal.auth_mode if principal.auth_mode in VALID_AUTH_MODES else "enforce"
    if mode == "enforce":
        if not principal.authenticated:
            raise AuthError(401, "missing_or_invalid_key")
        if requested_role not in principal.allowed_roles:
            raise AuthError(403, f"role_out_of_scope:{requested_role}")
    return EffectiveAccess(principal=principal, effective_role=requested_role)


# ── query-time compiler (§5) — explicit เสมอ แม้ admin, ห้ามคืน None ────────────
def compile_retrieval_filter(access: EffectiveAccess) -> list:
    """
    สร้าง filter spec (pure) ที่ต้อง AND ครบทุกเงื่อนไข:
      acl_schema_version == 1 และ policy_version == poc-v1 และ policy_status == ACTIVE
      และ allowed_roles ⊇ {effective_role}
    admin ก็ใช้ allowed_roles contains admin เช่นกัน (ไม่มี bypass) — missing/stale/malformed
    field จะไม่ match เองโดยธรรมชาติ (ดู matches_policy)
    """
    role = access.effective_role
    return [
        {"key": "acl_schema_version", "value": ACL_SCHEMA_VERSION},
        {"key": "policy_version", "value": POLICY_VERSION},
        {"key": "policy_status", "value": ACTIVE},
        {"key": "allowed_roles", "any": [role]},
    ]


def _type_exact_eq(stored, target) -> bool:
    """
    เทียบแบบ type-aware เลียนแบบ Qdrant (M1): Qdrant แยกชนิด — bool != int, int != float
    (Python มอง True == 1 และ 1 == 1.0 ซึ่งทำให้ fake matcher over-match ถ้าใช้ == ตรง ๆ)
    """
    if type(stored) is not type(target):
        return False
    return stored == target


def matches_policy(payload: dict, spec: list) -> bool:
    """
    conservative model ของ Qdrant Filter(must=[...]) — **type-aware** แต่ **ไม่ใช่** oracle
    ที่ครอบทุก JSON type/edge ของ Qdrant; conformance กับ Qdrant จริง = P5b (real-collection test)
    กติกาที่จำลอง:
      - field หาย / null (None) → ไม่ match (Qdrant: null ต้องใช้ IsNull ไม่ใช่ Match) — fail-closed
      - MatchValue: เทียบ type+value เป๊ะ (true != 1, 1.0 != 1)
      - MatchAny: stored scalar หรือ array — match เมื่อมีสมาชิก type+value ตรงอย่างน้อยหนึ่ง
        (หมายเหตุ: scalar `allowed_roles="qc"` **match ได้** เหมือน Qdrant → ต้องกันที่ write boundary
         ด้วยการ quarantine ไม่ใช่หวังให้ filter ตรวจ shape — ดู D1/resolve_document_policy)
    """
    for cond in spec:
        pv = payload.get(cond["key"], _MISSING)
        if pv is _MISSING or pv is None:
            return False
        if "value" in cond:
            if not _type_exact_eq(pv, cond["value"]):
                return False
        else:  # match-any (scalar หรือ array)
            vals = pv if isinstance(pv, list) else [pv]
            if not any(_type_exact_eq(v, a) for v in vals for a in cond["any"]):
                return False
    return True


# ── document policy resolver / validator (§4) ──────────────────────────────────
def resolve_document_policy(source_metadata: dict, rbac_lookup) -> DocumentPolicy:
    """
    resolve เอกสาร → effective ACL ตอน ingestion (deterministic; AI/Router ห้ามยุ่ง)
      source_metadata: {"source": str, ...}
      rbac_lookup: callable(source) -> {"collection_group","confidentiality_level","allowed_roles"}
                   (เช่น rbac_config.get_rbac — mapping/policy config อิสระ)
    - source ไม่รู้จักแต่โครงถูก → rbac_lookup คืน UNCLASSIFIED/admin-only (valid, ACTIVE)
    - source หาย/ผิดชนิด, mapping ผิด, ACL ว่าง/มี role แปลก → QUARANTINED
    """
    source = source_metadata.get("source")
    if not isinstance(source, str) or not source.strip():
        return _quarantine("missing_or_invalid_source", "UNCLASSIFIED", 0, [])
    try:
        rbac = rbac_lookup(source)
        coll = rbac["collection_group"]
        level = rbac["confidentiality_level"]
        roles = rbac["allowed_roles"]
    except Exception as e:  # mapping/config ผิดชนิด → quarantine ไม่ให้หลุดเข้า active
        return _quarantine(f"rbac_lookup_error:{type(e).__name__}", "UNCLASSIFIED", 0, [])

    # D1: allowed_roles ต้องเป็น list[str] เท่านั้น — scalar "qc" (ที่ Qdrant filter ได้แต่ผิด contract)
    # ต้อง quarantine ที่ write boundary (filter พิสูจน์ array shape เองไม่ได้)
    _coll = coll if isinstance(coll, str) and coll else "UNCLASSIFIED"
    if not isinstance(roles, list) or not roles or not all(isinstance(r, str) for r in roles):
        return _quarantine(f"allowed_roles_not_str_list:{type(roles).__name__}", _coll, 0, [])
    # M4: strict type/range — ไม่ coerce (bool/str/float ที่แอบผ่าน int() เดิม ต้อง quarantine)
    if isinstance(level, bool) or not isinstance(level, int) or not (0 <= level <= MAX_CONFIDENTIALITY_LEVEL):
        return _quarantine(f"bad_confidentiality_level:{level!r}", _coll, 0, list(roles))
    if not isinstance(coll, str) or not coll:
        return _quarantine("bad_collection_group", "UNCLASSIFIED", level, list(roles))

    candidate = DocumentPolicy(ACL_SCHEMA_VERSION, POLICY_VERSION, ACTIVE, coll, level, tuple(roles))
    ok, reason = validate_document_policy(candidate)
    if not ok:
        return _quarantine(reason, coll, level, list(roles))
    return candidate


def validate_document_policy(policy: DocumentPolicy) -> tuple:
    """คืน (ok, reason) — payload v1 ที่ contract ถูกต้อง (strict type) จึงเข้า active generation ได้"""
    if policy.acl_schema_version != ACL_SCHEMA_VERSION:
        return False, f"bad_acl_schema_version:{policy.acl_schema_version!r}"
    if policy.policy_version != POLICY_VERSION:
        return False, f"bad_policy_version:{policy.policy_version!r}"
    if policy.policy_status not in (ACTIVE, QUARANTINED):
        return False, f"bad_policy_status:{policy.policy_status!r}"
    if not isinstance(policy.collection_group, str) or not policy.collection_group:
        return False, "bad_collection_group"
    if isinstance(policy.confidentiality_level, bool) or not isinstance(policy.confidentiality_level, int):
        return False, f"bad_level_type:{type(policy.confidentiality_level).__name__}"
    if not (0 <= policy.confidentiality_level <= MAX_CONFIDENTIALITY_LEVEL):
        return False, f"level_out_of_range:{policy.confidentiality_level}"
    if not policy.allowed_roles:
        return False, "empty_acl"
    if not all(isinstance(r, str) for r in policy.allowed_roles):
        return False, "non_str_role_in_acl"
    unknown = set(policy.allowed_roles) - KNOWN_ROLES
    if unknown:
        return False, f"unknown_roles:{sorted(unknown)}"
    return True, ""


def _quarantine(reason: str, coll: str, level: int, roles: list) -> DocumentPolicy:
    """เอกสารที่ contract ผิด — QUARANTINED, ห้ามปรากฏใน standard retrieval แม้ admin (§4)"""
    return DocumentPolicy(ACL_SCHEMA_VERSION, POLICY_VERSION, QUARANTINED,
                          coll or "UNCLASSIFIED", level, tuple(roles), quarantine_reason=reason)


def is_active(policy: DocumentPolicy) -> bool:
    return policy.policy_status == ACTIVE


# ── legacy-writer guard (M2) — ingest.py = allowlisted policy-v1 writer เดียว ───
POLICY_V1_MARKERS = ("policy_version", "acl_schema_version", "policy_status")


def payload_is_policy_v1(payload) -> bool:
    return isinstance(payload, dict) and any(k in payload for k in POLICY_V1_MARKERS)


def assert_legacy_writer_allowed(sample_payloads: list, tool_name: str) -> None:
    """
    fail-fast สำหรับ writer เก่าที่ bypass policy resolver (M2): ถ้า collection มี payload policy-v1
    อยู่แล้ว ห้าม tool เก่าเขียนทับ (payload ไม่มี schema/status → point หายใต้ filter v1 = availability
    failure หรือพา malformed v1 เข้าปลายทาง). ให้ใช้ ingest.py หรือทำงานบน collection แยก
    """
    if any(payload_is_policy_v1(p) for p in sample_payloads):
        raise RuntimeError(
            f"[POLICY-GUARD] {tool_name}: พบ policy-v1 payload ใน collection — tool นี้ bypass "
            f"policy resolver จึงห้ามใช้กับ P1 collection. ใช้ ingest.py (allowlisted writer) "
            f"หรือรันบน collection แยก")


def validate_stored_payload(payload) -> tuple:
    """
    ตรวจ stored payload ว่าถูก contract — ใช้ตอน migrate (M2) กัน malformed policy-v1 หลุดเข้าปลายทาง
    legacy payload (ไม่มี marker v1) = ผ่าน (migrate corpus เก่าปกติได้); v1 ที่ผิด shape = ไม่ผ่าน
    """
    if not payload_is_policy_v1(payload):
        return True, ""
    roles = payload.get("allowed_roles")
    if not isinstance(roles, list):
        return False, f"allowed_roles_not_list:{type(roles).__name__}"
    pol = DocumentPolicy(payload.get("acl_schema_version"), payload.get("policy_version"),
                         payload.get("policy_status"), payload.get("collection_group"),
                         payload.get("confidentiality_level"), tuple(roles))
    return validate_document_policy(pol)


# ── replace-by-source generation (ปิด B1) + durable manifest (M3) ──────────────
def plan_source_replacement(chunks: list, rbac_lookup) -> dict:
    """
    วางแผน ingest แบบ replace-by-source: **revoke ทุก point เก่าของ source ที่ ingest รอบนี้**
    ก่อน upsert generation ใหม่ (ปิด B1) — เอกสารที่กลายเป็น QUARANTINED หรือ ACL แคบลง
    จะไม่ทิ้ง point รุ่นเก่าที่ ACL กว้างกว่าให้ standard retrieval ค้นเจอ

      chunks: list dict มีอย่างน้อย {"source", "id"}
      return {
        "delete_sources": [source ที่ต้อง delete-by-source ก่อน upsert],
        "active":      [{"source","id","policy"}]  (generation ใหม่ที่จะ upsert),
        "quarantined": [{"source","id","reason"}]  (ไม่เข้า active — ตรวจ workflow แยก),
      }
    ผู้เรียก (ingest) ต้อง: delete ทุก source ใน delete_sources ก่อน แล้วจึง upsert เฉพาะ active
    """
    active, quarantined = [], []
    for ch in chunks:
        pol = resolve_document_policy({"source": ch.get("source")}, rbac_lookup)
        if is_active(pol):
            active.append({"source": ch.get("source"), "id": ch.get("id"), "policy": pol})
        else:
            quarantined.append({"source": ch.get("source"), "id": ch.get("id"),
                                "reason": pol.quarantine_reason})
    delete_sources = sorted({ch.get("source") for ch in chunks
                             if isinstance(ch.get("source"), str) and ch.get("source")})
    return {"delete_sources": delete_sources, "active": active, "quarantined": quarantined}


def ingest_manifest_entries(plan: dict, run_id: str, ts: str) -> list:
    """
    durable manifest ต่อ source (M3) — terminal outcome ACTIVE/QUARANTINED ชัดเจน
    (ห้ามรายงาน success กำกวมเมื่อทั้งเอกสารถูก quarantine)
    """
    def row(source, outcome, reason):
        src = source if isinstance(source, str) else ""
        return {"source": src, "source_sha256": hashlib.sha256(src.encode()).hexdigest()[:16],
                "outcome": outcome, "reason": reason, "policy_version": POLICY_VERSION,
                "run_id": run_id, "ts": ts}
    rows = [row(r["source"], ACTIVE, "") for r in plan["active"]]
    rows += [row(r["source"], QUARANTINED, r["reason"]) for r in plan["quarantined"]]
    return rows
