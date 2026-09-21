#!/bin/bash
# ProcureAI — privileged first-boot hook for the `db` service.
#
# Runs ONCE, at first container start, from /docker-entrypoint-initdb.d,
# as the bootstrap superuser ($POSTGRES_USER, i.e. `postgres`).
# It performs the three steps that REQUIRE superuser, so that the runtime
# application role never needs superuser privileges:
#   1. CREATE ROLE <app> ... NOSUPERUSER   (least-privilege app role)
#   2. CREATE DATABASE <db> OWNER <app>   (app owns its schema objects)
#   3. CREATE EXTENSION vector            (superuser-only in stock Postgres)
#
# Everything after this — tables, IVFFlat indexes, policy seeding — runs
# as the unprivileged app role via the `init-db` service (database/db.py).
#
# This file is *sourced* by the postgres entrypoint (not executed in a
# subshell), so keep it `set -e` safe and avoid `exit` on the happy path
# idioms that would kill the entrypoint itself.

set -e

: "${APP_DB_USER:=procureai}"
: "${APP_DB_PASSWORD:=procureai}"
: "${APP_DB_NAME:=procureai_db}"

# Guard the identifiers we interpolate into SQL (these vars are
# reviewer-overridable, so validate before use).
for ident in "$APP_DB_USER" "$APP_DB_NAME"; do
  case "$ident" in
    ''|*[!a-zA-Z0-9_]*)
      echo "01-procureai.sh: refusing unsafe identifier: '$ident'" >&2
      return 1 2>/dev/null || exit 1
      ;;
  esac
done

# The password travels as a psql variable and is interpolated with :'var'
# (a quoted string literal), so special characters cannot break out of it.
# NOTE: psql does not interpolate variables inside DO $$ ... $$ blocks,
# so the conditional DDL below is written as plain SELECT ... \gexec,
# where :'var' interpolation works as documented.
psql -v ON_ERROR_STOP=1 \
     -v app_user="$APP_DB_USER" \
     -v app_password="$APP_DB_PASSWORD" \
     -v app_db="$APP_DB_NAME" \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" <<'EOSQL'
SELECT format(
         'CREATE ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE',
         :'app_user', :'app_password'
       )
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')\gexec
-- \gexec runs the generated CREATE DATABASE only when the DB is missing,
-- keeping this hook safe to re-run against an existing volume.
SELECT 'CREATE DATABASE "' || :'app_db' || '" OWNER "' || :'app_user' || '"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'app_db')\gexec
EOSQL

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$APP_DB_NAME" \
     -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "01-procureai.sh: role '$APP_DB_USER' (non-superuser), database '$APP_DB_NAME', and vector extension are ready."
