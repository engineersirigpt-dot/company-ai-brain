#!/usr/bin/env bash
# ephemeral PostgreSQL 16 + migrations + login roles → รัน enq_api/test_api.py (Codex H5)
# ใช้: PY=/path/to/python bash enq_api/run_api_tests.sh   (รันจาก repo root)
set -euo pipefail
# M2/H2: container ต่อ process + Docker เลือก host port เอง (atomic, ไม่ชน) + bind loopback เท่านั้น
NAME="rfq_apitest_$$"
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=rfqtest -p 127.0.0.1::5432 postgres:16 >/dev/null
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
until docker exec "$NAME" pg_isready -U postgres -q 2>/dev/null; do sleep 0.3; done
until docker exec -i "$NAME" psql -U postgres -d rfqtest -c 'SELECT 1' >/dev/null 2>&1; do sleep 0.3; done
PORT="$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"   # host port ที่ Docker จองบน 127.0.0.1
PSQL="docker exec -i $NAME psql -U postgres -d rfqtest -v ON_ERROR_STOP=1 -q"
for f in 001_rfq_core 002_triggers 003_field_policy 005_service_layer_v2 006_enq_ingest 007_enq_extraction 008_enq_orchestration 009_enq_role_split; do
  $PSQL < "migrations/${f}.sql" >/dev/null
done
$PSQL < enq_api/dev_roles.sql >/dev/null
export ENQ_API_KEY=test-key
unset ENQ_DEV_MODE || true
export RFQ_WRITE_DSN="host=127.0.0.1 port=${PORT} dbname=rfqtest user=rfq_ingest_login password=ingest connect_timeout=5"     # inbound
export RFQ_READ_DSN="host=127.0.0.1 port=${PORT} dbname=rfqtest user=rfq_read_api_login password=readapi connect_timeout=5"   # M1 read allowlist
export SUPER_DSN="host=127.0.0.1 port=${PORT} dbname=rfqtest user=postgres password=test connect_timeout=5"                   # F1 drift tests (grant/revoke)
PYTHONIOENCODING=utf-8 "${PY:-python}" enq_api/test_api.py
