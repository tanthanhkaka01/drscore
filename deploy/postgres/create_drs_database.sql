-- Run once by a PostgreSQL administrator (psql -U postgres -f create_drs_database.sql).
-- Creates the login DRS uses and its database. DRS itself creates the schemas "drs" and "drs_bi"
-- and every table on its first start (`serve`, or `python -m drscore db upgrade`).
-- Replace the password; it goes into the DRS_DB_PASSWORD environment variable, not into a file.

CREATE ROLE drs_app LOGIN PASSWORD 'change-me';
CREATE DATABASE drs OWNER drs_app ENCODING 'UTF8' TEMPLATE template0;

\connect drs
-- Nobody else may create objects in "public" or connect.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE drs FROM PUBLIC;
GRANT CONNECT ON DATABASE drs TO drs_app;

-- Variant for a DBA who does not want DRS to own the database: create the database owned by the
-- DBA, then only these two schemas for drs_app (DRS then creates the tables inside them):
--   CREATE SCHEMA drs AUTHORIZATION drs_app;
--   CREATE SCHEMA drs_bi AUTHORIZATION drs_app;
--   GRANT CONNECT ON DATABASE drs TO drs_app;

-- Optional, with Superset (docs/superset-setup.md): a read-only login for the BI tables only.
--   CREATE ROLE superset_reader LOGIN PASSWORD 'change-me-too';
--   GRANT CONNECT ON DATABASE drs TO superset_reader;
--   CREATE SCHEMA IF NOT EXISTS drs_bi AUTHORIZATION drs_app;
--   GRANT USAGE ON SCHEMA drs_bi TO superset_reader;
--   ALTER DEFAULT PRIVILEGES FOR ROLE drs_app IN SCHEMA drs_bi GRANT SELECT ON TABLES TO superset_reader;
