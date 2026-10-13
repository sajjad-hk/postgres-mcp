"""
Remote MCP server exposing read-only database query tools to Claude (or
any MCP-compatible client), plus an optional run_write_sql tool that is
only registered when WRITE_DATABASE_URL is set (see db_tools.py).

Recommended: deploy via Prefect Horizon (or its current name — check
gofastmcp.com/deployment, this platform has rebranded before). It gives
you HTTPS, OAuth (required for claude.ai/Desktop to connect interactively
— a plain bearer token isn't enough for those clients), and auto-redeploy
on push, all on the free personal tier.

Local testing only:
    fastmcp dev mcp_server.py

Self-hosting instead of the hosted platform is possible
(mcp.run(transport="http", host="0.0.0.0", port=8000)) but then you're
responsible for implementing the OAuth flow yourself — genuinely more
work than it looks, per FastMCP's own docs. Don't reach for this unless
the hosted platform doesn't fit for some reason.
"""

import sys
import pathlib

# FastMCP's loader (both the CLI and hosted platforms like Prefect Horizon)
# imports this file directly by path rather than running it the normal way
# a `python mcp_server.py` invocation would — which does not reliably put
# even this file's own directory on sys.path, breaking the sibling import
# of db_tools below. Do this before anything else in the file.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fastmcp import FastMCP

from db_tools import (
    WRITE_DATABASE_URL,
    list_tables as _list_tables,
    describe_table as _describe_table,
    run_sql as _run_sql,
    run_write_sql as _run_write_sql,
)

mcp = FastMCP("postgres-mcp")


@mcp.tool(annotations={"readOnlyHint": True})
def list_tables() -> list[dict]:
    """List every table available to query, with row counts. Call this
    first if you don't already know the schema."""
    return _list_tables()


@mcp.tool(annotations={"readOnlyHint": True})
def describe_table(table: str) -> dict:
    """Column names/types for a table, plus sample jsonb key names for any
    jsonb columns. If your data is stored as raw jsonb, call this before
    querying a table's jsonb column, since the real field names aren't
    guessable from the column name alone."""
    return _describe_table(table)


@mcp.tool(annotations={"readOnlyHint": True})
def run_sql(query: str) -> list[dict]:
    """Run a read-only SELECT (or WITH...SELECT) query and get results as
    a list of dicts, capped at 200 rows. Any other statement type is
    rejected — this connection can only ever read."""
    return _run_sql(query)


# Registered only when a write connection is configured, so a read-only
# deployment never even advertises a write tool.
if WRITE_DATABASE_URL:
    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False})
    def run_write_sql(query: str) -> dict:
        """Run ONE SQL statement (INSERT/UPDATE/DELETE, optionally with
        RETURNING) with the server's scoped write role and commit it.
        Returns {"rowcount", "rows"}. The role's database grants decide what
        you may write — use describe_table to see the target table first.
        A permission-denied error means that table/column is outside the
        role's grants: don't retry or work around it. If the database
        documents a workflow for writes (e.g. a planning table that a human
        approves before anything happens), follow it."""
        return _run_write_sql(query)


if __name__ == "__main__":
    # Local dev only. The hosted platform runs this file directly and
    # handles transport/hosting itself — no mcp.run(transport="http", ...)
    # call needed for that path.
    mcp.run()
