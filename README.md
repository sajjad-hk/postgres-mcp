# postgres-mcp

A generic, reusable [MCP](https://modelcontextprotocol.io) server for
read-only querying of any Postgres database via Claude (or any
MCP-compatible client). It has no knowledge of any particular schema —
`list_tables`, `describe_table`, and `run_sql` work purely off
`information_schema` and generic jsonb introspection, so it can be pointed
at any Postgres database without code changes.

## Security model

Two independent layers, defense in depth:

1. **DB-level**: the server connects as a role that only has `SELECT`
   granted — it cannot write even if a query tried to. Create this role
   with `setup_reader_role.sql`.
2. **App-level**: `run_sql()` rejects anything that isn't a plain
   `SELECT` (or `WITH ... SELECT`), enforces a 200-row cap, and sets a
   5-second statement timeout — all as a fast-fail check before even
   hitting the database.

## Setup

1. Create a read-only role in your target database:
   - Open `setup_reader_role.sql`, replace the password placeholder and
     `<your_db_name>` with real values, then run it once against your
     database (e.g. via `psql` or your DB provider's SQL console).
2. Set your connection string in a `.env` file in this directory:
   ```
   DATABASE_URL=postgresql://mcp_reader:yourpassword@host:5432/yourdb
   ANTHROPIC_API_KEY=sk-ant-...
   ```
   (`ANTHROPIC_API_KEY` is only needed for `chat.py`, not for running the
   MCP server itself.)
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

## Documenting tables for Claude

`list_tables` and `describe_table` also return each table's and column's
Postgres `COMMENT`, and the server tells the client to read and follow
them. That's how a database explains itself without any schema-specific
code here — e.g. "this is the planning table, insert rows here" or "this is
a read-only mirror, never write here":

```sql
comment on table my_table is 'What this table is for and how to use it.';
comment on column my_table.my_col is 'What this column means.';
```

## Optional: a scoped write tool

By default the server is read-only. If you also set
`WRITE_DATABASE_URL`, it registers one more tool, `run_write_sql`, that
runs a single statement on that **separate** connection and commits it.

- The write connection must use a role **you** scope with GRANTs — e.g.
  allowed to write a planning table, but with no access to secrets or
  approval tables. There's no keyword filtering on purpose: the role's
  grants are the only fence. **Never** point it at an owner/admin URL.
- Unset → the tool isn't registered at all, so a read-only deployment
  never advertises a write tool. `chat.py` never gets it either.
- Example: [own-my-garmin-data](https://github.com/sajjad-hk/own-my-garmin-data)'s
  optional coach loop creates a `coach` role for exactly this
  (`uv run install.py --coach-loop` writes its URL to that repo's `.env`
  as `COACH_DATABASE_URL`); set that value as `WRITE_DATABASE_URL` here.

On the hosted platform, add `WRITE_DATABASE_URL` next to `DATABASE_URL`
in the dashboard's environment variables, redeploy, then use "Refresh
tools" in the connector settings so the new tool shows up.

## Local testing

Test the query layer directly first, without any MCP client in the loop:

```
python chat.py "what tables do I have?"
```

Then test it as an actual MCP server with the Inspector:

```
fastmcp dev inspector mcp_server.py
```

If the Inspector's Node toolchain gives you trouble (this has happened
before), fall back to running the server over HTTP directly and hitting
it with the FastMCP Python client:

```
fastmcp run mcp_server.py --transport http --port 8000
```

```python
from fastmcp import Client
import asyncio

async def main():
    async with Client("http://localhost:8000/mcp") as client:
        print(await client.call_tool("list_tables", {}))

asyncio.run(main())
```

## Deployment (Prefect Horizon)

FastMCP's hosted deployment platform is currently called **Prefect
Horizon** (it was previously "FastMCP Cloud" — this has rebranded
before, so double-check the current name/URL at
[gofastmcp.com/deployment](https://gofastmcp.com/deployment) before
following these steps, in case it's changed again).

1. Push this repo to GitHub — a real remote must exist first (see the
   git commands at the bottom of this README if you haven't already).
2. Go to the current platform's site (as of writing,
   [horizon.prefect.io](https://horizon.prefect.io)) and sign in with
   GitHub.
3. Connect this repository.
4. Configure the deployment:
   - **Entrypoint**: `mcp_server.py:mcp` — the `:mcp` part is the
     variable name the server object is assigned to in the file (see the
     `mcp = FastMCP(...)` line in `mcp_server.py`). If you ever rename
     that variable or move the file, this entrypoint string must be
     updated to match exactly.
   - **Authentication**: turn this **ON**. Interactive MCP clients like
     claude.ai and Claude Desktop require real OAuth discovery endpoints
     to connect — a server without authentication enabled will not work
     with those clients, even if it works fine when you test it directly
     with a raw API call or the FastMCP Python client.
   - **Environment variables**: add `DATABASE_URL` in the platform's own
     dashboard. This is separate from, and does **not** read, this
     project's local `.env` file — the value must be entered directly in
     the dashboard for the deployed server to have DB access.
5. Deploy, and copy the resulting server URL. It'll look something like
   `https://<your-server-name>.fastmcp.app/mcp` (the exact domain may
   vary — use whatever the platform actually shows you).
6. Before connecting it anywhere else, test it with the platform's own
   built-in Inspector/testing tool. Call `list_tables` there first — it
   needs no arguments, so it's the fastest way to confirm the deployed
   server can actually reach your database.

## Connecting to claude.ai

1. Go to claude.ai → Settings → Connectors → Add custom connector.
2. Paste the deployed server URL from the deployment step above.
3. Complete the OAuth prompt it walks you through.
4. Start a **new** chat (not one that predates the connector being
   added) and enable the connector in that chat.
5. Test it with a simple question like "what tables do I have?"
6. If you add new tools later and they don't show up, try "Refresh
   tools" in the connector's settings before assuming something's
   broken — this is a known caching behavior, not a bug.

## Limits

This is genuinely schema-agnostic, but not limitation-free:

- **Postgres-specific.** It uses `jsonb_object_keys()` and Postgres
  catalog syntax (`information_schema`, etc.). Pointing it at MySQL or
  SQLite would need real code changes to `db_tools.py`, not just a new
  connection string.
- **Schema-agnostic ≠ zero setup per database.** Each new target
  database still needs its own read-only role created
  (`setup_reader_role.sql`) and its own deployment (or at minimum its
  own `DATABASE_URL`) pointed at it. This isn't a single server that
  transparently serves multiple databases.

## Git setup

If you're starting from this code without git history yet:

```bash
git init
git add .
git commit -m "Initial commit: generic read-only Postgres MCP server"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```
