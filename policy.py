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
from dataclasses import dataclass

# ── versioned policy constants (กัน payload/กติกาเก่าหลุดเข้า query) ─────────────
ACL_SCHEMA_VERSION = 1
POLICY_VERSION = "poc-v1"
ACTIVE, QUARANTINED = "ACTIVE", "QUARANTINED"

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
    if principal.auth_mode == "enforce":
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


def matches_policy(payload: dict, spec: list) -> bool:
    """
    executable semantics ของ filter (ต้องตรงกับที่ Qdrant Filter บังคับทุกประการ)
    ใช้ใน fake-Qdrant test + เป็นสัญญาว่า Qdrant ต้องทำอะไร
    field หาย → ไม่ match (fail-closed) ; list payload → ตรวจแบบ contains
    """
    for cond in spec:
        pv = payload.get(cond["key"], _MISSING)
        if "value" in cond:
            if pv != cond["value"]:
                return False
        else:  # match-any / contains
            vals = pv if isinstance(pv, list) else [pv]
            if not any(a in vals for a in cond["any"]):
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
        return _quarantine("missing_or_invalid_source", "", 0, [])
    try:
        rbac = rbac_lookup(source)
        coll = rbac["collection_group"]
        level = int(rbac["confidentiality_level"])
        roles = list(rbac["allowed_roles"])
    except Exception as e:  # mapping/config ผิดชนิด → quarantine ไม่ให้หลุดเข้า active
        return _quarantine(f"rbac_lookup_error:{type(e).__name__}", "", 0, [])

    candidate = DocumentPolicy(ACL_SCHEMA_VERSION, POLICY_VERSION, ACTIVE, coll, level, tuple(roles))
    ok, reason = validate_document_policy(candidate)
    if not ok:
        return _quarantine(reason, coll, level, roles)
    return candidate


def validate_document_policy(policy: DocumentPolicy) -> tuple:
    """คืน (ok, reason) — ต้องเป็น payload v1 ที่ contract ถูกต้องจึงเข้า active search generation ได้"""
    if policy.acl_schema_version != ACL_SCHEMA_VERSION:
        return False, f"bad_acl_schema_version:{policy.acl_schema_version}"
    if policy.policy_version != POLICY_VERSION:
        return False, f"bad_policy_version:{policy.policy_version}"
    if policy.policy_status not in (ACTIVE, QUARANTINED):
        return False, f"bad_policy_status:{policy.policy_status}"
    if not policy.collection_group:
        return False, "empty_collection_group"
    if not policy.allowed_roles:
        return False, "empty_acl"
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
