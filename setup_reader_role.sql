-- Run this ONCE against your target Postgres database, using a connection
-- that has permission to create roles and grant privileges.
--
-- Creates a role that can only ever SELECT — never insert/update/delete —
-- regardless of what a prompt or a bug in the query layer tries to do.
-- This is a real security boundary (enforced by Postgres), not just an
-- application-level check.

-- First, check your actual database name if you don't already know it:
--   select current_database();

create role mcp_reader with login password 'REPLACE_WITH_A_STRONG_PASSWORD';

-- Replace <your_db_name> below with the value from current_database() above.
grant connect on database <your_db_name> to mcp_reader;
grant usage on schema public to mcp_reader;
grant select on all tables in schema public to mcp_reader;

-- Ensures tables created LATER (e.g. new migrations) are also readable
-- automatically, without re-running this grant every time.
alter default privileges in schema public grant select on tables to mcp_reader;
