"""
RBAC Configuration — Company AI Brain
กำหนด: collection → level + roles ที่เข้าถึงได้
และ: source name → collection (ใช้ใน retag + ingest)
"""
import re

# --- Collection definitions ---
COLLECTIONS: dict[str, dict] = {
    "PRODUCTION": {
        "confidentiality_level": 2,
        "allowed_roles": ["production", "prepress", "qc", "engineering", "management", "admin"],
    },
    "PACKAGING": {
        "confidentiality_level": 2,
        "allowed_roles": ["production", "logistics", "qc", "management", "admin"],
    },
    "QUALITY": {
        "confidentiality_level": 2,
        "allowed_roles": ["qc", "production", "engineering", "management", "admin"],
    },
    "RECALL": {
        "confidentiality_level": 3,
        "allowed_roles": ["qc", "management", "admin"],
    },
    "ENGINEERING": {
        "confidentiality_level": 2,
        "allowed_roles": ["engineering", "production", "management", "admin"],
    },
    "LOGISTICS": {
        "confidentiality_level": 2,
        "allowed_roles": ["logistics", "sales", "management", "admin"],
    },
    "SALES": {
        "confidentiality_level": 3,
        "allowed_roles": ["sales", "management", "admin"],
    },
    "PURCHASING": {
        "confidentiality_level": 3,
        "allowed_roles": ["purchasing", "management", "admin"],
    },
    "HR": {
        "confidentiality_level": 3,
        "allowed_roles": ["hr", "management", "admin"],
    },
    "IT_SYSTEMS": {
        "confidentiality_level": 2,
        "allowed_roles": [
            "production", "prepress", "qc", "engineering",
            "sales", "purchasing", "logistics", "hr", "it",
            "management", "admin",
        ],
    },
    # default-deny: เอกสารที่จำแนกไม่ได้ต้องถูกกักไว้ให้ admin ตรวจก่อน
    # ห้ามเปิดให้แผนกใดเห็นจนกว่าจะ mapping ชัด (กัน data leak จากเอกสารชื่อแปลก)
    "UNCLASSIFIED": {
        "confidentiality_level": 3,
        "allowed_roles": ["admin"],
    },
}

# Keyword overrides ก่อน pattern matching (ตรวจสอบ case-insensitive)
_KEYWORD_MAP: list[tuple[str, str]] = [
    ("manual_managerae",     "IT_SYSTEMS"),
    ("dobot",                "ENGINEERING"),
    ("iso_jd",               "HR"),
    ("jd_form",              "HR"),
    ("training new",         "HR"),
    ("training_new",         "HR"),
    ("gp-580",               "RECALL"),
    ("qp-852",               "RECALL"),
    ("wi-423",               "QUALITY"),   # FSC symbol
    ("wi-722",               "SALES"),     # JOB Packaging
    ("wi-755-05",            "LOGISTICS"), # shipping/export
    ("wi-755-06",            "PACKAGING"), # กล่องลูกฟูก
]

# QP/WI numeric code → collection
_CODE_MAP: dict[int, str] = {
    423:  "QUALITY",
    630:  "QUALITY",
    710:  "PRODUCTION",
    713:  "ENGINEERING",
    721:  "SALES",
    722:  "SALES",
    723:  "SALES",
    724:  "SALES",
    731:  "SALES",
    741:  "PURCHASING",
    751:  "PRODUCTION",
    752:  "PRODUCTION",
    754:  "PURCHASING",
    755:  "LOGISTICS",   # default; overridden by keyword above for 755-05/06
    760:  "QUALITY",
    821:  "SALES",
    822:  "LOGISTICS",
    823:  "LOGISTICS",
    824:  "QUALITY",
    852:  "RECALL",
}


def get_collection(source_name: str) -> str:
    """แปลง source name → collection group"""
    s = source_name.lower()

    # 1. keyword overrides
    for kw, coll in _KEYWORD_MAP:
        if kw in s:
            return coll

    # 2. QP/WI/GP code — รองรับทั้ง hyphen (QP-721) และ underscore (QP_721)
    m = re.search(r'(?:qp|wi|gp)[-_](\d+)', s)
    if m:
        code = int(m.group(1))
        if re.match(r'gp[-_]', s):
            return "RECALL"
        return _CODE_MAP.get(code, "UNCLASSIFIED")

    return "UNCLASSIFIED"  # default-deny — admin ตรวจแล้วค่อยเพิ่ม mapping


def get_rbac(source_name: str) -> dict:
    """คืน dict พร้อม collection_group, confidentiality_level, allowed_roles"""
    coll = get_collection(source_name)
    return {
        "collection_group": coll,
        **COLLECTIONS[coll],
    }
