#!/bin/bash -eux

# Dév fixture initializing a cluster with a «previous state», needing a lot of
# synchronization. See openldap-data.ldif for details.

psql <<EOSQL
-- Purge everything.
DROP DATABASE IF EXISTS olddb;
DROP DATABASE IF EXISTS appdb;
DELETE FROM pg_catalog.pg_auth_members;
DELETE FROM pg_catalog.pg_authid WHERE rolname != 'postgres' AND rolname NOT LIKE 'pg_%';
REVOKE TEMPORARY ON DATABASE postgres FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE template1 FROM PUBLIC;

-- Create role as it should be. for NOOP
CREATE ROLE app WITH NOLOGIN;
CREATE ROLE daniel WITH LOGIN;
CREATE ROLE david WITH LOGIN;
CREATE ROLE denis WITH LOGIN;
CREATE ROLE alan WITH SUPERUSER LOGIN;
-- Create alice superuser without login, for ALTER.
CREATE ROLE alice WITH SUPERUSER NOLOGIN IN ROLE app;

-- Create spurious roles, for DROP.
CREATE ROLE old WITH LOGIN;
CREATE ROLE omar;
CREATE ROLE olivier;
CREATE ROLE oscar WITH LOGIN IN ROLE app, old;
CREATE ROLE œdipe;

-- Create databases
CREATE DATABASE olddb;
CREATE DATABASE appdb WITH OWNER app;
EOSQL

# Create a legacy table owned by a legacy user. For reassign before drop
# cascade.
PGDATABASE=olddb PGUSER=oscar psql <<EOSQL
CREATE TABLE keepme (id serial PRIMARY KEY);
EOSQL

# grant some privileges to daniel, to be revoked.
PGDATABASE=olddb psql <<EOSQL
CREATE SCHEMA oldns;
CREATE TABLE oldns.table1 (id INTEGER);
GRANT SELECT ON ALL TABLES IN SCHEMA oldns TO daniel;

-- For REVOKE
GRANT USAGE ON SCHEMA oldns TO daniel;
ALTER DEFAULT PRIVILEGES IN SCHEMA oldns GRANT SELECT ON TABLES TO daniel;
EOSQL

# Ensure daniel has no privileges on appdb, for grant.
PGDATABASE=appdb psql <<EOSQL
CREATE TABLE public.table1 (id INTEGER);

CREATE SCHEMA appns;
CREATE TABLE appns.table1 (id INTEGER);
CREATE TABLE appns.table2 (id INTEGER);

CREATE SCHEMA empty;

-- No grant to olivier.
-- Partial grant for revoke
GRANT SELECT ON TABLE appns.table1 TO omar;
-- full grant for revoke
GRANT SELECT ON ALL TABLES IN SCHEMA appns TO oscar;

-- No grant to denis, for first grant.
-- Partial grant for regrant
GRANT SELECT ON TABLE appns.table1 TO daniel;
-- Full grant for noop
GRANT SELECT ON ALL TABLES IN SCHEMA appns TO david;
EOSQL
