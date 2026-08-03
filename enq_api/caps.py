"""
Canonical capability spec (Codex F3–F7) — single source of the expected DB surface
==================================================================================
main.py + worker.py + tests import จากที่นี่ → กัน expected-surface drift (เขียนหลายที่)
ทุก check ใช้ **effective privilege** (has_*_privilege) → เห็นสิทธิ์สืบทอด/PUBLIC/WITH GRANT OPTION ด้วย
ครอบ 4 มิติ: (1) function OID (2) relation/column ทุก relkind (3) schema/sequence (4) grant-option
"""
from __future__ import annotations
import psycopg2

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

# ---- expected read columns (rfq_read_api) = ตรง SELECT/FK/filter/order ใน GET /enq/rfq (base table เท่านั้น) ----
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

REL_KINDS = "'r','p','v','m','f'"                  # F6: table, partitioned, view, matview, foreign table
MUT_PRIV = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_ALL_PRIV = ("SELECT",) + MUT_PRIV
SCHEMA_USAGE_SQL = "SELECT has_schema_privilege(current_user,'rfq','USAGE')"


def fn_drift_sql(role: str) -> str:
    """คืน (extra, missing) — F5: เทียบ OID set ของ function ที่ current_user execute ได้ vs expected signatures"""
    exp = ",".join("('rfq.%s'::regprocedure::oid)" % s for s in FN[role])
    return f"""WITH expected(oid) AS (VALUES {exp}),
      executable AS (SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='rfq' AND has_function_privilege(current_user, p.oid, 'EXECUTE'))
    SELECT (SELECT string_agg(oid::regprocedure::text, ', ') FROM (SELECT oid FROM executable EXCEPT SELECT oid FROM expected) a),
           (SELECT string_agg(oid::regprocedure::text, ', ') FROM (SELECT oid FROM expected EXCEPT SELECT oid FROM executable) b)"""


def no_data_access_sql(privs: tuple[str, ...] = _ALL_PRIV) -> str:
    """F3/F6: คืน (relation:priv) ที่ current_user มี (effective, ทุก relkind) — inbound/worker ต้อง NULL"""
    plist = ",".join("('%s')" % p for p in privs)
    return f"""SELECT string_agg(c.relname||':'||p.priv, ', ' ORDER BY c.relname, p.priv) FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL (VALUES {plist}) p(priv)
      WHERE n.nspname='rfq' AND c.relkind IN ({REL_KINDS})
        AND (has_table_privilege(current_user, c.oid, p.priv)
          OR (p.priv IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(current_user, c.oid, p.priv)))"""


def read_cols_drift_sql() -> str:
    """F3/F4/F6: คืน (extra, missing) — effective column SELECT ของ current_user (ทุก relkind) vs expected base columns"""
    exp = ",".join("('%s','%s')" % (t, c) for t, c in READ_COLUMNS)
    return f"""WITH expected(t,c) AS (VALUES {exp}),
      eff AS (SELECT cl.relname t, a.attname c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
        JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='rfq' AND cl.relkind IN ({REL_KINDS}) AND has_column_privilege(current_user, cl.oid, a.attnum, 'SELECT'))
    SELECT (SELECT string_agg(t||'.'||c, ', ' ORDER BY t,c) FROM (SELECT t,c FROM eff EXCEPT SELECT t,c FROM expected) x),
           (SELECT string_agg(t||'.'||c, ', ' ORDER BY t,c) FROM (SELECT t,c FROM expected EXCEPT SELECT t,c FROM eff) y)"""


def extra_privs_sql() -> str:
    """F7: คืน finding ของ schema CREATE, schema USAGE-WGO, sequence priv, function/column grant-option — ต้อง NULL ทุก role"""
    return f"""SELECT string_agg(x, ', ') FROM (
      SELECT 'schema:CREATE' x WHERE has_schema_privilege(current_user,'rfq','CREATE')
      UNION ALL SELECT 'schema:USAGE-WGO' WHERE has_schema_privilege(current_user,'rfq','USAGE WITH GRANT OPTION')
      UNION ALL
        SELECT 'seq:'||c.relname||':'||p.priv FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL (VALUES ('USAGE'),('SELECT'),('UPDATE')) p(priv)
          WHERE n.nspname='rfq' AND c.relkind='S' AND has_sequence_privilege(current_user, c.oid, p.priv)
      UNION ALL
        SELECT 'fn-wgo:'||p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='rfq' AND has_function_privilege(current_user, p.oid, 'EXECUTE WITH GRANT OPTION')
      UNION ALL
        SELECT 'col-wgo:'||cl.relname||'.'||a.attname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
          JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum>0 AND NOT a.attisdropped
          WHERE n.nspname='rfq' AND cl.relkind IN ({REL_KINDS})
            AND has_column_privilege(current_user, cl.oid, a.attnum, 'SELECT WITH GRANT OPTION')
    ) y"""


def _row(dsn: str, sql: str):
    c = psycopg2.connect(dsn)
    try:
        with c.cursor() as cur:
            cur.execute(sql); return cur.fetchone()
    finally:
        c.close()


def assert_role(dsn: str, label: str, kind: str):
    """canonical startup fail-closed — เทียบ effective surface ครบ 4 มิติ (F3–F7) ; RuntimeError ถ้า drift"""
    if not _row(dsn, SCHEMA_USAGE_SQL)[0]:
        raise RuntimeError(f"fail-closed: {label} ต้องมี USAGE ON SCHEMA rfq")
    xtra = _row(dsn, extra_privs_sql())[0]                                    # F7: schema/sequence/grant-option
    if xtra:
        raise RuntimeError(f"fail-closed: {label} มี privilege เกิน (schema/sequence/grant-option): {xtra}")
    extra, missing = _row(dsn, fn_drift_sql(kind))                            # F5: function OID set
    if extra or missing:
        raise RuntimeError(f"fail-closed: {label} function surface ผิด (extra={extra} missing={missing})")
    if kind == "read":                                                       # F3/F4/F6: exact read columns + no mutation
        e, m = _row(dsn, read_cols_drift_sql())
        if e or m:
            raise RuntimeError(f"fail-closed: {label} column surface ผิด (extra={e} missing={m})")
        mut = _row(dsn, no_data_access_sql(MUT_PRIV))[0]
        if mut:
            raise RuntimeError(f"fail-closed: {label} มี mutation privilege บน rfq: {mut}")
    else:                                                                    # inbound/worker: ห้าม data access ทุกชนิด (รวม SELECT)
        data = _row(dsn, no_data_access_sql())[0]
        if data:
            raise RuntimeError(f"fail-closed: {label} มี direct data access บน rfq (ต้องผ่าน SECURITY DEFINER): {data}")
