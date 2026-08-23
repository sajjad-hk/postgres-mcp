# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic",
#     "psycopg[binary]>=3.3",
#     "python-dotenv>=1.0",
# ]
# ///
"""
Minimal proof of the query access layer: ask a question about your database
in plain English, Claude explores the schema and queries the DB (read-only)
as needed, then answers.

Usage:
    python chat.py "How many rows are in the users table?"

Or run with no argument for an interactive loop.
"""

import os
import sys

from anthropic import Anthropic
from dotenv import load_dotenv

from db_tools import TOOL_DEFINITIONS, call_tool

load_dotenv()

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a data analyst with read-only access to a
database via SQL tools. Some data may be stored as raw jsonb, so always
call describe_table before querying a table you haven't inspected yet, to
find the real field names — don't guess them from the column name alone.
Answer the user's question clearly and concisely, in plain language, not as
a raw data dump. Cite specific numbers from what you actually queried."""


def ask(question: str) -> str:
    messages = [{"role": "user", "content": question}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result = call_tool(block.name, block.input)
                content = str(result)
            except Exception as e:
                content = f"Error: {e}"
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content}
            )

        messages.append({"role": "user", "content": tool_results})


def main() -> None:
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print(ask(question))
        return

    print("Ask a question about your database (Ctrl+C to quit):")
    while True:
        try:
            question = input("> ")
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not question.strip():
            continue
        print(ask(question))


if __name__ == "__main__":
    main()
