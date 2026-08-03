#!/usr/bin/env bash
# run_all.sh — reproducible RFQ migration test suite บน ephemeral postgres:16
# รัน: 020 (schema/invariant, +010 seed) + 030 (service v2) + concurrency harness (T01-T08)
#
# ต้องมี: docker, python ที่ติดตั้ง psycopg2  (ตั้ง PY=/path/to/python ถ้าไม่ใช่ `python`)
# ตัวอย่าง (เครื่อง dev นี้):
#   PY="/c/Users/Windows 10/rfqv/Scripts/python.exe" bash migrations/test/run_all.sh
#
# หมายเหตุ: migration เป็น prototype (ยังไม่มี live RFQ DB) — สคริปต์นี้สร้าง/ลบ
# container ของตัวเอง ไม่แตะ DB จริงของ clinic/siriwattana
set -euo pipefail
cd "$(dirname "$0")/.."          # -> migrations/
PY="${PY:-python}"
CT="rfq_test_$$"
PORT="${PORT:-5455}"
export PGHOST=localhost PGPORT="$PORT" PGDATABASE=rfqtest PGUSER=postgres PGPASSWORD=test

cleanup() { docker rm -f "$CT" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== start ephemeral postgres:16 ($CT :$PORT) =="
docker run -d --name "$CT" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=rfqtest -p "$PORT:5432" postgres:16 >/dev/null
until docker exec "$CT" pg_isready -U postgres -q 2>/dev/null; do sleep 0.3; done
until docker exec -i "$CT" psql -U postgres -d rfqtest -c 'SELECT 1' >/dev/null 2>&1; do sleep 0.3; done

PSQL="docker exec -i $CT psql -U postgres -d rfqtest -v ON_ERROR_STOP=1 -q"

reset_load() {   # reset schema+roles แล้วโหลด migration + (arg: seed files)
  $PSQL <<'SQL' >/dev/null 2>&1
DROP SCHEMA IF EXISTS rfq CASCADE;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_app')    THEN DROP OWNED BY rfq_app;    DROP ROLE rfq_app;    END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_ingest') THEN DROP OWNED BY rfq_ingest; DROP ROLE rfq_ingest; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rfq_owner')  THEN DROP OWNED BY rfq_owner;  DROP ROLE rfq_owner;  END IF;
END $$;
SQL
  for f in 001_rfq_core.sql 002_triggers.sql 003_field_policy.sql 005_service_layer_v2.sql 006_enq_ingest.sql 007_enq_extraction.sql 008_enq_orchestration.sql 009_enq_role_split.sql 010_rfq_signoff_v2.sql "$@"; do
    $PSQL < "$f" >/dev/null
  done
}

fail=0

run_sql_test() {   # $1 = test file (รันครั้งเดียว: POS3 ใน 020 เป็น mutating)
  local out rc
  out="$($PSQL < "$1" 2>&1)"; rc=$?
  echo "$out" | grep -oE "(PASS|FAIL|=====).*" || true
  [ "$rc" -eq 0 ] || { echo "  $(basename "$1") FAILED (rc=$rc)"; fail=1; }
}

echo "== 020 schema/invariant (with 010 seed) =="
reset_load test/010_seed_fixtures.sql
run_sql_test test/020_run_tests.sql

echo "== 030 service layer v2 =="
reset_load
run_sql_test test/030_service_tests.sql

echo "== 040 ENQ ingest (create_rfq_draft) =="
reset_load
run_sql_test test/040_ingest_tests.sql

echo "== 050 ENQ extraction (begin/claim/apply/fail) =="
reset_load
run_sql_test test/050_extraction_tests.sql

echo "== concurrency + security harness T01-T20 (2 connections) =="
reset_load
PYTHONIOENCODING=utf-8 "$PY" test/rfq_concurrency_tests.py || fail=1

echo ""
if [ "$fail" = 0 ]; then echo "ALL SUITES PASSED"; else echo "SOME SUITES FAILED"; fi
exit $fail
