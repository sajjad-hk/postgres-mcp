"""
Query tools for your database. These are the functions exposed to Claude
via tool-calling — see TOOL_DEFINITIONS at the bottom for the JSON schema
the API needs.

Security model (defense in depth, two independent layers):
  1. DB-level: connects as a read-only Postgres role that only has SELECT
     granted — it CANNOT write even if a query tried to. See
     setup_reader_role.sql to create one.
  2. App-level: run_sql() also rejects anything that isn't a plain SELECT,
     as a fast-fail check before even hitting the database.

Use a dedicated read-only DATABASE_URL here — don't reuse a write-capable
connection string from elsewhere, so nothing an LLM's queries touch ever
has write access.

Optional write tool (run_write_sql): only when WRITE_DATABASE_URL is set.
It uses a SEPARATE connection as a role YOU scope with GRANTs (e.g. one
that may write a planning table but can't read secrets). There is
deliberately no keyword filtering — that role's grants are the only fence,
so never point WRITE_DATABASE_URL at an owner/admin connection. It is not
in TOOL_DEFINITIONS, so chat.py stays read-only.
"""

import os
import re

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
WRITE_DATABASE_URL = os.environ.get("WRITE_DATABASE_URL")

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 5000

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|call|copy)\b",
    re.IGNORECASE,
)


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(DATABASE_URL)
    conn.execute(f"set statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
    return conn


def list_tables() -> list[dict]:
    """List every table in the public schema, with row counts and the
    table's description (its Postgres COMMENT, if any) — databases can use
    those comments to explain how a table is meant to be used."""
    with _connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' order by table_name"
            ).fetchall()
        ]
        result = []
        for t in tables:
            count = conn.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: S608 — table names from information_schema, not user input
            description = conn.execute(
                "select obj_description(to_regclass(quote_ident(%s)), 'pg_class')", (t,)
            ).fetchone()[0]
            entry = {"table": t, "row_count": count}
            if description:
                entry["description"] = description
            result.append(entry)
        return result


def describe_table(table: str) -> dict:
    """Column names/types for a table, plus (for jsonb columns) the key
    names found in one sample row — since information_schema can't see
    inside jsonb, this is how the model discovers what's actually queryable
    with raw->>'field' syntax."""
    with _connect() as conn:
        # table name is used in an f-string below only after this identifier
        # check, since information_schema doesn't parameterize identifiers
        valid_tables = {
            r[0]
            for r in conn.execute(
                "select table_name from information_schema.tables where table_schema = 'public'"
            ).fetchall()
        }
        if table not in valid_tables:
            raise ValueError(f"Unknown table: {table!r}. Call list_tables() first.")

        # ordinal_position is the column's attnum, which col_description takes.
        columns = conn.execute(
            "select column_name, data_type, "
            "col_description(to_regclass(quote_ident(table_name)), ordinal_position) "
            "from information_schema.columns "
            "where table_schema = 'public' and table_name = %s order by ordinal_position",
            (table,),
        ).fetchall()
        description = conn.execute(
            "select obj_description(to_regclass(quote_ident(%s)), 'pg_class')", (table,)
        ).fetchone()[0]

        jsonb_columns = [c for c, dtype, _ in columns if dtype == "jsonb"]
        sample_keys = {}
        for col in jsonb_columns:
            row = conn.execute(f"select jsonb_object_keys({col}) from {table} limit 20").fetchall()  # noqa: S608
            sample_keys[col] = sorted({r[0] for r in row})

        result = {
            "table": table,
            "columns": [
                {"name": c, "type": t, **({"description": d} if d else {})} for c, t, d in columns
            ],
            "jsonb_keys_sample": sample_keys,
        }
        if description:
            result["description"] = description
        return result


def run_sql(query: str) -> list[dict]:
    """Execute a read-only SELECT query and return rows as a list of dicts.
    Capped at MAX_ROWS. Only SELECT is permitted — enforced here AND at the
    database role level."""
    q = query.strip().rstrip(";")

    if not q.lower().startswith("select") and not q.lower().startswith("with"):
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    if _FORBIDDEN.search(q):
        raise ValueError("Query contains a disallowed keyword.")

    with _connect() as conn:
        cur = conn.execute(q)
        if cur.description is None:
            return []
        cols = [d.name for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
        return [dict(zip(cols, r)) for r in rows]


def run_write_sql(query: str) -> dict:
    """Execute ONE SQL statement on the WRITE_DATABASE_URL connection and
    commit. No keyword filtering on purpose: the write role's GRANTs are the
    fence. Returns {"rowcount", "rows"} (rows only for RETURNING / SELECT,
    capped at MAX_ROWS)."""
    if not WRITE_DATABASE_URL:
        raise RuntimeError("WRITE_DATABASE_URL is not set — this server is read-only.")
    with psycopg.connect(WRITE_DATABASE_URL) as conn:
        conn.execute(f"set statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        cur = conn.execute(query)
        rows = []
        if cur.description is not None:
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(MAX_ROWS)]
        conn.commit()
        return {"rowcount": cur.rowcount, "rows": rows}


TOOL_DEFINITIONS = [
    {
        "name": "list_tables",
        "description": "List every table available to query, with row counts and each table's description (if the database documents it). Call this first if you don't already know the schema, and follow any usage notes in the descriptions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_table",
        "description": "Get column names/types (with column descriptions, if documented) and the table's description for a table, plus sample jsonb key names for any jsonb columns (if your data is stored as raw jsonb, use this to find the field names before writing a query against them with raw->>'field').",
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "Table name, from list_tables()"}},
            "required": ["table"],
        },
    },
    {
        "name": "run_sql",
        "description": "Run a read-only SELECT query against the database and get the results. Use describe_table first to know what columns/jsonb keys exist. Results are capped at 200 rows.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A SELECT (or WITH...SELECT) SQL query."}},
            "required": ["query"],
        },
    },
]


def call_tool(name: str, tool_input: dict):
    if name == "list_tables":
        return list_tables()
    if name == "describe_table":
        return describe_table(tool_input["table"])
    if name == "run_sql":
        return run_sql(tool_input["query"])
    raise ValueError(f"Unknown tool: {name}")
