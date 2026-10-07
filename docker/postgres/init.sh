#!/bin/bash
# Runs once, when the PostgreSQL volume is created (docker-entrypoint-initdb.d).
#
#   drs_app          owns the DRS database: schema drs (metadata, users, cache, logs) and schema
#                    drs_bi (the dataset tables Superset reads).
#   superset         owns the Superset metadata database.
#   superset_reader  the login Superset uses to read DRS data: drs_bi only, read-only. It can
#                    NOT read the drs schema (password hashes, snapshots, grants).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE ROLE drs_app LOGIN PASSWORD '${DRS_DB_PASSWORD}';
    CREATE DATABASE drs OWNER drs_app;
    CREATE ROLE superset LOGIN PASSWORD '${SUPERSET_DB_PASSWORD}';
    CREATE DATABASE superset OWNER superset;
    CREATE ROLE superset_reader LOGIN PASSWORD '${SUPERSET_READER_PASSWORD}';
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname drs <<-EOSQL
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    REVOKE ALL ON DATABASE drs FROM PUBLIC;
    GRANT CONNECT ON DATABASE drs TO drs_app, superset_reader;
    CREATE SCHEMA drs AUTHORIZATION drs_app;
    CREATE SCHEMA drs_bi AUTHORIZATION drs_app;
    GRANT USAGE ON SCHEMA drs_bi TO superset_reader;
    -- DRS replaces a dataset table on every refresh: new tables get the grant automatically.
    ALTER DEFAULT PRIVILEGES FOR ROLE drs_app IN SCHEMA drs_bi GRANT SELECT ON TABLES TO superset_reader;
EOSQL
