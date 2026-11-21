#!/bin/bash
# Runs ONCE, when the lt-pgdata volume is first initialised (official postgres image entrypoint).
# Two databases with separate owner roles; neither role may connect to the other's database (no dblink/postgres_fdw installed),
# so cross-portal traffic still has to go through the service APIs. pg_stat_statements lives in the "postgres" database only
# (its view is cluster-wide), so the application schemas stay exactly as restored from the Neon load-test branches.
set -euo pipefail
: "${LT_INSTITUTE_PASSWORD:?set in tests/load/config/local/postgres.env}"
: "${LT_STUDENT_PASSWORD:?set in tests/load/config/local/postgres.env}"
: "${DATA_SOURCE_USER:?set in tests/load/config/local/postgres.env}"
: "${DATA_SOURCE_PASS:?set in tests/load/config/local/postgres.env}"

psql -v ON_ERROR_STOP=1 --no-psqlrc --username "$POSTGRES_USER" --dbname postgres \
     -v inst_pw="$LT_INSTITUTE_PASSWORD" -v stu_pw="$LT_STUDENT_PASSWORD" \
     -v mon_user="$DATA_SOURCE_USER" -v mon_pw="$DATA_SOURCE_PASS" <<'SQL'
CREATE ROLE lt_institute LOGIN PASSWORD :'inst_pw';
CREATE ROLE lt_student   LOGIN PASSWORD :'stu_pw';
CREATE ROLE :"mon_user"  LOGIN PASSWORD :'mon_pw' IN ROLE pg_monitor;

CREATE DATABASE lt_institute OWNER lt_institute;
CREATE DATABASE lt_student   OWNER lt_student;

REVOKE ALL ON DATABASE lt_institute FROM PUBLIC;
REVOKE ALL ON DATABASE lt_student   FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres  FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO :"mon_user";

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SQL
