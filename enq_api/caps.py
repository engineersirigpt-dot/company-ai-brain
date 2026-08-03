"""
Canonical capability spec (Codex F3/F4/F5) — single source of the expected DB surface
=====================================================================================
main.py + worker.py + tests import จากที่นี่ → กัน expected surface drift (เขียนหลายที่)
ทุก check ใช้ **effective privilege** (has_function_privilege/has_column_privilege/has_any_column_privilege)
→ เห็นสิทธิ์ที่สืบทอดจาก role อื่น + PUBLIC ด้วย ; function เทียบด้วย **OID/signature** ไม่ใช่ชื่อ
"""
from __future__ import annotations

# ---- expected EXECUTE function signatures ต่อ role (identity args → resolve เป็น OID ตอนตรวจ) ----
FN: dict[str, list[str]] = {
    "inbound": ["begin_rfq_extraction(uuid,text,text,text,text,uuid,uuid,jsonb,text,text,text)",
                "create_rfq_draft(jsonb,text,text,text)"],
    "worker":  ["apply_rfq_extraction(uuid,uuid,jsonb,text,text,text)",
                "claim_rfq_extraction(uuid,text,text,text)",
                "fail_rfq_extraction(uuid,uuid,text,text,text,text)",
                "list_claimable_extractions(int)"],
    "read":    ["get_extraction_status(uuid)"],
}

# ---- expected read columns (rfq_read_api) = ตรง SELECT/FK/filter/order ใน GET /enq/rfq ----
READ_COLUMNS: list[tuple[str, str]] = [
    ("rfq", "id"), ("rfq", "rfq_no"), ("rfq", "status_code"), ("rfq", "revision_no"), ("rfq", "enquiry_ref"),
    ("rfq", "source_channel"), ("rfq", "customer_name_raw"), ("rfq", "priority_code"),
    ("rfq_item", "id"), ("rfq_item", "rfq_id"), ("rfq_item", "line_no"), ("rfq_item", "job_name"), ("rfq_item", "is_reprint"),
    ("rfq_item", "previous_job_ref"), ("rfq_item", "finished_width_mm"), ("rfq_item", "finished_length_mm"),
    ("rfq_item", "finished_depth_mm"), ("rfq_item", "finishing_state"), ("rfq_item", "packing_state"), ("rfq_item", "artwork_state"),
    ("rfq_quantity_option", "rfq_item_id"), ("rfq_quantity_option", "option_no"), ("rfq_quantity_option", "quantity"),
    ("rfq_quantity_option", "unit_ref"), ("rfq_quantity_option", "is_primary"),
    ("rfq_component", "rfq_item_id"), ("rfq_component", "component_no"), ("rfq_component", "component_name"),
    ("rfq_component", "paper_name_snapshot"), ("rfq_component", "paper_gsm_snapshot"), ("rfq_component", "print_sides_code"),
    ("rfq_component", "color_outside_count"), ("rfq_component", "color_inside_count"),
    ("rfq_process_requirement", "rfq_item_id"), ("rfq_process_requirement", "sequence_no"), ("rfq_process_requirement", "process_ref"),
    ("rfq_process_requirement", "process_name_raw"), ("rfq_process_requirement", "side_code"),
    ("rfq_packing_requirement", "rfq_item_id"), ("rfq_packing_requirement", "sequence_no"), ("rfq_packing_requirement", "packing_name_raw"),
    ("rfq_packing_requirement", "quantity_per_pack"), ("rfq_packing_requirement", "unit_ref"),
    ("rfq_delivery", "rfq_item_id"), ("rfq_delivery", "delivery_no"), ("rfq_delivery", "destination_raw"), ("rfq_delivery", "requested_date"),
]

_ALL_PRIV = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_COL_PRIV = ("SELECT", "INSERT", "UPDATE", "REFERENCES")   # privilege ที่ grant ระดับ column ได้


def fn_drift_sql(role: str) -> str:
    """คืน (extra, missing) — F5: เทียบ **OID set** ของ function ที่ current_user execute ได้ vs expected signatures"""
    exp = ",".join("('rfq.%s'::regprocedure::oid)" % s for s in FN[role])
    return f"""WITH expected(oid) AS (VALUES {exp}),
      executable AS (SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rfq' AND has_function_privilege(current_user, p.oid, 'EXECUTE'))
    SELECT (SELECT string_agg(oid::regprocedure::text, ', ') FROM (SELECT oid FROM executable EXCEPT SELECT oid FROM expected) a),
           (SELECT string_agg(oid::regprocedure::text, ', ') FROM (SELECT oid FROM expected EXCEPT SELECT oid FROM executable) b)"""


def no_data_access_sql(privs: tuple[str, ...] = _ALL_PRIV) -> str:
    """คืน string ของ (table:priv) ที่ current_user มีสิทธิ์ (effective) — F3: inbound/worker ต้องเป็น NULL
    ใช้ has_table_privilege + has_any_column_privilege (เห็น column-level + inherited/PUBLIC)"""
    plist = ",".join("('%s')" % p for p in privs)
    return f"""SELECT string_agg(c.relname||':'||p.priv, ', ' ORDER BY c.relname, p.priv) FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL (VALUES {plist}) p(priv)
      WHERE n.nspname='rfq' AND c.relkind IN ('r','p')
        AND (has_table_privilege(current_user, c.oid, p.priv)
          OR (p.priv IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(current_user, c.oid, p.priv)))"""


def read_cols_drift_sql() -> str:
    """คืน (extra, missing) — F3/F4: effective column SELECT ของ current_user vs expected read columns"""
    exp = ",".join("('%s','%s')" % (t, c) for t, c in READ_COLUMNS)
    return f"""WITH expected(t,c) AS (VALUES {exp}),
      eff AS (SELECT cl.relname t, a.attname c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
        JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='rfq' AND cl.relkind IN ('r','p') AND has_column_privilege(current_user, cl.oid, a.attnum, 'SELECT'))
    SELECT (SELECT string_agg(t||'.'||c, ', ' ORDER BY t,c) FROM (SELECT t,c FROM eff EXCEPT SELECT t,c FROM expected) x),
           (SELECT string_agg(t||'.'||c, ', ' ORDER BY t,c) FROM (SELECT t,c FROM expected EXCEPT SELECT t,c FROM eff) y)"""
