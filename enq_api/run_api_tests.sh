#!/usr/bin/env bash
# ephemeral PostgreSQL 16 + migrations + login roles → รัน enq_api/test_api.py (Codex H5)
# ใช้: PY=/path/to/python bash enq_api/run_api_tests.sh   (รันจาก repo root)
set -euo pipefail
PORT="${APITEST_PORT:-5468}"
NAME=rfq_apitest
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=rfqtest -p "${PORT}:5432" postgres:16 >/dev/null
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
until docker exec "$NAME" pg_isready -U postgres -q 2>/dev/null; do sleep 0.3; done
until docker exec -i "$NAME" psql -U postgres -d rfqtest -c 'SELECT 1' >/dev/null 2>&1; do sleep 0.3; done
PSQL="docker exec -i $NAME psql -U postgres -d rfqtest -v ON_ERROR_STOP=1 -q"
for f in 001_rfq_core 002_triggers 003_field_policy 005_service_layer_v2 006_enq_ingest 007_enq_extraction; do
  $PSQL < "migrations/${f}.sql" >/dev/null
done
$PSQL < enq_api/dev_roles.sql >/dev/null
export ENQ_API_KEY=test-key
unset ENQ_DEV_MODE || true
export RFQ_WRITE_DSN="host=localhost port=${PORT} dbname=rfqtest user=rfq_ingest_login password=ingest connect_timeout=5"
export RFQ_READ_DSN="host=localhost port=${PORT} dbname=rfqtest user=rfq_app_login password=app connect_timeout=5"
PYTHONIOENCODING=utf-8 "${PY:-python}" enq_api/test_api.py
